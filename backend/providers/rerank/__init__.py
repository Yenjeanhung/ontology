"""召回结果精排（Rerank）。

RRF 融合只利用了各路召回的名次，没有判断查询与分片内容是否真正相关。
本模块在融合之后、送入 prompt 之前补一道精排，解决"名次靠前但不相关"的问题。

两档实现：
- ``cross-encoder``：本地 cross-encoder（默认 BAAI/bge-reranker-base），
  通过 LangChain 的 ``HuggingFaceCrossEncoder`` 封装调用，不可用时直接降级
  sentence-transformers；输出分数可为负，按 RERANK_MIN_SCORE 过滤。
- ``llm``：让 LLM 对候选逐批打 0~10 相关性分，无需下载模型，
  适合无法获取本地模型权重时的兜底（精度与成本均不如 cross-encoder）。

降级约定：任何异常都返回 None，由调用方保留 RRF 原始顺序，检索链路不中断。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from config import settings

logger = logging.getLogger(__name__)

_RERANK_LLM_SYSTEM = (
    "你是一个检索结果相关性评审员。请为给定的候选段落逐一打分，"
    "判断它与用户问题的相关程度。只输出 JSON 数组，不要任何解释。"
)

_RERANK_LLM_TEMPLATE = """用户问题：{question}

候选段落：
{candidates}

