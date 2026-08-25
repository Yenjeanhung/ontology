"""Vector store provider adapters.

This module keeps the rest of the app decoupled from the concrete vector DB.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from langchain_core.embeddings import Embeddings

from config import settings


class VectorStoreAdapter(ABC):
    provider_name: str

    @abstractmethod
    def create_store(self, kb_id: str, embeddings: Embeddings):
        raise NotImplementedError

    @abstractmethod
    def delete_collection(self, kb_id: str):
        raise NotImplementedError

    def health_check(self) -> tuple[bool, str, dict]:
        """连通性检测。返回 (ok, message, extra)。"""
        return True, "not implemented", {}

    def enrich_index_records(self, kb_id: str, records: list[dict]) -> list[dict]:
        """Best-effort provider-specific enrichment for inspector pages."""
        return records

    def list_kb_documents(self, kb_id: str) -> list[dict]:
        """返回 KB 下全部分片 [{id, content, metadata}]，供 BM25 等关键词索引构建。

        provider 不支持时抛 NotImplementedError；调用方需捕获并降级。
        """
        raise NotImplementedError

    def kb_document_count(self, kb_id: str) -> int:
        """KB 下分片总数（用于 BM25 缓存指纹）。默认 0 表示不支持。"""
        return 0


class ChromaAdapter(VectorStoreAdapter):
    provider_name = "chroma"

    def create_store(self, kb_id: str, embeddings: Embeddings):
        from langchain_chroma import Chroma
        return Chroma(
            collection_name=kb_id,
            embedding_function=embeddings,
            persist_directory=settings.CHROMA_PERSIST_DIR,
            collection_metadata={"hnsw:space": "cosine"},
        )

    def delete_collection(self, kb_id: str):
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        try:
            client.delete_collection(kb_id)
        except Exception:
            pass

    def health_check(self) -> tuple[bool, str, dict]:
        import chromadb

        try:
            client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
            heartbeat = client.heartbeat()
            collections = client.list_collections()
            return True, f"connected (heartbeat={heartbeat:.1f}s)", {
                "collection_count": len(collections),
                "persist_dir": settings.CHROMA_PERSIST_DIR,
            }
        except Exception as e:
            return False, f"connection failed: {e}", {}

    def enrich_index_records(self, kb_id: str, records: list[dict]) -> list[dict]:
        if not records:
            return records

        import chromadb

        ids = [r["embedding_id"] for r in records if r.get("embedding_id")]
        if not ids:
            return records

        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        try:
            collection = client.get_collection(kb_id)
            raw = collection.get(ids=ids, include=["documents", "metadatas"])
        except Exception:
            return records

        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        id_list = raw.get("ids") or []

        store_map = {}
        for idx, record_id in enumerate(id_list):
            store_map[record_id] = {
                "store_found": True,
                "store_document_preview": (documents[idx] or "")[:240] if idx < len(documents) else "",
                "store_metadata": metadatas[idx] if idx < len(metadatas) else {},
            }

        enriched = []
        for record in records:
            extra = store_map.get(record.get("embedding_id"), {
                "store_found": False,
                "store_document_preview": "",
                "store_metadata": {},
            })
            enriched.append({**record, **extra})
        return enriched

    def list_kb_documents(self, kb_id: str) -> list[dict]:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        try:
            collection = client.get_collection(kb_id)
            raw = collection.get(include=["documents", "metadatas"])
        except Exception:
            return []

        ids = raw.get("ids") or []
        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        return [
            {
                "id": ids[idx],
                "content": documents[idx] or "",
                "metadata": metadatas[idx] or {},
            }
            for idx in range(len(ids))
        ]

    def kb_document_count(self, kb_id: str) -> int:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        try:
            return client.get_collection(kb_id).count()
        except Exception:
            return 0


class MilvusAdapter(VectorStoreAdapter):
    provider_name = "milvus"

    def create_store(self, kb_id: str, embeddings: Embeddings):
        from langchain_milvus import Milvus

        return Milvus(
            collection_name=kb_id,
            embedding_function=embeddings,
            connection_args={"host": settings.MILVUS_HOST, "port": settings.MILVUS_PORT},
        )

    def delete_collection(self, kb_id: str):
        # Leave actual cleanup pluggable. When Milvus is enabled, this can be
        # implemented against the chosen collection schema without touching the app.
        pass

    def health_check(self) -> tuple[bool, str, dict]:
        from pymilvus import connections

        try:
            connections.connect(
                alias="monitor_health",
                host=settings.MILVUS_HOST,
                port=settings.MILVUS_PORT,
            )
            try:
                from pymilvus import utility

                has_default = utility.has_collection("default", using="monitor_health")
                extra = {"has_default_collection": bool(has_default)}
            except Exception:
                extra = {}
            finally:
                try:
                    connections.disconnect("monitor_health")
                except Exception:
                    pass
            return True, f"connected to {settings.MILVUS_HOST}:{settings.MILVUS_PORT}", extra
        except Exception as e:
            return False, f"connection failed: {e}", {}


def _get_adapter() -> VectorStoreAdapter:
    if settings.VECTOR_STORE_PROVIDER == "chroma":
        return ChromaAdapter()
    if settings.VECTOR_STORE_PROVIDER == "milvus":
        return MilvusAdapter()
    raise ValueError(f"Unknown vector store provider: {settings.VECTOR_STORE_PROVIDER}")


def create_vector_store(kb_id: str, embeddings: Embeddings):
    return _get_adapter().create_store(kb_id, embeddings)


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
