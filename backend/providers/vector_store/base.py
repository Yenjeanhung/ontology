"""Vector store provider 抽象基类。

具体后端（Chroma / Milvus）在同包子模块中实现，按需延迟导入，
未安装某后端驱动时不会拖累整个包的导入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings


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

    # ===== 启动自检契约：由各后端自行声明，避免自检里写死 provider 分支 =====

    def describe_target(self) -> str:
        """连接目标描述（展示用），如 host:19530 或本地库路径。"""
        return ""

    def socket_target(self) -> tuple[str, int] | None:
        """可做 TCP 探活的地址；本地嵌入式后端返回 None。"""
        return None

    def troubleshooting_hint(self) -> str:
        """组件不可用时的排查建议（展示用）。"""
        return ""

    def enrich_index_records(self, kb_id: str, records: list[dict]) -> list[dict]:
        """Best-effort provider-specific enrichment for inspector pages."""
        return records

    def delete_by_ids(
        self, kb_id: str, ids: list[str], embeddings: Embeddings = None
    ) -> int:
        """按 chunk_id 删除向量，返回删除条数。

        默认实现走 LangChain 的 delete(ids=...)，Milvus 因主键类型差异自行实现。
        """
        if not ids:
            return 0
        self.create_store(kb_id, embeddings).delete(ids=ids)
        return len(ids)

    def list_kb_documents(self, kb_id: str) -> list[dict]:
        """返回 KB 下全部分片 [{id, content, metadata}]，供 BM25 等关键词索引构建。

        provider 不支持时抛 NotImplementedError；调用方需捕获并降级。
        """
        raise NotImplementedError

    def kb_document_count(self, kb_id: str) -> int:
        """KB 下分片总数（用于 BM25 缓存指纹）。默认 0 表示不支持。"""
        return 0
