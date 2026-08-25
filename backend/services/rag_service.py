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

_RETRIEVAL_K = 50


def _filter_by_score(docs_with_scores, threshold):
    """过滤相似度低于阈值的结果。"""
    return [
        (doc, score) for doc, score in docs_with_scores
        if (1 - float(score)) >= threshold
    ]


class RAGService:

    @staticmethod
    async def query(
        db: AsyncSession, kb_id: str, query: str,
    ) -> dict:
        embeddings = create_embeddings()
        llm = create_llm()

        # 向量检索
        try:
            vectorstore = create_vector_store(kb_id, embeddings)
            docs_with_scores = await asyncio.to_thread(
                vectorstore.similarity_search_with_score, query, k=_RETRIEVAL_K,
            )
        except Exception:
            docs_with_scores = []

        docs_with_scores = _filter_by_score(docs_with_scores, settings.SIMILARITY_THRESHOLD)

        if not docs_with_scores:
            return {
                "query": query,
                "answer": "在知识库中未找到相关内容。",
                "chunks": [],
            }

        # 构建上下文（带编号）
        chunks_result = []
        context_parts = []
        for idx, (doc, score) in enumerate(docs_with_scores):
            chunks_result.append({
                "file_id": doc.metadata.get("file_id", ""),
                "file_name": doc.metadata.get("file_name", ""),
                "text": doc.page_content,
                "score": round(1 - float(score), 4),
                "index": idx + 1,
                "start_offset": doc.metadata.get("start_offset"),
                "end_offset": doc.metadata.get("end_offset"),
                "page_number": doc.metadata.get("page_number"),
                "file_ext": doc.metadata.get("file_ext", ""),
            })
            context_parts.append(f"[来源{idx + 1}]\n{doc.page_content}")

        # LLM 生成回答
        context_text = "\n\n".join(context_parts)
        prompt = RAG_USER_TEMPLATE.format(context=context_text, question=query)

        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        response = await llm.ainvoke(messages)

        return {
            "query": query,
            "answer": response.content,
            "chunks": chunks_result,
        }

    @staticmethod
    async def _hybrid_retrieve(kb_id: str, query: str, embeddings) -> list[dict]:
        """向量 + BM25 混合检索，RRF 融合，返回最终来源分片列表（含来源标注）。

        降级策略：BM25 关闭 / 依赖未装 / 语料拉取失败时，自动回退为纯向量（行为等同旧版）。
        """
        # ===== 1. 向量召回 =====
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

        vector_by_id: dict[str, dict] = {}
        vector_rank: list[str] = []
        for doc, score in vec_docs:
            cid = chunk_id_from_vector_metadata(doc.metadata)
            if not cid:
                continue
            vector_by_id[cid] = {
                "file_id": doc.metadata.get("file_id", ""),
                "file_name": doc.metadata.get("file_name", ""),
                "text": doc.page_content,
                "score": round(1 - float(score), 4),
                "start_offset": doc.metadata.get("start_offset"),
                "end_offset": doc.metadata.get("end_offset"),
                "page_number": doc.metadata.get("page_number"),
                "file_ext": doc.metadata.get("file_ext", ""),
            }
            vector_rank.append(cid)

        # ===== 2. BM25 关键词召回 =====
        bm25_by_id: dict[str, dict] = {}
        bm25_rank: list[str] = []
        if settings.BM25_ENABLED:
            try:
                index = await asyncio.to_thread(get_or_build_index, kb_id)
            except Exception:
                logger.exception("BM25 index load failed: kb_id=%s", kb_id)
                index = None
            if index is not None:
                try:
                    hits = await asyncio.to_thread(index.search, query, settings.BM25_RECALL_K)
                except Exception:
                    logger.exception("BM25 search failed: kb_id=%s", kb_id)
                    hits = []
                for cid, bm25_score, doc in hits:
                    meta = doc.get("metadata") or {}
                    bm25_by_id[cid] = {
                        "file_id": meta.get("file_id", ""),
                        "file_name": meta.get("file_name", ""),
                        "text": doc.get("content", ""),
                        "score": None,
                        "bm25_score": round(bm25_score, 4),
                        "start_offset": meta.get("start_offset"),
                        "end_offset": meta.get("end_offset"),
                        "page_number": meta.get("page_number"),
                        "file_ext": meta.get("file_ext", ""),
                    }
                    bm25_rank.append(cid)

        # ===== 3. RRF 融合重排 =====
        if bm25_rank:
            fused = rrf_fuse([vector_rank, bm25_rank], k=settings.HYBRID_RRF_K)
            top_ids = [cid for cid, _ in fused[: settings.HYBRID_TOP_N]]
        else:
            top_ids = vector_rank[: settings.HYBRID_TOP_N]

        # ===== 4. 组装最终分片 =====
        chunks_result: list[dict] = []
        for idx, cid in enumerate(top_ids):
            in_vec = cid in vector_by_id
            in_bm25 = cid in bm25_by_id
            base = vector_by_id.get(cid) or bm25_by_id.get(cid)
            if not base:
                continue
            item = dict(base)
            item["index"] = idx + 1
            item["retrieval"] = "both" if (in_vec and in_bm25) else ("vector" if in_vec else "bm25")
            item.setdefault("bm25_score", None)
            chunks_result.append(item)

        return chunks_result

    @staticmethod
    async def query_stream(kb_id: str, query: str):
        """SSE 流式问答：先发 chunks，再逐 token 流式输出回答。"""
        embeddings = create_embeddings()
        llm = create_llm()

        chunks_result = await RAGService._hybrid_retrieve(kb_id, query, embeddings)

        # 发送检索到的 chunks
        yield f"data: {json.dumps({'type': 'chunks', 'chunks': chunks_result}, ensure_ascii=False)}\n\n"

        if not chunks_result:
            yield f"data: {json.dumps({'type': 'token', 'content': '在知识库中未找到相关内容。'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 构建上下文（带来源编号）& 流式调用 LLM
        context_text = "\n\n".join(
            f"[来源{c['index']}]\n{c['text']}" for c in chunks_result
        )
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
