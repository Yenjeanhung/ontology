"""Milvus 向量库 adapter。

全部走 pymilvus 的 MilvusClient API（ORM 风格的 Collection/connections/utility
在 PyMilvus 3.1 中移除，此处统一迁移以消除 PyMilvusDeprecationWarning）。
"""

from __future__ import annotations

import logging
import threading
from itertools import islice

from langchain_core.embeddings import Embeddings

from config import settings

from .base import VectorStoreAdapter

logger = logging.getLogger(__name__)


# langchain_milvus 写入时使用固定的字段名：pk（主键）/ text（正文）/ vector（向量）。
# 其余 metadata 落在 Milvus 的动态字段里，读取时需要从 entity 中剔除非 metadata 字段。
_MILVUS_RESERVED_FIELDS = {"pk", "text", "vector", "$meta"}

# pk in [...] 表达式按批下发，避免单条表达式过长
_PK_EXPR_BATCH = 200
# 存量集合迁移时的读取批次
_MIGRATE_BATCH = 500

# 已完成主键兼容性检查的 collection（进程内缓存，避免每次建 store 都 describe 一次）
_checked_collections: set[str] = set()
_schema_lock = threading.Lock()


def _collection_name(kb_id: str) -> str:
    """kb_id → Milvus collection 名。

    Milvus 要求 collection 名以字母或下划线开头，而 kb_id 是 ``uuid4().hex[:12]``，
    约一半概率以数字开头（如 ``5e4631d3e55c``），直接使用会被 Milvus 拒绝。
    数字开头时统一加 ``kb_`` 前缀；字母开头的保持原样，兼容存量 collection。
    """
    name = kb_id or ""
    if name and (name[0].isalpha() or name[0] == "_"):
        return name
    return f"kb_{name}"


def _connect_client():
    """新建 MilvusClient 连接。MilvusClient 持有独立连接，调用方负责 close。"""
    from pymilvus import MilvusClient

    return MilvusClient(uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}")


def _close_client(client) -> None:
    try:
        client.close()
    except Exception:
        pass


def _pk_field_of(desc: dict | None):
    """从 describe_collection 结果中取主键字段描述。"""
    fields = (desc or {}).get("fields") or []
    return next((f for f in fields if f.get("is_primary")), None)


