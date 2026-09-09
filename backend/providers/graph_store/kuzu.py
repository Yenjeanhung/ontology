"""Kùzu 嵌入式图存储 adapter。

通过 lazy import 避免在未安装 kuzu 驱动时拖累整个包导入；具体图查询
实现见 GraphStoreAdapter 基类中的统一 OAG 只读检索逻辑。
"""

from __future__ import annotations

import threading
from typing import Any
from pathlib import Path

from config import settings

from .base import (
    GraphStoreAdapter,
    ChunkGraphData,
    _normalize_text,
    _entity_id,
    _relation_id,
)

# Kùzu 同一时刻只允许一个写事务，全局锁保证线程安全
_kuzu_write_lock = threading.Lock()


class KuzuGraphAdapter(GraphStoreAdapter):
    provider_name: str = "kuzu"

    def __init__(self):
        self._db: Any | None = None

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
        # 只清掉本文件的分片(Chunk)与文档(Document)节点。
        # 实体(Entity)/关系(Relation)节点是 KB 级共享的（id 不含 file_id），
        # 删除它们会误伤其它文件抽取到的同名实体/同三元组关系，以及手动维护的实例。
        # 分片用 DETACH DELETE，顺带带走本文件 Chunk 的 MENTIONS / HAS_RELATION 边；
        # 仅本文件独有的 Relation 会因此失去 HAS_RELATION 而在图谱视图中不可见
        # （fetch_graph_view 经 HAS_RELATION 检索），重新抽取时按同一 id MERGE 自动复联。
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

    def describe_target(self) -> str:
        return settings.KUZU_DB_PATH

    def troubleshooting_hint(self) -> str:
        return "检查 KUZU_DB_PATH 所在目录是否可读写"

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

        # 手动维护实体（未被任何 Chunk MENTIONS，例如「实体管理」页新增/修改的实体）。
        # 实体服务在新增/修改时已实时 upsert 到图库，这里把它们也纳入 KB 视图，
        # 作为「手动维护实体」分组展示，保证图库与实体管理数据一致。
        if not relation_filter:
            mentioned_ids = {row["entity_id"] for row in mentions}
            standalone_rows: list[dict] = []
            for row in self.list_kb_entities(kb_id, limit=5000):
                entity_id = row.get("entity_id") or ""
                if not entity_id or entity_id in mentioned_ids:
                    continue
                if entity_filter and entity_filter not in str(row.get("name", "")).lower():
                    continue
                standalone_rows.append(
                    {
                        "entity_id": entity_id,
                        "name": row.get("name") or entity_id,
                        "entity_type": row.get("entity_type") or "UNKNOWN",
                        "description": row.get("description") or "",
                    }
                )
            if standalone_rows:
                for entity in standalone_rows:
                    entity_node_id = f"entity:{entity['entity_id']}"
                    if entity_node_id in seen_nodes:
                        continue
                    seen_nodes.add(entity_node_id)
                    entity_type = (entity["entity_type"] or "UNKNOWN").upper()
                    entity_ids.add(entity["entity_id"])
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
                                "manual": True,
                            },
                        }
                    )
                filtered_result_count += len(standalone_rows)
                chunk_records.append(
                    {
                        "chunk_id": "__manual_entities__",
                        "chunk_index": -1,
                        "file_id": "__manual__",
                        "file_name": "手动维护实体",
                        "content_preview": "",
                        "content_full": "",
                        "entities": standalone_rows,
                        "relations": [],
                        "entity_count": len(standalone_rows),
                        "relation_count": 0,
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
        label: str = "",
        category_id: str = "",
    ):
        """以 SQLite entity.id 作为 Kùzu Entity.id 进行 upsert。

        与抽取流程的 hash-based id 共存：手动管理的实体使用 SQLite id，
        抽取流程产出的实体在阶段二B改造后也使用 SQLite id。

        label/category_id 参数仅与 Neo4j 适配器签名对齐：Kùzu 的实体统一
        存 :Entity 表（label 即表名，无第二标签能力），此处忽略。
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
