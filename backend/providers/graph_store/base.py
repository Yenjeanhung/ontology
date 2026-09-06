"""Graph store provider 抽象基类与共享类型/工具函数。

具体后端（Kùzu / Neo4j）实现放在同包的子模块中（.kuzu / .neo4j），
本模块不依赖任何外部图数据库驱动，可独立导入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
from urllib.parse import urlparse


@dataclass
class GraphEntity:
    name: str
    entity_type: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    # 可选：由调用方（抽取流程写完 SQLite 后）回填的实例 id。
    # 提供时 upsert_document_graph 直接用它作为 Kùzu Entity.id，
    # 保证实体管理菜单的编辑/删除能同步到图；未提供时回退到 hash id。
    id: str | None = None
    # 抽取的属性值（JSON 字符串），写入 Kùzu Entity.properties 与 SQLite entities.properties
    properties: str = ""
    # 可选：归属本体 id（写入 Kùzu Entity.ontology_id）
    ontology_id: str | None = None


@dataclass
class GraphRelation:
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str
    description: str = ""
    # 同 GraphEntity.id：调用方回填的 SQLite relation.id
    id: str | None = None
    # 归属关系定义 id（写入 Kùzu Relation.relation_def_id）
    relation_def_id: str | None = None
    # 起终点实体实例 id（由调用方在写完 SQLite 后回填，写入 Kùzu Relation.source_entity_id/target_entity_id）
    source_entity_id: str | None = None
    target_entity_id: str | None = None


@dataclass
class ChunkGraphData:
    chunk_id: str
    chunk_index: int
    content: str
    entities: list[GraphEntity] = field(default_factory=list)
    relations: list[GraphRelation] = field(default_factory=list)


class GraphStoreAdapter(ABC):
    provider_name: str

    @abstractmethod
    def ensure_schema(self):
        raise NotImplementedError

    @abstractmethod
    def delete_document_graph(self, file_id: str):
        raise NotImplementedError

    @abstractmethod
    def delete_kb_graph(self, kb_id: str):
        raise NotImplementedError

    @abstractmethod
    def upsert_document_graph(
        self,
        kb_id: str,
        kb_name: str,
        file_id: str,
        file_name: str,
        file_path: str,
        chunks: list[ChunkGraphData],
        clear_existing: bool = True,
    ):
        raise NotImplementedError

    @abstractmethod
    def health_check(self):
        raise NotImplementedError

    # ===== 启动自检契约：由各后端自行声明，避免自检里写死 provider 分支 =====

    def describe_target(self) -> str:
        """连接目标描述（展示用），如 bolt://host:7687 或本地库路径。"""
        return ""

    def socket_target(self) -> tuple[str, int] | None:
        """可做 TCP 探活的地址；本地嵌入式后端返回 None。"""
        return None

    def troubleshooting_hint(self) -> str:
        """组件不可用时的排查建议（展示用）。"""
        return ""

    @abstractmethod
    def list_relation_types(
        self,
        kb_id: str,
        file_id: str | None = None,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def fetch_graph_view(
        self,
        kb_id: str,
        file_id: str | None = None,
        entity_query: str | None = None,
        relation_type: str | None = None,
    ) -> dict:
        raise NotImplementedError

    # ===== OAG 只读检索（供智能体检索融合 / 上下文融合使用）=====
    # 设计：全部为只读查询，不触碰 _kuzu_write_lock，与抽取/同步流程隔离。
    # 基于 Kùzu 的 Cypher 方言；Neo4j 端直接委托此实现（语法兼容）。
    # 注意：以下是具体实现（非抽象），子类直接继承或按需 override；
    # 不保留同名 @abstractmethod 占位，以免误导静态分析将其当作未实现。

    def list_kb_entities(self, kb_id: str, limit: int = 5000) -> list[dict]:
        rows = self._execute_dict(
            """
            MATCH (e:Entity {kb_id: $kb_id})
            RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type
            ORDER BY e.name
            LIMIT $limit
            """,
            {"kb_id": kb_id, "limit": limit},
        )
        return rows

    def entities_mentioned_by_chunks(self, kb_id: str, chunk_ids: list[str]) -> list[dict]:
        if not chunk_ids:
            return []
        rows = self._execute_dict(
            """
            MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
            WHERE c.kb_id = $kb_id AND c.id IN $chunk_ids
            RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type,
                   e.description AS description, e.properties AS properties,
                   count(c) AS mention_count
            ORDER BY mention_count DESC
            """,
            {"kb_id": kb_id, "chunk_ids": chunk_ids},
        )
        return rows

    def chunks_mentioning_entities(self, kb_id: str, entity_ids: list[str], limit: int = 12) -> list[dict]:
        if not entity_ids:
            return []
        rows = self._execute_dict(
            """
            MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
            MATCH (d:Document)-[:HAS_CHUNK]->(c)
            WHERE c.kb_id = $kb_id AND e.id IN $entity_ids
            RETURN c.id AS chunk_id, c.file_id AS file_id, c.chunk_index AS chunk_index,
                   c.content AS content, d.name AS file_name
            LIMIT $limit
            """,
            {"kb_id": kb_id, "entity_ids": entity_ids, "limit": limit},
        )
        return rows

    def entity_neighborhood(
        self, kb_id: str, entity_ids: list[str], hops: int = 1, limit: int = 40,
    ) -> dict:
        # v1 固定 1 跳（hops 仅用于接口预留）
        if not entity_ids:
            return {"entities": [], "relations": []}
        seed_rows = self._execute_dict(
            """
            MATCH (e:Entity)
            WHERE e.kb_id = $kb_id AND e.id IN $entity_ids
            RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type,
                   e.description AS description, e.properties AS properties
            """,
            {"kb_id": kb_id, "entity_ids": entity_ids},
        )
        entities = [
            {
                "id": r.get("entity_id"),
                "name": r.get("name"),
                "entity_type": r.get("entity_type"),
                "description": (r.get("description") or ""),
                "properties": _safe_props(r.get("properties")),
            }
            for r in seed_rows
        ]
        rel_rows = self._execute_dict(
            """
            MATCH (e:Entity {kb_id: $kb_id})-[r:RELATES]->(n:Entity)
            WHERE e.id IN $entity_ids
            RETURN e.id AS source_id, e.name AS source_name, e.entity_type AS source_type,
                   r.relation_type AS relation_type, r.relation_id AS relation_id,
                   n.id AS target_id, n.name AS target_name, n.entity_type AS target_type
            LIMIT $limit
            """,
            {"kb_id": kb_id, "entity_ids": entity_ids, "limit": limit},
        )
        relations = []
        seen: set[tuple] = set()
        for r in rel_rows:
            key = (r.get("source_id"), r.get("relation_type"), r.get("target_id"))
            if key in seen:
                continue
            seen.add(key)
            relations.append({
                "source_name": r.get("source_name"),
                "source_type": r.get("source_type"),
                "relation_type": r.get("relation_type"),
                "target_name": r.get("target_name"),
                "target_type": r.get("target_type"),
            })
        return {"entities": entities, "relations": relations}

    # ===== 实体/关系实例级别同步（供"实体管理"菜单 CRUD 使用）=====
    # 设计：Kùzu Entity.id / Relation.id 直接复用 SQLite 实体/关系实例的 id，
    # 以 SQLite 为权威存储，Kùzu 仅承担图谱可视化与图遍历职责。

    @abstractmethod
    def upsert_entity(
        self,
        entity_id: str,
        kb_id: str,
        ontology_id: str,
        entity_type: str,
        name: str,
        description: str = "",
        properties: str = "",
    ):
        raise NotImplementedError

    @abstractmethod
    def delete_entity(self, entity_id: str):
        raise NotImplementedError

    @abstractmethod
    def upsert_relation(
        self,
        relation_id: str,
        kb_id: str,
        relation_type: str,
        description: str,
        source_entity_id: str,
        target_entity_id: str,
    ):
        raise NotImplementedError

    @abstractmethod
    def delete_relation(self, relation_id: str):
        raise NotImplementedError


def _normalize_text(value: str, default: str = "") -> str:
    value = (value or "").strip()
    return value or default


def _entity_id(kb_id: str, entity_name: str, entity_type: str) -> str:
    payload = f"{kb_id}|{entity_type.lower()}|{entity_name.lower()}".encode("utf-8")
    return "ent_" + hashlib.sha1(payload).hexdigest()[:20]


def _relation_id(
    kb_id: str,
    source_name: str,
    source_type: str,
    target_name: str,
    target_type: str,
    relation_type: str,
) -> str:
    payload = (
        f"{kb_id}|{source_type.lower()}|{source_name.lower()}|"
        f"{relation_type.lower()}|{target_type.lower()}|{target_name.lower()}"
    ).encode("utf-8")
    return "rel_" + hashlib.sha1(payload).hexdigest()[:20]


def _safe_props(raw) -> dict:
    """把 Kùzu/Neo4j 中 Entity.properties（JSON 字符串或 dict）解析为 dict。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_uri_host_port(uri: str, default_port: int) -> tuple[str, int] | None:
    """从 bolt://host:port 这类 URI 解析出可探活地址；解析失败返回 None。"""
    try:
        parsed = urlparse(uri)
        if not parsed.hostname:
            return None
        return parsed.hostname, parsed.port or default_port
    except Exception:
        return None
