"""Graph store provider adapters.

This module keeps graph persistence behind a provider boundary so we can
default to an embedded Kuzu database and switch to Neo4j later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import threading

from config import settings

# Kùzu database only allows one write transaction at a time
# This lock ensures thread-safe write operations
_kuzu_write_lock = threading.Lock()


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

    @abstractmethod
    def list_kb_entities(self, kb_id: str, limit: int = 5000) -> list[dict]:
        """列出 KB 下实体（轻量：id/name/entity_type），供实体链接词面匹配。"""
        raise NotImplementedError

    @abstractmethod
    def entities_mentioned_by_chunks(self, kb_id: str, chunk_ids: list[str]) -> list[dict]:
        """分片 → 它们 MENTION 的实体（带出现计数），供从向量分片反查实体。"""
        raise NotImplementedError

    @abstractmethod
    def chunks_mentioning_entities(self, kb_id: str, entity_ids: list[str], limit: int = 12) -> list[dict]:
        """实体 → 提到它们的分片（含 file_name/content），供图谱召回补向量漏召。"""
        raise NotImplementedError

    @abstractmethod
    def entity_neighborhood(
        self, kb_id: str, entity_ids: list[str], hops: int = 1, limit: int = 40,
    ) -> dict:
        """实体的 1 跳邻居关系 + 实体属性，返回 {entities, relations}，供图谱事实注入。"""
        raise NotImplementedError

    # ===== OAG 只读检索实现 =====

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


class KuzuGraphAdapter(GraphStoreAdapter):
    provider_name = "kuzu"

    def __init__(self):
        self._db = None

    def _connection(self):
        import kuzu

        db_path = Path(settings.KUZU_DB_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if self._db is None:
            self._db = kuzu.Database(str(db_path))
        return kuzu.Connection(self._db)

    def _execute(self, query: str, parameters: dict | None = None):
        # Kùzu only allows one write transaction at a time
        # Determine if this is a write operation
        query_upper = query.strip().upper()
        is_write = any(query_upper.startswith(op) for op in 
                       ('CREATE', 'INSERT', 'UPDATE', 'DELETE', 'DROP', 'MERGE', 'REMOVE', 'SET'))
        
        if is_write:
            with _kuzu_write_lock:
                return self._execute_internal(query, parameters)
        else:
            return self._execute_internal(query, parameters)

    def _execute_internal(self, query: str, parameters: dict | None = None):
        conn = self._connection()
        return conn.execute(query, parameters=parameters or {})

    def _execute_dict(self, query: str, parameters: dict | None = None) -> list[dict]:
        result = self._execute(query, parameters)
        return result.rows_as_dict().get_all()

    def ensure_schema(self):
        statements = [
            "CREATE NODE TABLE KnowledgeBase(id STRING PRIMARY KEY, name STRING, description STRING)",
            "CREATE NODE TABLE Document(id STRING PRIMARY KEY, kb_id STRING, name STRING, path STRING)",
            "CREATE NODE TABLE Chunk(id STRING PRIMARY KEY, file_id STRING, kb_id STRING, chunk_index INT64, content STRING)",
            "CREATE NODE TABLE Entity(id STRING PRIMARY KEY, kb_id STRING, name STRING, entity_type STRING, description STRING)",
            "CREATE NODE TABLE Relation(id STRING PRIMARY KEY, kb_id STRING, relation_type STRING, description STRING)",
            "CREATE REL TABLE HAS_DOCUMENT(FROM KnowledgeBase TO Document)",
            "CREATE REL TABLE HAS_CHUNK(FROM Document TO Chunk)",
            "CREATE REL TABLE NEXT_CHUNK(FROM Chunk TO Chunk, order_index INT64)",
            "CREATE REL TABLE MENTIONS(FROM Chunk TO Entity)",
            "CREATE REL TABLE HAS_RELATION(FROM Chunk TO Relation)",
            "CREATE REL TABLE RELATION_SOURCE(FROM Relation TO Entity)",
            "CREATE REL TABLE RELATION_TARGET(FROM Relation TO Entity)",
            "CREATE REL TABLE RELATES(FROM Entity TO Entity, relation_id STRING, relation_type STRING)",
        ]
        for statement in statements:
            try:
                self._execute(statement)
            except Exception:
                # Kuzu raises if the table already exists.
                pass
        # 在已有表上补充实例层字段（首次升级到含实体管理菜单的版本时执行）
        # 注意：Kùzu 的 ALTER TABLE 语法是 ADD（不是 ADD COLUMN），与 SQL 标准不同
        # 重复执行会抛错（列已存在），忽略即可
        for alter in (
            "ALTER TABLE Entity ADD ontology_id STRING DEFAULT ''",
            "ALTER TABLE Entity ADD properties STRING DEFAULT ''",
            "ALTER TABLE Relation ADD relation_def_id STRING DEFAULT ''",
            "ALTER TABLE Relation ADD source_entity_id STRING DEFAULT ''",
            "ALTER TABLE Relation ADD target_entity_id STRING DEFAULT ''",
        ):
            try:
                self._execute(alter)
            except Exception:
                pass

    def delete_document_graph(self, file_id: str):
        with _kuzu_write_lock:
            self._delete_document_graph_internal(file_id)

    def _delete_document_graph_internal(self, file_id: str):
        self._execute(
            """
            MATCH (r:Relation)<-[:HAS_RELATION]-(c:Chunk {file_id: $file_id})
            DETACH DELETE r
            """,
            {"file_id": file_id},
        )
        self._execute(
            """
            MATCH (c:Chunk {file_id: $file_id})
            DETACH DELETE c
            """,
            {"file_id": file_id},
        )
        self._execute(
            """
            MATCH (d:Document {id: $file_id})
            DETACH DELETE d
            """,
            {"file_id": file_id},
        )

    def delete_kb_graph(self, kb_id: str):
        self._execute(
            """
            MATCH (n)
            WHERE n.kb_id = $kb_id OR n.id = $kb_id
            DETACH DELETE n
            """,
            {"kb_id": kb_id},
        )

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
        with _kuzu_write_lock:
            if clear_existing:
                self._delete_document_graph_internal(file_id)
            self._execute_internal(
                """
                MERGE (kb:KnowledgeBase {id: $kb_id})
                ON CREATE SET kb.name = $kb_name, kb.description = ''
                ON MATCH SET kb.name = $kb_name
                """,
                {"kb_id": kb_id, "kb_name": kb_name},
            )
            self._execute_internal(
                """
                MERGE (d:Document {id: $file_id})
                ON CREATE SET d.kb_id = $kb_id, d.name = $file_name, d.path = $file_path
                ON MATCH SET d.kb_id = $kb_id, d.name = $file_name, d.path = $file_path
                """,
                {
                    "file_id": file_id,
                    "kb_id": kb_id,
                    "file_name": file_name,
                    "file_path": file_path,
                },
            )
            self._execute_internal(
                """
                MATCH (kb:KnowledgeBase {id: $kb_id}), (d:Document {id: $file_id})
                MERGE (kb)-[:HAS_DOCUMENT]->(d)
                """,
                {"kb_id": kb_id, "file_id": file_id},
            )

            previous_chunk_id = None
            for chunk in chunks:
                self._execute_internal(
                    """
                    MERGE (c:Chunk {id: $chunk_id})
                    ON CREATE SET c.file_id = $file_id, c.kb_id = $kb_id, c.chunk_index = $chunk_index, c.content = $content
                    ON MATCH SET c.file_id = $file_id, c.kb_id = $kb_id, c.chunk_index = $chunk_index, c.content = $content
                    """,
                    {
                        "chunk_id": chunk.chunk_id,
                        "file_id": file_id,
                        "kb_id": kb_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                    },
                )
                self._execute_internal(
                    """
                    MATCH (d:Document {id: $file_id}), (c:Chunk {id: $chunk_id})
                    MERGE (d)-[:HAS_CHUNK]->(c)
                    """,
                    {"file_id": file_id, "chunk_id": chunk.chunk_id},
                )

                if previous_chunk_id is not None:
                    self._execute_internal(
                        """
                        MATCH (prev:Chunk {id: $prev_chunk_id}), (curr:Chunk {id: $chunk_id})
                        MERGE (prev)-[r:NEXT_CHUNK]->(curr)
                        ON CREATE SET r.order_index = $chunk_index
                        ON MATCH SET r.order_index = $chunk_index
                        """,
                        {
                            "prev_chunk_id": previous_chunk_id,
                            "chunk_id": chunk.chunk_id,
                            "chunk_index": chunk.chunk_index,
                        },
                    )
                previous_chunk_id = chunk.chunk_id

                for entity in chunk.entities:
                    entity_name = _normalize_text(entity.name)
                    entity_type = _normalize_text(entity.entity_type, "UNKNOWN")
                    # 优先使用调用方回填的 SQLite 实体 id；未提供时回退到 hash id
                    entity_id = entity.id or _entity_id(kb_id, entity_name, entity_type)
                    self._execute_internal(
                        """
                        MERGE (e:Entity {id: $entity_id})
                        ON CREATE SET e.kb_id = $kb_id, e.name = $entity_name, e.entity_type = $entity_type, e.description = $description,
                                      e.ontology_id = $ontology_id, e.properties = $properties
                        ON MATCH SET e.description = CASE WHEN e.description = '' THEN $description ELSE e.description END,
                                     e.ontology_id = $ontology_id, e.properties = $properties
                        """,
                        {
                            "entity_id": entity_id,
                            "kb_id": kb_id,
                            "entity_name": entity_name,
                            "entity_type": entity_type,
                            "description": _normalize_text(entity.description),
                            "ontology_id": entity.ontology_id or "",
                            "properties": entity.properties or "",
                        },
                    )
                    self._execute_internal(
                        """
                        MATCH (c:Chunk {id: $chunk_id}), (e:Entity {id: $entity_id})
                        MERGE (c)-[:MENTIONS]->(e)
                        """,
                        {"chunk_id": chunk.chunk_id, "entity_id": entity_id},
                    )

                for relation in chunk.relations:
                    source_name = _normalize_text(relation.source_name)
                    source_type = _normalize_text(relation.source_type, "UNKNOWN")
                    target_name = _normalize_text(relation.target_name)
                    target_type = _normalize_text(relation.target_type, "UNKNOWN")
                    relation_type = _normalize_text(relation.relation_type, "RELATED_TO")
                    # 优先使用回填的 SQLite 实体 id（起终点）与关系实例 id
                    source_id = relation.source_entity_id or _entity_id(kb_id, source_name, source_type)
                    target_id = relation.target_entity_id or _entity_id(kb_id, target_name, target_type)
                    relation_id = relation.id or _relation_id(
                        kb_id,
                        source_name,
                        source_type,
                        target_name,
                        target_type,
                        relation_type,
                    )

                    self._execute_internal(
                        """
                        MERGE (source:Entity {id: $source_id})
                        ON CREATE SET source.kb_id = $kb_id, source.name = $source_name, source.entity_type = $source_type, source.description = ''
                        """,
                        {
                            "source_id": source_id,
                            "kb_id": kb_id,
                            "source_name": source_name,
                            "source_type": source_type,
                        },
                    )
                    self._execute_internal(
                        """
                        MERGE (target:Entity {id: $target_id})
                        ON CREATE SET target.kb_id = $kb_id, target.name = $target_name, target.entity_type = $target_type, target.description = ''
                        """,
                        {
                            "target_id": target_id,
                            "kb_id": kb_id,
                            "target_name": target_name,
                            "target_type": target_type,
                        },
                    )
                    self._execute_internal(
                        """
                        MERGE (r:Relation {id: $relation_id})
                        ON CREATE SET r.kb_id = $kb_id, r.relation_type = $relation_type, r.description = $description,
                                      r.relation_def_id = $relation_def_id,
                                      r.source_entity_id = $source_entity_id, r.target_entity_id = $target_entity_id
                        ON MATCH SET r.description = CASE WHEN r.description = '' THEN $description ELSE r.description END,
                                     r.relation_def_id = $relation_def_id,
                                     r.source_entity_id = $source_entity_id, r.target_entity_id = $target_entity_id
                        """,
                        {
                            "relation_id": relation_id,
                            "kb_id": kb_id,
                            "relation_type": relation_type,
                            "description": _normalize_text(relation.description),
                            "relation_def_id": relation.relation_def_id or "",
                            "source_entity_id": source_id,
                            "target_entity_id": target_id,
                        },
                    )
                    self._execute_internal(
                        """
                        MATCH (c:Chunk {id: $chunk_id}), (r:Relation {id: $relation_id})
                        MERGE (c)-[:HAS_RELATION]->(r)
                        """,
                        {"chunk_id": chunk.chunk_id, "relation_id": relation_id},
                    )
                    self._execute_internal(
                        """
                        MATCH (r:Relation {id: $relation_id}), (source:Entity {id: $source_id})
                        MERGE (r)-[:RELATION_SOURCE]->(source)
                        """,
                        {"relation_id": relation_id, "source_id": source_id},
                    )
                    self._execute_internal(
                        """
                        MATCH (r:Relation {id: $relation_id}), (target:Entity {id: $target_id})
                        MERGE (r)-[:RELATION_TARGET]->(target)
                        """,
                        {"relation_id": relation_id, "target_id": target_id},
                    )
                    self._execute_internal(
                        """
                        MATCH (source:Entity {id: $source_id}), (target:Entity {id: $target_id})
                        MERGE (source)-[rel:RELATES]->(target)
                        ON CREATE SET rel.relation_id = $relation_id, rel.relation_type = $relation_type
                        ON MATCH SET rel.relation_type = $relation_type
                        """,
                        {
                            "source_id": source_id,
                            "target_id": target_id,
                            "relation_id": relation_id,
                            "relation_type": relation_type,
                        },
                    )

    def health_check(self):
        result = self._execute("RETURN 1 AS ok")
        return list(result)[0][0] == 1

    def list_relation_types(self, kb_id: str, file_id: str | None = None) -> list[str]:
        rows = self._execute_dict(
            """
            MATCH (c:Chunk {kb_id: $kb_id})-[:HAS_RELATION]->(r:Relation)
            WHERE $file_id = '' OR c.file_id = $file_id
            RETURN DISTINCT r.relation_type AS relation_type
            ORDER BY relation_type
            """,
            {"kb_id": kb_id, "file_id": file_id or ""},
        )
        return [row["relation_type"] for row in rows if row.get("relation_type")]

    def fetch_graph_view(
        self,
        kb_id: str,
        file_id: str | None = None,
        entity_query: str | None = None,
        relation_type: str | None = None,
    ) -> dict:
        file_filter = file_id or ""
        entity_filter = (entity_query or "").strip().lower()
        relation_filter = (relation_type or "").strip().upper()

        documents = self._execute_dict(
            """
            MATCH (d:Document)
            WHERE d.kb_id = $kb_id AND ($file_id = '' OR d.id = $file_id)
            RETURN d.id AS file_id, d.name AS file_name, d.path AS file_path
            ORDER BY d.name
            """,
            {"kb_id": kb_id, "file_id": file_filter},
        )
        chunks = self._execute_dict(
            """
            MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
            WHERE d.kb_id = $kb_id AND ($file_id = '' OR d.id = $file_id)
            RETURN d.id AS file_id, d.name AS file_name, c.id AS chunk_id, c.chunk_index AS chunk_index, c.content AS content
            ORDER BY d.name, c.chunk_index
            """,
            {"kb_id": kb_id, "file_id": file_filter},
        )
        mentions = self._execute_dict(
            """
            MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
            WHERE c.kb_id = $kb_id AND ($file_id = '' OR c.file_id = $file_id)
            RETURN c.id AS chunk_id, e.id AS entity_id, e.name AS entity_name, e.entity_type AS entity_type, e.description AS entity_description
            ORDER BY c.chunk_index, e.name
            """,
            {"kb_id": kb_id, "file_id": file_filter},
        )
        relation_rows = self._execute_dict(
            """
            MATCH (c:Chunk)-[:HAS_RELATION]->(r:Relation)-[:RELATION_SOURCE]->(s:Entity)
            MATCH (r)-[:RELATION_TARGET]->(t:Entity)
            WHERE c.kb_id = $kb_id
              AND ($file_id = '' OR c.file_id = $file_id)
              AND ($relation_type = '' OR r.relation_type = $relation_type)
            RETURN c.id AS chunk_id,
                   r.id AS relation_id,
                   r.relation_type AS relation_type,
                   r.description AS relation_description,
                   s.id AS source_entity_id,
                   s.name AS source_name,
                   s.entity_type AS source_type,
                   t.id AS target_entity_id,
                   t.name AS target_name,
                   t.entity_type AS target_type
            ORDER BY c.chunk_index, r.relation_type
            """,
            {
                "kb_id": kb_id,
                "file_id": file_filter,
                "relation_type": relation_filter,
            },
        )

        filtered_entity_ids = set()
        if entity_filter:
            for row in mentions:
                if entity_filter in str(row.get("entity_name", "")).lower():
                    filtered_entity_ids.add(row["entity_id"])
            for row in relation_rows:
                if entity_filter in str(row.get("source_name", "")).lower():
                    filtered_entity_ids.add(row["source_entity_id"])
                if entity_filter in str(row.get("target_name", "")).lower():
                    filtered_entity_ids.add(row["target_entity_id"])

        chunk_map: dict[str, dict] = {}
        for row in chunks:
            chunk_map[row["chunk_id"]] = {
                "chunk_id": row["chunk_id"],
                "chunk_index": row["chunk_index"],
                "file_id": row["file_id"],
                "file_name": row["file_name"],
                "content_preview": (row.get("content") or "")[:240],
                "content_full": row.get("content") or "",
                "entities": [],
                "relations": [],
            }

        entity_by_chunk: dict[str, list[dict]] = {}
        for row in mentions:
            if entity_filter and row["entity_id"] not in filtered_entity_ids:
                continue
            entity = {
                "entity_id": row["entity_id"],
                "name": row["entity_name"],
                "entity_type": row["entity_type"],
                "description": row.get("entity_description") or "",
            }
            entity_by_chunk.setdefault(row["chunk_id"], []).append(entity)

        relation_by_chunk: dict[str, list[dict]] = {}
        for row in relation_rows:
            if entity_filter and (
                row["source_entity_id"] not in filtered_entity_ids
                and row["target_entity_id"] not in filtered_entity_ids
            ):
                continue
            relation = {
                "relation_id": row["relation_id"],
                "relation_type": row["relation_type"],
                "description": row.get("relation_description") or "",
                "source_entity_id": row["source_entity_id"],
                "source_name": row["source_name"],
                "source_type": row["source_type"],
                "target_entity_id": row["target_entity_id"],
                "target_name": row["target_name"],
                "target_type": row["target_type"],
            }
            relation_by_chunk.setdefault(row["chunk_id"], []).append(relation)

        chunk_records = []
        document_ids = set()
        chunk_ids = set()
        entity_ids = set()
        relation_ids = set()
        filtered_result_count = 0

        for chunk_id, chunk in chunk_map.items():
            chunk["entities"] = entity_by_chunk.get(chunk_id, [])
            chunk["relations"] = relation_by_chunk.get(chunk_id, [])
            chunk["entity_count"] = len(chunk["entities"])
            chunk["relation_count"] = len(chunk["relations"])
            if entity_filter or relation_filter:
                if not chunk["entities"] and not chunk["relations"]:
                    continue
            elif not chunk["entities"] and not chunk["relations"]:
                continue

            document_ids.add(chunk["file_id"])
            chunk_ids.add(chunk["chunk_id"])
            entity_ids.update(entity["entity_id"] for entity in chunk["entities"])
            relation_ids.update(relation["relation_id"] for relation in chunk["relations"])
            filtered_result_count += chunk["entity_count"] + chunk["relation_count"]
            chunk_records.append(chunk)

        entity_type_palette = {
            "PERSON": "#ef4444",
            "ORG": "#3b82f6",
            "PROJECT": "#8b5cf6",
            "PRODUCT": "#f59e0b",
            "TECHNOLOGY": "#10b981",
            "LOCATION": "#06b6d4",
            "DATE": "#64748b",
            "EVENT": "#ec4899",
            "CONCEPT": "#84cc16",
            "UNKNOWN": "#6b7280",
        }

        nodes: list[dict] = []
        edges: list[dict] = []
        seen_nodes = set()
        seen_edges = set()

        for document in documents:
            if file_filter and document["file_id"] not in document_ids and (entity_filter or relation_filter):
                continue
            node_id = f"document:{document['file_id']}"
            if node_id not in seen_nodes:
                seen_nodes.add(node_id)
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "document",
                        "label": document["file_name"],
                        "meta": {
                            "file_id": document["file_id"],
                            "file_path": document.get("file_path") or "",
                        },
                    }
                )

        for chunk in chunk_records:
            chunk_node_id = f"chunk:{chunk['chunk_id']}"
            if chunk_node_id not in seen_nodes:
                seen_nodes.add(chunk_node_id)
                nodes.append(
                    {
                        "id": chunk_node_id,
                        "kind": "chunk",
                        "label": f"Chunk {chunk['chunk_index']}",
                        "meta": {
                            "chunk_id": chunk["chunk_id"],
                            "file_id": chunk["file_id"],
                            "file_name": chunk["file_name"],
                            "content_preview": chunk["content_preview"],
                        },
                    }
                )
            edge_id = f"document:{chunk['file_id']}->chunk:{chunk['chunk_id']}"
            if edge_id not in seen_edges:
                seen_edges.add(edge_id)
                edges.append(
                    {
                        "id": edge_id,
                        "source": f"document:{chunk['file_id']}",
                        "target": chunk_node_id,
                        "kind": "has_chunk",
                        "label": "HAS_CHUNK",
                    }
                )

            for entity in chunk["entities"]:
                entity_node_id = f"entity:{entity['entity_id']}"
                if entity_node_id not in seen_nodes:
                    seen_nodes.add(entity_node_id)
                    entity_type = (entity["entity_type"] or "UNKNOWN").upper()
                    nodes.append(
                        {
                            "id": entity_node_id,
                            "kind": "entity",
                            "label": entity["name"],
                            "meta": {
                                "entity_id": entity["entity_id"],
                                "entity_type": entity["entity_type"],
                                "description": entity["description"],
                                "color": entity_type_palette.get(entity_type, entity_type_palette["UNKNOWN"]),
                            },
                        }
                    )
                mention_edge = f"chunk:{chunk['chunk_id']}->entity:{entity['entity_id']}"
                if mention_edge not in seen_edges:
                    seen_edges.add(mention_edge)
                    edges.append(
                        {
                            "id": mention_edge,
                            "source": chunk_node_id,
                            "target": entity_node_id,
                            "kind": "mentions",
                            "label": "MENTIONS",
                        }
                    )

            for relation in chunk["relations"]:
                relation_node_id = f"relation:{relation['relation_id']}"
                if relation_node_id not in seen_nodes:
                    seen_nodes.add(relation_node_id)
                    nodes.append(
                        {
                            "id": relation_node_id,
                            "kind": "relation",
                            "label": relation["relation_type"],
                            "meta": {
                                "relation_id": relation["relation_id"],
                                "relation_type": relation["relation_type"],
                                "description": relation["description"],
                            },
                        }
                    )
                relation_edge = f"chunk:{chunk['chunk_id']}->relation:{relation['relation_id']}"
                if relation_edge not in seen_edges:
                    seen_edges.add(relation_edge)
                    edges.append(
                        {
                            "id": relation_edge,
                            "source": chunk_node_id,
                            "target": relation_node_id,
                            "kind": "has_relation",
                            "label": "HAS_RELATION",
                        }
                    )

                source_link = f"relation:{relation['relation_id']}->entity:{relation['source_entity_id']}:source"
                if source_link not in seen_edges:
                    seen_edges.add(source_link)
                    edges.append(
                        {
                            "id": source_link,
                            "source": relation_node_id,
                            "target": f"entity:{relation['source_entity_id']}",
                            "kind": "relation_source",
                            "label": "SOURCE",
                        }
                    )
                target_link = f"relation:{relation['relation_id']}->entity:{relation['target_entity_id']}:target"
                if target_link not in seen_edges:
                    seen_edges.add(target_link)
                    edges.append(
                        {
                            "id": target_link,
                            "source": relation_node_id,
                            "target": f"entity:{relation['target_entity_id']}",
                            "kind": "relation_target",
                            "label": "TARGET",
                        }
                    )

        return {
            "summary": {
                "provider": self.provider_name,
                "entity_total": len(entity_ids),
                "relation_total": len(relation_ids),
                "file_count": len(document_ids) if (entity_filter or relation_filter) else len(documents),
                "chunk_count": len(chunk_ids) if (entity_filter or relation_filter) else len(chunks),
                "filtered_result_count": filtered_result_count,
            },
            "graph": {
                "nodes": nodes,
                "edges": edges,
            },
            "records": chunk_records,
        }

    # ===== 实体/关系实例级别同步 =====

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
        """以 SQLite entity.id 作为 Kùzu Entity.id 进行 upsert。

        与抽取流程的 hash-based id 共存：手动管理的实体使用 SQLite id，
        抽取流程产出的实体在阶段二B改造后也使用 SQLite id。
        """
        with _kuzu_write_lock:
            self._execute_internal(
                """
                MERGE (e:Entity {id: $entity_id})
                ON CREATE SET e.kb_id = $kb_id, e.name = $name,
                              e.entity_type = $entity_type, e.description = $description,
                              e.ontology_id = $ontology_id, e.properties = $properties
                ON MATCH SET e.kb_id = $kb_id, e.name = $name,
                             e.entity_type = $entity_type, e.description = $description,
                             e.ontology_id = $ontology_id, e.properties = $properties
                """,
                {
                    "entity_id": entity_id,
                    "kb_id": kb_id,
                    "name": _normalize_text(name),
                    "entity_type": _normalize_text(entity_type, "UNKNOWN"),
                    "description": _normalize_text(description),
                    "ontology_id": ontology_id or "",
                    "properties": properties or "",
                },
            )

    def delete_entity(self, entity_id: str):
        """删除 Kùzu Entity 节点（DETACH DELETE 一并清除入边出边）。

        同时删除该实体参与的所有 Relation 节点（作为 source 或 target），
        保持与 SQLite 级联语义一致（删除实体时其相关关系实例也会被清理）。
        """
        with _kuzu_write_lock:
            # 先删除该实体参与的 Relation 节点（防止孤儿关系节点）
            self._execute_internal(
                """
                MATCH (r:Relation)-[:RELATION_SOURCE]->(e:Entity {id: $entity_id})
                DETACH DELETE r
                """,
                {"entity_id": entity_id},
            )
            self._execute_internal(
                """
                MATCH (r:Relation)-[:RELATION_TARGET]->(e:Entity {id: $entity_id})
                DETACH DELETE r
                """,
                {"entity_id": entity_id},
            )
            self._execute_internal(
                """
                MATCH (e:Entity {id: $entity_id})
                DETACH DELETE e
                """,
                {"entity_id": entity_id},
            )

    def upsert_relation(
        self,
        relation_id: str,
        kb_id: str,
        relation_type: str,
        description: str,
        source_entity_id: str,
        target_entity_id: str,
    ):
        """以 SQLite relation.id 作为 Kùzu Relation.id 进行 upsert，并重建起终点连边。"""
        with _kuzu_write_lock:
            self._execute_internal(
                """
                MERGE (r:Relation {id: $relation_id})
                ON CREATE SET r.kb_id = $kb_id, r.relation_type = $relation_type,
                              r.description = $description,
                              r.source_entity_id = $source_entity_id,
                              r.target_entity_id = $target_entity_id
                ON MATCH SET r.kb_id = $kb_id, r.relation_type = $relation_type,
                             r.description = $description,
                             r.source_entity_id = $source_entity_id,
                             r.target_entity_id = $target_entity_id
                """,
                {
                    "relation_id": relation_id,
                    "kb_id": kb_id,
                    "relation_type": _normalize_text(relation_type, "RELATED_TO"),
                    "description": _normalize_text(description),
                    "source_entity_id": source_entity_id,
                    "target_entity_id": target_entity_id,
                },
            )
            # 重建起终点连边（先删后建，避免遗留旧连边）
            self._execute_internal(
                """
                MATCH (r:Relation {id: $relation_id})
                OPTIONAL MATCH (r)-[old_src:RELATION_SOURCE]->()
                DELETE old_src
                """,
                {"relation_id": relation_id},
            )
            self._execute_internal(
                """
                MATCH (r:Relation {id: $relation_id})
                OPTIONAL MATCH (r)-[old_tgt:RELATION_TARGET]->()
                DELETE old_tgt
                """,
                {"relation_id": relation_id},
            )
            self._execute_internal(
                """
                MATCH (r:Relation {id: $relation_id}), (s:Entity {id: $source_entity_id})
                MERGE (r)-[:RELATION_SOURCE]->(s)
                """,
                {"relation_id": relation_id, "source_entity_id": source_entity_id},
            )
            self._execute_internal(
                """
                MATCH (r:Relation {id: $relation_id}), (t:Entity {id: $target_entity_id})
                MERGE (r)-[:RELATION_TARGET]->(t)
                """,
                {"relation_id": relation_id, "target_entity_id": target_entity_id},
            )
            # 直接 RELATES 边（用于图遍历）
            self._execute_internal(
                """
                MATCH (s:Entity {id: $source_entity_id}), (t:Entity {id: $target_entity_id})
                MERGE (s)-[rel:RELATES]->(t)
                ON CREATE SET rel.relation_id = $relation_id, rel.relation_type = $relation_type
                ON MATCH SET rel.relation_id = $relation_id, rel.relation_type = $relation_type
                """,
                {
                    "source_entity_id": source_entity_id,
                    "target_entity_id": target_entity_id,
                    "relation_id": relation_id,
                    "relation_type": _normalize_text(relation_type, "RELATED_TO"),
                },
            )

    def delete_relation(self, relation_id: str):
        """删除 Kùzu Relation 节点及其 RELATES 直连边。"""
        with _kuzu_write_lock:
            # 删除对应的 RELATES 直连边（按 relation_id 过滤）
            self._execute_internal(
                """
                MATCH (s:Entity)-[rel:RELATES {relation_id: $relation_id}]->(t:Entity)
                DELETE rel
                """,
                {"relation_id": relation_id},
            )
            self._execute_internal(
                """
                MATCH (r:Relation {id: $relation_id})
                DETACH DELETE r
                """,
                {"relation_id": relation_id},
            )


class Neo4jGraphAdapter(GraphStoreAdapter):
    provider_name = "neo4j"

    def __init__(self):
        self._driver = None

    def _driver_instance(self):
        from neo4j import GraphDatabase

        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            )
        return self._driver

    def _execute(self, query: str, parameters: dict | None = None):
        driver = self._driver_instance()
        return driver.execute_query(
            query,
            parameters_=parameters or {},
            database_=settings.NEO4J_DATABASE,
        )

    def _execute_dict(self, query: str, parameters: dict | None = None) -> list[dict]:
        result = self._execute(query, parameters)
        return [record.data() for record in result.records]

    def ensure_schema(self):
        statements = [
            "CREATE CONSTRAINT kb_id IF NOT EXISTS FOR (n:KnowledgeBase) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT relation_id IF NOT EXISTS FOR (n:Relation) REQUIRE n.id IS UNIQUE",
        ]
        for statement in statements:
            self._execute(statement)

    def delete_document_graph(self, file_id: str):
        self._execute(
            """
            MATCH (d:Document {id: $file_id})-[:HAS_CHUNK]->(:Chunk)-[:HAS_RELATION]->(r:Relation)
            DETACH DELETE r
            """,
            {"file_id": file_id},
        )
        self._execute(
            """
            MATCH (d:Document {id: $file_id})
            DETACH DELETE d
            """,
            {"file_id": file_id},
        )

    def delete_kb_graph(self, kb_id: str):
        self._execute(
            """
            MATCH (n)
            WHERE n.kb_id = $kb_id OR n.id = $kb_id
            DETACH DELETE n
            """,
            {"kb_id": kb_id},
        )

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
        if clear_existing:
            self.delete_document_graph(file_id)
        self._execute(
            """
            MERGE (kb:KnowledgeBase {id: $kb_id})
            SET kb.name = $kb_name
            """,
            {"kb_id": kb_id, "kb_name": kb_name},
        )
        self._execute(
            """
            MERGE (d:Document {id: $file_id})
            SET d.kb_id = $kb_id, d.name = $file_name, d.path = $file_path
            WITH d
            MATCH (kb:KnowledgeBase {id: $kb_id})
            MERGE (kb)-[:HAS_DOCUMENT]->(d)
            """,
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "file_name": file_name,
                "file_path": file_path,
            },
        )

        previous_chunk_id = None
        for chunk in chunks:
            self._execute(
                """
                MERGE (c:Chunk {id: $chunk_id})
                SET c.file_id = $file_id, c.kb_id = $kb_id, c.chunk_index = $chunk_index, c.content = $content
                WITH c
                MATCH (d:Document {id: $file_id})
                MERGE (d)-[:HAS_CHUNK]->(c)
                """,
                {
                    "chunk_id": chunk.chunk_id,
                    "file_id": file_id,
                    "kb_id": kb_id,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                },
            )

            if previous_chunk_id is not None:
                self._execute(
                    """
                    MATCH (prev:Chunk {id: $prev_chunk_id}), (curr:Chunk {id: $chunk_id})
                    MERGE (prev)-[r:NEXT_CHUNK]->(curr)
                    SET r.order_index = $chunk_index
                    """,
                    {
                        "prev_chunk_id": previous_chunk_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_index": chunk.chunk_index,
                    },
                )
            previous_chunk_id = chunk.chunk_id

            for entity in chunk.entities:
                entity_name = _normalize_text(entity.name)
                entity_type = _normalize_text(entity.entity_type, "UNKNOWN")
                entity_id = entity.id or _entity_id(kb_id, entity_name, entity_type)
                self._execute(
                    """
                    MERGE (e:Entity {id: $entity_id})
                    SET e.kb_id = $kb_id, e.name = $entity_name, e.entity_type = $entity_type
                    SET e.description = CASE WHEN coalesce(e.description, '') = '' THEN $description ELSE e.description END
                    SET e.ontology_id = $ontology_id, e.properties = $properties
                    WITH e
                    MATCH (c:Chunk {id: $chunk_id})
                    MERGE (c)-[:MENTIONS]->(e)
                    """,
                    {
                        "entity_id": entity_id,
                        "kb_id": kb_id,
                        "entity_name": entity_name,
                        "entity_type": entity_type,
                        "description": _normalize_text(entity.description),
                        "ontology_id": entity.ontology_id or "",
                        "properties": entity.properties or "",
                        "chunk_id": chunk.chunk_id,
                    },
                )

            for relation in chunk.relations:
                source_name = _normalize_text(relation.source_name)
                source_type = _normalize_text(relation.source_type, "UNKNOWN")
                target_name = _normalize_text(relation.target_name)
                target_type = _normalize_text(relation.target_type, "UNKNOWN")
                relation_type = _normalize_text(relation.relation_type, "RELATED_TO")
                source_id = relation.source_entity_id or _entity_id(kb_id, source_name, source_type)
                target_id = relation.target_entity_id or _entity_id(kb_id, target_name, target_type)
                relation_id = relation.id or _relation_id(
                    kb_id,
                    source_name,
                    source_type,
                    target_name,
                    target_type,
                    relation_type,
                )
                self._execute(
                    """
                    MERGE (source:Entity {id: $source_id})
                    SET source.kb_id = $kb_id, source.name = $source_name, source.entity_type = $source_type
                    MERGE (target:Entity {id: $target_id})
                    SET target.kb_id = $kb_id, target.name = $target_name, target.entity_type = $target_type
                    MERGE (relNode:Relation {id: $relation_id})
                    SET relNode.kb_id = $kb_id, relNode.relation_type = $relation_type
                    SET relNode.description = CASE WHEN coalesce(relNode.description, '') = '' THEN $description ELSE relNode.description END
                    SET relNode.relation_def_id = $relation_def_id,
                        relNode.source_entity_id = $source_entity_id,
                        relNode.target_entity_id = $target_entity_id
                    WITH source, target, relNode
                    MATCH (c:Chunk {id: $chunk_id})
                    MERGE (c)-[:HAS_RELATION]->(relNode)
                    MERGE (relNode)-[:RELATION_SOURCE]->(source)
                    MERGE (relNode)-[:RELATION_TARGET]->(target)
                    MERGE (source)-[direct:RELATES]->(target)
                    SET direct.relation_id = $relation_id, direct.relation_type = $relation_type
                    """,
                    {
                        "source_id": source_id,
                        "source_name": source_name,
                        "source_type": source_type,
                        "target_id": target_id,
                        "target_name": target_name,
                        "target_type": target_type,
                        "relation_id": relation_id,
                        "relation_type": relation_type,
                        "description": _normalize_text(relation.description),
                        "relation_def_id": relation.relation_def_id or "",
                        "source_entity_id": source_id,
                        "target_entity_id": target_id,
                        "chunk_id": chunk.chunk_id,
                        "kb_id": kb_id,
                    },
                )

    def health_check(self):
        driver = self._driver_instance()
        driver.verify_connectivity()
        return True

    def list_relation_types(self, kb_id: str, file_id: str | None = None) -> list[str]:
        rows = self._execute_dict(
            """
            MATCH (c:Chunk {kb_id: $kb_id})-[:HAS_RELATION]->(r:Relation)
            WHERE $file_id = '' OR c.file_id = $file_id
            RETURN DISTINCT r.relation_type AS relation_type
            ORDER BY relation_type
            """,
            {"kb_id": kb_id, "file_id": file_id or ""},
        )
        return [row["relation_type"] for row in rows if row.get("relation_type")]

    def fetch_graph_view(
        self,
        kb_id: str,
        file_id: str | None = None,
        entity_query: str | None = None,
        relation_type: str | None = None,
    ) -> dict:
        return KuzuGraphAdapter.fetch_graph_view(self, kb_id, file_id, entity_query, relation_type)

    # ===== OAG 只读检索（Neo4j，Cypher 与 Kùzu 兼容，委托复用）=====

    def list_kb_entities(self, kb_id: str, limit: int = 5000) -> list[dict]:
        return KuzuGraphAdapter.list_kb_entities(self, kb_id, limit)

    def entities_mentioned_by_chunks(self, kb_id: str, chunk_ids: list[str]) -> list[dict]:
        return KuzuGraphAdapter.entities_mentioned_by_chunks(self, kb_id, chunk_ids)

    def chunks_mentioning_entities(self, kb_id: str, entity_ids: list[str], limit: int = 12) -> list[dict]:
        return KuzuGraphAdapter.chunks_mentioning_entities(self, kb_id, entity_ids, limit)

    def entity_neighborhood(
        self, kb_id: str, entity_ids: list[str], hops: int = 1, limit: int = 40,
    ) -> dict:
        return KuzuGraphAdapter.entity_neighborhood(self, kb_id, entity_ids, hops, limit)

    # ===== 实体/关系实例级别同步（Neo4j 实现）=====

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
        self._execute(
            """
            MERGE (e:Entity {id: $entity_id})
            SET e.kb_id = $kb_id, e.name = $name, e.entity_type = $entity_type,
                e.description = $description, e.ontology_id = $ontology_id,
                e.properties = $properties
            """,
            {
                "entity_id": entity_id,
                "kb_id": kb_id,
                "name": _normalize_text(name),
                "entity_type": _normalize_text(entity_type, "UNKNOWN"),
                "description": _normalize_text(description),
                "ontology_id": ontology_id or "",
                "properties": properties or "",
            },
        )

    def delete_entity(self, entity_id: str):
        self._execute(
            """
            MATCH (r:Relation)-[:RELATION_SOURCE|RELATION_TARGET]->(e:Entity {id: $entity_id})
            DETACH DELETE r
            """,
            {"entity_id": entity_id},
        )
        self._execute(
            """
            MATCH (e:Entity {id: $entity_id})
            DETACH DELETE e
            """,
            {"entity_id": entity_id},
        )

    def upsert_relation(
        self,
        relation_id: str,
        kb_id: str,
        relation_type: str,
        description: str,
        source_entity_id: str,
        target_entity_id: str,
    ):
        self._execute(
            """
            MERGE (r:Relation {id: $relation_id})
            SET r.kb_id = $kb_id, r.relation_type = $relation_type, r.description = $description,
                r.source_entity_id = $source_entity_id, r.target_entity_id = $target_entity_id
            WITH r
            MATCH (s:Entity {id: $source_entity_id}), (t:Entity {id: $target_entity_id})
            MERGE (r)-[:RELATION_SOURCE]->(s)
            MERGE (r)-[:RELATION_TARGET]->(t)
            MERGE (s)-[rel:RELATES]->(t)
            SET rel.relation_id = $relation_id, rel.relation_type = $relation_type
            """,
            {
                "relation_id": relation_id,
                "kb_id": kb_id,
                "relation_type": _normalize_text(relation_type, "RELATED_TO"),
                "description": _normalize_text(description),
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
            },
        )

    def delete_relation(self, relation_id: str):
        self._execute(
            """
            MATCH (s:Entity)-[rel:RELATES {relation_id: $relation_id}]->(t:Entity)
            DELETE rel
            """,
            {"relation_id": relation_id},
        )
        self._execute(
            """
            MATCH (r:Relation {id: $relation_id})
            DETACH DELETE r
            """,
            {"relation_id": relation_id},
        )


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
):
    _get_adapter().upsert_entity(
        entity_id, kb_id, ontology_id, entity_type, name, description, properties
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
