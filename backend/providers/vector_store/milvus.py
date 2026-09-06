"""Milvus 向量库 adapter。"""

from __future__ import annotations

from config import settings

from .base import VectorStoreAdapter


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

    def describe_target(self) -> str:
        return f"{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"

    def socket_target(self) -> tuple[str, int] | None:
        return settings.MILVUS_HOST, settings.MILVUS_PORT

    def troubleshooting_hint(self) -> str:
        return ("docker-compose up -d etcd minio milvus"
                "（Milvus 依赖 etcd/minio，启动较慢请稍候重试）")

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
