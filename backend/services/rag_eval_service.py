"""RAG 评测服务：评测集管理、在线评测任务、Badcase 标记与回流。

核心评测逻辑从 scripts/rag_eval/eval_rag_ragas.py 抽取而来，CLI 脚本与本服务共用
同一套指标定义与评分实现（脚本保留离线跑分、消融实验、TestsetGenerator 合成场景）。

评测执行模型（doc/知识库/RAG评测/RAG评测页面设计.md §4）：
- 全局同时只允许 1 个 running 任务（防 LLM 限流拖垮服务），新任务直接拒绝；
- 逐条采集即时落库（页面轮询可见进度），单条失败不中断；
- 采集完成后跑 ragas 评分回填逐条分数与指标均值，评分失败不影响已留存数据；
- 消融开关通过 ContextVar 做 asyncio task 级隔离，不污染并发的用户问答请求。
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import os
from datetime import datetime

# ragas 每次评估都向其官方遥测端点上报（当前网络必超时，白白占用评分线程），默认关闭
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "True")

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    EvalRun,
    EvalRunItem,
    EvalTestset,
    EvalTestsetItem,
    LLMConfig,
)
# 消融开关隔离（ContextVar 按 asyncio task 隔离，读取方 rag_service._cfg）
from services.rag_service import ablation_override

logger = logging.getLogger(__name__)


def _patch_ragas_vertexai_import() -> None:
    """ragas 0.4.x 在模块顶层硬 import langchain_community 的 Vertex 系列，
    而 langchain-community >=0.4 已移除 chat_models.vertexai 模块，导致 import ragas 直接失败。
    ragas 中这两个类仅用于「是否支持多补全」的 isinstance 判断（MULTIPLE_COMPLETION_SUPPORTED），
    评估实际走 OpenAI 系 LLM，缺 VertexAI 时注入空实现即可，无任何行为影响。
    """
    import sys
    import types

    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
        from langchain_community.llms import VertexAI  # noqa: F401
        return
    except (ModuleNotFoundError, ImportError):
        pass
    stub = types.ModuleType("langchain_community.chat_models.vertexai")
    stub.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules.setdefault("langchain_community.chat_models.vertexai", stub)
    import langchain_community.llms as _llms

    if not hasattr(_llms, "VertexAI"):
        _llms.VertexAI = type("VertexAI", (), {})


_patch_ragas_vertexai_import()


def _now() -> str:
    return datetime.now().isoformat()


# ────────────────────────── 指标定义（与脚本一致）──────────────────────────
# name → (ragas.metrics 类名, 是否需要 reference)
METRIC_SPECS: dict[str, tuple[str, bool]] = {
    "faithfulness": ("Faithfulness", False),
    "response_relevancy": ("ResponseRelevancy", False),
    "context_precision": ("LLMContextPrecisionWithReference", True),
    "context_recall": ("LLMContextRecall", True),
    "factual_correctness": ("FactualCorrectness", True),
    "noise_sensitivity": ("NoiseSensitivity", True),
}
DEFAULT_METRICS = ["faithfulness", "response_relevancy", "context_precision", "context_recall"]

BADCASE_REASONS = [
    "retrieval_miss",   # 检索未召回
    "bad_ranking",      # 排序差
    "hallucination",    # 答案幻觉
    "off_topic",        # 答非所问
    "wrong_label",      # 标注错误（回流前需修正标注）
    "other",
]

ABLACTION_KEYS = ("BM25_ENABLED", "QUERY_REWRITE_ENABLED", "RERANK_ENABLED")


# ────────────────────────── 评测 LLM（ragas 评估用）──────────────────────────

async def resolve_eval_llm(db: AsyncSession, llm_config_id: str | None):
    """评估用 LLM：指定 llm_config_id 用该套配置，否则跟随页面生效配置。

    与脚本 resolve_llm(--eval-model) 逻辑一致，数据源从命令行参数换成 llm_configs 表。
    """
    from langchain_openai import ChatOpenAI

    row: LLMConfig | None = None
    if llm_config_id:
        row = await db.get(LLMConfig, llm_config_id)
        if row is None:
            raise ValueError(f"模型配置不存在：{llm_config_id}")
    else:
        row = (await db.execute(
            select(LLMConfig).where(LLMConfig.is_active == 1)
        )).scalars().first()
    if row is not None and row.api_key and row.model:
        return ChatOpenAI(
            model=row.model,
            api_key=row.api_key,
            base_url=row.base_url or None,
            temperature=0,
        ), row
    # 无页面配置时回退 .env / settings（与脚本 ensure_llm_config 的兜底一致）
    llm = _default_llm_from_settings()
    return llm, None


def _default_llm_from_settings():
    from providers.llm import create_llm

    llm = create_llm()
    if llm is None:
        raise ValueError("未配置评估模型：请到「模型配置」页配置，或发起评测时指定模型配置")
    return llm


async def list_llm_options(db: AsyncSession) -> list[dict]:
    """评估模型下拉选项：模型配置页全部配置的精简视图（不暴露密钥）。"""
    rows = (await db.execute(
        select(LLMConfig).order_by(LLMConfig.is_active.desc(), LLMConfig.created_at)
    )).scalars().all()
    return [
        {"id": r.id, "name": r.name, "model": r.model, "is_active": bool(r.is_active)}
        for r in rows
    ]


# ────────────────────────── 评分（ragas）──────────────────────────

def _build_metrics(names: list[str], has_reference: bool):
    """按名构建 ragas 指标实例；缺 reference 时跳过需 reference 的指标。"""
    import ragas.metrics as m

    metrics, skipped = [], []
    for name in names:
        if name not in METRIC_SPECS:
            raise ValueError(f"未知指标 {name}，可选：{', '.join(METRIC_SPECS)}")
        cls_name, needs_ref = METRIC_SPECS[name]
        if needs_ref and not has_reference:
            skipped.append(name)
            continue
        cls = getattr(m, cls_name, None)
        if cls is None and cls_name == "ResponseRelevancy":
            cls = getattr(m, "AnswerRelevancy", None)  # ragas 旧版命名
        if cls is None:
            raise ValueError(f"当前 ragas 版本无 {cls_name}，请升级 ragas")
        metrics.append(cls())
    return metrics, skipped


def _clean_score(v) -> float | None:
    """NaN / None → None（JSON 可序列化，均值计算时忽略）。"""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v, 4)


def score_rows_sync(rows: list[dict], metric_names: list[str], eval_llm):
    """ragas 评分（同步阻塞，内部线程池并行；调用方用 asyncio.to_thread 包装）。

    返回 (逐条分数 {run_item_id: {metric: score}}, 均值 {metric: mean},
          各指标成功评分条数 {metric: n}, 跳过的指标)。
    rows 元素需含 id/question/reference/answer/contexts。
    逐条 LLM 输出解析失败/超时时 ragas 静默置 NaN（raise_exceptions=False），
    由 _clean_score 转为 None 并不计入均值——scored 计数用于向页面暴露覆盖面。
    """
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas import SingleTurnSample
    from providers.embedding import create_embeddings

    has_reference = any((r.get("reference") or "").strip() for r in rows)
    metrics, skipped = _build_metrics(metric_names, has_reference)
    if not metrics:
        raise ValueError("没有可评估的指标（评测集缺 reference 时无法跑需 reference 的指标）")

    samples = [
        SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=[c for c in (r.get("contexts") or []) if c],
            response=r.get("answer") or "",
            reference=(r.get("reference") or "").strip() or None,
        )
        for r in rows
    ]
    result = evaluate(
        dataset=EvaluationDataset(samples),
        metrics=metrics,
        llm=LangchainLLMWrapper(eval_llm),
        embeddings=LangchainEmbeddingsWrapper(create_embeddings()),
        run_config=RunConfig(max_workers=4, max_retries=5, timeout=240),
        raise_exceptions=False,
        show_progress=False,
    )
    # result.scores 与 dataset 顺序对齐：每条为 {metric_name: score|NaN}
    per_item: dict[str, dict] = {}
    means: dict[str, float] = {}
    scored: dict[str, int] = {}
    metric_names_used = [mt.name for mt in metrics]
    for row, scores in zip(rows, result.scores):
        per_item[row["id"]] = {name: _clean_score(scores.get(name)) for name in metric_names_used}
    for name in metric_names_used:
        vals = [s[name] for s in per_item.values() if s.get(name) is not None]
        scored[name] = len(vals)
        if vals:
            means[name] = round(sum(vals) / len(vals), 4)
    return per_item, means, scored, skipped


# ────────────────────────── 评测集管理 ──────────────────────────

def _ts_row(t: EvalTestset, item_count: int = 0, last_run: dict | None = None) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description or "",
        "default_kb_id": t.default_kb_id or "",
        "source": t.source,
        "item_count": item_count,
        "last_run": last_run,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


async def list_testsets(db: AsyncSession) -> list[dict]:
    sets = (await db.execute(
        select(EvalTestset).order_by(EvalTestset.updated_at.desc())
    )).scalars().all()
    counts = dict((await db.execute(
        select(EvalTestsetItem.testset_id, func.count())
        .where(EvalTestsetItem.enabled == 1)
        .group_by(EvalTestsetItem.testset_id)
    )).all())
    # 各评测集最近一次已完成任务的指标均值（列表页横向对比）
    last_runs: dict[str, dict] = {}
    runs = (await db.execute(
        select(EvalRun).where(EvalRun.status == "done")
        .order_by(EvalRun.finished_at.desc())
    )).scalars().all()
    for r in runs:
        if r.testset_id not in last_runs and r.metrics_summary_json:
            last_runs[r.testset_id] = {
                "run_id": r.id,
                "name": r.name,
                "finished_at": r.finished_at,
                "metrics": json.loads(r.metrics_summary_json),
            }
    return [_ts_row(t, counts.get(t.id, 0), last_runs.get(t.id)) for t in sets]


async def create_testset(db: AsyncSession, payload: dict) -> dict:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("评测集名称不能为空")
    dup = (await db.execute(
        select(EvalTestset).where(EvalTestset.name == name)
    )).scalars().first()
    if dup:
        raise ValueError(f"评测集名称已存在：{name}")
    t = EvalTestset(
        name=name,
        description=payload.get("description") or "",
        default_kb_id=payload.get("default_kb_id") or "",
        source=payload.get("source") or "manual",
    )
    db.add(t)
    await db.commit()
    return _ts_row(t)


async def update_testset(db: AsyncSession, testset_id: str, payload: dict) -> dict:
    t = await db.get(EvalTestset, testset_id)
    if not t:
        raise ValueError("评测集不存在")
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("评测集名称不能为空")
        dup = (await db.execute(
            select(EvalTestset).where(EvalTestset.name == name, EvalTestset.id != testset_id)
        )).scalars().first()
        if dup:
            raise ValueError(f"评测集名称已存在：{name}")
        t.name = name
    for k in ("description", "default_kb_id"):
        if k in payload:
            setattr(t, k, payload.get(k) or "")
    t.updated_at = _now()
    await db.commit()
    return _ts_row(t)


async def delete_testset(db: AsyncSession, testset_id: str) -> None:
    t = await db.get(EvalTestset, testset_id)
    if not t:
        raise ValueError("评测集不存在")
    in_use = (await db.execute(
        select(func.count()).select_from(EvalRun).where(EvalRun.testset_id == testset_id)
    )).scalar() or 0
    if in_use:
        raise ValueError(f"该评测集已被 {in_use} 个评测任务引用，不可删除（可清空条目后停用）")
    await db.execute(delete(EvalTestsetItem).where(EvalTestsetItem.testset_id == testset_id))
    await db.delete(t)
    await db.commit()


def _item_row(i: EvalTestsetItem) -> dict:
    return {
        "id": i.id,
        "testset_id": i.testset_id,
        "question": i.question,
        "reference": i.reference or "",
        "kb_id": i.kb_id or "",
        "enabled": bool(i.enabled),
        "origin": i.origin,
        "source_run_item_id": i.source_run_item_id or "",
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    }


async def list_items(
    db: AsyncSession, testset_id: str, *,
    origin: str = "", keyword: str = "", enabled: int | None = None,
    page: int = 1, page_size: int = 50,
) -> dict:
    q = select(EvalTestsetItem).where(EvalTestsetItem.testset_id == testset_id)
    if origin:
        q = q.where(EvalTestsetItem.origin == origin)
    if enabled is not None:
        q = q.where(EvalTestsetItem.enabled == enabled)
    if keyword:
        q = q.where(EvalTestsetItem.question.ilike(f"%{keyword}%"))
    total = (await db.execute(
        select(func.count()).select_from(q.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        q.order_by(EvalTestsetItem.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [_item_row(i) for i in rows],
    }


async def _add_items(db: AsyncSession, testset_id: str, rows: list[dict],
                     origin: str, *, skip_duplicates: bool = True) -> dict:
    """批量插入条目。rows 元素：{question, reference?, kb_id?}。去重键 (testset_id, question)。"""
    added, duplicates, invalid = 0, 0, 0
    for row in rows:
        question = (row.get("question") or "").strip()
        if not question:
            invalid += 1
            continue
        if skip_duplicates:
            dup = (await db.execute(
                select(EvalTestsetItem.id).where(
                    EvalTestsetItem.testset_id == testset_id,
                    EvalTestsetItem.question == question,
                )
            )).scalars().first()
            if dup:
                duplicates += 1
                continue
        db.add(EvalTestsetItem(
            testset_id=testset_id,
            question=question,
            reference=(row.get("reference") or "").strip(),
            kb_id=(row.get("kb_id") or "").strip(),
            origin=origin,
        ))
        added += 1
    if added:
        t = await db.get(EvalTestset, testset_id)
        if t:
            t.updated_at = _now()
    await db.commit()
    return {"added": added, "duplicates": duplicates, "invalid": invalid}


async def add_item(db: AsyncSession, testset_id: str, payload: dict) -> dict:
    if not await db.get(EvalTestset, testset_id):
        raise ValueError("评测集不存在")
    res = await _add_items(db, testset_id, [payload], payload.get("origin") or "manual")
    if res["added"] == 0:
        raise ValueError("新增失败：条目为空或已存在")
    return res


async def update_item(db: AsyncSession, testset_id: str, item_id: str, payload: dict) -> dict:
    i = await db.get(EvalTestsetItem, item_id)
    if not i or i.testset_id != testset_id:
        raise ValueError("条目不存在")
    if "question" in payload:
        question = (payload.get("question") or "").strip()
        if not question:
            raise ValueError("question 不能为空")
        dup = (await db.execute(
            select(EvalTestsetItem.id).where(
                EvalTestsetItem.testset_id == testset_id,
                EvalTestsetItem.question == question,
                EvalTestsetItem.id != item_id,
            )
        )).scalars().first()
        if dup:
            raise ValueError("同问题条目已存在")
        i.question = question
    if "reference" in payload:
        i.reference = (payload.get("reference") or "").strip()
    if "kb_id" in payload:
        i.kb_id = (payload.get("kb_id") or "").strip()
    if "enabled" in payload:
        i.enabled = 1 if payload.get("enabled") else 0
    i.updated_at = _now()
    await db.commit()
    return _item_row(i)


async def delete_item(db: AsyncSession, testset_id: str, item_id: str) -> None:
    i = await db.get(EvalTestsetItem, item_id)
    if not i or i.testset_id != testset_id:
        raise ValueError("条目不存在")
    await db.delete(i)
    await db.commit()


# ────────────────────────── 导入 / 导出 ──────────────────────────

_REF_ALIASES = ("reference", "ground_truth", "标准答案", "答案")
_Q_ALIASES = ("question", "user_input", "问题")
_KB_ALIASES = ("kb_id", "知识库id", "知识库")


def _pick(d: dict, aliases) -> str:
    for a in aliases:
        if a in d and d[a] is not None:
            return str(d[a]).strip()
    return ""


async def import_items(db: AsyncSession, testset_id: str, filename: str, content: bytes) -> dict:
    """上传导入：JSONL / CSV / XLSX。列名兼容 ragas 合成集（user_input/ground_truth）。"""
    if not await db.get(EvalTestset, testset_id):
        raise ValueError("评测集不存在")
    lower = (filename or "").lower()
    rows: list[dict] = []
    if lower.endswith((".jsonl", ".json")):
        text = content.decode("utf-8-sig")
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            obj = json.loads(ln)
            if isinstance(obj, list):
                rows.extend(obj)
            else:
                rows.append(obj)
    elif lower.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows.extend(dict(r) for r in reader)
    elif lower.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
        except ImportError as exc:
            raise ValueError("导入 Excel 需要 openpyxl：pip install openpyxl") from exc
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        headers: list[str] = []
        for row in ws.iter_rows(values_only=True):
            if not headers:
                headers = [str(c or "").strip() for c in row]
                continue
            rows.append({
                headers[idx]: c for idx, c in enumerate(row)
                if idx < len(headers) and c is not None
            })
        wb.close()
    else:
        raise ValueError("仅支持 JSONL / CSV / XLSX 文件")
    norm = [
        {
            "question": _pick(r, _Q_ALIASES),
            "reference": _pick(r, _REF_ALIASES),
            "kb_id": _pick(r, _KB_ALIASES),
        }
        for r in rows if isinstance(r, dict)
    ]
    res = await _add_items(db, testset_id, norm, "upload")
    return {**res, "parsed": len(norm)}


async def export_items(db: AsyncSession, testset_id: str, fmt: str = "jsonl") -> tuple[str, str, bytes]:
    """导出条目。返回 (filename, mime, content)。"""
    t = await db.get(EvalTestset, testset_id)
    if not t:
        raise ValueError("评测集不存在")
    items = (await db.execute(
        select(EvalTestsetItem).where(EvalTestsetItem.testset_id == testset_id)
        .order_by(EvalTestsetItem.created_at)
    )).scalars().all()
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["question", "reference", "kb_id", "origin", "enabled"])
        for i in items:
            writer.writerow([i.question, i.reference or "", i.kb_id or "", i.origin, i.enabled])
        return f"{t.name}.csv", "text/csv", buf.getvalue().encode("utf-8-sig")
    # 默认 JSONL（可直接喂给 eval_rag_ragas.py run --testset）
    lines = [
        json.dumps({"question": i.question, "reference": i.reference or "", "kb_id": i.kb_id or ""},
                   ensure_ascii=False)
        for i in items if i.enabled
    ]
    return f"{t.name}.jsonl", "application/jsonl", ("\n".join(lines) + "\n").encode("utf-8")


# ────────────────────────── 评测任务 ──────────────────────────

# 进程内正在执行的任务（单任务并发控制 + 取消句柄）
_active_run_tasks: dict[str, asyncio.Task] = {}

# 进度事件 SSE 订阅者（参照 files.py 状态推送模式：执行器广播 → /eval/runs/stream 推送）
_event_subscribers: set[asyncio.Queue] = set()


def subscribe_events() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _event_subscribers.add(q)
    return q


def unsubscribe_events(q: asyncio.Queue) -> None:
    _event_subscribers.discard(q)


def _publish_event(payload: dict) -> None:
    """广播进度事件；慢消费者队列满则丢弃（前端收到任意事件即拉列表，丢事件自愈）。"""
    for q in list(_event_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def _run_row(r: EvalRun, testset_name: str = "") -> dict:
    return {
        "id": r.id,
        "testset_id": r.testset_id,
        "testset_name": testset_name,
        "kb_id": r.kb_id or "",
        "name": r.name,
        "status": r.status,
        "config": json.loads(r.config_json or "{}"),
        "total": r.total,
        "done": r.done,
        "failed_count": r.failed_count,
        "metrics": json.loads(r.metrics_summary_json) if r.metrics_summary_json else {},
        "scored": json.loads(r.scored_count_json) if r.scored_count_json else {},
        "error": r.error or "",
        "created_by": r.created_by or "",
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


async def _testset_name_map(db: AsyncSession, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = (await db.execute(
        select(EvalTestset.id, EvalTestset.name).where(EvalTestset.id.in_(ids))
    )).all()
    return {rid: name for rid, name in rows}


async def list_runs(db: AsyncSession, testset_id: str = "", page: int = 1, page_size: int = 20) -> dict:
    q = select(EvalRun)
    if testset_id:
        q = q.where(EvalRun.testset_id == testset_id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    runs = (await db.execute(
        q.order_by(EvalRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    names = await _testset_name_map(db, [r.testset_id for r in runs])
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [_run_row(r, names.get(r.testset_id, "")) for r in runs],
    }


async def get_run(db: AsyncSession, run_id: str) -> dict:
    r = await db.get(EvalRun, run_id)
    if not r:
        raise ValueError("评测任务不存在")
    names = await _testset_name_map(db, [r.testset_id])
    return _run_row(r, names.get(r.testset_id, ""))


def _run_item_row(it: EvalRunItem, *, with_contexts: bool = False) -> dict:
    data = {
        "id": it.id,
        "run_id": it.run_id,
        "item_id": it.item_id or "",
        "question": it.question,
        "reference": it.reference or "",
        "kb_id": it.kb_id or "",
        "answer": it.answer or "",
        "latency_s": it.latency_s,
        "error": it.error or "",
        "metric_scores": json.loads(it.metric_scores_json) if it.metric_scores_json else {},
        "is_badcase": bool(it.is_badcase),
        "badcase_reason": it.badcase_reason or "",
        "badcase_note": it.badcase_note or "",
        "marked_by": it.marked_by or "",
        "marked_at": it.marked_at,
        "created_at": it.created_at,
    }
    if with_contexts:
        data["contexts"] = json.loads(it.contexts_json or "[]")
        data["retrieval_paths"] = json.loads(it.retrieval_paths_json or "[]")
    return data


async def list_run_items(
    db: AsyncSession, run_id: str, *,
    filter: str = "", low_score_metric: str = "faithfulness", low_score_threshold: float = 0.6,
    page: int = 1, page_size: int = 20,
) -> dict:
    """逐条结果分页。filter: all / badcase / error / low_score。"""
    q = select(EvalRunItem).where(EvalRunItem.run_id == run_id)
    if filter == "badcase":
        q = q.where(EvalRunItem.is_badcase == 1)
    elif filter == "error":
        q = q.where(EvalRunItem.error != "")
    elif filter == "low_score":
        # 逐条分数在 JSON 字段里，跨库通用做法是 Python 侧过滤（量级 = 单次评测规模，可接受）
        rows = (await db.execute(q.order_by(EvalRunItem.created_at))).scalars().all()
        matched = []
        for it in rows:
            try:
                scores = json.loads(it.metric_scores_json or "{}")
            except json.JSONDecodeError:
                continue
            v = scores.get(low_score_metric)
            if v is not None and v < low_score_threshold:
                matched.append(it)
        total = len(matched)
        page_items = matched[(page - 1) * page_size: page * page_size]
        return {"total": total, "page": page, "page_size": page_size,
                "items": [_run_item_row(it) for it in page_items]}
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    items = (await db.execute(
        q.order_by(EvalRunItem.created_at).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_run_item_row(it) for it in items]}


async def get_run_item(db: AsyncSession, run_id: str, item_id: str) -> dict:
    it = await db.get(EvalRunItem, item_id)
    if not it or it.run_id != run_id:
        raise ValueError("结果条目不存在")
    return _run_item_row(it, with_contexts=True)


async def create_run(db: AsyncSession, payload: dict, username: str) -> dict:
    """创建评测任务并启动后台执行。全局同时仅允许 1 个 running/pending 任务。"""
    testset_id = payload.get("testset_id") or ""
    t = await db.get(EvalTestset, testset_id)
    if not t:
        raise ValueError("评测集不存在")
    # 惰性僵尸清理：服务重启后 DB 里遗留的 running/pending/scoring 标记为 failed
    stale = (await db.execute(
        select(EvalRun).where(EvalRun.status.in_(("running", "pending", "scoring")))
    )).scalars().all()
    for s in stale:
        if s.id not in _active_run_tasks:
            s.status = "failed"
            s.error = "服务重启导致任务中断，请重新发起"
            s.finished_at = _now()
    live = [s for s in stale if s.id in _active_run_tasks]
    if live:
        raise ValueError(f"已有评测任务在执行（{live[0].name or live[0].id}），完成后再发起")

    metrics = payload.get("metrics") or list(DEFAULT_METRICS)
    unknown = [m for m in metrics if m not in METRIC_SPECS]
    if unknown:
        raise ValueError(f"未知指标：{', '.join(unknown)}")
    ablation = payload.get("ablation") or {}
    ablation = {k: bool(v) for k, v in ablation.items() if k in ABLACTION_KEYS}
    if llm_config_id := (payload.get("llm_config_id") or ""):
        from models import LLMConfig as _LC
        if not await db.get(_LC, llm_config_id):
            raise ValueError(f"模型配置不存在：{llm_config_id}")

    run = EvalRun(
        testset_id=testset_id,
        kb_id=payload.get("kb_id") or t.default_kb_id or "",
        name=(payload.get("name") or "").strip() or f"{t.name}·{datetime.now().strftime('%m-%d %H:%M')}",
        status="pending",
        config_json=json.dumps({
            "metrics": metrics,
            "ablation": ablation,
            "llm_config_id": llm_config_id,
        }, ensure_ascii=False),
        created_by=username,
    )
    db.add(run)
    await db.commit()

    task = asyncio.create_task(execute_run(run.id))
    _active_run_tasks[run.id] = task
    task.add_done_callback(lambda _t: _active_run_tasks.pop(run.id, None))
    names = await _testset_name_map(db, [run.testset_id])
    return _run_row(run, names.get(run.testset_id, ""))


async def cancel_run(db: AsyncSession, run_id: str) -> dict:
    r = await db.get(EvalRun, run_id)
    if not r:
        raise ValueError("评测任务不存在")
    if r.status not in ("running", "pending", "scoring"):
        raise ValueError(f"任务状态为 {r.status}，无法取消")
    task = _active_run_tasks.get(run_id)
    if task:
        task.cancel()
    r.status = "cancelled"
    r.finished_at = _now()
    await db.commit()
    _publish_event({"type": "status", "run_id": run_id, "status": "cancelled",
                    "done": r.done, "total": r.total})
    names = await _testset_name_map(db, [r.testset_id])
    return _run_row(r, names.get(r.testset_id, ""))


async def delete_run(db: AsyncSession, run_id: str) -> None:
    """删除评测任务及其全部结果条目（执行中的任务须先取消）。"""
    r = await db.get(EvalRun, run_id)
    if not r:
        raise ValueError("评测任务不存在")
    if r.status in ("running", "pending", "scoring") and r.id in _active_run_tasks:
        raise ValueError("任务正在执行，请先取消再删除")
    await db.execute(delete(EvalRunItem).where(EvalRunItem.run_id == run_id))
    await db.delete(r)
    await db.commit()


async def execute_run(run_id: str) -> None:
    """评测任务执行引擎：逐条采集即时落库 → ragas 评分回填。

    独立 session（不随请求生命周期）；单条失败记 error 继续；
    评分失败不覆盖已采集数据，run 置 done 并在 error 注明。
    """
    from database import async_session
    from services.rag_service import RAGService

    async with async_session() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.status != "pending":
            return
        config = json.loads(run.config_json or "{}")
        run.status = "running"
        run.started_at = _now()
        await db.commit()
        _publish_event({"type": "status", "run_id": run_id, "status": "running",
                        "done": 0, "total": run.total})

        items = (await db.execute(
            select(EvalTestsetItem)
            .where(EvalTestsetItem.testset_id == run.testset_id, EvalTestsetItem.enabled == 1)
            .order_by(EvalTestsetItem.created_at)
        )).scalars().all()
        run.total = len(items)
        await db.commit()
        if not items:
            run.status = "failed"
            run.error = "评测集无启用条目"
            run.finished_at = _now()
            await db.commit()
            _publish_event({"type": "status", "run_id": run_id, "status": "failed",
                            "done": 0, "total": 0})
            return

        flags = {k: bool(v) for k, v in (config.get("ablation") or {}).items()}
        rows: list[dict] = []
        try:
            with ablation_override(flags):
                for it in items:
                    # 取消检查：置为 cancelled 后跳出
                    await db.refresh(run)
                    if run.status == "cancelled":
                        return
                    kb_id = it.kb_id or run.kb_id
                    t0 = datetime.now()
                    error = ""
                    try:
                        result = await RAGService.query(db, kb_id, it.question)
                    except Exception as exc:  # 单条失败不中断
                        logger.warning("评测采集失败 run=%s q=%s: %s", run_id, it.question[:50], exc)
                        result, error = {}, str(exc)
                    latency = round((datetime.now() - t0).total_seconds(), 2)
                    contexts = [
                        (c.get("text") or "") for c in (result.get("chunks") or [])
                    ]
                    paths = [
                        (c.get("retrieval") or "") for c in (result.get("chunks") or [])
                    ]
                    answer = result.get("answer") or ""
                    db.add(EvalRunItem(
                        run_id=run_id,
                        item_id=it.id,
                        question=it.question,
                        reference=it.reference or "",
                        kb_id=kb_id,
                        answer=answer,
                        contexts_json=json.dumps(contexts, ensure_ascii=False),
                        retrieval_paths_json=json.dumps(paths, ensure_ascii=False),
                        latency_s=latency,
                        error=error,
                    ))
                    rows.append({
                        "id": "",  # 落库后回填
                        "question": it.question,
                        "reference": it.reference or "",
                        "answer": answer,
                        "contexts": contexts,
                    })
                    run.done = (run.done or 0) + 1
                    if error:
                        run.failed_count = (run.failed_count or 0) + 1
                    await db.commit()
                    _publish_event({"type": "progress", "run_id": run_id, "status": "running",
                                    "done": run.done, "total": run.total,
                                    "failed_count": run.failed_count})

            # 评分阶段（采集已全部落库；置 scoring 让前端区分「采集完正在评分」与卡死）
            run.status = "scoring"
            await db.commit()
            _publish_event({"type": "status", "run_id": run_id, "status": "scoring",
                            "done": run.done, "total": run.total})
            await _score_and_fill(db, run, config)
            if run.status != "cancelled":
                run.status = "done"
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:
            logger.exception("评测任务异常 run=%s", run_id)
            run.status = "failed"
            run.error = str(exc)
        finally:
            run.finished_at = _now()
            await db.commit()
            _publish_event({"type": "status", "run_id": run_id, "status": run.status,
                            "done": run.done, "total": run.total})


async def _score_and_fill(db: AsyncSession, run: EvalRun, config: dict) -> None:
    """评分公共段（execute_run 与 rescore_run 共用）：对已落库条目跑 ragas，
    回填逐条分数、指标均值与各指标成功评分条数。

    评分失败不覆盖已采集数据，仅写 run.error（含跳过指标说明）。
    """
    run_id = run.id
    saved = (await db.execute(
        select(EvalRunItem).where(EvalRunItem.run_id == run_id)
        .order_by(EvalRunItem.created_at)
    )).scalars().all()
    score_rows = [
        {
            "id": it.id,
            "question": it.question,
            "reference": it.reference or "",
            "answer": it.answer or "",
            "contexts": json.loads(it.contexts_json or "[]"),
        }
        for it in saved if not it.error
    ]
    if not score_rows:
        return
    try:
        eval_llm, _ = await resolve_eval_llm(db, config.get("llm_config_id"))
        per_item, means, scored, skipped = await asyncio.to_thread(
            score_rows_sync, score_rows, config.get("metrics") or list(DEFAULT_METRICS), eval_llm
        )
        for it in saved:
            s = per_item.get(it.id)
            if s is not None:
                it.metric_scores_json = json.dumps(s, ensure_ascii=False)
        run.metrics_summary_json = json.dumps(means, ensure_ascii=False)
        run.scored_count_json = json.dumps(scored, ensure_ascii=False)
        if skipped:
            run.error = f"跳过需 reference 的指标：{', '.join(skipped)}（评测集条目缺标注）"
    except Exception as exc:
        logger.exception("评测评分失败 run=%s", run_id)
        run.error = f"采集完成，但评分失败：{exc}"


async def rescore_run(db: AsyncSession, run_id: str) -> dict:
    """重新评分：不重新采集，直接对已留存结果重跑 ragas。

    适用：评分失败、部分条目缺分（LLM 输出解析失败/超时被置 NaN）、更换评估模型后想重打分。
    """
    r = await db.get(EvalRun, run_id)
    if not r:
        raise ValueError("评测任务不存在")
    if r.status in ("running", "pending", "scoring") or r.id in _active_run_tasks:
        raise ValueError("任务正在执行，无法重评")
    if not r.done:
        raise ValueError("无已采集结果，无法重评（请重新发起评测）")
    config = json.loads(r.config_json or "{}")
    unknown = [m for m in (config.get("metrics") or []) if m not in METRIC_SPECS]
    if unknown:
        raise ValueError(f"配置含未知指标：{', '.join(unknown)}")
    r.status = "scoring"
    r.error = ""
    await db.commit()
    _publish_event({"type": "status", "run_id": run_id, "status": "scoring",
                    "done": r.done, "total": r.total})
    task = asyncio.create_task(_rescore_task(run_id))
    _active_run_tasks[run_id] = task
    task.add_done_callback(lambda _t: _active_run_tasks.pop(run_id, None))
    names = await _testset_name_map(db, [r.testset_id])
    return _run_row(r, names.get(r.testset_id, ""))


async def _rescore_task(run_id: str) -> None:
    from database import async_session

    async with async_session() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.status != "scoring":
            return
        config = json.loads(run.config_json or "{}")
        try:
            await _score_and_fill(db, run, config)
            if run.status == "scoring":
                run.status = "done"
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:
            logger.exception("重评任务异常 run=%s", run_id)
            run.status = "failed"
            run.error = str(exc)
        finally:
            run.finished_at = _now()
            await db.commit()
            _publish_event({"type": "status", "run_id": run_id, "status": run.status,
                            "done": run.done, "total": run.total})


# ────────────────────────── Badcase 标记与回流 ──────────────────────────

async def mark_badcase(db: AsyncSession, run_id: str, item_id: str, payload: dict, username: str) -> dict:
    it = await db.get(EvalRunItem, item_id)
    if not it or it.run_id != run_id:
        raise ValueError("结果条目不存在")
    is_badcase = bool(payload.get("is_badcase"))
    it.is_badcase = 1 if is_badcase else 0
    if is_badcase:
        reason = payload.get("badcase_reason") or "other"
        if reason not in BADCASE_REASONS:
            raise ValueError(f"badcase_reason 须为：{', '.join(BADCASE_REASONS)}")
        it.badcase_reason = reason
        it.badcase_note = payload.get("badcase_note") or ""
        it.marked_by = username
        it.marked_at = _now()
    else:
        it.badcase_reason = ""
        it.badcase_note = ""
        it.marked_by = username
        it.marked_at = _now()
    await db.commit()
    return _run_item_row(it)


async def mark_badcase_batch(db: AsyncSession, run_id: str, payload: dict, username: str) -> dict:
    item_ids: list[str] = payload.get("run_item_ids") or []
    if not item_ids:
        raise ValueError("run_item_ids 不能为空")
    count = 0
    for item_id in item_ids:
        try:
            await mark_badcase(db, run_id, item_id, payload, username)
            count += 1
        except ValueError:
            continue
    return {"marked": count}


async def backflow(db: AsyncSession, run_id: str, payload: dict) -> dict:
    """把已标记的 Badcase 回流进评测集：origin=badcase，带溯源 run_item_id。

    重复 question（目标集内已存在）自动跳过并在响应中列出。
    """
    run = await db.get(EvalRun, run_id)
    if not run:
        raise ValueError("评测任务不存在")
    target_id = payload.get("target_testset_id") or run.testset_id
    t = await db.get(EvalTestset, target_id)
    if not t:
        raise ValueError("目标评测集不存在")
    item_ids: list[str] = payload.get("run_item_ids") or []
    q = select(EvalRunItem).where(EvalRunItem.run_id == run_id, EvalRunItem.is_badcase == 1)
    if item_ids:
        q = q.where(EvalRunItem.id.in_(item_ids))
    marked = (await db.execute(q)).scalars().all()
    if not marked:
        raise ValueError("没有已标记的 Badcase 可回流")
    flowed, skipped = 0, []
    for it in marked:
        dup = (await db.execute(
            select(EvalTestsetItem.id).where(
                EvalTestsetItem.testset_id == target_id,
                EvalTestsetItem.question == it.question,
            )
        )).scalars().first()
        if dup:
            skipped.append(it.question[:60])
            continue
        db.add(EvalTestsetItem(
            testset_id=target_id,
            question=it.question,
            reference=it.reference or "",
            kb_id=it.kb_id or "",
            origin="badcase",
            source_run_item_id=it.id,
        ))
        flowed += 1
    t.updated_at = _now()
    await db.commit()
    return {"flowed": flowed, "skipped": skipped, "target_testset_id": target_id}


# ────────────────────────── 报告导出 ──────────────────────────

async def export_report(db: AsyncSession, run_id: str) -> tuple[str, str, bytes]:
    """导出逐条明细 CSV（question/答案/上下文/分数/耗时/badcase）。"""
    run = await db.get(EvalRun, run_id)
    if not run:
        raise ValueError("评测任务不存在")
    items = (await db.execute(
        select(EvalRunItem).where(EvalRunItem.run_id == run_id)
        .order_by(EvalRunItem.created_at)
    )).scalars().all()
    metric_keys: list[str] = []
    for it in items:
        try:
            for k in json.loads(it.metric_scores_json or "{}"):
                if k not in metric_keys:
                    metric_keys.append(k)
        except json.JSONDecodeError:
            continue
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["question", "reference", "answer", "error", "latency_s",
                     *metric_keys, "is_badcase", "badcase_reason", "badcase_note", "contexts"])
    for it in items:
        try:
            scores = json.loads(it.metric_scores_json or "{}")
        except json.JSONDecodeError:
            scores = {}
        writer.writerow([
            it.question, it.reference or "", it.answer or "", it.error or "", it.latency_s,
            *[scores.get(k, "") if scores.get(k) is not None else "" for k in metric_keys],
            "是" if it.is_badcase else "", it.badcase_reason or "", it.badcase_note or "",
            " || ".join(json.loads(it.contexts_json or "[]")),
        ])
    name = (run.name or run_id).replace(" ", "_")
    return f"eval_report_{name}.csv", "text/csv", buf.getvalue().encode("utf-8-sig")
