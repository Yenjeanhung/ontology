#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAG 评测脚本（RAGAS 框架）。

针对 services/rag_service.py 的混合检索链路（查询改写 → 向量+BM25 → RRF → Rerank → LLM 生成）
做端到端质量评估；同一评测集也可对 OAG 链路做横向对比与消融实验。

子命令：
  gen-testset  从知识库分片合成评测集（ragas TestsetGenerator，可选）
  run          跑链路采集 (question, contexts, answer)，并默认顺带评估
  score        对已保存的 results JSONL 重新算分（换指标/评估模型时免重跑链路）

评测集 JSONL 格式（每行一条）：
  {"question": "...", "reference": "标准答案", "kb_id": "可选，缺省用 --kb-id"}

用法（脚本自动 chdir 到 backend/，相对路径按后端运行目录解析）：
  python scripts/eval_rag_ragas.py run --kb-id <KB_ID> --testset scripts/eval_data/golden.jsonl
  python scripts/eval_rag_ragas.py run --kb-id <KB_ID> --testset golden.jsonl --no-bm25 --tag nobm25
  python scripts/eval_rag_ragas.py score --results eval_out/results_xxx.jsonl
  python scripts/eval_rag_ragas.py gen-testset --kb-id <KB_ID> --size 20 --out scripts/eval_data/testset_auto.jsonl

依赖：pip install "ragas>=0.2.6" datasets pandas
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))
# .env / CHROMA_PERSIST_DIR / UPLOAD_DIR 等相对路径均按 backend/ 解析
import os  # noqa: E402

os.chdir(_BACKEND_DIR)

# Windows GBK 控制台兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import settings  # noqa: E402
from database import async_session  # noqa: E402
from providers.embedding import create_embeddings  # noqa: E402
from providers.llm import create_llm  # noqa: E402
from providers.vector_store import list_kb_documents  # noqa: E402
from services.rag_service import RAGService  # noqa: E402

EVAL_OUT_DIR = _BACKEND_DIR / "eval_out"

# KB 名称 → id 解析缓存
_KB_ID_CACHE: dict[str, str] = {}


async def resolve_kb_name(kb_name: str) -> str:
    """按名称模糊解析知识库 id（唯一命中才通过，多个/零个时报错并列出候选）。"""
    if kb_name in _KB_ID_CACHE:
        return _KB_ID_CACHE[kb_name]
    from sqlalchemy import select

    from models import KnowledgeBase

    async with async_session() as session:
        rows = await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.name.like(f"%{kb_name}%"))
        )
        kbs = rows.scalars().all()
    if not kbs:
        sys.exit(f"[FAIL] 数据库中未找到名称含「{kb_name}」的知识库")
    if len(kbs) > 1:
        listing = "\n".join(f"  - {kb.id}  {kb.name}" for kb in kbs)
        sys.exit(
            f"[FAIL] 名称含「{kb_name}」的知识库有 {len(kbs)} 个，"
            f"请改用 --kb-id 精确指定：\n{listing}"
        )
    _KB_ID_CACHE[kb_name] = str(kbs[0].id)
    print(f"[INFO] 知识库「{kb_name}」 -> id={kbs[0].id}")
    return str(kbs[0].id)

# ────────────────────────── 指标定义 ──────────────────────────
# name → (ragas.metrics 类名, 是否需要 reference)
METRIC_SPECS = {
    "faithfulness": ("Faithfulness", False),
    "response_relevancy": ("ResponseRelevancy", False),
    "context_precision": ("LLMContextPrecisionWithReference", True),
    "context_recall": ("LLMContextRecall", True),
    "factual_correctness": ("FactualCorrectness", True),
    "noise_sensitivity": ("NoiseSensitivity", True),
}
DEFAULT_METRICS = "faithfulness,response_relevancy,context_precision,context_recall"


# ────────────────────────── 通用工具 ──────────────────────────

