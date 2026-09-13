#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OAG 评测脚本（RAGAS 框架）——被测入口换成 OAGService，其余复用 ../eval_rag_ragas.py。

被测链路（services/oag_service.py）：
  向量召回 → 实体链接（词面匹配 ∪ 向量分片 MENTIONS 反查）→ 图谱召回（1 跳子图）
  → RRF 融合 → LLM 生成（prompt 注入【图谱事实】）
  KB 未绑定本体 / 无实体链接时自动降级为纯向量（degraded=True，无 BM25/改写/重排）。

用法（脚本自动 chdir 到 backend/，相对路径按后端运行目录解析）：
  python scripts/rag_eval/oag_eval/eval_oag_ragas.py run --kb-id <KB_ID> \
      --testset scripts/rag_eval/eval_data/flight_ops_advisory_golden.jsonl
  python scripts/rag_eval/oag_eval/eval_oag_ragas.py score \
      --results scripts/rag_eval/oag_eval/eval_out/results_oag_xxx.jsonl

结果输出：scripts/rag_eval/oag_eval/eval_out/
  results_<tag>_<时间戳>.jsonl        逐条明细（含 entities / graph_retrieval_path）
  results_<tag>_<时间戳>.config.json  OAG 配置快照
  report_<tag>_<时间戳>.csv           ragas 逐条得分
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_RAG_EVAL_DIR = _SCRIPT_DIR.parent          # backend/scripts/rag_eval/
_BACKEND_DIR = _RAG_EVAL_DIR.parent         # backend/
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_RAG_EVAL_DIR))      # 便于 import 同级 eval_rag_ragas

import os  # noqa: E402

os.chdir(_BACKEND_DIR)

# Windows GBK 控制台兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 复用 RAG 评测脚本的通用设施：LLM 配置、指标定义、ragas 评分、工具函数
import eval_rag_ragas as base  # noqa: E402

# OAG 结果独立目录（score_rows 引用 base.EVAL_OUT_DIR，运行时覆盖生效）
base.EVAL_OUT_DIR = _SCRIPT_DIR / "eval_out"
EVAL_OUT_DIR = base.EVAL_OUT_DIR

from database import async_session  # noqa: E402
from services.oag_service import OAGService  # noqa: E402

# kb_id -> ontology_schema / kb_name 缓存（每库只加载一次）
_ONTOLOGY_CACHE: dict[str, dict | None] = {}
_KB_NAME_CACHE: dict[str, str] = {}


async def load_ontology_schema(kb_id: str) -> dict | None:
    """与路由层（routers/agent.py）一致：预加载本体抽取约束；未绑定/失败返回 None。"""
    if kb_id in _ONTOLOGY_CACHE:
        return _ONTOLOGY_CACHE[kb_id]
    from services.ontology_service import OntologyService

    schema: dict | None = None
    try:
        async with async_session() as session:
            schema = await OntologyService.get_kb_extraction_constraints(session, kb_id)
    except Exception as exc:
        print(f"[WARN] 加载本体 schema 失败（OAG 将无图谱事实可用）：{exc}")
    _ONTOLOGY_CACHE[kb_id] = schema
    bound = bool(schema and schema.get("ontologies"))
    print(f"[INFO] kb={kb_id} 本体抽取约束: {'已绑定（%d 个本体）' % len(schema['ontologies']) if bound else '未绑定 → OAG 将降级为纯向量'}")
    return schema


async def resolve_kb_name_by_id(kb_id: str) -> str:
    if kb_id in _KB_NAME_CACHE:
        return _KB_NAME_CACHE[kb_id]
    from models import KnowledgeBase

    async with async_session() as session:
        kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        sys.exit(f"[FAIL] 知识库不存在：kb_id={kb_id}")
    _KB_NAME_CACHE[kb_id] = kb.name or ""
    return _KB_NAME_CACHE[kb_id]


def config_snapshot() -> dict:
    """OAG 链路配置快照。"""
    from config import settings

    return {
        "pipeline": "oag",
        "oag_enabled": settings.OAG_ENABLED,
        "oag_vec_k": settings.OAG_VEC_K,
        "oag_top_n": settings.OAG_TOP_N,
        "oag_seed_entity_limit": settings.OAG_SEED_ENTITY_LIMIT,
        "oag_graph_chunk_limit": settings.OAG_GRAPH_CHUNK_LIMIT,
        "oag_rrf_k": settings.OAG_RRF_K,
        "oag_neighbor_hops": settings.OAG_NEIGHBOR_HOPS,
        "oag_neighbor_limit": settings.OAG_NEIGHBOR_LIMIT,
        "oag_bm25_enabled": settings.OAG_BM25_ENABLED,
        "oag_bm25_recall_k": settings.OAG_BM25_RECALL_K,
        "oag_empty_recall_threshold": settings.OAG_EMPTY_RECALL_THRESHOLD,
        "similarity_threshold": settings.SIMILARITY_THRESHOLD,
        "llm_model": settings.LLM_MODEL,
        "llm_temperature": settings.LLM_TEMPERATURE,
        "embedding_model": settings.EMBEDDING_MODEL,
    }


