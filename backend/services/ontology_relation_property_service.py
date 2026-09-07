"""S5（P1-2）关系属性服务：链接本身可带属性（时间区间/权重/置信度/来源等）。

关系属性定义挂在 ``ontology_relations`` 上；关系实例属性以 JSON 形式存储在
``relations.properties``，由图迁入 / 抽取链路按定义写入。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import OntologyRelation, OntologyRelationProperty


def _dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def serialize_property(p: OntologyRelationProperty) -> dict:
    return {
        "id": p.id,
        "relation_id": p.relation_id,
        "name": p.name,
        "code": p.code or "",
        "data_type": p.data_type,
        "description": p.description or "",
        "is_required": bool(p.is_required),
        "enum_values": json.loads(p.enum_values) if p.enum_values else None,
        "sort_order": p.sort_order,
        "created_at": p.created_at,
    }


class RelationPropertyService:
    @staticmethod
    async def list_for_relation(db: AsyncSession, relation_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyRelationProperty)
            .where(OntologyRelationProperty.relation_id == relation_id)
            .order_by(OntologyRelationProperty.sort_order)
        )).scalars().all()
        return [serialize_property(p) for p in rows]

    @staticmethod
    async def create(db: AsyncSession, relation_id: str, req) -> tuple[dict | None, str | None]:
        rel = await db.get(OntologyRelation, relation_id)
        if not rel:
            return None, "关系不存在"
        code = (req.code or "").strip()
        if code:
            dup = await db.execute(
                select(OntologyRelationProperty.id).where(
                    OntologyRelationProperty.relation_id == relation_id,
                    OntologyRelationProperty.code == code,
                )
            )
            if dup.scalar_one_or_none():
                return None, f'属性编码 "{code}" 在该关系下已存在'
        prop = OntologyRelationProperty(
            relation_id=relation_id,
            name=req.name.strip(),
            code=code or None,
            data_type=req.data_type,
            description=(req.description or "").strip(),
            is_required=int(bool(req.is_required)),
            enum_values=_dump_json(req.enum_values) if req.enum_values else None,
            sort_order=req.sort_order or 0,
            created_at=datetime.now().isoformat(),
        )
        db.add(prop)
        await db.commit()
        await db.refresh(prop)
        return serialize_property(prop), None

    @staticmethod
    async def update(db: AsyncSession, prop_id: str, req) -> tuple[dict | None, str | None]:
        prop = await db.get(OntologyRelationProperty, prop_id)
        if not prop:
            return None, "关系属性不存在"
        if req.name is not None:
            prop.name = req.name.strip()
        if req.code is not None:
            new_code = req.code.strip() or None
            if new_code and new_code != (prop.code or ""):
                dup = await db.execute(
                    select(OntologyRelationProperty.id).where(
                        OntologyRelationProperty.relation_id == prop.relation_id,
                        OntologyRelationProperty.code == new_code,
                        OntologyRelationProperty.id != prop_id,
                    )
                )
                if dup.scalar_one_or_none():
                    return None, f'属性编码 "{new_code}" 在该关系下已存在'
            prop.code = new_code
        if req.data_type is not None:
            prop.data_type = req.data_type
        if req.description is not None:
            prop.description = req.description.strip()
        if req.is_required is not None:
            prop.is_required = int(bool(req.is_required))
        if req.enum_values is not None:
            prop.enum_values = _dump_json(req.enum_values)
        if req.sort_order is not None:
            prop.sort_order = req.sort_order
        await db.commit()
        await db.refresh(prop)
        return serialize_property(prop), None

    @staticmethod
    async def delete(db: AsyncSession, prop_id: str) -> bool:
        prop = await db.get(OntologyRelationProperty, prop_id)
        if not prop:
            return False
        await db.delete(prop)
        await db.commit()
        return True

    @staticmethod
    async def delete_for_relation(db: AsyncSession, relation_id: str) -> None:
        await db.execute(
            delete(OntologyRelationProperty).where(OntologyRelationProperty.relation_id == relation_id)
        )
        await db.commit()