def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def save_jsonl(rows: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[OK] 已保存 {len(rows)} 条 -> {path}")


def _require_ragas() -> None:
    try:
        import ragas  # noqa: F401
    except ImportError:
        sys.exit('[FAIL] 缺少 ragas：请先安装  pip install "ragas>=0.2.6" datasets pandas')


def resolve_llm(args):
    """评估/合成用 LLM：默认复用项目 LLM，--eval-model 时用独立 ChatOpenAI。"""
    if getattr(args, "eval_model", None):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=args.eval_model,
            api_key=args.eval_api_key or os.environ.get("OPENAI_API_KEY", "EMPTY"),
            base_url=args.eval_base_url or None,
            temperature=0,
        )
    llm = create_llm()
    if llm is None:
        sys.exit(
            "[FAIL] 未配置 LLM（.env 的 LLM_PROVIDER / OPENAI_*），"
            "或用 --eval-model 指定评估模型"
        )
    return llm


def apply_ablation(args) -> dict:
    """消融开关：直接覆盖 settings（RAGService 每次调用时读取，即时生效）。"""
    if getattr(args, "no_bm25", False):
        settings.BM25_ENABLED = False
    if getattr(args, "no_rewrite", False):
        settings.QUERY_REWRITE_ENABLED = False
    if getattr(args, "no_rerank", False):
        settings.RERANK_ENABLED = False
    if getattr(args, "enable_rewrite", False):
        settings.QUERY_REWRITE_ENABLED = True
    if getattr(args, "enable_rerank", False):
        settings.RERANK_ENABLED = True
    temp = getattr(args, "llm_temperature", None)
    if temp is not None:
        settings.LLM_TEMPERATURE = temp
    return {
        "bm25_enabled": settings.BM25_ENABLED,
        "query_rewrite_enabled": settings.QUERY_REWRITE_ENABLED,
        "rerank_enabled": settings.RERANK_ENABLED,
        "hybrid_top_n": settings.HYBRID_TOP_N,
        "similarity_threshold": settings.SIMILARITY_THRESHOLD,
        "llm_model": settings.LLM_MODEL,
        "llm_temperature": settings.LLM_TEMPERATURE,
        "embedding_model": settings.EMBEDDING_MODEL,
    }


# ────────────────────────── run：跑链路采集 ──────────────────────────

async def run_pipeline(args) -> list[dict]:
    items = load_jsonl(args.testset)
    if args.limit:
        items = items[: args.limit]
    if not items:
        sys.exit(f"[FAIL] 评测集为空或不存在：{args.testset}")

    config_snapshot = apply_ablation(args)
    default_kb_id = args.kb_id
    if not default_kb_id and getattr(args, "kb_name", None):
        default_kb_id = await resolve_kb_name(args.kb_name)
    rows: list[dict] = []
    async with async_session() as session:
        for i, item in enumerate(items, 1):
            q = (item.get("question") or "").strip()
            if not q:
                continue
            if item.get("kb_name"):  # 行内 kb_name 优先
                kb_id = await resolve_kb_name(item["kb_name"])
            else:
                kb_id = item.get("kb_id") or default_kb_id
            if not kb_id:
                sys.exit(f"[FAIL] 第 {i} 条缺少 kb_id/kb_name，且未提供 --kb-id 或 --kb-name")
            t0 = time.perf_counter()
            try:
                out = await RAGService.query(session, kb_id, q)
            except Exception as exc:  # 单条失败不中断
                out = {"query": q, "answer": "", "chunks": [], "error": str(exc)}
            latency = time.perf_counter() - t0
            chunks = out.get("chunks") or []
            rows.append({
                "question": q,
                "reference": item.get("reference", ""),
                "kb_id": kb_id,
                "answer": out.get("answer", ""),
                "contexts": [c.get("text", "") for c in chunks],
                "retrieval_paths": [c.get("retrieval") for c in chunks],
                "latency_s": round(latency, 2),
                "error": out.get("error"),
            })
            err = f"  [ERROR] {rows[-1]['error']}" if rows[-1]["error"] else ""
            print(f"[{i}/{len(items)}] {latency:5.1f}s  {q[:40]}{err}")

    tag = args.tag or "run"
    results_path = Path(args.save_results) if args.save_results else \
        EVAL_OUT_DIR / f"results_{tag}_{_now_tag()}.jsonl"
    save_jsonl(rows, results_path)
    save_jsonl([config_snapshot], results_path.with_suffix(".config.json"))
    return rows