class MilvusAdapter(VectorStoreAdapter):
    provider_name = "milvus"

    def create_store(self, kb_id: str, embeddings: Embeddings):
        self._ensure_chunk_id_primary_key(kb_id, embeddings)
        return self._build_store(_collection_name(kb_id), embeddings)

    def _build_store(self, collection_name: str, embeddings: Embeddings):
        from langchain_milvus import Milvus

        return Milvus(
            collection_name=collection_name,
            embedding_function=embeddings,
            connection_args={"host": settings.MILVUS_HOST, "port": settings.MILVUS_PORT},
            # 让 metadata 落在动态字段中，读取时可直接随 entity 返回
            enable_dynamic_field=True,
            # 主键使用写入侧传入的 chunk_id（f"{file_id}_{chunk_index}"），与
            # chunks.embedding_id 一致，删除/回查才能按 pk 精确定位。
            # auto_id=True 时 Milvus 生成 Int64 主键并忽略传入 ids，会导致两者脱节。
            auto_id=False,
        )

    def _ensure_chunk_id_primary_key(self, kb_id: str, embeddings: Embeddings) -> None:
        """存量集合兼容：早期以 auto_id=True 建集合，pk 是 Milvus 自动生成的 Int64。

        这类集合与 chunks.embedding_id（字符串）对不上，删除会报
        "cannot parse expression: pk in [...]"、回查全部 miss。这里把它们重建为
        VARCHAR 主键并回填原有分片。
        """
        name = _collection_name(kb_id)
        if name in _checked_collections:
            return
        with _schema_lock:
            if name in _checked_collections:
                return
            try:
                self._rebuild_int_pk_collection(name, embeddings)
            except Exception:
                logger.exception(
                    "Milvus primary key check failed: collection=%s "
                    "(legacy int pk may remain, rebuild the KB index if writes fail)",
                    name,
                )
            finally:
                _checked_collections.add(name)

    def _rebuild_int_pk_collection(self, name: str, embeddings: Embeddings) -> None:
        from pymilvus import DataType

        client = _connect_client()
        try:
            if not client.has_collection(name):
                return
            pk_field = _pk_field_of(client.describe_collection(name))
            if pk_field is None or pk_field.get("type") != DataType.INT64:
                return
            self._migrate_legacy_collection(client, name, embeddings)
        finally:
            _close_client(client)

    def _migrate_legacy_collection(self, client, name: str, embeddings: Embeddings) -> None:
        logger.warning(
            "Migrating Milvus collection %s: int auto-id pk -> chunk_id varchar pk", name
        )
        client.load_collection(name)

        ids: list[str] = []
        texts: list[str] = []
        vectors: list = []
        metadatas: list[dict] = []
        seen: set[str] = set()
        iterator = client.query_iterator(
            name,
            batch_size=_MIGRATE_BATCH,
            filter="",
            output_fields=["pk", "text", "vector", "*"],
        )
        try:
            while True:
                batch = iterator.next()
                if not batch:
                    break
                for entity in batch:
                    metadata = {
                        k: v for k, v in entity.items() if k not in _MILVUS_RESERVED_FIELDS
                    }
                    file_id = metadata.get("file_id")
                    chunk_index = metadata.get("chunk_index")
                    if file_id and chunk_index is not None:
                        new_id = f"{file_id}_{chunk_index}"
                    else:
                        new_id = f"legacy_{entity.get('pk')}"
                    if new_id in seen:
                        suffix = 1
                        while f"{new_id}#{suffix}" in seen:
                            suffix += 1
                        new_id = f"{new_id}#{suffix}"
                    seen.add(new_id)
                    ids.append(new_id)
                    texts.append(entity.get("text") or "")
                    vectors.append(entity.get("vector"))
                    metadatas.append(metadata)
        finally:
            try:
                iterator.close()
            except Exception:
                pass

        if not ids:
            client.drop_collection(name)
            logger.info("Dropped empty legacy Milvus collection: %s", name)
            return

        tmp_name = f"{name}__migrate"
        if client.has_collection(tmp_name):
            client.drop_collection(tmp_name)
        try:
            store = self._build_store(tmp_name, embeddings)
            store.add_embeddings(
                texts=texts, embeddings=vectors, metadatas=metadatas, ids=ids
            )
            client.flush(tmp_name)
            written = self._fetch_entity_count(tmp_name)
            if written < len(ids):
                raise RuntimeError(
                    f"migration incomplete: {written}/{len(ids)} entities rewritten"
                )
            client.drop_collection(name)
            client.rename_collection(tmp_name, name)
            logger.info(
                "Migrated Milvus collection %s: %s entities rewritten with chunk_id pk",
                name,
                written,
            )
        except Exception:
            if client.has_collection(tmp_name):
                client.drop_collection(tmp_name)
            raise

    def delete_by_ids(self, kb_id: str, ids: list[str], embeddings: Embeddings = None) -> int:
        """按 chunk_id 删除向量。

        pk 为 VARCHAR（chunk_id）时直接按 pk 过滤；存量集合 pk 仍是 Int64 时，
        退回按动态字段 file_id 过滤（chunk_id 形如 f"{file_id}_{chunk_index}"）。
        """
        if not ids:
            return 0

        from pymilvus import DataType

        name = _collection_name(kb_id)
        client = _connect_client()
        try:
            if not client.has_collection(name):
                return 0
            pk_field = _pk_field_of(client.describe_collection(name))

            deleted = 0
            if pk_field is not None and pk_field.get("type") == DataType.INT64:
                file_ids = sorted({cid.rsplit("_", 1)[0] for cid in ids if "_" in cid})
                if not file_ids:
                    return 0
                logger.warning(
                    "Milvus collection %s still uses legacy int pk, "
                    "deleting by file_id filter instead",
                    name,
                )
                deleted += self._delete_count(
                    client.delete(name, filter=f"file_id in {file_ids!r}")
                )
            else:
                iterator = iter(ids)
                while batch := list(islice(iterator, _PK_EXPR_BATCH)):
                    deleted += self._delete_count(
                        client.delete(name, filter=f"pk in {batch!r}")
                    )

            if deleted:
                client.flush(name)
            return deleted
        finally:
            _close_client(client)

    @staticmethod
    def _delete_count(result) -> int:
        if isinstance(result, dict):
            count = result.get("delete_count")
        else:
            count = getattr(result, "delete_count", None)
            if count is None:
                count = getattr(result, "delete_cnt", None)
        return int(count or 0)

    def delete_collection(self, kb_id: str):
        name = _collection_name(kb_id)
        client = _connect_client()
        try:
            if client.has_collection(name):
                client.drop_collection(name)
        finally:
            _close_client(client)

    def health_check(self) -> tuple[bool, str, dict]:
        try:
            client = _connect_client()
        except Exception as e:
            return False, f"connection failed: {e}", {}
        try:
            try:
                has_default = client.has_collection("default")
                extra = {"has_default_collection": bool(has_default)}
            except Exception:
                extra = {}
            return True, f"connected to {settings.MILVUS_HOST}:{settings.MILVUS_PORT}", extra
        except Exception as e:
            return False, f"connection failed: {e}", {}
        finally:
            _close_client(client)

    def describe_target(self) -> str:
        return f"{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"

    def socket_target(self) -> tuple[str, int] | None:
        return settings.MILVUS_HOST, settings.MILVUS_PORT

    def troubleshooting_hint(self) -> str:
        return ("docker-compose up -d etcd minio milvus"
                "（Milvus 依赖 etcd/minio，启动较慢请稍候重试）")

    def list_collections(self) -> list[str]:
        client = _connect_client()
        try:
            return list(client.list_collections())
        finally:
            _close_client(client)

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

        from pymilvus import DataType

        name = _collection_name(kb_id)
        client = _connect_client()
        try:
            if not client.has_collection(name):
                return records
            client.load_collection(name)
            pk_field = _pk_field_of(client.describe_collection(name))
            if pk_field is not None and pk_field.get("type") != DataType.VARCHAR:
                # 存量 int 主键集合：只能按 file_id / chunk_index 回查
                raw = self._query_legacy_by_file(client, name, records)
            else:
                # pk 由 langchain 写入时为字符串（chunk_id），用 in 表达式批量回查
                raw = client.query(
                    name, filter=f"pk in {ids!r}", output_fields=["pk", "text"]
                )
        except Exception:
            return records
        finally:
            _close_client(client)

        store_map = {}
        for entity in raw or []:
            pk = entity.get("pk")
            if pk is None:
                file_id = entity.get("file_id")
                chunk_index = entity.get("chunk_index")
                if file_id is None or chunk_index is None:
                    continue
                pk = f"{file_id}_{chunk_index}"
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

    @staticmethod
    def _query_legacy_by_file(client, name: str, records: list[dict]) -> list[dict]:
        """存量 int 主键集合的回查：按 file_id 批量取，再用 chunk_index 对齐。"""
        file_ids = sorted({r.get("file_id") for r in records if r.get("file_id")})
        if not file_ids:
            return []
        return client.query(
            name,
            filter=f"file_id in {file_ids!r}",
            output_fields=["file_id", "chunk_index", "text"],
        )

    def list_kb_documents(self, kb_id: str) -> list[dict]:
        """全量导出 KB 分片，供 BM25 关键词索引构建。

        使用 query_iterator 分批拉取，避免 Milvus 单次 query 的返回条数上限。
        """
        name = _collection_name(kb_id)
        client = _connect_client()
        try:
            if not client.has_collection(name):
                return []
            client.load_collection(name)

            results: list[dict] = []
            iterator = client.query_iterator(
                name, batch_size=1000, filter="", output_fields=["pk", "text"]
            )
            try:
                while True:
                    batch = iterator.next()
                    if not batch:
                        break
                    for entity in batch:
                        metadata = {
                            k: v for k, v in entity.items()
                            if k not in _MILVUS_RESERVED_FIELDS
                        }
                        results.append({
                            "id": entity.get("pk"),
                            "content": entity.get("text") or "",
                            "metadata": metadata,
                        })
            finally:
                try:
                    iterator.close()
                except Exception:
                    pass
            return results
        except Exception:
            return []
        finally:
            _close_client(client)

    @staticmethod
    def _fetch_entity_count(name: str) -> int:
        """统计集合实体数。

        get_collection_stats 返回已 flush 的行数，与旧 Collection.num_entities
        语义一致，且无需将集合加载（load）进内存。
        """
        client = _connect_client()
        try:
            stats = client.get_collection_stats(name) or {}
            return int(stats.get("row_count") or 0)
        finally:
            _close_client(client)

    def kb_document_count(self, kb_id: str) -> int:
        try:
            return self._fetch_entity_count(_collection_name(kb_id))
        except Exception:
            return 0
