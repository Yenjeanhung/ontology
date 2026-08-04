"""实体/关系实例层 service。

职责：
- 实体实例（entities 表）与关系实例（relations 表）的 CRUD。
- 以 SQLite 为权威存储，Kùzu 图数据库做可视化同步：每次增删改均 best-effort 同步 Kùzu。
- 级联：删除实体时一并删除其参与的关系实例（SQLite + Kùzu）。

注意：本服务只处理"实例层"。本体类型/关系字典/三元组约束等"定义层"
由 `ontology_service` 维护，二者通过 `ontology_id` / `relation_def_id`
逻辑关联，无外键。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, Ontology, OntologyRelation, Relation
from providers.graph_store import (
    delete_entity as kuzu_delete_entity,
    delete_relation as kuzu_delete_relation,
    upsert_entity as kuzu_upsert_entity,
    upsert_relation as kuzu_upsert_relation,
)

logger = logging.getLogger(__name__)


# ---------- 辅助序列化 ----------

def _parse_properties(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _dump_properties(value: dict | None) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _serialize_entity(
    ent: Entity,
    *,
    ontology_name: str | None = None,
    include_properties: bool = True,
) -> dict:
    return {
        "id": ent.id,
        "kb_id": ent.kb_id,
        "ontology_id": ent.ontology_id,
        "ontology_name": ontology_name,
        "entity_type": ent.entity_type,
        "name": ent.name,
        "description": ent.description or "",
        "properties": _parse_properties(ent.properties) if include_properties else None,
        "source_file_id": ent.source_file_id,
        "source_chunk_id": ent.source_chunk_id,
        "created_at": ent.created_at,
        "updated_at": ent.updated_at,
    }


async def _enrich_entity_ontology_name(db: AsyncSession, ent: Entity) -> str | None:
    row = await db.execute(select(Ontology.name).where(Ontology.id == ent.ontology_id))
    return row.scalar_one_or_none()


async def _enrich_relation_names(db: AsyncSession, rel: Relation) -> dict:
    """补全关系实例的冗余展示字段（起终点实体名/类型、关系定义名）。"""
    src_row = await db.execute(
        select(Entity.name, Entity.entity_type).where(Entity.id == rel.source_entity_id)
    )
    src = src_row.first()
    tgt_row = await db.execute(
        select(Entity.name, Entity.entity_type).where(Entity.id == rel.target_entity_id)
    )
    tgt = tgt_row.first()
    rel_def_row = await db.execute(
        select(OntologyRelation.name).where(OntologyRelation.id == rel.relation_def_id)
    )
    rel_def_name = rel_def_row.scalar_one_or_none()
    return {
        "source_entity_name": src[0] if src else None,
        "source_entity_type": src[1] if src else None,
        "target_entity_name": tgt[0] if tgt else None,
        "target_entity_type": tgt[1] if tgt else None,
        "relation_def_name": rel_def_name,
    }


def _serialize_relation(rel: Relation, extra: dict | None = None) -> dict:
    payload = {
        "id": rel.id,
        "kb_id": rel.kb_id,
        "relation_def_id": rel.relation_def_id,
        "relation_type": rel.relation_type,
        "source_entity_id": rel.source_entity_id,
        "target_entity_id": rel.target_entity_id,
        "description": rel.description or "",
        "source_file_id": rel.source_file_id,
        "source_chunk_id": rel.source_chunk_id,
        "created_at": rel.created_at,
        "updated_at": rel.updated_at,
    }
    if extra:
        payload.update(extra)
    return payload


# ---------- Kùzu 同步（best-effort）----------

def _sync_upsert_entity(ent: Entity):
    try:
        kuzu_upsert_entity(
            entity_id=ent.id,
            kb_id=ent.kb_id,
            ontology_id=ent.ontology_id,
            entity_type=ent.entity_type,
            name=ent.name,
            description=ent.description or "",
            properties=ent.properties or "",
        )
    except Exception:
        logger.exception("Kùzu upsert_entity failed: entity_id=%s", ent.id)


def _sync_delete_entity(entity_id: str):
    try:
        kuzu_delete_entity(entity_id)
    except Exception:
        logger.exception("Kùzu delete_entity failed: entity_id=%s", entity_id)


def _sync_upsert_relation(rel: Relation):
    try:
        kuzu_upsert_relation(
            relation_id=rel.id,
            kb_id=rel.kb_id,
            relation_type=rel.relation_type,
            description=rel.description or "",
            source_entity_id=rel.source_entity_id,
            target_entity_id=rel.target_entity_id,
        )
    except Exception:
        logger.exception("Kùzu upsert_relation failed: relation_id=%s", rel.id)


def _sync_delete_relation(relation_id: str):
    try:
        kuzu_delete_relation(relation_id)
    except Exception:
        logger.exception("Kùzu delete_relation failed: relation_id=%s", relation_id)


# ---------- 分页响应构造 ----------

def _paginate(total: int, items: list, page: int, page_size: int) -> dict:
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_prev": page > 1,
        "has_next": page * page_size < total,
        "items": items,
    }


class EntityService:
    # ===== 实体实例 CRUD =====

    @staticmethod
    async def list_entities(
        db: AsyncSession,
        *,
        kb_id: str | None = None,
        ontology_id: str | None = None,
        entity_type: str | None = None,
        q: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        page = max(1, page)
        page_size = max(1, min(page_size, 200))

        filters = []
        if kb_id:
            filters.append(Entity.kb_id == kb_id)
        if ontology_id:
            filters.append(Entity.ontology_id == ontology_id)
        if entity_type:
            filters.append(Entity.entity_type == entity_type)
        if q:
            like = f"%{q.strip()}%"
            filters.append(
                or_(Entity.name.like(like), Entity.description.like(like))
            )

        total_stmt = select(func.count()).select_from(Entity)
        if filters:
            total_stmt = total_stmt.where(*filters)
        total = (await db.execute(total_stmt)).scalar_one()

        stmt = (
            select(Entity)
            .order_by(Entity.created_at.desc(), Entity.name.asc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        if filters:
            stmt = stmt.where(*filters)
        rows = (await db.execute(stmt)).scalars().all()

        # 批量补 ontology_name（一次查询）
        ont_ids = {r.ontology_id for r in rows if r.ontology_id}
        ont_map: dict[str, str] = {}
        if ont_ids:
            ont_rows = await db.execute(
                select(Ontology.id, Ontology.name).where(Ontology.id.in_(ont_ids))
            )
            ont_map = {row[0]: row[1] for row in ont_rows}

        items = [
            _serialize_entity(r, ontology_name=ont_map.get(r.ontology_id), include_properties=False)
            for r in rows
        ]
        return _paginate(total, items, page, page_size)

    @staticmethod
    async def get_entity(db: AsyncSession, entity_id: str) -> dict | None:
        row = await db.execute(select(Entity).where(Entity.id == entity_id))
        ent = row.scalar_one_or_none()
        if not ent:
            return None
        ont_name = await _enrich_entity_ontology_name(db, ent)

        # 关联关系实例（作为起点或终点）
        rels_row = await db.execute(
            select(Relation).where(
                or_(Relation.source_entity_id == entity_id,
                    Relation.target_entity_id == entity_id)
            ).order_by(Relation.created_at.desc())
        )
        related_relations = []
        for rel in rels_row.scalars().all():
            extra = await _enrich_relation_names(db, rel)
            extra["role"] = "source" if rel.source_entity_id == entity_id else "target"
            related_relations.append(_serialize_relation(rel, extra))

        result = _serialize_entity(ent, ontology_name=ont_name)
        result["relations"] = related_relations
        return result

    @staticmethod
    async def create_entity(
        db: AsyncSession,
        *,
        kb_id: str,
        ontology_id: str,
        entity_type: str,
        name: str,
        description: str = "",
        properties: dict | None = None,
        source_file_id: str | None = None,
        source_chunk_id: str | None = None,
    ) -> dict:
        """手动创建实体实例（也可被抽取流程复用）。

        若 (kb_id, entity_type, name) 已存在，则按 upsert 语义更新已有记录。
        """
        existing_row = await db.execute(
            select(Entity).where(
                Entity.kb_id == kb_id,
                Entity.entity_type == entity_type,
                Entity.name == name,
            )
        )
        ent = existing_row.scalar_one_or_none()
        if ent is None:
            ent = Entity(
                kb_id=kb_id,
                ontology_id=ontology_id,
                entity_type=entity_type,
                name=name.strip(),
                description=(description or "").strip(),
                properties=_dump_properties(properties),
                source_file_id=source_file_id,
                source_chunk_id=source_chunk_id,
            )
            db.add(ent)
            await db.flush()
        else:
            # upsert：更新本体归属、描述、属性；保留原 source 信息
            ent.ontology_id = ontology_id
            ent.entity_type = entity_type
            ent.name = name.strip()
            if description is not None:
                ent.description = description.strip()
            if properties is not None:
                ent.properties = _dump_properties(properties)
            ent.updated_at = datetime.now().isoformat()
            await db.flush()

        await db.commit()
        await db.refresh(ent)

        ont_name = await _enrich_entity_ontology_name(db, ent)
        payload = _serialize_entity(ent, ontology_name=ont_name)
        _sync_upsert_entity(ent)
        return payload

    @staticmethod
    async def update_entity(
        db: AsyncSession,
        entity_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        properties: dict | None = None,
    ) -> dict | None:
        row = await db.execute(select(Entity).where(Entity.id == entity_id))
        ent = row.scalar_one_or_none()
        if not ent:
            return None

        if name is not None:
            ent.name = name.strip()
        if description is not None:
            ent.description = description.strip()
        if properties is not None:
            ent.properties = _dump_properties(properties)
        ent.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(ent)

        ont_name = await _enrich_entity_ontology_name(db, ent)
        payload = _serialize_entity(ent, ontology_name=ont_name)
        _sync_upsert_entity(ent)
        return payload

    @staticmethod
    async def delete_entity(db: AsyncSession, entity_id: str) -> bool:
        row = await db.execute(select(Entity).where(Entity.id == entity_id))
        ent = row.scalar_one_or_none()
        if not ent:
            return False

        # 级联删除：该实体参与的所有关系实例
        rels_row = await db.execute(
            select(Relation).where(
                or_(Relation.source_entity_id == entity_id,
                    Relation.target_entity_id == entity_id)
            )
        )
        relation_ids = [r.id for r in rels_row.scalars().all()]
        if relation_ids:
            await db.execute(
                delete(Relation).where(Relation.id.in_(relation_ids))
            )

        await db.delete(ent)
        await db.commit()

        # Kùzu 同步：delete_entity 会级联删除该实体参与的 Kùzu Relation 节点
        _sync_delete_entity(entity_id)
        return True

    # ===== 关系实例 CRUD =====

    @staticmethod
    async def list_relations(
        db: AsyncSession,
        *,
        kb_id: str | None = None,
        relation_type: str | None = None,
        relation_def_id: str | None = None,
        entity_id: str | None = None,
        q: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        page = max(1, page)
        page_size = max(1, min(page_size, 200))

        filters = []
        if kb_id:
            filters.append(Relation.kb_id == kb_id)
        if relation_type:
            filters.append(Relation.relation_type == relation_type)
        if relation_def_id:
            filters.append(Relation.relation_def_id == relation_def_id)
        if entity_id:
            filters.append(
                or_(Relation.source_entity_id == entity_id,
                    Relation.target_entity_id == entity_id)
            )
        if q:
            like = f"%{q.strip()}%"
            filters.append(
                or_(Relation.relation_type.like(like), Relation.description.like(like))
            )

        total_stmt = select(func.count()).select_from(Relation)
        if filters:
            total_stmt = total_stmt.where(*filters)
        total = (await db.execute(total_stmt)).scalar_one()

        stmt = (
            select(Relation)
            .order_by(Relation.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        if filters:
            stmt = stmt.where(*filters)
        rows = (await db.execute(stmt)).scalars().all()

        items = []
        for rel in rows:
            extra = await _enrich_relation_names(db, rel)
            items.append(_serialize_relation(rel, extra))

        return _paginate(total, items, page, page_size)

    @staticmethod
    async def get_relation(db: AsyncSession, relation_id: str) -> dict | None:
        row = await db.execute(select(Relation).where(Relation.id == relation_id))
        rel = row.scalar_one_or_none()
        if not rel:
            return None
        extra = await _enrich_relation_names(db, rel)
        return _serialize_relation(rel, extra)

    @staticmethod
    async def create_relation(
        db: AsyncSession,
        *,
        kb_id: str,
        relation_def_id: str,
        relation_type: str,
        source_entity_id: str,
        target_entity_id: str,
        description: str = "",
        source_file_id: str | None = None,
        source_chunk_id: str | None = None,
    ) -> dict:
        """创建关系实例。upsert 语义：同 (kb_id, source, relation_type, target) 已存在则更新。"""
        existing_row = await db.execute(
            select(Relation).where(
                Relation.kb_id == kb_id,
                Relation.source_entity_id == source_entity_id,
                Relation.relation_type == relation_type,
                Relation.target_entity_id == target_entity_id,
            )
        )
        rel = existing_row.scalar_one_or_none()
        if rel is None:
            rel = Relation(
                kb_id=kb_id,
                relation_def_id=relation_def_id,
                relation_type=relation_type,
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                description=(description or "").strip(),
                source_file_id=source_file_id,
                source_chunk_id=source_chunk_id,
            )
            db.add(rel)
            await db.flush()
        else:
            rel.relation_def_id = relation_def_id
            rel.relation_type = relation_type
            if description is not None:
                rel.description = description.strip()
            rel.updated_at = datetime.now().isoformat()
            await db.flush()

        await db.commit()
        await db.refresh(rel)

        extra = await _enrich_relation_names(db, rel)
        payload = _serialize_relation(rel, extra)
        _sync_upsert_relation(rel)
        return payload

    @staticmethod
    async def update_relation(
        db: AsyncSession,
        relation_id: str,
        *,
        relation_type: str | None = None,
        description: str | None = None,
    ) -> dict | None:
        row = await db.execute(select(Relation).where(Relation.id == relation_id))
        rel = row.scalar_one_or_none()
        if not rel:
            return None
        if relation_type is not None:
            rel.relation_type = relation_type
        if description is not None:
            rel.description = description.strip()
        rel.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(rel)

        extra = await _enrich_relation_names(db, rel)
        payload = _serialize_relation(rel, extra)
        _sync_upsert_relation(rel)
        return payload

    @staticmethod
    async def delete_relation(db: AsyncSession, relation_id: str) -> bool:
        row = await db.execute(select(Relation).where(Relation.id == relation_id))
        rel = row.scalar_one_or_none()
        if not rel:
            return False
        await db.delete(rel)
        await db.commit()
        _sync_delete_relation(relation_id)
        return True

    # ===== 统计 =====

    @staticmethod
    async def stats(db: AsyncSession, kb_id: str | None = None) -> dict:
        ent_stmt = select(func.count()).select_from(Entity)
        rel_stmt = select(func.count()).select_from(Relation)
        type_stmt = (
            select(Entity.entity_type, func.count())
            .group_by(Entity.entity_type)
            .order_by(func.count().desc())
        )
        if kb_id:
            ent_stmt = ent_stmt.where(Entity.kb_id == kb_id)
            rel_stmt = rel_stmt.where(Relation.kb_id == kb_id)
            type_stmt = type_stmt.where(Entity.kb_id == kb_id)

        entity_total = (await db.execute(ent_stmt)).scalar_one()
        relation_total = (await db.execute(rel_stmt)).scalar_one()
        type_rows = (await db.execute(type_stmt)).all()
        return {
            "kb_id": kb_id,
            "entity_total": entity_total,
            "relation_total": relation_total,
            "entity_type_distribution": [
                {"entity_type": t or "UNKNOWN", "count": c} for t, c in type_rows
            ],
        }
