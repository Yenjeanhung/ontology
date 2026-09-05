from __future__ import annotations

import asyncio

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, KnowledgeBase, Ontology, Relation
from providers.graph_store import (
    fetch_graph_view,
    get_graph_store_provider_name,
    list_graph_relation_types,
)

# asyncpg 单条语句参数上限 32767，IN(...) 查询按批拆分留足余量
_IN_BATCH = 2000
# 本体视图首屏配额：骨架边（双端都在主实体集）与跨边（带新邻居）分别限流，
# 配合前端"加载更多/双击展开"渐进补全，避免一次性渲染上万节点
_INNER_RELATION_PAGE = 300
_CROSS_RELATION_PAGE = 50


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
            # 把全量度数写进实体节点 meta，前端用于节点大小与“可展开邻居”角标
            for node in data["nodes"]:
                if node.get("kind") == "entity":
                    meta = node.setdefault("meta", {})
                    meta["degree"] = entity_degree.get(meta.get("entity_id", ""), 0)

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

        # 实体过滤 + 全量度数（source/target 关系计数之和），按度数降序分页
        src_cnt = (
            select(Relation.source_entity_id.label("eid"), func.count().label("cnt"))
            .group_by(Relation.source_entity_id)
            .subquery()
        )
        tgt_cnt = (
            select(Relation.target_entity_id.label("eid"), func.count().label("cnt"))
            .group_by(Relation.target_entity_id)
            .subquery()
        )
        degree_col = (func.coalesce(src_cnt.c.cnt, 0) + func.coalesce(tgt_cnt.c.cnt, 0)).label("degree")

        pattern = f"%{entity_query}%" if entity_query else ""
        ent_q = (
            select(Entity, degree_col)
            .outerjoin(src_cnt, Entity.id == src_cnt.c.eid)
            .outerjoin(tgt_cnt, Entity.id == tgt_cnt.c.eid)
        )
        if ontology_ids:
            ent_q = ent_q.where(Entity.ontology_id.in_(ontology_ids))
        if entity_query:
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

        # 分页实体（度数大的先加载，同度数按名称稳定排序）
        ent_rows_raw = (
            await db.execute(
                ent_q.order_by(desc("degree"), Entity.name).limit(limit).offset(offset)
            )
        ).all()
        ent_rows = [row[0] for row in ent_rows_raw]
        base_degree = {row[0].id: int(row[1] or 0) for row in ent_rows_raw}
        entity_ids = {e.id for e in ent_rows}

        # 关系分两层配额截断（按关系 id 稳定排序），保证首屏只出"骨架"：
        # 1) 骨架边：双端都在主实体集，展示高度实体之间的核心结构，不引入新节点
        # 2) 跨边：恰好一端在主实体集，每条至多带 1 个新邻居，只给小配额
        # 完整邻居由前端双击节点懒加载展开；节点 meta.degree 仍为全量度数
        in_src = Relation.source_entity_id.in_(entity_ids)
        in_tgt = Relation.target_entity_id.in_(entity_ids)
        base_cond = in_src | in_tgt
        inner_cond = in_src & in_tgt
        cross_cond = (in_src & ~in_tgt) | (in_tgt & ~in_src)

        rel_total_q = select(func.count()).select_from(Relation).where(base_cond)
        inner_q = select(Relation).where(inner_cond)
        cross_q = select(Relation).where(cross_cond)
        if relation_type:
            rel_total_q = rel_total_q.where(Relation.relation_type == relation_type)
            inner_q = inner_q.where(Relation.relation_type == relation_type)
            cross_q = cross_q.where(Relation.relation_type == relation_type)
        relation_total = (await db.scalar(rel_total_q)) or 0
        inner_rows = (
            await db.execute(inner_q.order_by(Relation.id).limit(_INNER_RELATION_PAGE))
        ).scalars().all()
        cross_rows = (
            await db.execute(cross_q.order_by(Relation.id).limit(_CROSS_RELATION_PAGE))
        ).scalars().all()
        rel_rows = list(inner_rows) + list(cross_rows)

        # 收集关联节点 ID（仅基于截断后的关系，邻居规模可控）
        related_ids = set()
        for r in rel_rows:
            related_ids.add(r.source_entity_id)
            related_ids.add(r.target_entity_id)
        extra_ids = related_ids - entity_ids
        extra_entities = {}
        extra_list = sorted(extra_ids)
        for i in range(0, len(extra_list), _IN_BATCH):
            part = extra_list[i : i + _IN_BATCH]
            rows = (
                await db.execute(select(Entity).where(Entity.id.in_(part)))
            ).scalars().all()
            for e in rows:
                extra_entities[e.id] = e
        extra_degree = await GraphDataService._degree_map(db, extra_list)

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
                    "degree": base_degree.get(e.id, extra_degree.get(e.id, 0)),
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
                "relation_total": relation_total,
                "relation_shown": len(rel_rows),
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

    @staticmethod
    async def _degree_map(db: AsyncSession, entity_ids: list[str]) -> dict[str, int]:
        """批量统计实体在 relations 表中的全量度数（作为起点与终点次数之和）。

        内部按 _IN_BATCH 分批查询，规避 asyncpg 单条语句 32767 个参数的上限。
        """
        ids = list(dict.fromkeys(entity_ids))
        merged: dict[str, int] = {}
        if not ids:
            return merged
        for i in range(0, len(ids), _IN_BATCH):
            part = ids[i : i + _IN_BATCH]
            src = {
                row[0]: int(row[1])
                for row in (
                    await db.execute(
                        select(Relation.source_entity_id, func.count())
                        .where(Relation.source_entity_id.in_(part))
                        .group_by(Relation.source_entity_id)
                    )
                ).all()
            }
            tgt = {
                row[0]: int(row[1])
                for row in (
                    await db.execute(
                        select(Relation.target_entity_id, func.count())
                        .where(Relation.target_entity_id.in_(part))
                        .group_by(Relation.target_entity_id)
                    )
                ).all()
            }
            for eid in part:
                merged[eid] = src.get(eid, 0) + tgt.get(eid, 0)
        return merged

    @staticmethod
    async def expand_entity(
        db: AsyncSession,
        entity_id: str,
        relation_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """双击展开：返回实体的一跳关系与邻居实体（按稳定顺序分页），用于图谱懒加载。

        直接查权威存储（SQL/PG），与 KB 模式 / 本体模式视图均兼容。
        """
        entity = (
            await db.execute(select(Entity).where(Entity.id == entity_id))
        ).scalar_one_or_none()
        if not entity:
            raise ValueError("Entity not found")

        cond = or_(
            Relation.source_entity_id == entity_id,
            Relation.target_entity_id == entity_id,
        )
        rel_q = select(Relation).where(cond)
        cnt_q = select(func.count()).select_from(Relation).where(cond)
        if relation_type:
            rel_q = rel_q.where(Relation.relation_type == relation_type)
            cnt_q = cnt_q.where(Relation.relation_type == relation_type)

        total = (await db.scalar(cnt_q)) or 0
        rel_rows = (
            await db.execute(rel_q.order_by(Relation.id).limit(limit).offset(offset))
        ).scalars().all()

        involved = {entity_id}
        for r in rel_rows:
            involved.add(str(r.source_entity_id))
            involved.add(str(r.target_entity_id))
        ent_rows = (
            await db.execute(select(Entity).where(Entity.id.in_(involved)))
        ).scalars().all()
        degree_map = await GraphDataService._degree_map(db, sorted(involved))

        nodes = [
            {
                "id": f"entity:{e.id}",
                "kind": "entity",
                "label": e.name,
                "meta": {
                    "entity_id": e.id,
                    "entity_type": e.entity_type,
                    "description": e.description or "",
                    "degree": degree_map.get(str(e.id), 0),
                },
            }
            for e in ent_rows
        ]
        edges = [
            {
                "id": f"relation:{r.id}",
                "source": f"entity:{r.source_entity_id}",
                "target": f"entity:{r.target_entity_id}",
                "kind": "relates",
                "label": r.relation_type,
                "meta": {
                    "relation_id": r.id,
                    "relation_type": r.relation_type,
                    "description": r.description or "",
                },
            }
            for r in rel_rows
        ]

        return {
            "nodes": nodes,
            "edges": edges,
            "summary": {
                "entity_id": entity_id,
                "entity_name": entity.name,
                "degree": degree_map.get(entity_id, 0),
                "relation_total": total,
                "relation_shown": len(rel_rows),
                "has_more": offset + limit < total,
                "limit": limit,
                "offset": offset,
            },
        }
