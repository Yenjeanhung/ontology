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

    def list_collections(self) -> list[str]:
        """列出当前向量库下全部 collection（KB）名称。"""
        return []

    def query_collection(
        self,
        kb_id: str,
        query_text: str,
        embeddings: Embeddings,
        top_k: int = 5,
    ) -> list[dict]:
        """对指定 collection 执行向量相似度检索，返回文档列表。"""
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


# langchain_milvus 写入时使用固定的字段名：pk（主键）/ text（正文）/ vector（向量）。
# 其余 metadata 落在 Milvus 的动态字段里，读取时需要从 entity 中剔除非 metadata 字段。
_MILVUS_RESERVED_FIELDS = {"pk", "text", "vector", "$meta"}


class MilvusAdapter(VectorStoreAdapter):
    provider_name = "milvus"

    def create_store(self, kb_id: str, embeddings: Embeddings):
        from langchain_milvus import Milvus

        return Milvus(
            collection_name=kb_id,
            embedding_function=embeddings,
            connection_args={"host": settings.MILVUS_HOST, "port": settings.MILVUS_PORT},
            # 让 metadata 落在动态字段中，读取时可直接随 entity 返回
            enable_dynamic_field=True,
            auto_id=True,
        )

    def _connect(self, alias: str):
        """建立连接并返回别名，调用方负责在 finally 中断开。"""
        from pymilvus import connections

        connections.connect(alias=alias, host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
        return alias

    @staticmethod
    def _disconnect(alias: str):
        from pymilvus import connections

        try:
            connections.disconnect(alias)
        except Exception:
            pass

    def delete_collection(self, kb_id: str):
        from pymilvus import utility

        alias = f"del_{id(kb_id) & 0xFFFFFF:X}"
        self._connect(alias)
        try:
            if utility.has_collection(kb_id, using=alias):
                utility.drop_collection(kb_id, using=alias)
        finally:
            self._disconnect(alias)

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

    def list_collections(self) -> list[str]:
        from pymilvus import connections, utility

        connections.connect(
            alias="monitor_list",
            host=settings.MILVUS_HOST,
            port=settings.MILVUS_PORT,
        )
        try:
            return list(utility.list_collections(using="monitor_list"))
        finally:
            try:
                connections.disconnect("monitor_list")
            except Exception:
                pass

    def query_collection(
        self,
        kb_id: str,
        query_text: str,
        embeddings: Embeddings,
        top_k: int = 5,
    ) -> list[dict]:
        store = self.create_store(kb_id, embeddings)
        docs = store.similarity_search(query_text, k=top_k)
        return [
            {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "source": doc.metadata.get("source") or doc.metadata.get("file") or "",
                # Milvus 经 langchain 不暴露距离分数，置 None
                "score": None,
            }
            for doc in docs
        ]

    def enrich_index_records(self, kb_id: str, records: list[dict]) -> list[dict]:
        """按 embedding_id 回查 Milvus 中的正文与 metadata。"""
        if not records:
            return records

        ids = [r["embedding_id"] for r in records if r.get("embedding_id")]
        if not ids:
            return records

        from pymilvus import Collection, utility

        alias = f"enr_{id(records) & 0xFFFFFF:X}"
        self._connect(alias)
        try:
            if not utility.has_collection(kb_id, using=alias):
                return records
            col = Collection(kb_id, using=alias)
            col.load()
            # pk 由 langchain 写入时为字符串，用 in 表达式批量回查
            raw = col.query(expr=f'pk in {ids!r}', output_fields=["pk", "text"])
        except Exception:
            return records
        finally:
            self._disconnect(alias)

        store_map = {}
        for entity in raw or []:
            pk = entity.get("pk")
            metadata = {k: v for k, v in entity.items() if k not in _MILVUS_RESERVED_FIELDS}
            store_map[pk] = {
                "store_found": True,
                "store_document_preview": (entity.get("text") or "")[:240],
                "store_metadata": metadata,
            }

        return [
            {
                **record,
                **store_map.get(record.get("embedding_id"), {
                    "store_found": False,
                    "store_document_preview": "",
                    "store_metadata": {},
                }),
            }
            for record in records
        ]

    def list_kb_documents(self, kb_id: str) -> list[dict]:
        """全量导出 KB 分片，供 BM25 关键词索引构建。

        使用 query_iterator 分批拉取，避免 Milvus 单次 query 的返回条数上限。
        """
        from pymilvus import Collection, utility

        alias = f"lst_{id(kb_id) & 0xFFFFFF:X}"
        self._connect(alias)
        try:
            if not utility.has_collection(kb_id, using=alias):
                return []
            col = Collection(kb_id, using=alias)
            col.load()

            results: list[dict] = []
            iterator = col.query_iterator(batch_size=1000, expr="", output_fields=["pk", "text"])
            while True:
                batch = iterator.next()
                if not batch:
                    iterator.close()
                    break
                for entity in batch:
                    metadata = {k: v for k, v in entity.items() if k not in _MILVUS_RESERVED_FIELDS}
                    results.append({
                        "id": entity.get("pk"),
                        "content": entity.get("text") or "",
                        "metadata": metadata,
                    })
            return results
        except Exception:
            return []
        finally:
            self._disconnect(alias)

    def kb_document_count(self, kb_id: str) -> int:
        from pymilvus import Collection, utility

        alias = f"cnt_{id(kb_id) & 0xFFFFFF:X}"
        self._connect(alias)
        try:
            if not utility.has_collection(kb_id, using=alias):
                return 0
            col = Collection(kb_id, using=alias)
            return int(col.num_entities)
        except Exception:
            return 0
        finally:
            self._disconnect(alias)


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
