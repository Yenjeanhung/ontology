"""LangChain 文本切分器适配层。

把 ``langchain_text_splitters`` / ``langchain_experimental`` 的切分能力包装成与
``core.chunker`` 内部执行器一致的 ``Iterator[ChunkItem]``，这样替换底层实现时无需
改动 offset / page_number 回填与 metadata 合并逻辑。

覆盖范围（有意为之，不做全量替换）：
- recursive → ``RecursiveCharacterTextSplitter``：与自研递归切分等价，行为更标准。
- heading   → ``MarkdownHeaderTextSplitter``：仅对 ATX 标题（#/##/...）生效，
              中文章节、数字编号等非 markdown 文档由调用方回落自研实现。
- semantic  → ``SemanticChunker``：真正基于 embedding 相似度断点切分，替代
              自研"按段落长度聚合"的伪语义实现（需 langchain-experimental）。

降级约定：依赖缺失 / 构造失败 / 运行异常一律返回空或不抛出，由 ``chunker``
回落到自研实现，保证分片链路不会因可选依赖中断。
"""

from __future__ import annotations

import logging
import re

from config import settings

logger = logging.getLogger(__name__)

# 与 chunker 自研递归切分保持一致的中文优先分隔符（段落 → 行 → 句 → 子句 → 字符）
_RECURSIVE_SEPARATORS = [
    "\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", " ", "",
]

_HEADING_SPLIT_ON = [
    ("#", "H1"),
    ("##", "H2"),
    ("###", "H3"),
    ("####", "H4"),
    ("#####", "H5"),
    ("######", "H6"),
]
_HEADING_ORDER = ("H1", "H2", "H3", "H4", "H5", "H6")

# ATX 标题行（# / ## / ...）。MarkdownHeaderTextSplitter 只认这一种，
# 文档中不存在时直接产出空，交由 chunker 回落自研 heading（支持中文章节与数字编号）
_ATX_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)


def available() -> bool:
    """langchain_text_splitters 是否可用。"""
    try:
        import langchain_text_splitters  # noqa: F401

        return True
    except Exception:
        return False


def semantic_available() -> bool:
    """langchain_experimental.SemanticChunker 是否可用（真语义切分）。"""
    try:
        from langchain_experimental.text_splitter import SemanticChunker  # noqa: F401

        return True
    except Exception:
        return False


def _build_recursive_splitter(chunk_size: int, overlap: int):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(
        chunk_size=max(1, chunk_size),
        chunk_overlap=max(0, overlap),
        separators=_RECURSIVE_SEPARATORS,
        length_function=len,
        keep_separator=True,
        strip_whitespace=False,
    )


def iter_recursive_texts(content: str, params):
    """LangChain 递归字符切分，等价于自研 recursive 策略。"""
    if not content or not content.strip():
        return
    splitter = _build_recursive_splitter(params.chunk_size, params.overlap)
    for piece in splitter.split_text(content):
        if piece:
            yield piece, {}


def iter_heading_texts(content: str, params):
    """LangChain markdown 标题切分。

    仅处理 ATX 标题（#/##/...）；文档中没有 ATX 标题时产出为空，
    由调用方回落自研 heading 实现（自研版额外支持中文章节与数字编号）。
    """
    if not content or not content.strip():
        return
    if not _ATX_HEADING_RE.search(content):
        # 非 markdown 标题结构：回落自研 heading，保住中文章节 / 数字编号的层级识别
        return
    from langchain_text_splitters import MarkdownHeaderTextSplitter

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADING_SPLIT_ON, strip_headers=False,
    )
    docs = splitter.split_text(content)
    if not docs:
        return

    # 标题切分只按标题边界切，不控制长度：相邻同路径小节先合并，超长小节再递归下钻
    merged: list[tuple[list[str], str]] = []
    for doc in docs:
        meta = doc.metadata or {}
        path = [str(meta[h]) for h in _HEADING_ORDER if meta.get(h)]
        text = doc.page_content or ""
        if not text:
            continue
        # 标题切分不保证块大小，小节过短会让检索拿不到足够上下文；
        # 因此相邻小节在不超过 chunk_size 的前提下合并（路径取首块，即最上层标题）
        if merged and len(merged[-1][1]) + 2 + len(text) <= params.chunk_size:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n\n" + text)
        else:
            merged.append((path, text))

    sub = _build_recursive_splitter(params.chunk_size, params.overlap)

    for path, text in merged:
        extra = {"headings": path, "section": path[-1] if path else ""}
        if len(text) > params.chunk_size:
            for piece in sub.split_text(text):
                if piece:
                    yield piece, extra
        else:
            yield text, extra


def iter_semantic_texts(content: str, params):
    """LangChain SemanticChunker：基于 embedding 相似度断点做真语义切分。

    与自研 semantic（按段落长度聚合）的区别在于这里真的计算了句子间语义距离，
    代价是需要逐句 embedding，耗时显著高于其它策略。
    """
    if not content or not content.strip():
        return
    from langchain_experimental.text_splitter import SemanticChunker

    from providers.embedding import create_embeddings

    embeddings = create_embeddings()
    splitter = SemanticChunker(
        embeddings,
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=95,
        min_chunk_size=max(1, params.chunk_size // 4),
    )
    docs = splitter.create_documents([content])
    for doc in docs:
        text = doc.page_content or ""
        if text:
            yield text, {}


# 策略名 → LangChain 执行器；未列出的策略（fixed / sentence）无对应优势实现，保持自研
_EXECUTORS = {
    "recursive": iter_recursive_texts,
    "heading": iter_heading_texts,
    "semantic": iter_semantic_texts,
}


def executor_for(strategy: str):
    """返回该策略可用的 LangChain 执行器；不可用返回 None，由调用方回落自研。"""
    fn = _EXECUTORS.get(strategy)
    if fn is None:
        return None
    if strategy == "semantic" and not semantic_available():
        logger.info("LangChain semantic splitter unavailable, fallback to builtin")
        return None
    if not available():
        return None
    return fn


def backend_enabled() -> bool:
    """配置是否启用了 LangChain 分片后端。"""
    return getattr(settings, "CHUNK_BACKEND", "self") == "langchain"
