import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from providers.bm25 import get_or_build_index
from providers.embedding import create_embeddings
from providers.retrieval import chunk_id_from_vector_metadata, rrf_fuse
from providers.vector_store import create_vector_store
from providers.llm import chunk_text, create_llm
from providers.rerank import rerank

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = (
    "你是一个知识库问答助手。请根据以下参考资料回答用户的问题。"
    "如果资料中没有相关信息，请如实说明，不要编造答案。"
    "回答时必须在引用资料的位置标注来源编号，格式为[来源1]、[来源2]等，"
    "编号对应参考资料的序号。每个引用了资料的事实或观点都应标注来源。"
)

RAG_USER_TEMPLATE = """参考资料：
{context}

用户问题：{question}

请根据以上参考资料回答问题，引用资料时标注[来源N]："""

QUERY_REWRITE_SYSTEM = (
    "你是一个检索查询改写助手。你的任务是把用户的提问改写成多个适合关键词与向量检索的查询。"
    "只输出 JSON 数组，不要任何解释。"
)

QUERY_REWRITE_TEMPLATE = """用户提问：{question}

请生成 {count} 个检索查询变体，要求：
1. 语义与用户提问一致，但用词、句式、角度不同；
2. 补充原提问中隐含但未明说的同义词或专业术语；
3. 不要输出与原提问完全相同的句子。

输出格式：["变体1", "变体2", ...]"""

_RETRIEVAL_K = 50


def _filter_by_score(docs_with_scores, threshold):
    """过滤相似度低于阈值的结果。"""
    return [
        (doc, score) for doc, score in docs_with_scores
        if (1 - float(score)) >= threshold
    ]


def _chunk_item(meta: dict, text: str, score=None, bm25_score=None) -> dict:
    """统一构造来源分片结构，保证向量 / BM25 两路召回字段一致。"""
    return {
        "file_id": (meta or {}).get("file_id", ""),
        "file_name": (meta or {}).get("file_name", ""),
        "text": text,
        "score": score,
        "bm25_score": bm25_score,
        "start_offset": (meta or {}).get("start_offset"),
        "end_offset": (meta or {}).get("end_offset"),
        "page_number": (meta or {}).get("page_number"),
        "file_ext": (meta or {}).get("file_ext", ""),
    }


async def _expand_queries(llm, query: str) -> list[str]:
    """Multi-Query：用 LLM 生成查询变体，扩大召回面。

    任何异常或解析失败都退化为 [query]，行为与关闭改写时完全一致。
    """
    if not settings.QUERY_REWRITE_ENABLED or llm is None:
        return [query]

    count = max(1, int(settings.QUERY_REWRITE_COUNT))
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = QUERY_REWRITE_TEMPLATE.format(question=query, count=count)
        response = await llm.ainvoke(
            [SystemMessage(content=QUERY_REWRITE_SYSTEM), HumanMessage(content=prompt)]
        )
        raw = chunk_text(response)
    except Exception:
        logger.warning("Query rewrite failed, use original query", exc_info=True)
        return [query]

    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            text = text[start: end + 1]
        variants = json.loads(text)
        if not isinstance(variants, list):
            return [query]
    except Exception:
        logger.warning("Query rewrite: unparsable variants, use original query", exc_info=True)
        return [query]

    result = [query]
    for variant in variants:
        if not isinstance(variant, str):
            continue
        cleaned = variant.strip()
        if cleaned and cleaned != query and cleaned not in result:
            result.append(cleaned)
    return result[: count + 1]


