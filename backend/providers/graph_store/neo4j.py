"""Neo4j 图存储 adapter。

通过 lazy import 避免在未安装 neo4j 驱动时拖累整个包导入；
OAG 只读检索与图可视化查询复用 Kùzu 实现（Cypher 兼容）。
"""

from __future__ import annotations

from config import settings

from .base import (
    GraphStoreAdapter,
    ChunkGraphData,
    _normalize_text,
    _entity_id,
    _relation_id,
)
from .kuzu import KuzuGraphAdapter


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
            ("kb_id", "CREATE CONSTRAINT kb_id IF NOT EXISTS FOR (n:KnowledgeBase) REQUIRE n.id IS UNIQUE"),
            ("document_id", "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE"),
            ("chunk_id", "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE"),
            ("entity_id", "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE"),
            ("relation_id", "CREATE CONSTRAINT relation_id IF NOT EXISTS FOR (n:Relation) REQUIRE n.id IS UNIQUE"),
        ]
        # 已存在的约束直接跳过：IF NOT EXISTS 虽幂等，但服务端仍会返回
        # IndexOrConstraintAlreadyExists 通知，每次启动刷一屏
        existing = self._existing_constraint_names()
        for name, statement in statements:
            if name in existing:
                continue
            self._execute(statement)

    def _existing_constraint_names(self) -> set[str]:
        """当前库已有约束名；查询失败时返回空集（回退为逐条执行建约束语句）。"""
        try:
            rows = self._execute_dict("SHOW CONSTRAINTS YIELD name")
        except Exception:
            return set()
        return {row.get("name") for row in rows if row.get("name")}

    def delete_document_graph(self, file_id: str):
        # 与 Kùzu 一致：只清本文件分片与文档节点，保留 KB 级共享的实体/关系节点
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

    def describe_target(self) -> str:
        return settings.NEO4J_URI

    def socket_target(self) -> tuple[str, int] | None:
        return _parse_uri_host_port(settings.NEO4J_URI, default_port=7687)

    def troubleshooting_hint(self) -> str:
        return "docker-compose up -d neo4j（管理界面 http://localhost:7474）"

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
        label: str = "",
        category_id: str = "",
    ):
        """实例级 upsert：节点形态与 graph_sync_service._import_category 对齐。

        label（本体编码）/category_id 由调用方从权威库解析后传入：
        - label 非空时补打第二标签（``SET e:`编码```），类别维度查询立即可见；
        - category_id 非空时才覆盖，避免把迁入写好的归属清空。
        """
        set_category = ", e.category_id = $category_id" if (category_id or "").strip() else ""
        # 只有知识库抽取的实体才写 kb_id；kb_id 为空（手动创建）时 REMOVE，
        # 顺带清掉历史误写的 kb_id，保持图与权威库一致
        if (kb_id or "").strip():
            set_kb = ", e.kb_id = $kb_id"
            remove_kb = ""
        else:
            set_kb = ""
            remove_kb = " REMOVE e.kb_id"
        ident = (label or "").strip().replace("`", "")
        extra_label = f" SET e:`{ident}`" if ident else ""
        self._execute(
            f"""
            MERGE (e:Entity {{id: $entity_id}})
            SET e.name = $name, e.entity_type = $entity_type,
                e.description = $description, e.ontology_id = $ontology_id,
                e.properties = $properties{set_kb}{set_category}{extra_label}{remove_kb}
            """,
            {
                "entity_id": entity_id,
                "kb_id": kb_id,
                "name": _normalize_text(name),
                "entity_type": _normalize_text(entity_type, "UNKNOWN"),
                "description": _normalize_text(description),
                "ontology_id": ontology_id or "",
                "properties": properties or "",
                "category_id": category_id or "",
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