# ────────────────────────── run：跑 OAG 链路采集 ──────────────────────────

async def run_pipeline(args) -> list[dict]:
    items = base.load_jsonl(args.testset)
    if args.limit:
        items = items[: args.limit]
    if not items:
        sys.exit(f"[FAIL] 评测集为空或不存在：{args.testset}")

    if args.llm_temperature is not None:
        from config import settings

        settings.LLM_TEMPERATURE = args.llm_temperature

    default_kb_id = args.kb_id
    if not default_kb_id and args.kb_name:
        default_kb_id = await base.resolve_kb_name(args.kb_name)

    rows: list[dict] = []
    for i, item in enumerate(items, 1):
        q = (item.get("question") or "").strip()
        if not q:
            continue
        kb_id = item.get("kb_id") or default_kb_id
        if not kb_id:
            sys.exit(f"[FAIL] 第 {i} 条缺少 kb_id，且未提供 --kb-id 或 --kb-name")
        kb_name = item.get("kb_name") or await resolve_kb_name_by_id(kb_id)
        ontology_schema = await load_ontology_schema(kb_id)

        t0 = time.perf_counter()
        try:
            out = await OAGService.run(kb_id, q, kb_name, ontology_schema)
        except Exception as exc:  # 单条失败不中断
            out = {"answer": "", "chunks": [], "entities": [], "subgraph": {}, "error": str(exc)}
        latency = time.perf_counter() - t0

        chunks = out.get("chunks") or []
        rp = (out.get("subgraph") or {}).get("retrieval_path") or {}
        entities = out.get("entities") or []
        rows.append({
            "question": q,
            "reference": item.get("reference", ""),
            "kb_id": kb_id,
            "answer": out.get("answer", ""),
            "contexts": [c.get("text", "") for c in chunks],
            "retrieval_paths": [c.get("retrieval") for c in chunks],
            "entities": [e.get("name") for e in entities if e.get("name")],
            "graph_retrieval_path": rp,
            "latency_s": round(latency, 2),
            "error": out.get("error"),
        })
        err = f"  [ERROR] {rows[-1]['error']}" if rows[-1]["error"] else ""
        print(
            f"[{i}/{len(items)}] {latency:5.1f}s  ent={len(entities):<2d} "
            f"degraded={rp.get('degraded')}  {q[:36]}{err}"
        )

    tag = args.tag or "oag"
    results_path = Path(args.save_results) if args.save_results else \
        EVAL_OUT_DIR / f"results_{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    base.save_jsonl(rows, results_path)
    base.save_jsonl([config_snapshot()], results_path.with_suffix(".config.json"))
    return rows


# ────────────────────────── CLI ──────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="OAG 评测（RAGAS，复用 rag_eval 通用设施）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑 OAG 链路采集输出并评估")
    p_run.add_argument("--kb-id", default=None,
                       help="缺省知识库 id（评测集行内 kb_id 优先）")
    p_run.add_argument("--kb-name", default=None,
                       help="按名称解析缺省知识库（评测集行内 kb_name 优先）")
    p_run.add_argument("--testset", required=True, help="评测集 JSONL 路径")
    p_run.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    p_run.add_argument("--tag", default=None, help="结果文件标识（默认 oag）")
    p_run.add_argument("--save-results", default=None, help="results JSONL 输出路径")
    p_run.add_argument("--skip-score", action="store_true", help="只采集不算分")
    p_run.add_argument("--llm-temperature", type=float, default=None,
                       help="覆盖 LLM_TEMPERATURE（评测建议 0.1）")
    base.add_eval_args(p_run)

    p_score = sub.add_parser("score", help="对已保存的 results JSONL 重新算分")
    p_score.add_argument("--results", required=True, help="run 保存的 results JSONL")
    p_score.add_argument("--tag", default=None)
    base.add_eval_args(p_score)

    args = parser.parse_args()
    base.ensure_llm_config()
    if args.cmd == "run":
        rows = asyncio.run(run_pipeline(args))
        if not args.skip_score:
            base.score_rows(rows, args, fatal=False)
    elif args.cmd == "score":
        base.score_rows(base.load_jsonl(args.results), args)


if __name__ == "__main__":
    main()
