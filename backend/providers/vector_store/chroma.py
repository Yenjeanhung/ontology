"""Chroma 本地向量库 adapter。"""

from __future__ import annotations

from config import settings

from .base import VectorStoreAdapter


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

    def describe_target(self) -> str:
        return settings.CHROMA_PERSIST_DIR

    def troubleshooting_hint(self) -> str:
        return "检查 CHROMA_PERSIST_DIR 目录权限"

    def list_collections(self) -> list[str]:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        collections = client.list_collections()
        # chromadb 新版本返回 Collection 对象，兼容旧版本字符串
        return [getattr(c, "name", c) for c in collections]

    def query_collection(
        self,
        kb_id: str,
        query_text: str,
        embeddings: Embeddings,
        top_k: int = 5,
    ) -> list[dict]:
        import chromadb

        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        collection = client.get_collection(kb_id)
        # 使用与写入一致的 cosine 距离空间
        query_embedding = embeddings.embed_query(query_text)
        resp = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        documents = (resp.get("documents") or [[]])[0]
        metadatas = (resp.get("metadatas") or [[]])[0]
        distances = (resp.get("distances") or [[]])[0]

        results = []
        for idx in range(len(documents)):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            # chroma 使用 cosine 距离，转成相似度分数（0~1，越大越相关）
            distance = distances[idx] if idx < len(distances) else None
            score = None
            if distance is not None:
                score = round(max(0.0, 1.0 - float(distance)), 4)
            results.append(
                {
                    "content": documents[idx] or "",
                    "metadata": metadata,
                    "source": metadata.get("source") or metadata.get("file") or "",
                    "score": score,
                }
            )
        return results

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