class RAGService:

    @staticmethod
    async def _recall(kb_id: str, queries: list[str], embeddings):
        """多查询变体 ×（向量 + BM25）召回。

        返回 (vector_by_id, bm25_by_id, rank_lists)。rank_lists 中每个列表内部已去重，
        不同列表（不同查询变体 / 不同召回通道）共同参与 RRF 融合。
        降级策略：BM25 关闭 / 依赖未装 / 语料拉取失败时，自动回退为纯向量。
        """
        vector_by_id: dict[str, dict] = {}
        bm25_by_id: dict[str, dict] = {}
        rank_lists: list[list[str]] = []

        for query in queries:
            # ===== 向量召回 =====
            vec_docs: list[tuple] = []
            try:
                vectorstore = create_vector_store(kb_id, embeddings)
                docs_with_scores = await asyncio.to_thread(
                    vectorstore.similarity_search_with_score, query, k=_RETRIEVAL_K,
                )
                vec_docs = _filter_by_score(docs_with_scores, settings.SIMILARITY_THRESHOLD)
            except Exception:
                logger.exception("Vector recall failed: kb_id=%s", kb_id)
                vec_docs = []

            vec_rank: list[str] = []
            for doc, score in vec_docs:
                cid = chunk_id_from_vector_metadata(doc.metadata)
                if not cid or cid in vec_rank:
                    continue
                item = _chunk_item(doc.metadata, doc.page_content, score=round(1 - float(score), 4))
                # 多查询变体可能召回同一分片，保留相似度最高的一次
                existed = vector_by_id.get(cid)
                if existed is None or (item["score"] or 0) > (existed.get("score") or 0):
                    vector_by_id[cid] = item
                vec_rank.append(cid)
            if vec_rank:
                rank_lists.append(vec_rank)

            # ===== BM25 关键词召回 =====
            if not settings.BM25_ENABLED:
                continue
            try:
                index = await asyncio.to_thread(get_or_build_index, kb_id)
            except Exception:
                logger.exception("BM25 index load failed: kb_id=%s", kb_id)
                index = None
            if index is None:
                continue
            try:
                hits = await asyncio.to_thread(index.search, query, settings.BM25_RECALL_K)
            except Exception:
                logger.exception("BM25 search failed: kb_id=%s", kb_id)
                hits = []

            bm25_rank: list[str] = []
            for cid, bm25_score, doc in hits:
                if not cid or cid in bm25_rank:
                    continue
                meta = doc.get("metadata") or {}
                item = _chunk_item(
                    meta, doc.get("content", ""), bm25_score=round(bm25_score, 4),
                )
                existed = bm25_by_id.get(cid)
                if existed is None or (item["bm25_score"] or 0) > (existed.get("bm25_score") or 0):
                    bm25_by_id[cid] = item
                bm25_rank.append(cid)
            if bm25_rank:
                rank_lists.append(bm25_rank)

        return vector_by_id, bm25_by_id, rank_lists

    @staticmethod
    async def _hybrid_retrieve(
        kb_id: str, query: str, embeddings, llm=None,
    ) -> list[dict]:
        """查询改写 → 向量 + BM25 混合召回 → RRF 融合 → （可选）Rerank 精排。

        返回最终来源分片列表（含 index / retrieval 等来源标注）。
        """
        queries = await _expand_queries(llm, query)
        if len(queries) > 1:
            logger.info("Query expanded to %s variants: %s", len(queries), queries)

        vector_by_id, bm25_by_id, rank_lists = await RAGService._recall(kb_id, queries, embeddings)
        if not rank_lists:
            return []

        # ===== RRF 融合 =====
        fused = rrf_fuse(rank_lists, k=settings.HYBRID_RRF_K)
        ordered_ids = [cid for cid, _ in fused]

        # 精排需要更大的候选池，否则只是把 TOP_N 内部重新排序，收益有限
        if settings.RERANK_ENABLED:
            candidate_limit = max(settings.HYBRID_TOP_N, settings.RERANK_CANDIDATE_K)
        else:
            candidate_limit = settings.HYBRID_TOP_N

        candidates: list[dict] = []
        for cid in ordered_ids[:candidate_limit]:
            in_vec = cid in vector_by_id
            in_bm25 = cid in bm25_by_id
            base = vector_by_id.get(cid) or bm25_by_id.get(cid)
            if not base:
                continue
            item = dict(base)
            item["retrieval"] = (
                "both" if (in_vec and in_bm25) else ("vector" if in_vec else "bm25")
            )
            candidates.append(item)

        # ===== Rerank 精排 =====
        limit = settings.HYBRID_TOP_N
        if settings.RERANK_ENABLED and candidates:
            reranked = await rerank(query, candidates, settings.RERANK_TOP_N, llm)
            if reranked:
                candidates = reranked
                limit = settings.RERANK_TOP_N
            else:
                logger.info("Rerank unavailable, keep RRF order")

        final = candidates[:limit]
        for idx, item in enumerate(final):
            item["index"] = idx + 1
        return final

    @staticmethod
    def _build_context(chunks_result: list[dict]) -> str:
        return "\n\n".join(
            f"[来源{c['index']}]\n{c['text']}" for c in chunks_result
        )

    @staticmethod
    async def query(
        db: AsyncSession, kb_id: str, query: str,
    ) -> dict:
        embeddings = create_embeddings()
        llm = create_llm()

        chunks_result = await RAGService._hybrid_retrieve(kb_id, query, embeddings, llm)

        if not chunks_result:
            return {
                "query": query,
                "answer": "在知识库中未找到相关内容。",
                "chunks": [],
            }

        if llm is None:
            return {
                "query": query,
                "answer": "未配置 LLM，无法生成回答。",
                "chunks": chunks_result,
            }

        # LLM 生成回答
        context_text = RAGService._build_context(chunks_result)
        prompt = RAG_USER_TEMPLATE.format(context=context_text, question=query)

        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = await llm.ainvoke(messages)

        return {
            "query": query,
            "answer": chunk_text(response),
            "chunks": chunks_result,
        }

    @staticmethod
    async def query_stream(kb_id: str, query: str):
        """SSE 流式问答：先发 chunks，再逐 token 流式输出回答。"""
        embeddings = create_embeddings()
        llm = create_llm()

        chunks_result = await RAGService._hybrid_retrieve(kb_id, query, embeddings, llm)

        # 发送检索到的 chunks
        yield f"data: {json.dumps({'type': 'chunks', 'chunks': chunks_result}, ensure_ascii=False)}\n\n"

        if not chunks_result:
            yield f"data: {json.dumps({'type': 'token', 'content': '在知识库中未找到相关内容。'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        if llm is None:
            yield f"data: {json.dumps({'type': 'token', 'content': '未配置 LLM，无法生成回答。'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 构建上下文（带来源编号）& 流式调用 LLM
        context_text = RAGService._build_context(chunks_result)
        prompt = RAG_USER_TEMPLATE.format(context=context_text, question=query)

        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        async for chunk in llm.astream(messages):
            text = chunk_text(chunk)
            if text:
                yield f"data: {json.dumps({'type': 'token', 'content': text}, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"
