from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import KnowledgeBase
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
