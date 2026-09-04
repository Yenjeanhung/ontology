from __future__ import annotations

import asyncio

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, KnowledgeBase, Ontology, Relation
from providers.graph_store import (
    fetch_graph_view,
    get_graph_store_provider_name,
    list_graph_relation_types,
)


class GraphDataService:
    @staticmethod
    async def list_relation_types(kb_id: str, file_id: str | None = None) -> dict:
        relation_types = await asyncio.to_thread(list_graph_relation_types, kb_id, file_id)
        return {
            "provider": get_graph_store_provider_name(),
            "kb_id": kb_id,
            "file_id": file_id,
            "items": relation_types,
        }

    @staticmethod
    async def get_view(
        db: AsyncSession,
        kb_id: str,
        file_id: str | None = None,
        entity_query: str | None = None,
        relation_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        kb = (
            await db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
        ).scalar_one_or_none()
        if not kb:
            raise ValueError("Knowledge base not found")

        data = await asyncio.to_thread(
            fetch_graph_view,
            kb_id,
            file_id,
            entity_query,
            relation_type,
        )

        # Sort entities by degree (relation count) desc, apply limit/offset
        records = data.get("records", [])
        if records:
            entity_degree = {}
            for rec in records:
                for rel in rec.get("relations", []):
                    sid = rel.get("source_entity_id", "")
                    tid = rel.get("target_entity_id", "")
                    if sid:
                        entity_degree[sid] = entity_degree.get(sid, 0) + 1
                    if tid:
                        entity_degree[tid] = entity_degree.get(tid, 0) + 1

            # Sort unique entity IDs by degree desc
            sorted_entities = sorted(entity_degree.keys(), key=lambda eid: entity_degree[eid], reverse=True)

            total_entities = len(sorted_entities)
            selected_entities = set(sorted_entities[offset:offset + limit])

            # Filter records: only keep selected entities and relations between them
            filtered_records = []
            shown_entity_ids = set()
            for rec in records:
                filtered_entities = [
                    e for e in rec.get("entities", [])
                    if e.get("entity_id", "") in selected_entities
                ]
                filtered_relations = [
                    r for r in rec.get("relations", [])
                    if r.get("source_entity_id", "") in selected_entities
                    and r.get("target_entity_id", "") in selected_entities
                ]
                if filtered_entities or filtered_relations:
                    for e in filtered_entities:
                        shown_entity_ids.add(e.get("entity_id", ""))
                    rec_copy = {
                        **rec,
                        "entities": filtered_entities,
                        "relations": filtered_relations,
                        "entity_count": len(filtered_entities),
                        "relation_count": len(filtered_relations),
                    }
                    filtered_records.append(rec_copy)

            data["records"] = filtered_records
            data["summary"]["entity_total"] = total_entities
            data["summary"]["entity_shown"] = len(shown_entity_ids)
            data["summary"]["has_more"] = offset + limit < total_entities
            data["summary"]["limit"] = limit
            data["summary"]["offset"] = offset

            # Rebuild nodes/edges for filtered records only
            data["nodes"] = GraphDataService._build_filtered_nodes(filtered_records, data.get("nodes", []))
            data["edges"] = GraphDataService._build_filtered_edges(filtered_records, data.get("edges", []))

        data["provider"] = get_graph_store_provider_name()
        data["kb"] = {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
        }
        data["filters"] = {
            "kb_id": kb_id,
            "file_id": file_id,
            "entity_query": entity_query or "",
            "relation_type": relation_type or "",
        }
        return data

    @staticmethod
    def _build_filtered_nodes(records: list[dict], all_nodes: list[dict]) -> list[dict]:
        keep_chunk_ids = {rec["chunk_id"] for rec in records}
        keep_entity_ids = set()
        keep_relation_ids = set()
        keep_file_ids = {rec.get("file_id", "") for rec in records}

        for rec in records:
            for ent in rec.get("entities", []):
                keep_entity_ids.add(ent.get("entity_id", ""))
            for rel in rec.get("relations", []):
                keep_relation_ids.add(rel.get("relation_id", ""))

        keep = set()
        for node in all_nodes:
            nid = node.get("id", "")
            kind = node.get("kind", "")
            meta = node.get("meta", {})
            if kind == "document":
                if meta.get("file_id", "") in keep_file_ids:
                    keep.add(nid)
            elif kind == "chunk":
                if meta.get("chunk_id", "") in keep_chunk_ids:
                    keep.add(nid)
            elif kind == "entity":
                if meta.get("entity_id", "") in keep_entity_ids:
                    keep.add(nid)
            elif kind == "relation":
                if meta.get("relation_id", "") in keep_relation_ids:
                    keep.add(nid)

        keep_relation_ids.update(keep_entity_ids)
        keep_relation_ids.update(keep_chunk_ids)
        keep_relation_ids.update(keep_file_ids)

        result = []
        seen = set()
        for node in all_nodes:
            if node["id"] in keep and node["id"] not in seen:
                seen.add(node["id"])
                result.append(node)
        return result

    @staticmethod
    def _build_filtered_edges(records: list[dict], all_edges: list[dict]) -> list[dict]:
        keep_chunk_ids = {rec["chunk_id"] for rec in records}
        keep_entity_ids = set()
        keep_relation_ids = set()
        keep_file_ids = {rec.get("file_id", "") for rec in records}

        for rec in records:
            for ent in rec.get("entities", []):
                keep_entity_ids.add(ent.get("entity_id", ""))
            for rel in rec.get("relations", []):
                keep_relation_ids.add(rel.get("relation_id", ""))

        keep_ids = (
            {f"document:{fid}" for fid in keep_file_ids}
            | {f"chunk:{cid}" for cid in keep_chunk_ids}
            | {f"entity:{eid}" for eid in keep_entity_ids}
            | {f"relation:{rid}" for rid in keep_relation_ids}
        )

        result = []
        seen = set()
        for edge in all_edges:
            if edge["id"] in seen:
                continue
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in keep_ids or tgt in keep_ids:
                seen.add(edge["id"])
                result.append(edge)

        return result

    @staticmethod
    async def get_ontology_view(
        db: AsyncSession,
        category_id: str | None = None,
        ontology_id: str | None = None,
        entity_query: str | None = None,
        relation_type: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        """按本体类别/域从 SQLite 直接构建图谱视图，适用于非文件抽取实体。"""
        # 先收集符合 ontology_id / category_id 的本体 ID
        ontology_ids = []
        if ontology_id:
            ontology_ids = [ontology_id]
        elif category_id:
            rows = await db.execute(
                select(Ontology.id).where(Ontology.category_id == category_id)
            )
            ontology_ids = rows.scalars().all()

        # 实体过滤
        ent_q = select(Entity)
        if ontology_ids:
            ent_q = ent_q.where(Entity.ontology_id.in_(ontology_ids))
        if entity_query:
            pattern = f"%{entity_query}%"
            ent_q = ent_q.where(
                or_(
                    Entity.name.ilike(pattern),
                    Entity.description.ilike(pattern),
                )
            )

        # 总数
        total_q = select(func.count(Entity.id))
        if ontology_ids:
            total_q = total_q.where(Entity.ontology_id.in_(ontology_ids))
        if entity_query:
            total_q = total_q.where(
                or_(
                    Entity.name.ilike(pattern),
                    Entity.description.ilike(pattern),
                )
            )
        total = (await db.scalar(total_q)) or 0

        # 分页实体
        ent_rows = (await db.execute(ent_q.order_by(Entity.name).limit(limit).offset(offset))).scalars().all()
        entity_ids = {e.id for e in ent_rows}

        # 关系过滤：包含与当前实体相关的一跳关系，确保图谱不是孤立点
        rel_q = select(Relation).where(
            (Relation.source_entity_id.in_(entity_ids)) |
            (Relation.target_entity_id.in_(entity_ids))
        )
        if relation_type:
            rel_q = rel_q.where(Relation.relation_type == relation_type)
        rel_rows = (await db.execute(rel_q)).scalars().all()

        # 收集关联节点 ID
        related_ids = set()
        for r in rel_rows:
            related_ids.add(r.source_entity_id)
            related_ids.add(r.target_entity_id)
        extra_ids = related_ids - entity_ids
        extra_entities = {}
        if extra_ids:
            extra_rows = (await db.execute(select(Entity).where(Entity.id.in_(extra_ids)))).scalars().all()
            extra_entities = {e.id: e for e in extra_rows}

        node_map = {e.id: e for e in ent_rows}
        node_map.update(extra_entities)

        nodes = [
            {
                "id": f"entity:{e.id}",
                "kind": "entity",
                "label": e.name,
                "meta": {
                    "entity_id": e.id,
                    "entity_type": e.entity_type,
                    "ontology_id": e.ontology_id,
                    "kb_id": e.kb_id,
                    "name": e.name,
                    "description": e.description or "",
                },
            }
            for e in node_map.values()
        ]

        edges = [
            {
                "id": f"relation:{r.id}",
                "source": f"entity:{r.source_entity_id}",
                "target": f"entity:{r.target_entity_id}",
                "label": r.relation_type,
                "meta": {
                    "relation_id": r.id,
                    "relation_type": r.relation_type,
                    "description": r.description or "",
                },
            }
            for r in rel_rows
        ]

        records = [
            {
                "chunk_id": "",
                "file_id": "",
                "entities": [
                    {
                        "entity_id": e.id,
                        "name": e.name,
                        "entity_type": e.entity_type,
                        "description": e.description or "",
                    }
                    for e in node_map.values()
                ],
                "relations": [
                    {
                        "relation_id": r.id,
                        "source_entity_id": r.source_entity_id,
                        "source_name": node_map[r.source_entity_id].name,
                        "source_type": node_map[r.source_entity_id].entity_type,
                        "target_entity_id": r.target_entity_id,
                        "target_name": node_map[r.target_entity_id].name,
                        "target_type": node_map[r.target_entity_id].entity_type,
                        "relation_type": r.relation_type,
                        "description": r.description or "",
                    }
                    for r in rel_rows
                ],
                "entity_count": len(node_map),
                "relation_count": len(rel_rows),
            }
        ]

        return {
            "provider": get_graph_store_provider_name(),
            "kb": {"id": "", "name": "按本体类别", "description": ""},
            "nodes": nodes,
            "edges": edges,
            "records": records,
            "summary": {
                "entity_total": total,
                "entity_shown": len(ent_rows),
                "relation_total": len(rel_rows),
                "has_more": offset + limit < total,
                "limit": limit,
                "offset": offset,
            },
            "filters": {
                "category_id": category_id or "",
                "ontology_id": ontology_id or "",
                "entity_query": entity_query or "",
                "relation_type": relation_type or "",
            },
        }
