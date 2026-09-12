"""文档特征分析 + 分片策略推荐引擎。

上传文档后自动提取结构与语义特征，按规则引擎（R1~R8）推荐分片策略与参数，
推荐结果写入 File.detail.analysis，供前端确认对话框展示，用户可调整后再执行分片。
"""
from __future__ import annotations

import logging
import math
import re
from statistics import pstdev

from core.chunker import find_heading_info

logger = logging.getLogger(__name__)

# 结构特征统计的采样上限（正则统计开销低，超长文档截断采样）
MAX_PROFILE_CHARS = 100_000
# 语义抽样（相邻句 embedding 相似度）的句数上限
SEMANTIC_SAMPLE_SENTENCES = 100
SEMANTIC_SAMPLE_CHARS = 50_000
# 数据类扩展名：按记录边界切分，关闭重叠
DATA_EXTS = {"csv", "json"}

_SENT_SEP = re.compile(r"(?<=[。！？；.!?;\n])")
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*•·]|\d{1,3}[.)、])\s+\S")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|")
_CODE_FENCE_RE = re.compile(r"^\s*```")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

STRATEGY_LABELS = {
    "fixed": "固定长度切分",
    "sentence": "句子/段落切分",
    "recursive": "递归字符切分",
    "semantic": "语义切分",
    "heading": "层级/标题切分",
}


def profile_document(content: str, ext: str = "") -> dict:
    """提取文档结构特征（纯正则统计，低开销；语义特征见 analyze_document）。"""
    sample = content[:MAX_PROFILE_CHARS]
    lines = sample.split("\n")
    non_empty_lines = [ln for ln in lines if ln.strip()]

    heading_levels: list[int] = []
    heading_lines = 0
    in_code = False
    code_lines = 0
    for line in lines:
        if _CODE_FENCE_RE.match(line):
            in_code = not in_code
            code_lines += 1
            continue
        if in_code:
            code_lines += 1
            continue
        info = find_heading_info(line)
        if info:
            heading_lines += 1
            heading_levels.append(info[0])

    paragraphs = [p.strip() for p in sample.split("\n\n") if p.strip()]
    para_lens = [len(p) for p in paragraphs]
    sentences = [s.strip() for s in _SENT_SEP.split(sample) if s.strip()]
    sent_lens = [len(s) for s in sentences]

    def _avg(values: list[int]) -> int:
        return int(sum(values) / len(values)) if values else 0

    line_total = max(len(non_empty_lines), 1)
    return {
        "ext": (ext or "").lower().lstrip("."),
        "total_chars": len(content),
        "heading_count": heading_lines,
        "max_heading_depth": max(heading_levels) if heading_levels else 0,
        "paragraph_count": len(paragraphs),
        "avg_para_len": _avg(para_lens),
        "para_len_std": int(pstdev(para_lens)) if len(para_lens) > 1 else 0,
        "sentence_count": len(sentences),
        "avg_sentence_len": _avg(sent_lens),
        "list_line_ratio": round(sum(1 for ln in non_empty_lines if _LIST_LINE_RE.match(ln)) / line_total, 3),
        "table_line_ratio": round(sum(1 for ln in non_empty_lines if _TABLE_LINE_RE.match(ln)) / line_total, 3),
        "code_block_ratio": round(code_lines / max(len(lines), 1), 3),
        "cjk_ratio": round(len(_CJK_RE.findall(sample)) / max(len(sample), 1), 3),
    }