# ────────────────────────── score：ragas 评估 ──────────────────────────

def build_samples(rows: list[dict]):
    from ragas import SingleTurnSample

    samples = []
    for r in rows:
        samples.append(SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=[c for c in r.get("contexts", []) if c],
            response=r.get("answer", ""),
            reference=r.get("reference") or None,
        ))
    return samples


def build_metrics(names: list[str], has_reference: bool):
    import ragas.metrics as m

    metrics, skipped = [], []
    for name in names:
        if name not in METRIC_SPECS:
            sys.exit(
                f"[FAIL] 未知指标 {name}，可选：{', '.join(METRIC_SPECS)}"
            )
        cls_name, needs_ref = METRIC_SPECS[name]
        if needs_ref and not has_reference:
            skipped.append(name)
            continue
        cls = getattr(m, cls_name, None)
        if cls is None and cls_name == "ResponseRelevancy":
            cls = getattr(m, "AnswerRelevancy", None)  # ragas 旧版命名
        if cls is None:
            sys.exit(f"[FAIL] 当前 ragas 版本无 {cls_name}，请升级 ragas")
        metrics.append(cls())
    return metrics, skipped


def score_rows(rows: list[dict], args) -> None:
    _require_ragas()
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    has_reference = any((r.get("reference") or "").strip() for r in rows)
    names = [n.strip() for n in (args.metrics or DEFAULT_METRICS).split(",") if n.strip()]
    metrics, skipped = build_metrics(names, has_reference)
    if not metrics:
        sys.exit("[FAIL] 没有可评估的指标（评测集缺 reference 时无法跑需 reference 的指标）")
    for name in skipped:
        print(f"[WARN] 评测集无 reference，跳过指标: {name}")

    evaluator_llm = LangchainLLMWrapper(resolve_llm(args))
    evaluator_emb = LangchainEmbeddingsWrapper(create_embeddings())
    dataset = EvaluationDataset(build_samples(rows))

    print(f"\n[INFO] ragas 评估中：{len(rows)} 条 × {len(metrics)} 指标 ...")
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=evaluator_llm,
        embeddings=evaluator_emb,
        run_config=RunConfig(max_workers=4, max_retries=3, timeout=180),
        raise_exceptions=False,
        show_progress=True,
    )

    try:
        df = result.to_pandas()
    except ImportError:
        sys.exit("[FAIL] 需要 pandas：pip install pandas")
    tag = getattr(args, "tag", None) or "score"
    report_path = EVAL_OUT_DIR / f"report_{tag}_{_now_tag()}.csv"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(report_path, index=False, encoding="utf-8-sig")

    print("\n========== 评测结果 ==========")
    metric_names = [mt.name for mt in metrics]
    for name in metric_names:
        if name in df.columns:
            print(f"  {name:22s} {df[name].mean():.4f}")
    print(f"  样本数: {len(df)}")
    print(f"  逐条明细: {report_path}")
    print("  （NaN = 该条评估失败，见逐条明细列）")


# ────────────────────────── gen-testset：合成评测集 ──────────────────────────

