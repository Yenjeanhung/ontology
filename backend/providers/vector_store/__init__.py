"""Vector store provider adapters.

本包把应用其余部分与具体向量库解耦。具体实现见子模块
（.chroma / .milvus），本文件只暴露统一门面与后端工厂，并重新导出
所有公开符号以保持与拆分前的导入路径兼容。
"""

from __future__ import annotations

from config import settings

from .base import VectorStoreAdapter
from .chroma import ChromaAdapter
from .milvus import MilvusAdapter

__all__ = [
    "VectorStoreAdapter",
    "ChromaAdapter",
    "MilvusAdapter",
    "create_vector_store",
    "delete_vector_ids",
    "delete_kb_collection",
    "enrich_vector_index_records",
    "list_kb_documents",
    "kb_document_count",
    "get_vector_store_provider_name",
    "health_check",
    "vector_store_target",
    "vector_store_socket_target",
    "vector_store_hint",
    "list_collections",
    "query_collection",
]


def _get_adapter() -> VectorStoreAdapter:
    if settings.VECTOR_STORE_PROVIDER == "chroma":
        return ChromaAdapter()
    if settings.VECTOR_STORE_PROVIDER == "milvus":
        return MilvusAdapter()
    raise ValueError(f"Unknown vector store provider: {settings.VECTOR_STORE_PROVIDER}")


def create_vector_store(kb_id: str, embeddings: Embeddings):
    return _get_adapter().create_store(kb_id, embeddings)


def delete_vector_ids(kb_id: str, ids: list[str], embeddings: Embeddings = None) -> int:
    """按 chunk_id 删除向量，返回删除条数（各后端自行保证主键匹配）。"""
    return _get_adapter().delete_by_ids(kb_id, ids, embeddings)


def delete_kb_collection(kb_id: str):
    _get_adapter().delete_collection(kb_id)


def enrich_vector_index_records(kb_id: str, records: list[dict]) -> list[dict]:
    return _get_adapter().enrich_index_records(kb_id, records)


def list_kb_documents(kb_id: str) -> list[dict]:
    return _get_adapter().list_kb_documents(kb_id)


def kb_document_count(kb_id: str) -> int:
    return _get_adapter().kb_document_count(kb_id)


def get_vector_store_provider_name() -> str:
    return _get_adapter().provider_name


def health_check() -> tuple[bool, str, dict]:
    """向量库连通性检测。返回 (ok, message, extra)。"""
    return _get_adapter().health_check()


def vector_store_target() -> str:
    """当前配置下向量库的连接目标描述（自检展示用）。"""
    return _get_adapter().describe_target()


def vector_store_socket_target() -> tuple[str, int] | None:
    """向量库可 TCP 探活的地址；本地嵌入式后端（如 chroma）返回 None。"""
    return _get_adapter().socket_target()


def vector_store_hint() -> str:
    """向量库不可用时的排查建议。"""
    return _get_adapter().troubleshooting_hint()


def list_collections() -> list[str]:
    """列出当前向量库下全部 collection/kb 名称。"""
    return _get_adapter().list_collections()


def query_collection(
    kb_id: str,
    query_text: str,
    embeddings: Embeddings,
    top_k: int = 5,
) -> list[dict]:
    """对指定 collection 执行向量相似度检索。"""
    return _get_adapter().query_collection(kb_id, query_text, embeddings, top_k)