def suggest_params(features: dict, strategy: str) -> dict:
    """按文档长度与形态给出 chunk_size / overlap 建议。"""
    total = max(features.get("total_chars", 0), 1)
    if total < 1500:
        chunk_size = max(200, total // 3)
    elif total > 100_000:
        chunk_size = 1200
    else:
        chunk_size = 800
    if strategy == "semantic":
        chunk_size = min(chunk_size, 600)
    if features.get("avg_sentence_len", 0) > 150:
        overlap = int(chunk_size * 0.15)  # 合同/长句类，重叠取 15%
    else:
        overlap = int(chunk_size * 0.10)
    # 仅 fixed(滑动窗口用法)/recursive 消费重叠；sentence 语义边界完整不宜重叠，
    # semantic/heading 执行器不消费重叠
    if strategy in ("fixed", "sentence", "semantic", "heading") or features.get("ext") in DATA_EXTS:
        overlap = 0
    return {"chunk_size": chunk_size, "overlap": overlap}


def recommend_strategy(features: dict) -> dict:
    """规则引擎（R1~R8，按优先级短路），返回推荐 + 候选 + 理由。"""
    total = features.get("total_chars", 0)
    ext = features.get("ext", "")
    heading_count = features.get("heading_count", 0)
    max_depth = features.get("max_heading_depth", 0)
    avg_sent = features.get("avg_sentence_len", 0)
    para_count = features.get("paragraph_count", 0)
    avg_para = features.get("avg_para_len", 0)

    hits: list[dict] = []

    def hit(strategy: str, confidence: float, reason: str, params: dict | None = None):
        hits.append({
            "strategy": strategy,
            "confidence": confidence,
            "reason": reason,
            "params": params or suggest_params(features, strategy),
        })

    # R1 超短文档：整体单块
    if 0 < total < 500:
        hit(
            "fixed", 0.95,
            f"文档仅 {total} 字符，建议整体作为单个分片",
            params={"chunk_size": max(64, total), "overlap": 0},
        )
    # R2 数据类文件：行/记录边界敏感，关闭重叠
    if ext in DATA_EXTS:
        hit(
            "recursive", 0.8,
            "CSV/JSON 数据按递归切分并关闭重叠，避免破坏行/记录边界",
        )
    # R3/R4 标题结构
    if heading_count >= 5 and max_depth >= 2 and total > 5000:
        hit(
            "heading", 0.85,
            f"检测到 {heading_count} 个标题、最深 {max_depth} 级，按章节层级切分可保留结构",
        )
    elif heading_count >= 5 and total > 5000:
        hit(
            "heading", 0.7,
            f"检测到 {heading_count} 个标题，按章节结构切分更利于检索定位",
        )
    # R5 语义突变密度（仅当抽样特征存在时参与判断）
    density = features.get("semantic_break_density")
    if density is not None and density > 0.35:
        hit(
            "semantic", 0.75,
            f"语义突变密度 {density:.2f}，话题切换频繁，语义切分可保持单块主题聚焦",
        )
    # R6 长句少段（合同/法务类特征）：加大重叠防边界截断
    if avg_sent > 150 and para_count < max(total / 2000, 1):
        hit(
            "recursive", 0.7,
            f"平均句长 {avg_sent} 字符且段落稀疏（合同/规范类特征），采用递归切分并加大重叠",
            params={**suggest_params(features, "recursive"), "overlap": int(suggest_params(features, "recursive")["chunk_size"] * 0.15)},
        )
    # R7 段落均匀的普通文本
    if 100 <= avg_para <= 800 and para_count >= 3:
        hit(
            "recursive", 0.65,
            f"段落分布均匀（均长 {avg_para} 字符），递归切分可在语义边界处断开",
        )
    # R8 兜底
    if not hits:
        hit("recursive", 0.5, "未检测到显著结构特征，采用递归字符切分作为通用默认策略")

    best = hits[0]
    return {
        "strategy": best["strategy"],
        "params": best["params"],
        "confidence": best["confidence"],
        "reasons": [h["reason"] for h in hits[:3]],
        "candidates": [
            {"strategy": h["strategy"], "confidence": h["confidence"]}
            for h in hits[1:4]
        ],
        # 各策略建议参数：前端切换策略时联动应用
        "suggestions": {
            s: suggest_params(features, s)
            for s in ("fixed", "sentence", "recursive", "semantic", "heading")
        },
    }


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _semantic_break_density(content: str) -> float | None:
    """抽样计算相邻句 embedding 相似度跌落点占比；失败返回 None（best-effort）。"""
    try:
        from providers.embedding import create_embeddings

        sentences = [
            s.strip()
            for s in _SENT_SEP.split(content[:SEMANTIC_SAMPLE_CHARS])
            if len(s.strip()) >= 10
        ]
        if len(sentences) < 8:
            return None
        step = max(1, len(sentences) // SEMANTIC_SAMPLE_SENTENCES)
        sampled = sentences[::step][:SEMANTIC_SAMPLE_SENTENCES]
        vectors = create_embeddings().embed_documents(sampled)
        if len(vectors) < 3:
            return None
        sims = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
        mean = sum(sims) / len(sims)
        std = pstdev(sims)
        breaks = sum(1 for s in sims if s < mean - 1.5 * std)
        return round(breaks / len(sims), 3)
    except Exception as exc:  # noqa: BLE001 - 语义抽样为可选增强，任何失败都跳过
        logger.warning("Semantic sampling skipped: %s", exc)
        return None


def analyze_document(content: str, ext: str = "", semantic_sample: bool = False) -> dict:
    """完整分析：特征提取（可选语义抽样）+ 规则推荐 + 分片数预估。"""
    features = profile_document(content, ext)
    if semantic_sample:
        density = _semantic_break_density(content)
        if density is not None:
            features["semantic_break_density"] = density
    recommendation = recommend_strategy(features)
    chunk_size = max(recommendation["params"].get("chunk_size", 800), 1)
    estimate = {"chunk_count": math.ceil(max(features["total_chars"], 1) / chunk_size)}
    return {
        "features": features,
        "recommendation": recommendation,
        "estimate": estimate,
    }
