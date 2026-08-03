from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from config import settings


_SENT_SEP = re.compile(r"(?<=[。！？；.!?;\n])")
_FIXED_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ")


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
        start_offset = offset
    end_offset = start_offset + len(chunk_text)
    next_offset = start_offset + 1
    return {
        "index": index,
        "content": chunk_text,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "page_number": _find_page(page_map, start_offset, end_offset),
        "metadata": metadata or {},
    }, next_offset


def _records_from_texts(content: str, chunk_texts: Iterable[str], metadata: dict) -> Iterator[dict]:
    offset = 0
    for index, chunk_text in enumerate(chunk_texts):
        if not chunk_text:
            continue
        record, offset = _chunk_record(content, chunk_text, index, offset, metadata)
        yield record


def _pick_fixed_end(content: str, start: int) -> int:
    chunk_size = max(1, settings.CHUNK_SIZE)
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


def _iter_fixed_texts(content: str) -> Iterator[str]:
    if not content.strip():
        return

    start = 0
    overlap = max(0, settings.CHUNK_OVERLAP)

    while start < len(content):
        end = _pick_fixed_end(content, start)
        if end <= start:
            end = min(start + max(1, settings.CHUNK_SIZE), len(content))

        chunk_text = content[start:end]
        if chunk_text.strip():
            yield chunk_text

        if end >= len(content):
            break

        next_start = max(0, end - overlap)
        if next_start <= start:
            next_start = end
        start = next_start


def _iter_sentence_texts(content: str) -> Iterator[str]:
    sentences = [s for s in _SENT_SEP.split(content) if s.strip()]
    if not sentences:
        return

    buf: list[str] = []
    buf_len = 0
    for sentence in sentences:
        if buf_len + len(sentence) > settings.CHUNK_SIZE and buf:
            joined = "".join(buf)
            yield joined
            overlap_text = joined[-settings.CHUNK_OVERLAP:] if settings.CHUNK_OVERLAP > 0 else ""
            buf = [overlap_text] if overlap_text else []
            buf_len = len(buf[0]) if buf else 0
        buf.append(sentence)
        buf_len += len(sentence)

    if buf:
        yield "".join(buf)


def _iter_semantic_texts(content: str) -> Iterator[str]:
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return

    buf: list[str] = []
    buf_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > settings.CHUNK_SIZE:
            if buf:
                yield "\n\n".join(buf)
                buf = []
                buf_len = 0

            sub_buf: list[str] = []
            sub_len = 0
            for sentence in [s for s in _SENT_SEP.split(paragraph) if s.strip()]:
                if sub_len + len(sentence) > settings.CHUNK_SIZE and sub_buf:
                    yield "".join(sub_buf)
                    sub_buf = [sentence]
                    sub_len = len(sentence)
                else:
                    sub_buf.append(sentence)
                    sub_len += len(sentence)
            if sub_buf:
                yield "".join(sub_buf)
            continue

        if buf_len + len(paragraph) > settings.CHUNK_SIZE and buf:
            yield "\n\n".join(buf)
            buf = []
            buf_len = 0

        buf.append(paragraph)
        buf_len += len(paragraph)

    if buf:
        yield "\n\n".join(buf)


_TEXT_STRATEGIES = {
    "fixed": _iter_fixed_texts,
    "sentence": _iter_sentence_texts,
    "semantic": _iter_semantic_texts,
}


def iter_text_chunks(content: str, metadata: dict | None = None) -> Iterator[dict]:
    strategy = settings.CHUNK_STRATEGY
    text_iter = _TEXT_STRATEGIES.get(strategy, _iter_fixed_texts)
    yield from _records_from_texts(content, text_iter(content), metadata or {})


def split_text(content: str, metadata: dict | None = None) -> list[dict]:
    return list(iter_text_chunks(content, metadata))
