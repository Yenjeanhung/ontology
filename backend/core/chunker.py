from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from config import settings

logger = logging.getLogger(__name__)

# ── 策略常量 ──────────────────────────────────────────────
STRATEGY_FIXED = "fixed"
STRATEGY_SENTENCE = "sentence"
STRATEGY_RECURSIVE = "recursive"
STRATEGY_SEMANTIC = "semantic"
STRATEGY_HEADING = "heading"

CHUNK_STRATEGIES = (
    STRATEGY_FIXED,
    STRATEGY_SENTENCE,
    STRATEGY_RECURSIVE,
    STRATEGY_SEMANTIC,
    STRATEGY_HEADING,
)

_SENT_SEP = re.compile(r"(?<=[。！？；.!?;\n])")
_FIXED_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ")
# 递归切分分隔符优先级：段落 → 行 → 句 → 子句 → 字符
_RECURSIVE_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", "")
# 标题行识别：markdown ATX / 中文章节 / 数字编号（1.2 / 1.2.3）
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_CN_HEADING_RE = re.compile(r"^(第[一二三四五六七八九十百千零两0-9]+[章节篇部回])[、.．:：\s]*(.*)$")
_NUM_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})(?:[、.．]\s*|\s+)(\S.*)$")


@dataclass
class ChunkParams:
    """分片参数；缺省时回退全局配置，保证旧行为兼容。"""

    chunk_size: int
    overlap: int = 0

    @classmethod
    def from_settings(cls) -> "ChunkParams":
        return cls(
            chunk_size=max(1, int(settings.CHUNK_SIZE)),
            overlap=max(0, int(settings.CHUNK_OVERLAP)),
        )

    @classmethod
    def coerce(cls, data: "ChunkParams | dict | None") -> "ChunkParams":
        if isinstance(data, ChunkParams):
            return data
        if isinstance(data, dict):
            try:
                return cls(
                    chunk_size=int(data.get("chunk_size") or settings.CHUNK_SIZE),
                    overlap=int(data.get("overlap") or 0),
                )
            except (TypeError, ValueError):
                return cls.from_settings()
        return cls.from_settings()

    def clamp(self) -> "ChunkParams":
        self.chunk_size = max(64, min(8000, self.chunk_size))
        self.overlap = max(0, min(self.overlap, self.chunk_size // 2))
        return self


def find_heading_info(line: str) -> tuple[int, str] | None:
    """识别标题行，返回 (层级, 标题)；非标题返回 None。供分片器与文档分析器共用。"""
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    m = _MD_HEADING_RE.match(stripped)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _CN_HEADING_RE.match(stripped)
    if m:
        return 1, f"{m.group(1)} {m.group(2)}".strip()
    m = _NUM_HEADING_RE.match(stripped)
    if m and len(m.group(2)) <= 100:
        return m.group(1).count(".") + 1, f"{m.group(1)} {m.group(2)}".strip()
    return None


def _find_page(page_map, start: int, end: int):
    if not page_map:
        return None
    mid = (start + end) // 2
    for pm in page_map:
        if pm["start"] <= mid < pm["end"]:
            return pm["page_number"]
    return None


def _chunk_record(
    content: str,
    chunk_text: str,
    index: int,
    offset: int,
    metadata: dict,
) -> tuple[dict, int]:
    page_map = (metadata or {}).get("page_map")
    start_offset = content.find(chunk_text, offset)
    if start_offset == -1:
        # 外部切分器（如 LangChain）可能对文本做规范化（剥离 # 标记、合并空白），
        # 导致片段无法在原文中精确匹配。此时退化为按累积位置估算，并让游标前进，
        # 避免后续所有分片都错位到同一 offset。
        start_offset = min(offset, len(content))
        next_offset = start_offset + len(chunk_text)
    else:
        # 命中时游标只前进 1：允许 overlap 分片回退到上一分片的内部重新定位
        next_offset = start_offset + 1
    end_offset = min(start_offset + len(chunk_text), len(content))
    return {
        "index": index,
        "content": chunk_text,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "page_number": _find_page(page_map, start_offset, end_offset),
        "metadata": metadata or {},
    }, next_offset


ChunkItem = tuple[str, dict]  # (分片文本, 附加元数据)


def _records_from_items(content: str, items: Iterator[ChunkItem], metadata: dict) -> Iterator[dict]:
    offset = 0
    for index, (chunk_text, extra) in enumerate(items):
        if not chunk_text:
            continue
        record, offset = _chunk_record(content, chunk_text, index, offset, metadata)
        if extra:
            record["metadata"] = {**metadata, **extra}
        yield record


# ── fixed：固定长度切分 ────────────────────────────────────


def _pick_fixed_end(content: str, start: int, chunk_size: int) -> int:
    max_end = min(start + chunk_size, len(content))
    if max_end >= len(content):
        return len(content)

    search_floor = start + max(1, min(chunk_size // 2, chunk_size - 1))
    window = content[start:max_end]
    floor_in_window = max(0, search_floor - start)

    for separator in _FIXED_SEPARATORS:
        idx = window.rfind(separator, floor_in_window)
        if idx != -1:
            return start + idx + len(separator)
    return max_end


def _iter_fixed_texts(content: str, params: ChunkParams) -> Iterator[ChunkItem]:
    if not content.strip():
        return

    start = 0
    overlap = max(0, params.overlap)

    while start < len(content):
        end = _pick_fixed_end(content, start, params.chunk_size)
        if end <= start:
            end = min(start + max(1, params.chunk_size), len(content))

        chunk_text = content[start:end]
        if chunk_text.strip():
            yield chunk_text, {}

        if end >= len(content):
            break

        next_start = max(0, end - overlap)
        if next_start <= start:
            next_start = end
        start = next_start


# ── sentence：句子/段落切分 ────────────────────────────────


def _iter_sentence_texts(content: str, params: ChunkParams) -> Iterator[ChunkItem]:
    sentences = [s for s in _SENT_SEP.split(content) if s.strip()]
    if not sentences:
        return

    def _join(parts: list[str]) -> str:
        # 句间以换行分隔：避免句号后无空格粘连（"limited.1 Introduction"）
        return "\n".join(p.strip() for p in parts if p.strip())

    buf: list[str] = []
    buf_len = 0
    for sentence in sentences:
        stripped_len = len(sentence.strip())
        if buf_len + stripped_len > params.chunk_size and buf:
            joined = _join(buf)
            yield joined, {}
            overlap_text = joined[-params.overlap:] if params.overlap > 0 else ""
            buf = [overlap_text] if overlap_text else []
            buf_len = sum(len(p.strip()) for p in buf)
        buf.append(sentence)
        buf_len += stripped_len

    if buf:
        yield _join(buf), {}


# ── recursive：递归字符切分 ────────────────────────────────


def _split_keep_sep(text: str, sep: str) -> list[str]:
    """按分隔符切分并保留分隔符在段尾；sep 为空串时按单字符切。"""
    if sep == "":
        return list(text)
    parts = text.split(sep)
    segments: list[str] = []
    for i, part in enumerate(parts):
        piece = part + sep if i < len(parts) - 1 else part
        if piece:
            segments.append(piece)
    return segments


def _recursive_split(text: str, size: int, overlap: int, depth: int) -> Iterator[str]:
    if len(text) <= size:
        if text.strip():
            yield text
        return
    if depth >= len(_RECURSIVE_SEPARATORS):
        if text.strip():
            yield text
        return

    sep = _RECURSIVE_SEPARATORS[depth]
    segments = _split_keep_sep(text, sep)
    if len(segments) <= 1:
        # 当前层级无法切开，降级到下一层级
        yield from _recursive_split(text, size, overlap, depth + 1)
        return

    buf: list[str] = []
    buf_len = 0
    for seg in segments:
        if len(seg) > size:
            # 单段仍超限：先冲刷当前缓冲，再对段内递归降级
            if buf:
                block = "".join(buf)
                if block.strip():
                    yield block
                buf, buf_len = [], 0
            yield from _recursive_split(seg, size, overlap, depth + 1)
            continue
        if buf_len + len(seg) > size and buf:
            block = "".join(buf)
            yield block
            tail = block[-overlap:] if overlap > 0 and overlap < len(block) else ""
            buf = [tail] if tail else []
            buf_len = len(tail)
        buf.append(seg)
        buf_len += len(seg)

    if buf:
        block = "".join(buf)
        if block.strip():
            yield block


def _iter_recursive_texts(content: str, params: ChunkParams) -> Iterator[ChunkItem]:
    if not content.strip():
        return
    size = max(1, params.chunk_size)
    overlap = max(0, params.overlap)
    for piece in _recursive_split(content, size, overlap, 0):
        yield piece, {}


# ── semantic：段落级语义切分（相邻段落聚合，超限下钻句子） ────


def _iter_semantic_texts(content: str, params: ChunkParams) -> Iterator[ChunkItem]:
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return

    buf: list[str] = []
    buf_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > params.chunk_size:
            if buf:
                yield "\n\n".join(buf), {}
                buf = []
                buf_len = 0

            sub_buf: list[str] = []
            sub_len = 0
            for sentence in [s for s in _SENT_SEP.split(paragraph) if s.strip()]:
                if sub_len + len(sentence) > params.chunk_size and sub_buf:
                    yield "".join(sub_buf), {}
                    sub_buf = [sentence]
                    sub_len = len(sentence)
                else:
                    sub_buf.append(sentence)
                    sub_len += len(sentence)
            if sub_buf:
                yield "".join(sub_buf), {}
            continue

        if buf_len + len(paragraph) > params.chunk_size and buf:
            yield "\n\n".join(buf), {}
            buf = []
            buf_len = 0

        buf.append(paragraph)
        buf_len += len(paragraph)

    if buf:
        yield "\n\n".join(buf), {}


# ── heading：层级/标题切分 ─────────────────────────────────


def _extract_heading_sections(content: str) -> list[tuple[list[str], str]]:
    """按标题行把文档切成小节，返回 [(章节路径, 小节文本), ...]。"""
    sections: list[tuple[list[str], str]] = []
    stack: list[str] = []
    buf: list[str] = []

    def flush():
        text = "\n".join(buf)
        if text.strip():
            sections.append((list(stack), text))
        buf.clear()

    for line in content.split("\n"):
        info = find_heading_info(line)
        if info:
            flush()
            level, title = info
            stack = stack[: max(0, level - 1)]
            stack.append(title)
        buf.append(line)
    flush()
    return sections


def _iter_heading_texts(content: str, params: ChunkParams) -> Iterator[ChunkItem]:
    sections = _extract_heading_sections(content)
    if not any(path for path, _ in sections):
        logger.info("Heading chunking: no headings detected, degrade to recursive")
        yield from _iter_recursive_texts(content, params)
        return

    size = max(1, params.chunk_size)

    def extra_for(path: list[str]) -> dict:
        return {"headings": list(path), "section": path[-1] if path else ""}

    merged_buf: list[str] = []
    merged_path: list[str] = []
    merged_len = 0

    for path, text in sections:
        if len(text) > size:
            # 单节超限：先冲刷缓冲，节内按递归策略下钻
            if merged_buf:
                yield "\n".join(merged_buf), extra_for(merged_path)
                merged_buf, merged_path, merged_len = [], [], 0
            for piece, _ in _iter_recursive_texts(text, params):
                yield piece, extra_for(path)
            continue
        if merged_len + len(text) > size and merged_buf:
            yield "\n".join(merged_buf), extra_for(merged_path)
            merged_buf, merged_path, merged_len = [], [], 0
        if not merged_path:
            merged_path = list(path)
        merged_buf.append(text)
        merged_len += len(text)

    if merged_buf:
        yield "\n".join(merged_buf), extra_for(merged_path)


# ── 统一入口 ──────────────────────────────────────────────

_STRATEGY_EXECUTORS = {
    STRATEGY_FIXED: _iter_fixed_texts,
    STRATEGY_SENTENCE: _iter_sentence_texts,
    STRATEGY_RECURSIVE: _iter_recursive_texts,
    STRATEGY_SEMANTIC: _iter_semantic_texts,
    STRATEGY_HEADING: _iter_heading_texts,
}


def _with_langchain_fallback(
    strategy_name: str,
    lc_executor: Callable[[str, ChunkParams], Iterator[ChunkItem]],
    builtin_executor: Callable[[str, ChunkParams], Iterator[ChunkItem]],
):
    """优先走 LangChain 执行器，不可用 / 无产出 / 抛错时回落自研实现。

    heading 策略尤其依赖此行为：MarkdownHeaderTextSplitter 只认 ATX 标题，
    非 markdown 文档（中文章节、数字编号）产出为空，必须回落自研才能保留层级。
    """

    def _run(content: str, params: ChunkParams):
        try:
            items = list(lc_executor(content, params))
        except Exception:
            logger.warning(
                "LangChain splitter failed for strategy=%s, fallback to builtin",
                strategy_name, exc_info=True,
            )
            items = []
        if items:
            yield from items
            return
        logger.info(
            "LangChain splitter produced nothing for strategy=%s, fallback to builtin",
            strategy_name,
        )
        yield from builtin_executor(content, params)

    return _run


def _resolve_executor(strategy_name: str):
    """按 CHUNK_BACKEND 解析最终执行器。"""
    builtin = _STRATEGY_EXECUTORS[strategy_name]
    try:
        from core import splitter_langchain

        if not splitter_langchain.backend_enabled():
            return builtin
        lc_executor = splitter_langchain.executor_for(strategy_name)
    except Exception:
        return builtin
    if lc_executor is None:
        return builtin
    return _with_langchain_fallback(strategy_name, lc_executor, builtin)


def iter_text_chunks(
    content: str,
    metadata: dict | None = None,
    *,
    strategy: str | None = None,
    params: ChunkParams | dict | None = None,
) -> Iterator[dict]:
    """参数化分片入口。

    strategy/params 缺省时回退全局配置（CHUNK_STRATEGY / CHUNK_SIZE / CHUNK_OVERLAP），
    保持旧行为兼容；heading 无标题时自动降级 recursive。
    """
    chunk_params = ChunkParams.coerce(params).clamp()
    strategy_name = strategy or settings.CHUNK_STRATEGY
    executor = _STRATEGY_EXECUTORS.get(strategy_name)
    if executor is None:
        fallback = (
            settings.CHUNK_STRATEGY
            if settings.CHUNK_STRATEGY in _STRATEGY_EXECUTORS
            else STRATEGY_RECURSIVE
        )
        logger.warning("Unknown chunk strategy %r, fallback to %s", strategy_name, fallback)
        strategy_name = fallback
    executor = _resolve_executor(strategy_name)
    logger.info("Chunking with strategy=%s chunk_size=%s overlap=%s", strategy_name, chunk_params.chunk_size, chunk_params.overlap)
    yield from _records_from_items(content, executor(content, chunk_params), metadata or {})


def split_text(
    content: str,
    metadata: dict | None = None,
    *,
    strategy: str | None = None,
    params: ChunkParams | dict | None = None,
) -> list[dict]:
    return list(iter_text_chunks(content, metadata, strategy=strategy, params=params))