def cmd_gen_testset(args) -> None:
    _require_ragas()
    from langchain_core.documents import Document
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.testset import TestsetGenerator

    kb_id = args.kb_id
    if not kb_id and args.kb_name:
        kb_id = asyncio.run(resolve_kb_name(args.kb_name))
    if not kb_id:
        sys.exit("[FAIL] 请提供 --kb-id 或 --kb-name")

    docs_raw = list_kb_documents(kb_id)
    docs = [
        Document(page_content=d.get("content") or "", metadata=d.get("metadata") or {})
        for d in docs_raw
        if (d.get("content") or "").strip()
    ]
    if not docs:
        sys.exit(f"[FAIL] kb_id={kb_id} 在向量库中没有分片，请先完成文件处理")
    rng = random.Random(args.seed)
    sample_docs = rng.sample(docs, k=min(args.sample_docs, len(docs)))
    print(f"[INFO] 语料：{len(docs)} 片中抽样 {len(sample_docs)} 片")

    generator = TestsetGenerator(
        llm=LangchainLLMWrapper(resolve_llm(args)),
        embedding_model=LangchainEmbeddingsWrapper(create_embeddings()),
    )
    try:
        testset = generator.generate_with_langchain_docs(sample_docs, test_size=args.size)
    except AttributeError:  # 兼容新版 API
        testset = generator.generate_with_llms(
            llm=LangchainLLMWrapper(resolve_llm(args)),
            embedding_model=LangchainEmbeddingsWrapper(create_embeddings()),
            test_size=args.size,
        )

    df = testset.to_pandas()
    col_q = "user_input" if "user_input" in df.columns else "question"
    col_r = "reference" if "reference" in df.columns else "ground_truth"
    rows = [
        {"question": str(r[col_q]).strip(), "reference": str(r[col_r]).strip(), "kb_id": kb_id}
        for _, r in df.iterrows()
        if str(r[col_q]).strip()
    ]
    save_jsonl(rows, args.out)
    print("[WARN] 合成集仅供参考，请人工抽查中文质量后再作为评测集使用")


# ────────────────────────── CLI ──────────────────────────

def add_eval_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--metrics", default=DEFAULT_METRICS,
                   help=f"逗号分隔，可选：{', '.join(METRIC_SPECS)}")
    p.add_argument("--eval-model", default=None,
                   help="评估用 LLM 模型（默认复用项目 LLM）")
    p.add_argument("--eval-api-key", default=None)
    p.add_argument("--eval-base-url", default=None,
                   help="评估用 LLM 的 OpenAI 兼容 base_url")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 评测（RAGAS）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑链路采集输出并评估")
    p_run.add_argument("--kb-id", default=None,
                       help="缺省知识库 id（评测集行内 kb_id/kb_name 优先）")
    p_run.add_argument("--kb-name", default=None,
                       help="按名称解析缺省知识库（评测集行内 kb_name 优先）")
    p_run.add_argument("--testset", required=True, help="评测集 JSONL 路径")
    p_run.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    p_run.add_argument("--tag", default=None, help="结果文件标识（消融对比时区分组名）")
    p_run.add_argument("--save-results", default=None, help="results JSONL 输出路径")
    p_run.add_argument("--skip-score", action="store_true", help="只采集不算分")
    # 消融开关
    p_run.add_argument("--no-bm25", action="store_true")
    p_run.add_argument("--no-rewrite", action="store_true")
    p_run.add_argument("--no-rerank", action="store_true")
    p_run.add_argument("--enable-rewrite", action="store_true")
    p_run.add_argument("--enable-rerank", action="store_true")
    p_run.add_argument("--llm-temperature", type=float, default=None,
                       help="覆盖 LLM_TEMPERATURE（评测建议 0.1）")
    add_eval_args(p_run)

    p_score = sub.add_parser("score", help="对已保存的 results JSONL 重新算分")
    p_score.add_argument("--results", required=True, help="run 保存的 results JSONL")
    p_score.add_argument("--tag", default=None)
    add_eval_args(p_score)

    p_gen = sub.add_parser("gen-testset", help="从知识库分片合成评测集")
    p_gen.add_argument("--kb-id", default=None)
    p_gen.add_argument("--kb-name", default=None, help="按名称定位知识库")
    p_gen.add_argument("--size", type=int, default=20, help="生成问题数")
    p_gen.add_argument("--sample-docs", type=int, default=40, help="送入生成的分片抽样数")
    p_gen.add_argument("--seed", type=int, default=42)
    p_gen.add_argument("--out", default="scripts/eval_data/testset_auto.jsonl")
    add_eval_args(p_gen)

    args = parser.parse_args()
    if args.cmd == "run":
        rows = asyncio.run(run_pipeline(args))
        if not args.skip_score:
            score_rows(rows, args)
    elif args.cmd == "score":
        score_rows(load_jsonl(args.results), args)
    elif args.cmd == "gen-testset":
        cmd_gen_testset(args)


if __name__ == "__main__":
    main()
