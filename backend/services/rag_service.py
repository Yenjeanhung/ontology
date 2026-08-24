import asyncio
import json

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from providers.embedding import create_embeddings
from providers.vector_store import create_vector_store
from providers.llm import chunk_text, create_llm

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
    async def query_stream(kb_id: str, query: str):
        """SSE 流式问答：先发 chunks，再逐 token 流式输出回答。"""
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

        chunks_result = []
        for doc, score in docs_with_scores:
            chunks_result.append({
                "file_id": doc.metadata.get("file_id", ""),
                "file_name": doc.metadata.get("file_name", ""),
                "text": doc.page_content,
                "score": round(1 - float(score), 4),
                "start_offset": doc.metadata.get("start_offset"),
                "end_offset": doc.metadata.get("end_offset"),
                "page_number": doc.metadata.get("page_number"),
                "file_ext": doc.metadata.get("file_ext", ""),
            })

        # 发送检索到的 chunks
        yield f"data: {json.dumps({'type': 'chunks', 'chunks': chunks_result}, ensure_ascii=False)}\n\n"

        if not docs_with_scores:
            yield f"data: {json.dumps({'type': 'token', 'content': '在知识库中未找到相关内容。'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 构建上下文 & 流式调用 LLM
        context_text = "\n\n".join(doc.page_content for doc, _ in docs_with_scores)
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
