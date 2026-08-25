"""BM25 关键词检索 provider（基于 rank_bm25 直接实现）。

以向量库（Chroma）里的全部分片为语料，用 jieba 分词构建 rank_bm25.BM25Okapi 内存索引，
为知识问答提供稀疏/关键词召回，与向量召回做 RRF 融合。

设计：
- 惰性构建 + 按 kb_id 缓存；用 Chroma collection.count() 做指纹，计数变化即重建。
- 依赖（jieba / rank_bm25）未安装或语料拉取失败时，get_or_build_index 返回 None，
  由调用方降级为纯向量检索，不影响主流程。
"""
from __future__ import annotations

import logging
import re

from providers.vector_store import kb_document_count, list_kb_documents

logger = logging.getLogger(__name__)

# 极简中文停用词（可后续扩展）
_STOPWORDS = set(
    "的 了 和 是 就 都 而 及 与 或 在 对 把 被 等 这 那 个 也 又 还 有 无 于 之 其 且 并 但 却 则 因 所 以 为 很 更 最 不 没 可 能 会 要 应 该 你 我 他 她 它 们 什么 怎么 如何 为什么".split()
)
# 纯标点/符号（含下划线等非文字字符）
_PUNCT_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """中文分词 + 清洗：返回用于 BM25 的 token 列表。

    使用 jieba 搜索模式（lcut_for_search），对长词做细粒度切分以提高中文召回。
    """
    if not text:
        return []
    import jieba

    tokens = jieba.lcut_for_search(text.lower())
    out: list[str] = []
    for t in tokens:
        token = t.strip()
        if not token or token in _STOPWORDS or _PUNCT_RE.match(token):
            continue
        out.append(token)
    return out


class Bm25Index:
    """基于 rank_bm25 的内存索引：持有分片 id / 正文 / 元数据与分词语料。"""

    def __init__(self, chunk_ids: list[str], docs: list[dict]):
        from rank_bm25 import BM25Okapi

        self.chunk_ids = chunk_ids
        self.docs = docs  # 与 chunk_ids 平行，元素 {id, content, metadata}
        self.chunk_count = len(chunk_ids)
        self._tokenized = [tokenize(d["content"]) for d in docs]
        self._bm25 = BM25Okapi(self._tokenized)

    def search(self, query: str, k: int) -> list[tuple[str, float, dict]]:
        """返回 [(chunk_id, bm25_score, doc_dict), ...]，按分数降序，仅保留 score > 0。"""
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        out: list[tuple[str, float, dict]] = []
        for i in order:
            s = float(scores[i])
            if s <= 0:
                break
            out.append((self.chunk_ids[i], s, self.docs[i]))
            if len(out) >= k:
                break
        return out


# kb_id -> Bm25Index（进程内缓存）
_cache: dict[str, Bm25Index] = {}


def invalidate(kb_id: str):
    """显式清缓存（预留：后续文件增删钩子可调用）。"""
    _cache.pop(kb_id, None)


def get_or_build_index(kb_id: str) -> Bm25Index | None:
    """按 kb_id 惰性构建并缓存 BM25 索引；失败/空语料返回 None（调用方降级）。"""
    count = kb_document_count(kb_id)
    if count <= 0:
        return None

    cached = _cache.get(kb_id)
    if cached is not None and cached.chunk_count == count:
        return cached

    docs = list_kb_documents(kb_id)
    if not docs:
        return None

    try:
        index = Bm25Index([d["id"] for d in docs], docs)
    except Exception:
        logger.exception("BM25 index build failed: kb_id=%s", kb_id)
        return None

    _cache[kb_id] = index
    return index
