"""共享属性 + 本体接口（Interface）业务层。

设计来源：《本体能力对标Palantir_补强设计》S1（共享属性）与 S2（接口）。

与既有「属性模板」的区别（务必区分）
=====================================
- **属性模板**：定义**拷贝**，本体引用后属性成为本体自有副本，改模板不影响已引用本体的历史值；
- **共享属性**：定义**契约引用**，本体属性通过 ``shared_property_id`` 指向它，改共享属性的
  元数据（类型/枚举/单位）对所有引用它的本体属性立即生效，值仍各自存储；
- **接口**：在共享属性之上再加一层「形状契约」——接口 = 一组共享属性 + 链接契约，本体
  ``implements`` 接口后，可被**同一份视图/动作/函数/查询**统一处理（多态）。

无外键约定：所有关联由本模块在 service 层维护（级联删除、引用清理）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import and_, delete, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Entity,
    Ontology,
    OntologyAttribute,
    OntologyInterface,
    OntologyInterfaceImplementation,
    OntologyInterfaceLink,
    OntologyInterfaceProperty,
    OntologySharedProperty,
)

logger = logging.getLogger(__name__)

# 允许的对象类型状态
ONTOLOGY_STATUSES = ("draft", "active", "deprecated")


def _now() -> str:
    return datetime.now().isoformat()


def _load_json(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


# ───────────────────────── 共享属性 ─────────────────────────


def _sp_to_dict(sp: OntologySharedProperty, usage_count: int = 0,
                ontology_ids: list[str] | None = None) -> dict:
    return {
        "id": sp.id,
        "name": sp.name,
        "code": sp.code,
        "data_type": sp.data_type,
        "description": sp.description or "",
        "is_required": bool(sp.is_required),
        "default_value": sp.default_value,
        "enum_values": _load_json(sp.enum_values, None),
        "unit": sp.unit or "",
        "format": sp.format or "",
        "is_system": bool(sp.is_system),
        "usage_count": usage_count,
        "ontology_ids": ontology_ids or [],
        "created_at": sp.created_at,
        "updated_at": sp.updated_at,
    }


class SharedPropertyService:
    """共享属性：跨本体统一定义，值各自独立。"""

    @staticmethod
    async def list_properties(db: AsyncSession, q: str = "") -> list[dict]:
        stmt = select(OntologySharedProperty).order_by(OntologySharedProperty.created_at)
        if q:
            stmt = stmt.where(
                OntologySharedProperty.name.contains(q)
                | OntologySharedProperty.code.contains(q)
            )
        rows = (await db.execute(stmt)).scalars().all()
        if not rows:
            return []
        usage = await SharedPropertyService._usage_counts(db)
        ont_ids = await SharedPropertyService._usage_ontology_ids(db)
        return [_sp_to_dict(r, usage.get(r.id, 0), ont_ids.get(r.id, [])) for r in rows]

    @staticmethod
    async def _usage_counts(db: AsyncSession) -> dict[str, int]:
        rows = await db.execute(
            select(OntologyAttribute.shared_property_id, func.count())
            .where(OntologyAttribute.shared_property_id != "")
            .group_by(OntologyAttribute.shared_property_id)
        )
        return {row[0]: int(row[1]) for row in rows.all() if row[0]}

    @staticmethod
    async def _usage_ontology_ids(db: AsyncSession) -> dict[str, list[str]]:
        """共享属性 → 已挂载的本体 id 列表（去重）。"""
        rows = await db.execute(
            select(OntologyAttribute.shared_property_id, OntologyAttribute.ontology_id)
            .where(OntologyAttribute.shared_property_id != "")
            .distinct()
        )
        result: dict[str, list[str]] = {}
        for sp_id, ont_id in rows.all():
            if sp_id and ont_id:
                result.setdefault(sp_id, []).append(ont_id)
        return result

    @staticmethod
    async def get_property(db: AsyncSession, prop_id: str) -> dict | None:
        sp = await db.get(OntologySharedProperty, prop_id)
        if not sp:
            return None
        usage = await SharedPropertyService._usage_counts(db)
        ont_ids = await SharedPropertyService._usage_ontology_ids(db)
        return _sp_to_dict(sp, usage.get(sp.id, 0), ont_ids.get(sp.id, []))

    @staticmethod
    async def create_property(db: AsyncSession, req) -> dict:
        code = (req.code or "").strip() or None
        if code:
            dup = (await db.execute(
                select(OntologySharedProperty.id).where(OntologySharedProperty.code == code)
            )).scalar_one_or_none()
            if dup:
                raise ValueError(f'共享属性编码 "{code}" 已存在')
        sp = OntologySharedProperty(
            name=req.name.strip(),
            code=code,
            data_type=req.data_type,
            description=(req.description or "").strip(),
            is_required=int(req.is_required),
            default_value=req.default_value,
            enum_values=json.dumps(req.enum_values, ensure_ascii=False) if req.enum_values else None,
            unit=(req.unit or "").strip(),
            format=(req.format or "").strip(),
        )
        db.add(sp)
        await db.commit()
        await db.refresh(sp)
        return _sp_to_dict(sp)

    @staticmethod
    async def update_property(db: AsyncSession, prop_id: str, req) -> dict | None:
        sp = await db.get(OntologySharedProperty, prop_id)
        if not sp:
            return None
        if req.name is not None:
            sp.name = req.name.strip()
        if req.code is not None:
            code = req.code.strip() or None
            if code and code != sp.code:
                dup = (await db.execute(
                    select(OntologySharedProperty.id).where(
                        OntologySharedProperty.code == code,
                        OntologySharedProperty.id != prop_id,
                    )
                )).scalar_one_or_none()
                if dup:
                    raise ValueError(f'共享属性编码 "{code}" 已存在')
            sp.code = code
        for field in ("data_type", "description", "is_required", "default_value",
                      "enum_values", "unit", "format"):
            value = getattr(req, field, None)
            if value is None:
                continue
            if field == "description":
                sp.description = value.strip()
            elif field == "is_required":
                sp.is_required = int(value)
            elif field == "enum_values":
                sp.enum_values = json.dumps(value, ensure_ascii=False) if value else None
            else:
                setattr(sp, field, value)
        sp.updated_at = _now()

        # 同步元数据到所有引用它的本体属性（契约语义：改一处、全局生效）
        await db.execute(
            OntologyAttribute.__table__.update()
            .where(OntologyAttribute.shared_property_id == prop_id)
            .values(
                data_type=sp.data_type,
                is_required=sp.is_required,
                default_value=sp.default_value,
            )
        )
        await db.commit()
        await db.refresh(sp)
        return _sp_to_dict(sp)

    @staticmethod
    async def delete_property(db: AsyncSession, prop_id: str) -> bool:
        sp = await db.get(OntologySharedProperty, prop_id)
        if not sp:
            return False
        if sp.is_system:
            raise ValueError("系统内置共享属性不可删除")
        # 解除引用（本体属性保留，仅清空引用标记）
        await db.execute(
            OntologyAttribute.__table__.update()
            .where(OntologyAttribute.shared_property_id == prop_id)
            .values(shared_property_id="")
        )
        await db.delete(sp)
        await db.commit()
        return True

    @staticmethod
    async def apply_to_ontologies(
        db: AsyncSession, prop_id: str, ontology_ids: list[str], overwrite: bool = False,
        delete_manual_ids: list[str] | None = None,
    ) -> dict:
        """把共享属性挂到一个或多个本体：本体无同名属性则新建，已有则按 overwrite 决定是否同步；
        未选中的已挂载本体解除引用（生成的属性删除；手工同名属性默认仅解绑，除非在 delete_manual_ids 中指定删除）。"""
        sp = await db.get(OntologySharedProperty, prop_id)
        if not sp:
            raise ValueError("共享属性不存在")
        ontology_ids_set = set(ontology_ids or [])
        delete_manual = set(delete_manual_ids or [])
        created, updated, skipped, detached, bound = 0, 0, 0, 0, 0

        # 1) 处理取消勾选：已挂载但不在 ontology_ids_set 里的本体
        #    - 由共享属性生成的属性（is_shared_created=1）直接删除
        #    - 手工同名属性（is_shared_created=0）：默认仅解除引用；若用户在 delete_manual_ids 中显式选择则一并删除
        detached_rows = (await db.execute(
            select(OntologyAttribute).where(
                OntologyAttribute.shared_property_id == prop_id,
                OntologyAttribute.ontology_id.notin_(list(ontology_ids_set) or ["__none__"]),
            )
        )).scalars().all()
        for attr in detached_rows:
            if attr.is_shared_created or attr.ontology_id in delete_manual:
                await db.delete(attr)
            else:
                attr.shared_property_id = ""
            attr.updated_at = _now()
            detached += 1

        # 2) 处理新增/同步
        for ont_id in ontology_ids_set:
            ont = await db.get(Ontology, ont_id)
            if not ont:
                skipped += 1
                continue
            existing = (await db.execute(
                select(OntologyAttribute).where(
                    OntologyAttribute.ontology_id == ont_id,
                    OntologyAttribute.name == sp.name,
                )
            )).scalar_one_or_none()
            if existing:
                if existing.shared_property_id == sp.id:
                    # 已经是该共享属性的引用，无需重复处理
                    skipped += 1
                    continue
                if overwrite:
                    # 同步已有同名属性并绑定引用
                    existing.data_type = sp.data_type
                    existing.is_required = sp.is_required
                    existing.default_value = sp.default_value
                    existing.code = existing.code or sp.code
                    existing.shared_property_id = sp.id
                    existing.updated_at = _now()
                    updated += 1
                    continue
                # 本体已有同名属性但未勾选「同步已有」：仅绑定引用，保留原属性定义
                existing.shared_property_id = sp.id
                existing.updated_at = _now()
                bound += 1
                continue
            max_order = (await db.execute(
                select(func.coalesce(func.max(OntologyAttribute.sort_order), 0))
                .where(OntologyAttribute.ontology_id == ont_id)
            )).scalar() or 0
            db.add(OntologyAttribute(
                ontology_id=ont_id,
                name=sp.name,
                code=sp.code,
                data_type=sp.data_type,
                description=sp.description or "",
                is_required=sp.is_required,
                default_value=sp.default_value,
                sort_order=int(max_order) + 1,
                is_edit_only=0,
                shared_property_id=sp.id,
                is_shared_created=1,
            ))
            created += 1
        await db.commit()
        return {"created": created, "updated": updated, "skipped": skipped, "detached": detached, "bound": bound}

    @staticmethod
    async def preview_apply(db: AsyncSession, prop_id: str, ontology_ids: list[str]) -> list[dict]:
        """挂载预览：返回拟挂载本体中已存在同名属性（手工重复）的本体列表。"""
        sp = await db.get(OntologySharedProperty, prop_id)
        if not sp:
            raise ValueError("共享属性不存在")
        ids = list(ontology_ids or [])
        if not ids:
            return []
        rows = (await db.execute(
            select(OntologyAttribute).where(
                OntologyAttribute.ontology_id.in_(ids),
                OntologyAttribute.name == sp.name,
            )
        )).scalars().all()
        # 已绑定到本共享属性的不算重复（只是已挂载）
        dup_ont_ids = {a.ontology_id for a in rows if a.shared_property_id != prop_id}
        onts = (await db.execute(
            select(Ontology).where(Ontology.id.in_(ids))
        )).scalars().all()
        name_map = {o.id: o.name for o in onts}
        result = []
        for oid in ids:
            if oid in dup_ont_ids:
                result.append({"ontology_id": oid, "ontology_name": name_map.get(oid, oid)})
        return result

    @staticmethod
    async def preview_detach(db: AsyncSession, prop_id: str, detach_ids: list[str]) -> list[dict]:
        """取消挂载预览：返回拟取消挂载本体的属性来源（生成/手工），供用户选择删除或保留。"""
        sp = await db.get(OntologySharedProperty, prop_id)
        if not sp:
            raise ValueError("共享属性不存在")
        ids = list(detach_ids or [])
        if not ids:
            return []
        rows = (await db.execute(
            select(OntologyAttribute).where(
                OntologyAttribute.shared_property_id == prop_id,
                OntologyAttribute.ontology_id.in_(ids),
            )
        )).scalars().all()
        flag_map = {a.ontology_id: bool(a.is_shared_created) for a in rows}
        onts = (await db.execute(
            select(Ontology).where(Ontology.id.in_(ids))
        )).scalars().all()
        name_map = {o.id: o.name for o in onts}
        return [
            {
                "ontology_id": oid,
                "ontology_name": name_map.get(oid, oid),
                "is_shared_created": flag_map.get(oid, False),
            }
            for oid in ids
        ]


# ───────────────────────── 本体接口 ─────────────────────────


def _iface_to_dict(i: OntologyInterface, property_count: int = 0,
                   implementation_count: int = 0) -> dict:
    return {
        "id": i.id,
        "category_id": i.category_id,
        "name": i.name,
        "code": i.code,
        "description": i.description or "",
        "icon": i.icon or "",
        "extends": _load_json(i.extends, []),
        "interface_kind": i.interface_kind or "functional",
        "is_system": bool(i.is_system),
        "property_count": property_count,
        "implementation_count": implementation_count,
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    }


def _iface_prop_to_dict(p: OntologyInterfaceProperty) -> dict:
    return {
        "id": p.id,
        "interface_id": p.interface_id,
        "name": p.name,
        "code": p.code,
        "data_type": p.data_type,
        "description": p.description or "",
        "is_required": bool(p.is_required),
        "default_value": p.default_value,
        "enum_values": _load_json(p.enum_values, None),
        "shared_property_id": p.shared_property_id or "",
        "sort_order": p.sort_order,
    }


def _iface_link_to_dict(l: OntologyInterfaceLink) -> dict:
    return {
        "id": l.id,
        "interface_id": l.interface_id,
        "name": l.name,
        "code": l.code,
        "target_interface_id": l.target_interface_id,
        "target_ontology_id": l.target_ontology_id or "",
        "cardinality": l.cardinality,
        "is_required": bool(l.is_required),
    }


class OntologyInterfaceService:
    """接口：共享属性/链接契约 → 多态查询与统一交互。"""

    # ── 列表 / 详情 ──

    @staticmethod
    async def list_interfaces(db: AsyncSession, category_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyInterface)
            .where(OntologyInterface.category_id == category_id)
            .order_by(OntologyInterface.created_at)
        )).scalars().all()
        if not rows:
            return []
        ids = [r.id for r in rows]
        prop_counts = dict((await db.execute(
            select(OntologyInterfaceProperty.interface_id, func.count())
            .where(OntologyInterfaceProperty.interface_id.in_(ids))
            .group_by(OntologyInterfaceProperty.interface_id)
        )).all())
        impl_counts = dict((await db.execute(
            select(OntologyInterfaceImplementation.interface_id, func.count())
            .where(OntologyInterfaceImplementation.interface_id.in_(ids))
            .group_by(OntologyInterfaceImplementation.interface_id)
        )).all())
        return [
            _iface_to_dict(r, int(prop_counts.get(r.id, 0)), int(impl_counts.get(r.id, 0)))
            for r in rows
        ]

    @staticmethod
    async def get_interface_detail(db: AsyncSession, interface_id: str) -> dict | None:
        iface = await db.get(OntologyInterface, interface_id)
        if not iface:
            return None
        props = (await db.execute(
            select(OntologyInterfaceProperty)
            .where(OntologyInterfaceProperty.interface_id == interface_id)
            .order_by(OntologyInterfaceProperty.sort_order)
        )).scalars().all()
        links = (await db.execute(
            select(OntologyInterfaceLink)
            .where(OntologyInterfaceLink.interface_id == interface_id)
        )).scalars().all()
        impls = (await db.execute(
            select(OntologyInterfaceImplementation)
            .where(OntologyInterfaceImplementation.interface_id == interface_id)
        )).scalars().all()

        ont_ids = [i.ontology_id for i in impls]
        names: dict[str, str] = {}
        if ont_ids:
            names = dict((await db.execute(
                select(Ontology.id, Ontology.name).where(Ontology.id.in_(ont_ids))
            )).all())

        return {
            **_iface_to_dict(iface, len(props), len(impls)),
            "properties": [_iface_prop_to_dict(p) for p in props],
            "links": [_iface_link_to_dict(l) for l in links],
            "implementations": [
                {
                    "id": i.id,
                    "ontology_id": i.ontology_id,
                    "ontology_name": names.get(i.ontology_id, ""),
                    "property_mapping": _load_json(i.property_mapping, {}),
                    "link_mapping": _load_json(i.link_mapping, {}),
                    "status": i.status,
                    "created_at": i.created_at,
                }
                for i in impls
            ],
        }

    # ── 接口 CRUD ──

    @staticmethod
    async def create_interface(db: AsyncSession, category_id: str, req) -> dict:
        code = (req.code or "").strip()
        if not code:
            raise ValueError("接口编码不能为空")
        dup = (await db.execute(
            select(OntologyInterface.id).where(
                OntologyInterface.category_id == category_id,
                OntologyInterface.code == code,
            )
        )).scalar_one_or_none()
        if dup:
            raise ValueError(f'接口编码 "{code}" 在本体类别中已存在')
        iface = OntologyInterface(
            category_id=category_id,
            name=req.name.strip(),
            code=code,
            description=(req.description or "").strip(),
            icon=(req.icon or "").strip(),
            extends=json.dumps(req.extends or [], ensure_ascii=False),
            interface_kind=req.interface_kind or "functional",
        )
        db.add(iface)
        await db.commit()
        await db.refresh(iface)
        return _iface_to_dict(iface)

    @staticmethod
    async def update_interface(db: AsyncSession, interface_id: str, req) -> dict | None:
        iface = await db.get(OntologyInterface, interface_id)
        if not iface:
            return None
        if req.name is not None:
            iface.name = req.name.strip()
        if req.code is not None:
            code = req.code.strip()
            if code and code != iface.code:
                dup = (await db.execute(
                    select(OntologyInterface.id).where(
                        OntologyInterface.category_id == iface.category_id,
                        OntologyInterface.code == code,
                        OntologyInterface.id != interface_id,
                    )
                )).scalar_one_or_none()
                if dup:
                    raise ValueError(f'接口编码 "{code}" 在本体类别中已存在')
            iface.code = code or iface.code
        for field in ("description", "icon", "interface_kind"):
            value = getattr(req, field, None)
            if value is not None:
                setattr(iface, field, value.strip() if isinstance(value, str) else value)
        if req.extends is not None:
            iface.extends = json.dumps(req.extends, ensure_ascii=False)
        iface.updated_at = _now()
        await db.commit()
        await db.refresh(iface)
        return _iface_to_dict(iface)

    @staticmethod
    async def delete_interface(db: AsyncSession, interface_id: str) -> bool:
        iface = await db.get(OntologyInterface, interface_id)
        if not iface:
            return False
        if iface.is_system:
            raise ValueError("系统内置接口不可删除")
        await OntologyInterfaceService._cascade_interface(db, interface_id)
        await db.delete(iface)
        await db.commit()
        return True

    @staticmethod
    async def _cascade_interface(db: AsyncSession, interface_id: str) -> None:
        await db.execute(
            delete(OntologyInterfaceProperty)
            .where(OntologyInterfaceProperty.interface_id == interface_id)
        )
        await db.execute(
            delete(OntologyInterfaceLink)
            .where(OntologyInterfaceLink.interface_id == interface_id)
        )
        await db.execute(
            delete(OntologyInterfaceImplementation)
            .where(OntologyInterfaceImplementation.interface_id == interface_id)
        )

    @staticmethod
    async def delete_for_category(db: AsyncSession, category_id: str) -> int:
        """删除本体类别时级联清理其下接口（返回删除的接口数）。"""
        ids = [row[0] for row in (await db.execute(
            select(OntologyInterface.id).where(OntologyInterface.category_id == category_id)
        )).all()]
        for iid in ids:
            await OntologyInterfaceService._cascade_interface(db, iid)
        if ids:
            await db.execute(
                delete(OntologyInterface).where(OntologyInterface.category_id == category_id)
            )
        return len(ids)

    @staticmethod
    async def delete_for_ontology(db: AsyncSession, ontology_id: str) -> int:
        """删除本体时解除其接口实现。"""
        res = await db.execute(
            delete(OntologyInterfaceImplementation)
            .where(OntologyInterfaceImplementation.ontology_id == ontology_id)
        )
        return int(res.rowcount or 0)

    # ── 接口属性 / 链接契约（整体替换）──

    @staticmethod
    async def set_properties(db: AsyncSession, interface_id: str, items: list) -> list[dict]:
        iface = await db.get(OntologyInterface, interface_id)
        if not iface:
            raise ValueError("接口不存在")
        codes = [(i.code or "").strip() for i in items if (i.code or "").strip()]
        if len(codes) != len(set(codes)):
            raise ValueError("接口属性编码重复")
        await db.execute(
            delete(OntologyInterfaceProperty)
            .where(OntologyInterfaceProperty.interface_id == interface_id)
        )
        for idx, item in enumerate(items):
            db.add(OntologyInterfaceProperty(
                interface_id=interface_id,
                name=item.name.strip(),
                code=item.code.strip(),
                data_type=item.data_type,
                description=(item.description or "").strip(),
                is_required=int(item.is_required),
                default_value=item.default_value,
                enum_values=json.dumps(item.enum_values, ensure_ascii=False) if item.enum_values else None,
                shared_property_id=(item.shared_property_id or "").strip(),
                sort_order=item.sort_order if item.sort_order is not None else idx,
            ))
        await db.commit()
        rows = (await db.execute(
            select(OntologyInterfaceProperty)
            .where(OntologyInterfaceProperty.interface_id == interface_id)
            .order_by(OntologyInterfaceProperty.sort_order)
        )).scalars().all()
        return [_iface_prop_to_dict(p) for p in rows]

    @staticmethod
    async def set_links(db: AsyncSession, interface_id: str, items: list) -> list[dict]:
        iface = await db.get(OntologyInterface, interface_id)
        if not iface:
            raise ValueError("接口不存在")
        await db.execute(
            delete(OntologyInterfaceLink)
            .where(OntologyInterfaceLink.interface_id == interface_id)
        )
        for item in items:
            db.add(OntologyInterfaceLink(
                interface_id=interface_id,
                name=item.name.strip(),
                code=item.code.strip(),
                target_interface_id=item.target_interface_id,
                target_ontology_id=(item.target_ontology_id or "").strip(),
                cardinality=item.cardinality or "ONE_TO_MANY",
                is_required=int(item.is_required),
            ))
        await db.commit()
        rows = (await db.execute(
            select(OntologyInterfaceLink)
            .where(OntologyInterfaceLink.interface_id == interface_id)
        )).scalars().all()
        return [_iface_link_to_dict(l) for l in rows]

    # ── 实现（implements）──

    @staticmethod
    async def _ontology_attributes(db: AsyncSession, ontology_id: str) -> dict[str, dict]:
        rows = (await db.execute(
            select(OntologyAttribute).where(OntologyAttribute.ontology_id == ontology_id)
        )).scalars().all()
        return {a.name: {"data_type": a.data_type, "id": a.id} for a in rows}

    @staticmethod
    async def validate_implementation(
        db: AsyncSession, interface_id: str, ontology_id: str,
        property_mapping: dict[str, str],
    ) -> dict:
        """校验本体→接口的属性映射：必填是否覆盖、属性是否存在、类型是否兼容。"""
        iface = await db.get(OntologyInterface, interface_id)
        if not iface:
            raise ValueError("接口不存在")
        ont = await db.get(Ontology, ontology_id)
        if not ont:
            raise ValueError("本体不存在")

        props = (await db.execute(
            select(OntologyInterfaceProperty)
            .where(OntologyInterfaceProperty.interface_id == interface_id)
        )).scalars().all()
        attrs = await OntologyInterfaceService._ontology_attributes(db, ontology_id)
        # 继承（extends）的属性契约也纳入校验
        extended = await OntologyInterfaceService._collect_extended_properties(db, iface)

        required_codes = {p.code for p in props if p.is_required}
        extended_required = {p["code"] for p in extended if p.get("is_required")}
        required_codes |= extended_required

        missing, unknown, mismatched = [], [], []
        for code in sorted(required_codes):
            if code not in property_mapping:
                missing.append(code)
        for code, attr_name in property_mapping.items():
            if attr_name not in attrs:
                unknown.append(code)
                continue
            spec = next((p for p in props if p.code == code), None)
            spec_dt = spec.data_type if spec else next(
                (p.get("data_type") for p in extended if p.get("code") == code), None)
            if spec_dt and attrs[attr_name]["data_type"] != spec_dt:
                mismatched.append({
                    "code": code,
                    "interface_type": spec_dt,
                    "ontology_type": attrs[attr_name]["data_type"],
                })
        return {
            "ok": not missing and not unknown and not mismatched,
            "missing": missing,
            "unknown": unknown,
            "mismatched": mismatched,
        }

    @staticmethod
    async def _collect_extended_properties(db: AsyncSession, iface: OntologyInterface) -> list[dict]:
        """收集接口 extends 链上的父接口属性（一层展开，防循环）。"""
        parent_ids = _load_json(iface.extends, []) or []
        if not parent_ids:
            return []
        rows = (await db.execute(
            select(OntologyInterfaceProperty)
            .where(OntologyInterfaceProperty.interface_id.in_(parent_ids))
        )).scalars().all()
        return [_iface_prop_to_dict(p) for p in rows]

    @staticmethod
    async def implement(db: AsyncSession, interface_id: str, req) -> dict:
        check = await OntologyInterfaceService.validate_implementation(
            db, interface_id, req.ontology_id, req.property_mapping or {},
        )
        status = "active" if check["ok"] else "partial"
        existing = (await db.execute(
            select(OntologyInterfaceImplementation).where(
                OntologyInterfaceImplementation.interface_id == interface_id,
                OntologyInterfaceImplementation.ontology_id == req.ontology_id,
            )
        )).scalar_one_or_none()
        if existing:
            existing.property_mapping = json.dumps(req.property_mapping or {}, ensure_ascii=False)
            existing.link_mapping = json.dumps(req.link_mapping or {}, ensure_ascii=False)
            existing.status = status
            impl = existing
        else:
            impl = OntologyInterfaceImplementation(
                interface_id=interface_id,
                ontology_id=req.ontology_id,
                property_mapping=json.dumps(req.property_mapping or {}, ensure_ascii=False),
                link_mapping=json.dumps(req.link_mapping or {}, ensure_ascii=False),
                status=status,
            )
            db.add(impl)
        await db.commit()
        await db.refresh(impl)
        ont = await db.get(Ontology, req.ontology_id)
        return {
            "id": impl.id,
            "interface_id": interface_id,
            "ontology_id": impl.ontology_id,
            "ontology_name": ont.name if ont else "",
            "property_mapping": _load_json(impl.property_mapping, {}),
            "link_mapping": _load_json(impl.link_mapping, {}),
            "status": impl.status,
            "validation": check,
        }

    @staticmethod
    async def remove_implementation(db: AsyncSession, interface_id: str, ontology_id: str) -> bool:
        res = await db.execute(
            delete(OntologyInterfaceImplementation).where(
                OntologyInterfaceImplementation.interface_id == interface_id,
                OntologyInterfaceImplementation.ontology_id == ontology_id,
            )
        )
        await db.commit()
        return bool(res.rowcount)

    @staticmethod
    async def list_ontology_interfaces(db: AsyncSession, ontology_id: str) -> list[dict]:
        """某本体实现的全部接口（含映射与校验状态）。"""
        impls = (await db.execute(
            select(OntologyInterfaceImplementation)
            .where(OntologyInterfaceImplementation.ontology_id == ontology_id)
        )).scalars().all()
        if not impls:
            return []
        iface_ids = [i.interface_id for i in impls]
        ifaces = {
            r.id: r for r in (await db.execute(
                select(OntologyInterface).where(OntologyInterface.id.in_(iface_ids))
            )).scalars().all()
        }
        return [
            {
                "interface_id": i.interface_id,
                "code": ifaces[i.interface_id].code if i.interface_id in ifaces else "",
                "name": ifaces[i.interface_id].name if i.interface_id in ifaces else "",
                "property_mapping": _load_json(i.property_mapping, {}),
                "link_mapping": _load_json(i.link_mapping, {}),
                "status": i.status,
            }
            for i in impls
        ]

    # ── 多态查询：按接口查对象 ──

    @staticmethod
    async def resolve_objects(
        db: AsyncSession, category_id: str, interface_code: str,
        q: str = "", ontology_id: str = "", prop_filters: dict | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict | None:
        """按接口查询对象：把各实现本体的本地属性投影为接口属性，返回统一结构。

        :param prop_filters: 按接口属性筛选，形如 ``{接口属性code: 关键字}``；
            每个接口属性会通过各实现的 ``property_mapping`` 反查成本体本地属性键再过滤
            （不同本体的本地键不同，按 (本体, 本地键) 组合做 OR，多个属性之间为 AND）。
        """
        iface = (await db.execute(
            select(OntologyInterface).where(
                OntologyInterface.category_id == category_id,
                OntologyInterface.code == interface_code,
            )
        )).scalar_one_or_none()
        if not iface:
            return None

        impls = (await db.execute(
            select(OntologyInterfaceImplementation)
            .where(OntologyInterfaceImplementation.interface_id == iface.id)
        )).scalars().all()
        if not impls:
            return {"interface": _iface_to_dict(iface), "total": 0, "items": []}

        mapping_by_ont = {i.ontology_id: _load_json(i.property_mapping, {}) for i in impls}
        ont_ids = list(mapping_by_ont.keys())
        names = dict((await db.execute(
            select(Ontology.id, Ontology.name).where(Ontology.id.in_(ont_ids))
        )).all())

        stmt = select(Entity).where(Entity.ontology_id.in_(ont_ids))
        if q:
            stmt = stmt.where(Entity.name.contains(q))
        if ontology_id and ontology_id in ont_ids:
            stmt = stmt.where(Entity.ontology_id == ontology_id)
        # 按接口属性筛选：接口属性 code → 各实现的本地属性键
        for code, kw in (prop_filters or {}).items():
            kw = str(kw or "").strip()
            if not kw:
                continue
            ors = []
            for oid, mapping in mapping_by_ont.items():
                local_key = mapping.get(code)
                if not local_key:
                    continue
                ors.append(and_(
                    Entity.ontology_id == oid,
                    Entity.properties.like(f'%"{local_key}": "%{kw}%'),
                ))
            # 属性存在但没有任何本体映射它 → 不可能匹配到任何对象
            stmt = stmt.where(or_(*ors) if ors else false())
        total = int((await db.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar() or 0)
        rows = (await db.execute(
            stmt.order_by(Entity.name).offset(offset).limit(limit)
        )).scalars().all()

        items = []
        for e in rows:
            raw_props = _load_json(e.properties, {}) or {}
            projected: dict[str, object] = {}
            for code, attr_name in mapping_by_ont.get(e.ontology_id, {}).items():
                projected[code] = raw_props.get(attr_name)
            items.append({
                "id": e.id,
                "name": e.name,
                "entity_type": e.entity_type,
                "ontology_id": e.ontology_id,
                "ontology_name": names.get(e.ontology_id, ""),
                "properties": projected,
            })
        return {
            "interface": _iface_to_dict(iface, len(impls), len(impls)),
            "total": total,
            "items": items,
        }
