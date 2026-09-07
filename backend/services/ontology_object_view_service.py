"""S6（P1-3）对象视图服务：可配置实体详情页布局。

视图是一段 JSON 布局（tabs → sections → widgets），前端 EntityDetailPage 按
此渲染；无视图时回落到当前固定布局（向后兼容）。视图可绑定到：
- 某个本体（ontology_id）；
- 整个类别的缺省视图（ontology_id 空）；
- 某个接口（interface_code，作为该接口所有实现本体的统一视图）。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import OntologyObjectView


def _dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def serialize_view(v: OntologyObjectView) -> dict:
    return {
        "id": v.id,
        "category_id": v.category_id,
        "ontology_id": v.ontology_id or "",
        "interface_code": v.interface_code or "",
        "name": v.name,
        "layout": json.loads(v.layout) if v.layout else {},
        "is_default": bool(v.is_default),
        "version": v.version,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
    }


class ObjectViewService:
    @staticmethod
    async def list_for_category(db: AsyncSession, category_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyObjectView)
            .where(OntologyObjectView.category_id == category_id)
            .order_by(OntologyObjectView.updated_at.desc())
        )).scalars().all()
        return [serialize_view(v) for v in rows]

    @staticmethod
    async def get(db: AsyncSession, view_id: str) -> OntologyObjectView | None:
        return await db.get(OntologyObjectView, view_id)

    @staticmethod
    async def create(db: AsyncSession, category_id: str, req) -> tuple[dict | None, str | None]:
        view = OntologyObjectView(
            category_id=category_id,
            ontology_id=(req.ontology_id or "").strip(),
            interface_code=(req.interface_code or "").strip(),
            name=req.name.strip(),
            layout=_dump_json(req.layout or {}),
            is_default=int(bool(req.is_default)),
            version=1,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        db.add(view)
        if req.is_default or req.set_default:
            await ObjectViewService._unset_default(db, category_id, view.ontology_id, view.interface_code)
            view.is_default = 1
        await db.commit()
        await db.refresh(view)
        return serialize_view(view), None

    @staticmethod
    async def update(db: AsyncSession, view_id: str, req) -> tuple[dict | None, str | None]:
        view = await db.get(OntologyObjectView, view_id)
        if not view:
            return None, "对象视图不存在"
        if req.name is not None:
            view.name = req.name.strip()
        if req.ontology_id is not None:
            view.ontology_id = req.ontology_id.strip()
        if req.interface_code is not None:
            view.interface_code = req.interface_code.strip()
        if req.layout is not None:
            view.layout = _dump_json(req.layout)
        if req.version is not None:
            view.version = req.version
        set_default = req.is_default if req.is_default is not None else req.set_default
        if set_default:
            await ObjectViewService._unset_default(db, view.category_id, view.ontology_id, view.interface_code, except_id=view.id)
            view.is_default = 1
        elif req.is_default is not None and not req.is_default:
            view.is_default = 0
        view.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(view)
        return serialize_view(view), None

    @staticmethod
    async def delete(db: AsyncSession, view_id: str) -> bool:
        view = await db.get(OntologyObjectView, view_id)
        if not view:
            return False
        await db.delete(view)
        await db.commit()
        return True

    @staticmethod
    async def set_default(db: AsyncSession, view_id: str) -> tuple[dict | None, str | None]:
        view = await db.get(OntologyObjectView, view_id)
        if not view:
            return None, "对象视图不存在"
        await ObjectViewService._unset_default(db, view.category_id, view.ontology_id, view.interface_code, except_id=view.id)
        view.is_default = 1
        view.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(view)
        return serialize_view(view), None

    @staticmethod
    async def resolve(db: AsyncSession, category_id: str, ontology_id: str = "") -> dict | None:
        """解析某本体应使用的视图：优先本体专属缺省，其次类别缺省。"""
        if ontology_id:
            row = await db.execute(
                select(OntologyObjectView).where(
                    OntologyObjectView.category_id == category_id,
                    OntologyObjectView.ontology_id == ontology_id,
                    OntologyObjectView.is_default == 1,
                )
            )
            v = row.scalar_one_or_none()
            if v:
                return serialize_view(v)
        row = await db.execute(
            select(OntologyObjectView).where(
                OntologyObjectView.category_id == category_id,
                OntologyObjectView.ontology_id == "",
                OntologyObjectView.is_default == 1,
            )
        )
        v = row.scalar_one_or_none()
        return serialize_view(v) if v else None

    @staticmethod
    async def _unset_default(
        db: AsyncSession, category_id: str, ontology_id: str, interface_code: str, except_id: str | None = None
    ) -> None:
        stmt = select(OntologyObjectView).where(
            OntologyObjectView.category_id == category_id,
            OntologyObjectView.is_default == 1,
        )
        rows = (await db.execute(stmt)).scalars().all()
        for v in rows:
            if v.id == except_id:
                continue
            if v.ontology_id == ontology_id and v.interface_code == interface_code:
                v.is_default = 0
        await db.flush()
