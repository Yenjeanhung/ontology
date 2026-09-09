"""Graph store provider adapters.

本包把图持久化隐藏在 provider 边界之后：默认使用嵌入式 Kùzu，
也可通过 GRAPH_STORE_PROVIDER 切换到 Neo4j。具体实现见子模块
（.kuzu / .neo4j），本文件只暴露统一门面与后端工厂，并重新导出
所有公开符号以保持与拆分前的导入路径兼容。
"""

from __future__ import annotations

from config import settings

from .base import (
    GraphStoreAdapter,
    GraphEntity,
    GraphRelation,
    ChunkGraphData,
)
from .kuzu import KuzuGraphAdapter
from .neo4j import Neo4jGraphAdapter

__all__ = [
    "GraphStoreAdapter",
    "GraphEntity",
    "GraphRelation",
    "ChunkGraphData",
    "KuzuGraphAdapter",
    "Neo4jGraphAdapter",
    "_get_adapter",
    "ensure_graph_schema",
    "upsert_document_graph",
    "delete_document_graph",
    "delete_kb_graph",
    "graph_store_health_check",
    "get_graph_store_provider_name",
    "graph_store_target",
    "graph_store_socket_target",
    "graph_store_hint",
    "list_graph_relation_types",
    "fetch_graph_view",
    "list_kb_entities",
    "entities_mentioned_by_chunks",
    "chunks_mentioning_entities",
    "entity_neighborhood",
    "upsert_entity",
    "delete_entity",
    "upsert_relation",
    "delete_relation",
]


_adapter: GraphStoreAdapter | None = None


def _get_adapter() -> GraphStoreAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter
    if settings.GRAPH_STORE_PROVIDER == "kuzu":
        _adapter = KuzuGraphAdapter()
        return _adapter
    if settings.GRAPH_STORE_PROVIDER == "neo4j":
        _adapter = Neo4jGraphAdapter()
        return _adapter
    raise ValueError(f"Unknown graph store provider: {settings.GRAPH_STORE_PROVIDER}")


def ensure_graph_schema():
    _get_adapter().ensure_schema()


def upsert_document_graph(
    kb_id: str,
    kb_name: str,
    file_id: str,
    file_name: str,
    file_path: str,
    chunks: list[ChunkGraphData],
    clear_existing: bool = True,
):
    _get_adapter().upsert_document_graph(kb_id, kb_name, file_id, file_name, file_path, chunks, clear_existing)


def delete_document_graph(file_id: str):
    _get_adapter().delete_document_graph(file_id)


def delete_kb_graph(kb_id: str):
    _get_adapter().delete_kb_graph(kb_id)


def graph_store_health_check():
    return _get_adapter().health_check()


def get_graph_store_provider_name() -> str:
    return _get_adapter().provider_name


def graph_store_target() -> str:
    """当前配置下图存储的连接目标描述（自检展示用）。"""
    return _get_adapter().describe_target()


def graph_store_socket_target() -> tuple[str, int] | None:
    """图存储可 TCP 探活的地址；本地嵌入式后端（如 kuzu）返回 None。"""
    return _get_adapter().socket_target()


def graph_store_hint() -> str:
    """图存储不可用时的排查建议。"""
    return _get_adapter().troubleshooting_hint()


def list_graph_relation_types(kb_id: str, file_id: str | None = None) -> list[str]:
    return _get_adapter().list_relation_types(kb_id, file_id)


def fetch_graph_view(
    kb_id: str,
    file_id: str | None = None,
    entity_query: str | None = None,
    relation_type: str | None = None,
) -> dict:
    return _get_adapter().fetch_graph_view(kb_id, file_id, entity_query, relation_type)


# ===== OAG 只读检索（模块级便捷函数）=====


def list_kb_entities(kb_id: str, limit: int = 5000) -> list[dict]:
    return _get_adapter().list_kb_entities(kb_id, limit)


def entities_mentioned_by_chunks(kb_id: str, chunk_ids: list[str]) -> list[dict]:
    return _get_adapter().entities_mentioned_by_chunks(kb_id, chunk_ids)


def chunks_mentioning_entities(kb_id: str, entity_ids: list[str], limit: int = 12) -> list[dict]:
    return _get_adapter().chunks_mentioning_entities(kb_id, entity_ids, limit)


def entity_neighborhood(
    kb_id: str, entity_ids: list[str], hops: int = 1, limit: int = 40,
) -> dict:
    return _get_adapter().entity_neighborhood(kb_id, entity_ids, hops, limit)


# ===== 实体/关系实例级别同步（模块级便捷函数）=====


def upsert_entity(
    entity_id: str,
    kb_id: str,
    ontology_id: str,
    entity_type: str,
    name: str,
    description: str = "",
    properties: str = "",
    label: str = "",
    category_id: str = "",
):
    _get_adapter().upsert_entity(
        entity_id, kb_id, ontology_id, entity_type, name, description, properties,
        label, category_id,
    )


def delete_entity(entity_id: str):
    _get_adapter().delete_entity(entity_id)


def upsert_relation(
    relation_id: str,
    kb_id: str,
    relation_type: str,
    description: str,
    source_entity_id: str,
    target_entity_id: str,
):
    _get_adapter().upsert_relation(
        relation_id, kb_id, relation_type, description, source_entity_id, target_entity_id
    )


def delete_relation(relation_id: str):
    _get_adapter().delete_relation(relation_id)
