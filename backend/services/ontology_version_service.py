"""S7（P1-4）版本 / 提案 / 影响分析 / 回滚。

- 版本快照：每次发布保存类别定义层的不可变 JSON 快照（不含实体）。
- 回滚：把快照按 id 还原（upsert 已有、删除快照外、重建缺失），从而保留实体引用。
- 影响分析（Usages）：删除/改名前预览影响面（约束、接口实现、动作、函数、派生属性、视图、实体、KB 绑定）。
- 提案：复用 ontology_suggestions（proposal_type='change'），合并时自动打新版本。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Entity,
    KbOntologyBinding,
    Ontology,
    OntologyAttribute,
    OntologyDerivedProperty,
    OntologyFunction,
    OntologyInterface,
    OntologyInterfaceImplementation,
    OntologyInterfaceLink,
    OntologyInterfaceProperty,
    OntologyObjectView,
    OntologyRelation,
    OntologyRelationConstraint,
    OntologyService,
    OntologySuggestion,
    OntologyVersion,
)

# 快照涉及的表（按 category 维度）：table -> (model, scope_col)
# scope_col 可为 'category_id'（按类别）或 'ontology'（按类别下全部本体）。
_SNAPSHOT_TABLES = [
    ("ontologies", Ontology, "category_id"),
    ("ontology_attributes", OntologyAttribute, "ontology"),
    ("ontology_relations", OntologyRelation, "category_id"),
    ("ontology_relation_constraints", OntologyRelationConstraint, "category_id"),
    ("ontology_interfaces", OntologyInterface, "category_id"),
    ("ontology_interface_properties", OntologyInterfaceProperty, "interface"),
    ("ontology_interface_links", OntologyInterfaceLink, "interface"),
    ("ontology_interface_implementations", OntologyInterfaceImplementation, "interface"),
    ("ontology_object_views", OntologyObjectView, "category_id"),
    ("ontology_services", OntologyService, "ontology"),
    ("ontology_functions", OntologyFunction, "ontology_or_category"),
    ("ontology_derived_properties", OntologyDerivedProperty, "ontology"),
]


def _now() -> str:
    return datetime.now().isoformat()


def _row_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


async def _category_ontology_ids(db: AsyncSession, category_id: str) -> list[str]:
    rows = (await db.execute(
        select(Ontology.id).where(Ontology.category_id == category_id)
    )).scalars().all()
    return list(rows)


async def _build_snapshot(db: AsyncSession, category_id: str) -> dict:
    ont_ids = await _category_ontology_ids(db, category_id)
    ont_id_set = set(ont_ids)
    interfaces = (await db.execute(
        select(OntologyInterface).where(OntologyInterface.category_id == category_id)
    )).scalars().all()
    iface_ids = {i.id for i in interfaces}

    snapshot: dict[str, list[dict]] = {}
    snapshot["ontologies"] = [_row_to_dict(o) for o in (await db.execute(
        select(Ontology).where(Ontology.category_id == category_id))).scalars().all()]
    snapshot["ontology_attributes"] = [_row_to_dict(a) for a in (await db.execute(
        select(OntologyAttribute).where(OntologyAttribute.ontology_id.in_(ont_id_set or ["__none__"]))
    )).scalars().all()] if ont_ids else []
    snapshot["ontology_relations"] = [_row_to_dict(r) for r in (await db.execute(
        select(OntologyRelation).where(OntologyRelation.category_id == category_id))).scalars().all()]
    snapshot["ontology_relation_constraints"] = [_row_to_dict(c) for c in (await db.execute(
        select(OntologyRelationConstraint).where(OntologyRelationConstraint.category_id == category_id)
    )).scalars().all()]
    snapshot["ontology_interfaces"] = [_row_to_dict(i) for i in interfaces]
    snapshot["ontology_interface_properties"] = [_row_to_dict(p) for p in (await db.execute(
        select(OntologyInterfaceProperty).where(OntologyInterfaceProperty.interface_id.in_(iface_ids or ["__none__"]))
    )).scalars().all()] if iface_ids else []
    snapshot["ontology_interface_links"] = [_row_to_dict(l) for l in (await db.execute(
        select(OntologyInterfaceLink).where(OntologyInterfaceLink.interface_id.in_(iface_ids or ["__none__"]))
    )).scalars().all()] if iface_ids else []
    snapshot["ontology_interface_implementations"] = [_row_to_dict(m) for m in (await db.execute(
        select(OntologyInterfaceImplementation).where(
            OntologyInterfaceImplementation.interface_id.in_(iface_ids or ["__none__"]))
    )).scalars().all()] if iface_ids else []
    snapshot["ontology_object_views"] = [_row_to_dict(v) for v in (await db.execute(
        select(OntologyObjectView).where(OntologyObjectView.category_id == category_id))).scalars().all()]
    snapshot["ontology_services"] = [_row_to_dict(s) for s in (await db.execute(
        select(OntologyService).where(
            OntologyService.owner_type == "ontology",
            OntologyService.ontology_id.in_(ont_id_set or ["__none__"]))
    )).scalars().all()] if ont_ids else []
    funcs = (await db.execute(
        select(OntologyFunction).where(
            (OntologyFunction.category_id == category_id) |
            (OntologyFunction.ontology_id.in_(ont_id_set or ["__none__"])))
    )).scalars().all()
    snapshot["ontology_functions"] = [_row_to_dict(f) for f in funcs]
    snapshot["ontology_derived_properties"] = [_row_to_dict(d) for d in (await db.execute(
        select(OntologyDerivedProperty).where(OntologyDerivedProperty.ontology_id.in_(ont_id_set or ["__none__"]))
    )).scalars().all()] if ont_ids else []
    return snapshot


class OntologyVersionService:
    @staticmethod
    async def next_version_no(db: AsyncSession, category_id: str) -> int:
        row = (await db.execute(
            select(func.max(OntologyVersion.version_no)).where(OntologyVersion.category_id == category_id)
        )).scalar()
        return (int(row) if row else 0) + 1

    @staticmethod
    async def create_version(
        db: AsyncSession, category_id: str, note: str = "", source: str = "manual",
        created_by: str = "",
    ) -> dict:
        snapshot = await _build_snapshot(db, category_id)
        version_no = await OntologyVersionService.next_version_no(db, category_id)
        v = OntologyVersion(
            category_id=category_id, version_no=version_no,
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            source=source, note=(note or "").strip(), created_by=created_by or "",
            created_at=_now(),
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return OntologyVersionService._serialize(v)

    @staticmethod
    async def list_versions(db: AsyncSession, category_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyVersion)
            .where(OntologyVersion.category_id == category_id)
            .order_by(OntologyVersion.version_no.desc())
        )).scalars().all()
        return [OntologyVersionService._serialize(v) for v in rows]

    @staticmethod
    async def get_version(db: AsyncSession, version_id: str) -> dict | None:
        v = await db.get(OntologyVersion, version_id)
        return OntologyVersionService._serialize(v) if v else None

    @staticmethod
    async def rollback(db: AsyncSession, version_id: str) -> tuple[dict | None, str | None]:
        v = await db.get(OntologyVersion, version_id)
        if not v:
            return None, "版本不存在"
        try:
            snapshot = json.loads(v.snapshot) if v.snapshot else {}
        except Exception:
            return None, "快照解析失败"
        category_id = v.category_id
        ont_ids = set(await _category_ontology_ids(db, category_id))

        for table_name, model, scope in _SNAPSHOT_TABLES:
            rows = snapshot.get(table_name, [])
            snap_ids = {r["id"] for r in rows if "id" in r}
            # 1) 删除快照外、当前属于该类别的行
            stmt = select(model)
            if scope == "category_id":
                stmt = stmt.where(getattr(model, "category_id") == category_id)
            elif scope == "ontology":
                # 回滚只动本体级服务，避免误删实体自定义服务
                if model is OntologyService:
                    stmt = stmt.where(OntologyService.owner_type == "ontology")
                stmt = stmt.where(getattr(model, "ontology_id").in_(ont_ids or ["__none__"]))
            elif scope == "interface":
                # 接口级表：先取类别下的接口 id
                iface_ids = {i.id for i in (await db.execute(
                    select(OntologyInterface).where(OntologyInterface.category_id == category_id)
                )).scalars().all()}
                stmt = stmt.where(getattr(model, "interface_id").in_(iface_ids or ["__none__"]))
            elif scope == "ontology_or_category":
                stmt = stmt.where(
                    (getattr(model, "category_id") == category_id) |
                    (getattr(model, "ontology_id").in_(ont_ids or ["__none__"]))
                )
            existing = (await db.execute(stmt)).scalars().all()
            for e in existing:
                if e.id not in snap_ids:
                    await db.delete(e)
            # 2) upsert 快照行
            for r in rows:
                data = dict(r)
                data.pop("created_at", None)  # 保留原始 created_at？这里允许覆盖
                inst = model(**data)
                await db.merge(inst)
        await db.commit()
        return {"version_id": v.id, "version_no": v.version_no, "restored": True}, None

    @staticmethod
    async def analyze_usages(db: AsyncSession, category_id: str, ontology_id: str) -> dict:
        constraint_rows = (await db.execute(
            select(OntologyRelationConstraint.id).where(
                (OntologyRelationConstraint.source_ontology_id == ontology_id) |
                (OntologyRelationConstraint.target_ontology_id == ontology_id)
            )
        )).scalars().all()
        impl_rows = (await db.execute(
            select(OntologyInterfaceImplementation, OntologyInterface.name)
            .join(OntologyInterface, OntologyInterface.id == OntologyInterfaceImplementation.interface_id)
            .where(OntologyInterfaceImplementation.ontology_id == ontology_id)
        )).all()
        svc_rows = (await db.execute(
            select(OntologyService.id, OntologyService.name).where(
                OntologyService.owner_type == "ontology",
                OntologyService.ontology_id == ontology_id,
            )
        )).all()
        fn_rows = (await db.execute(
            select(OntologyFunction.id, OntologyFunction.name).where(
                OntologyFunction.ontology_id == ontology_id)
        )).all()
        dp_rows = (await db.execute(
            select(OntologyDerivedProperty.id, OntologyDerivedProperty.name).where(
                OntologyDerivedProperty.ontology_id == ontology_id)
        )).all()
        view_rows = (await db.execute(
            select(OntologyObjectView.id, OntologyObjectView.name).where(
                OntologyObjectView.ontology_id == ontology_id)
        )).all()
        entity_count = (await db.execute(
            select(func.count(Entity.id)).where(Entity.ontology_id == ontology_id)
        )).scalar() or 0
        kb_rows = (await db.execute(
            select(KbOntologyBinding.kb_id).where(KbOntologyBinding.category_id == category_id)
        )).scalars().all()

        return {
            "ontology_id": ontology_id,
            "constraint_count": len(constraint_rows),
            "constraints": len(constraint_rows),
            "interfaces": [{"interface_id": i.id, "interface_name": name} for i, name in impl_rows],
            "services": [{"id": s, "name": n} for s, n in svc_rows],
            "functions": [{"id": f, "name": n} for f, n in fn_rows],
            "derived_properties": [{"id": d, "name": n} for d, n in dp_rows],
            "object_views": [{"id": v, "name": n} for v, n in view_rows],
            "entity_count": int(entity_count),
            "kb_bindings": list(kb_rows),
        }

    @staticmethod
    async def upgrade_suggestion(
        db: AsyncSession, suggestion_id: str, note: str = "", reviewers: str = "",
        base_version: int | None = None, diff: list | None = None,
    ) -> tuple[dict | None, str | None]:
        """把一条本体建议升级为变更提案（proposal_type='change'）。"""
        s = await db.get(OntologySuggestion, suggestion_id)
        if not s:
            return None, "建议不存在"
        s.proposal_type = "change"
        s.base_version = base_version or 0
        s.reviewers = reviewers or ""
        s.diff = json.dumps(diff, ensure_ascii=False) if diff is not None else s.diff
        if note:
            s.review_notes = note
        if s.status in ("generating", "ready") or not s.status:
            s.status = "pending"  # 待审查
        await db.commit()
        await db.refresh(s)
        from services.ontology_service import OntologySuggestionService
        return OntologySuggestionService._serialize_suggestion(s) if hasattr(OntologySuggestionService, "_serialize_suggestion") else {
            "id": s.id, "proposal_type": s.proposal_type, "status": s.status,
        }, None

    @staticmethod
    async def merge_proposal(
        db: AsyncSession, suggestion_id: str, note: str = "", created_by: str = ""
    ) -> tuple[dict | None, str | None]:
        """合并变更提案：应用 suggestion_data 到本体类别，并自动打新版本。"""
        from services.ontology_service import OntologySuggestionService

        s = await db.get(OntologySuggestion, suggestion_id)
        if not s:
            return None, "提案不存在"
        # 复用既有审核通过逻辑，把 diff/suggestion_data 落地到本体类别
        try:
            await OntologySuggestionService.approve_suggestion(db, suggestion_id)
        except ValueError as e:
            return None, str(e)
        # 确定目标类别：优先 KB 绑定类别
        binding = (await db.execute(
            select(KbOntologyBinding).where(KbOntologyBinding.kb_id == s.kb_id)
        )).scalar_one_or_none()
        target_category_id = binding.category_id if binding else None
        merged_version_id = ""
        if target_category_id:
            v = await OntologyVersionService.create_version(
                db, target_category_id, note=(note or "提案合并自动版本"), source="proposal", created_by=created_by or ""
            )
            merged_version_id = v["id"]
            s.merged_version_id = merged_version_id
            await db.commit()
        return {
            "id": s.id,
            "status": s.status,
            "merged_version_id": merged_version_id,
        }, None

    @staticmethod
    def _serialize(v: OntologyVersion) -> dict:
        try:
            snap = json.loads(v.snapshot) if v.snapshot else {}
        except Exception:
            snap = {}
        counts = {k: len(val) for k, val in snap.items() if isinstance(val, list)}
        return {
            "id": v.id,
            "category_id": v.category_id,
            "version_no": v.version_no,
            "source": v.source,
            "note": v.note or "",
            "created_by": v.created_by or "",
            "merged_suggestion_id": v.merged_suggestion_id or "",
            "created_at": v.created_at,
            "counts": counts,
        }