请为每个候选段落的相关性打分（0~10 的整数，10 为完全相关且能直接回答问题）。
输出格式：[{{"index": 1, "score": 8}}, ...]，必须覆盖全部 {count} 个候选。"""

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# cross-encoder 模型进程级缓存（首次加载较慢）
_ce_model = None
_ce_failed = False


def _candidate_text(item: dict) -> str:
    text = (item.get("text") or "").strip()
    limit = max(1, int(settings.RERANK_MAX_CHARS))
    return text[:limit]


def _load_cross_encoder():
    """加载 cross-encoder 模型（进程级缓存）。

    使用 LangChain 的 ``HuggingFaceCrossEncoder`` 封装而非 ``CrossEncoderReranker``：
    后者作为 retriever 压缩器只回传排序后的文档、不暴露原始分值，而
    ``RERANK_MIN_SCORE`` 需要真实分数做过滤。封装不可用时降级 sentence-transformers。
    """
    global _ce_model, _ce_failed
    if _ce_model is not None:
        return _ce_model
    if _ce_failed:
        return None

    try:
        from langchain_community.cross_encoders import HuggingFaceCrossEncoder

        _ce_model = HuggingFaceCrossEncoder(model_name=settings.RERANK_MODEL)
        logger.info("Rerank: LangChain HuggingFaceCrossEncoder loaded (%s)", settings.RERANK_MODEL)
        return _ce_model
    except Exception:
        logger.info(
            "Rerank: LangChain cross-encoder unavailable, fallback to sentence-transformers",
            exc_info=True,
        )

    try:
        from sentence_transformers import CrossEncoder

        _ce_model = CrossEncoder(settings.RERANK_MODEL, max_length=512)
        logger.info("Rerank: sentence-transformers CrossEncoder loaded (%s)", settings.RERANK_MODEL)
        return _ce_model
    except Exception:
        _ce_failed = True
        logger.warning(
            "Rerank: cross-encoder unavailable (%s). Install sentence-transformers "
            "or switch RERANK_PROVIDER=llm.", settings.RERANK_MODEL, exc_info=True,
        )
        return None


def _rerank_cross_encoder(query: str, candidates: list[dict], top_n: int):
    model = _load_cross_encoder()
    if model is None:
        return None

    pairs = [(query, _candidate_text(c)) for c in candidates]
    try:
        # LangChain 封装提供 score()，sentence-transformers 提供 predict()
        raw = model.score(pairs) if hasattr(model, "score") else model.predict(pairs)
        scores = []
        for value in raw:
            try:
                scores.append(float(value))
            except (TypeError, ValueError):
                scores.append(0.0)
    except Exception:
        logger.warning("Rerank: cross-encoder scoring failed", exc_info=True)
        return None

    return _apply_scores(candidates, scores, top_n, min_score=settings.RERANK_MIN_SCORE)


def _apply_scores(candidates: list[dict], scores: list[float], top_n: int, min_score: float):
    scored = []
    for item, score in zip(candidates, scores):
        if score < min_score:
            continue
        enriched = dict(item)
        enriched["rerank_score"] = round(float(score), 4)
        scored.append(enriched)
    scored.sort(key=lambda x: -x["rerank_score"])
    return scored[:top_n] if top_n > 0 else scored


def _parse_llm_scores(raw: str, count: int) -> list[float] | None:
    """解析 LLM 打分输出，容错裸 JSON / 带代码块 / 纯数字序列。"""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\[.*\]", text, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                parsed = None

    scores: dict[int, float] = {}
    if isinstance(parsed, list):
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                scores[int(entry.get("index"))] = float(entry.get("score"))
            except (TypeError, ValueError):
                continue

    if not scores:
        # 兜底：按出现顺序读取所有数字
        numbers = [float(n) for n in _NUMBER_RE.findall(text)]
        if len(numbers) >= count:
            return numbers[:count]
        return None

    # 提示词里候选从 1 开始编号，需还原为 0 基下标；LLM 偶尔会自行按 0 基输出，
    # 这里按编号范围判断基准，避免整体错位导致选中最不相关的候选。
    keys = sorted(scores)
    base = 1 if min(keys) >= 1 and max(keys) <= count else 0
    return [scores.get(i + base, 0.0) for i in range(count)]


async def _rerank_llm(llm, query: str, candidates: list[dict], top_n: int):
    if llm is None:
        return None
    from langchain_core.messages import HumanMessage, SystemMessage

    count = len(candidates)
    batch = max(1, int(settings.RERANK_LLM_BATCH))
    all_scores: list[float] = []

    for start in range(0, count, batch):
        group = candidates[start: start + batch]
        block = "\n\n".join(
            f"[{start + i + 1}] {_candidate_text(c)}" for i, c in enumerate(group)
        )
        prompt = _RERANK_LLM_TEMPLATE.format(question=query, candidates=block, count=len(group))
        try:
            response = await llm.ainvoke(
                [SystemMessage(content=_RERANK_LLM_SYSTEM), HumanMessage(content=prompt)]
            )
            raw = getattr(response, "content", "") or ""
            if isinstance(raw, list):
                raw = "".join(
                    b.get("text", "") for b in raw if isinstance(b, dict)
                )
        except Exception:
            logger.warning("Rerank: llm scoring failed at batch %s", start, exc_info=True)
            return None

        parsed = _parse_llm_scores(raw, len(group))
        if parsed is None:
            logger.warning("Rerank: unparsable llm scores at batch %s", start)
            return None
        all_scores.extend(parsed)

    if len(all_scores) < count:
        all_scores.extend([0.0] * (count - len(all_scores)))
    # llm 分数为 0~10，低于一半视为不相关（等价于 min_score=5）
    return _apply_scores(candidates, all_scores[:count], top_n, min_score=5.0)


async def rerank(query: str, candidates: list[dict], top_n: int, llm=None) -> list[dict] | None:
    """对候选分片精排，返回重排后的列表；失败返回 None（调用方保留原顺序）。

    ``candidates`` 为 ``_hybrid_retrieve`` 产出的分片 dict，需含 ``text``。
    返回结果会新增 ``rerank_score`` 字段。
    """
    if not candidates or top_n == 0:
        return None
    if not settings.RERANK_ENABLED:
        return None

    try:
        if settings.RERANK_PROVIDER == "llm":
            return await _rerank_llm(llm, query, candidates, top_n)
        return await asyncio.to_thread(_rerank_cross_encoder, query, candidates, top_n)
    except Exception:
        logger.warning("Rerank failed, keep original order", exc_info=True)
        return None
