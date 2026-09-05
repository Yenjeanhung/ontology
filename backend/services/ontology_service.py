from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Entity,
    KbOntologyBinding,
    Ontology,
    OntologyAttribute,
    OntologyAttributeTemplate,
    OntologyCategory,
    OntologyRelation,
    OntologyRelationConstraint,
    OntologySuggestion,
    OntologyTemplateAttribute,
    OntologyTemplateBinding,
)
from models import OntologyService as OntologyServiceModel  # 本体服务（动作）ORM，避免与业务类同名冲突
import json
import logging

from services.ontology_action_service import OntologyServiceService

logger = logging.getLogger(__name__)


# ---------- 辅助序列化 ----------

def _serialize_attribute(attr, source: str = "own") -> dict:
    return {
        "id": attr.id,
        "name": attr.name,
        "code": attr.code,
        "data_type": attr.data_type,
        "description": attr.description or "",
        "is_required": bool(attr.is_required),
        "default_value": attr.default_value,
        "sort_order": attr.sort_order,
        "source": source,
    }


async def _serialize_constraint(db: AsyncSession, c: OntologyRelationConstraint) -> dict:
    src = await db.execute(select(Ontology.name).where(Ontology.id == c.source_ontology_id))
    tgt = await db.execute(select(Ontology.name).where(Ontology.id == c.target_ontology_id))
    rel = await db.execute(select(OntologyRelation.name).where(OntologyRelation.id == c.relation_id))
    return {
        "id": c.id,
        "category_id": c.category_id,
        "source_ontology_id": c.source_ontology_id,
        "source_ontology_name": src.scalar_one_or_none(),
        "relation_id": c.relation_id,
        "relation_name": rel.scalar_one_or_none(),
        "target_ontology_id": c.target_ontology_id,
        "target_ontology_name": tgt.scalar_one_or_none(),
        "description": c.description or "",
        "created_at": c.created_at,
    }


class OntologyService:
    # ===== 模块一：本体类别 CRUD =====

    @staticmethod
    async def list_categories(db: AsyncSession, q: str = "") -> list[dict]:
        stmt = select(OntologyCategory).order_by(OntologyCategory.created_at.desc())
        if q:
            stmt = stmt.where(OntologyCategory.name.contains(q))
        result = await db.execute(stmt)
        cats = result.scalars().all()
        out = []
        for cat in cats:
            cnt = await db.execute(
                select(Ontology).where(Ontology.category_id == cat.id)
            )
            out.append({
                "id": cat.id,
                "name": cat.name,
                "description": cat.description or "",
                "is_system": bool(cat.is_system),
                "ontology_count": len(cnt.scalars().all()),
                "created_at": cat.created_at,
            })
        return out

    @staticmethod
    async def get_category_detail(db: AsyncSession, category_id: str) -> dict | None:
        result = await db.execute(
            select(OntologyCategory).where(OntologyCategory.id == category_id)
        )
        cat = result.scalar_one_or_none()
        if not cat:
            return None

        # ontologies + 属性 + 模板绑定
        ontologies_result = await db.execute(
            select(Ontology)
            .where(Ontology.category_id == category_id)
            .order_by(Ontology.sort_order, Ontology.created_at)
        )
        ontology_rows = ontologies_result.scalars().all()
        ont_ids = [ont.id for ont in ontology_rows]
        entity_counts = {}
        if ont_ids:
            counts_result = await db.execute(
                select(Entity.ontology_id, func.count())
                .where(Entity.ontology_id.in_(ont_ids))
                .group_by(Entity.ontology_id)
            )
            entity_counts = {row[0]: row[1] for row in counts_result}

        ontology_list = []
        for ont in ontology_rows:
            attrs_result = await db.execute(
                select(OntologyAttribute)
                .where(OntologyAttribute.ontology_id == ont.id)
                .order_by(OntologyAttribute.sort_order)
            )
            attrs = [_serialize_attribute(a) for a in attrs_result.scalars().all()]
            bindings_result = await db.execute(
                select(OntologyTemplateBinding)
                .where(OntologyTemplateBinding.ontology_id == ont.id)
                .order_by(OntologyTemplateBinding.sort_order)
            )
            template_ids = [b.template_id for b in bindings_result.scalars().all()]
            ontology_list.append({
                "id": ont.id,
                "name": ont.name,
                "description": ont.description or "",
                "color": ont.color,
                "sort_order": ont.sort_order,
                "attributes": attrs,
                "template_ids": template_ids,
                "entity_count": int(entity_counts.get(ont.id, 0)),
                "created_at": ont.created_at,
            })

        # 关系字典
        relations_result = await db.execute(
            select(OntologyRelation)
            .where(OntologyRelation.category_id == category_id)
            .order_by(OntologyRelation.created_at)
        )
        relations = [
            {"id": r.id, "name": r.name, "description": r.description or "", "created_at": r.created_at}
            for r in relations_result.scalars().all()
        ]

        # 三元组
        constraints_result = await db.execute(
            select(OntologyRelationConstraint)
            .where(OntologyRelationConstraint.category_id == category_id)
            .order_by(OntologyRelationConstraint.created_at)
        )
        constraints = [
            await _serialize_constraint(db, c)
            for c in constraints_result.scalars().all()
        ]

        # 绑定的知识库
        kb_result = await db.execute(
            select(KbOntologyBinding).where(KbOntologyBinding.category_id == category_id)
        )
        kb_bindings = [
            {"kb_id": b.kb_id, "created_at": b.created_at}
            for b in kb_result.scalars().all()
        ]

        return {
            "id": cat.id,
            "name": cat.name,
            "description": cat.description or "",
            "is_system": bool(cat.is_system),
            "created_at": cat.created_at,
            "entity_count": sum(int(entity_counts.get(ont.id, 0)) for ont in ontology_rows),
            "ontologies": ontology_list,
            "relations": relations,
            "constraints": constraints,
            "kb_bindings": kb_bindings,
        }

    @staticmethod
    async def create_category(db: AsyncSession, name: str, description: str = "") -> dict:
        cat = OntologyCategory(name=name.strip(), description=(description or "").strip())
        db.add(cat)
        await db.commit()
        await db.refresh(cat)
        return {"id": cat.id, "name": cat.name, "description": cat.description}

    @staticmethod
    async def update_category(
        db: AsyncSession, category_id: str, name: str | None, description: str | None
    ) -> dict | None:
        result = await db.execute(
            select(OntologyCategory).where(OntologyCategory.id == category_id)
        )
        cat = result.scalar_one_or_none()
        if not cat:
            return None
        if name is not None:
            cat.name = name.strip()
        if description is not None:
            cat.description = description.strip()
        cat.updated_at = datetime.now().isoformat()
        await db.commit()
        return {"id": cat.id, "name": cat.name, "description": cat.description}

    @staticmethod
    async def delete_category(db: AsyncSession, category_id: str) -> bool:
        result = await db.execute(
            select(OntologyCategory).where(OntologyCategory.id == category_id)
        )
        cat = result.scalar_one_or_none()
        if not cat:
            return False

        # 级联删除：ontologies 的属性与模板绑定
        ont_ids_result = await db.execute(
            select(Ontology.id).where(Ontology.category_id == category_id)
        )
        ont_ids = [row[0] for row in ont_ids_result]
        if ont_ids:
            await db.execute(
                delete(OntologyAttribute).where(OntologyAttribute.ontology_id.in_(ont_ids))
            )
            await db.execute(
                delete(OntologyTemplateBinding).where(OntologyTemplateBinding.ontology_id.in_(ont_ids))
            )
            # 级联：本体服务（动作）
            for oid in ont_ids:
                await OntologyServiceService.delete_for_ontology(db, oid)
        # 三元组、关系字典、本体、绑定
        await db.execute(
            delete(OntologyRelationConstraint).where(OntologyRelationConstraint.category_id == category_id)
        )
        await db.execute(
            delete(OntologyRelation).where(OntologyRelation.category_id == category_id)
        )
        await db.execute(delete(Ontology).where(Ontology.category_id == category_id))
        await db.execute(
            delete(KbOntologyBinding).where(KbOntologyBinding.category_id == category_id)
        )
        await db.delete(cat)
        await db.commit()
        return True

    # ===== 模块二：本体 CRUD =====

    @staticmethod
    async def list_ontologies(db: AsyncSession, category_id: str) -> list[dict]:
        """本体轻量列表：仅返回基础字段与各类计数，不加载完整属性/模板，用于表格展示。"""
        result = await db.execute(
            select(Ontology)
            .where(Ontology.category_id == category_id)
            .order_by(Ontology.sort_order, Ontology.created_at)
        )
        rows = result.scalars().all()
        ont_ids = [o.id for o in rows]
        if not ont_ids:
            return []

        async def _counts(model):
            res = await db.execute(
                select(model.ontology_id, func.count())
                .where(model.ontology_id.in_(ont_ids))
                .group_by(model.ontology_id)
            )
            return {row[0]: int(row[1]) for row in res.all()}

        attr_counts = await _counts(OntologyAttribute)
        tpl_counts = await _counts(OntologyTemplateBinding)
        entity_counts = await _counts(Entity)
        svc_counts = await _counts(OntologyServiceModel)

        return [
            {
                "id": ont.id,
                "category_id": ont.category_id,
                "name": ont.name,
                "description": ont.description or "",
                "color": ont.color,
                "sort_order": ont.sort_order,
                "attribute_count": attr_counts.get(ont.id, 0),
                "template_count": tpl_counts.get(ont.id, 0),
                "service_count": svc_counts.get(ont.id, 0),
                "entity_count": entity_counts.get(ont.id, 0),
                "created_at": ont.created_at,
            }
            for ont in rows
        ]

    @staticmethod
    async def get_ontology_detail(db: AsyncSession, category_id: str, ontology_id: str) -> dict | None:
        """单个本体详情：完整自有属性 + 模板绑定 + 计数，点击行时按需加载。"""
        result = await db.execute(
            select(Ontology).where(
                Ontology.id == ontology_id, Ontology.category_id == category_id
            )
        )
        ont = result.scalar_one_or_none()
        if not ont:
            return None

        attrs_result = await db.execute(
            select(OntologyAttribute)
            .where(OntologyAttribute.ontology_id == ont.id)
            .order_by(OntologyAttribute.sort_order)
        )
        attrs = [_serialize_attribute(a) for a in attrs_result.scalars().all()]

        bindings_result = await db.execute(
            select(OntologyTemplateBinding)
            .where(OntologyTemplateBinding.ontology_id == ont.id)
            .order_by(OntologyTemplateBinding.sort_order)
        )
        template_ids = [b.template_id for b in bindings_result.scalars().all()]

        entity_count = int(
            (await db.execute(select(func.count()).where(Entity.ontology_id == ont.id))).scalar() or 0
        )
        service_count = int(
            (await db.execute(
                select(func.count()).where(OntologyServiceModel.ontology_id == ont.id)
            )).scalar() or 0
        )

        return {
            "id": ont.id,
            "category_id": ont.category_id,
            "name": ont.name,
            "description": ont.description or "",
            "color": ont.color,
            "sort_order": ont.sort_order,
            "attributes": attrs,
            "template_ids": template_ids,
            "attribute_count": len(attrs),
            "template_count": len(template_ids),
            "service_count": service_count,
            "entity_count": entity_count,
            "created_at": ont.created_at,
        }

    @staticmethod
    async def create_ontology(
        db: AsyncSession, category_id: str, name: str,
        description: str = "", color: str | None = None, sort_order: int = 0,
    ) -> dict:
        ont = Ontology(
            category_id=category_id, name=name.strip(),
            description=(description or "").strip(), color=color, sort_order=sort_order,
        )
        db.add(ont)
        await db.commit()
        await db.refresh(ont)
        return {"id": ont.id, "category_id": ont.category_id, "name": ont.name,
                "description": ont.description, "color": ont.color, "sort_order": ont.sort_order}

    @staticmethod
    async def update_ontology(
        db: AsyncSession, ontology_id: str, name: str | None = None,
        description: str | None = None, color: str | None = None, sort_order: int | None = None,
    ) -> dict | None:
        result = await db.execute(select(Ontology).where(Ontology.id == ontology_id))
        ont = result.scalar_one_or_none()
        if not ont:
            return None
        if name is not None:
            ont.name = name.strip()
        if description is not None:
            ont.description = description.strip()
        if color is not None:
            ont.color = color
        if sort_order is not None:
            ont.sort_order = sort_order
        ont.updated_at = datetime.now().isoformat()
        await db.commit()
        return {"id": ont.id, "name": ont.name, "description": ont.description,
                "color": ont.color, "sort_order": ont.sort_order}

    @staticmethod
    async def delete_ontology(db: AsyncSession, ontology_id: str) -> bool:
        result = await db.execute(select(Ontology).where(Ontology.id == ontology_id))
        ont = result.scalar_one_or_none()
        if not ont:
            return False
        # 级联：属性、模板绑定、本体服务、引用它的三元组
        await db.execute(
            delete(OntologyAttribute).where(OntologyAttribute.ontology_id == ontology_id)
        )
        await db.execute(
            delete(OntologyTemplateBinding).where(OntologyTemplateBinding.ontology_id == ontology_id)
        )
        await OntologyServiceService.delete_for_ontology(db, ontology_id)
        await db.execute(
            delete(OntologyRelationConstraint).where(
                (OntologyRelationConstraint.source_ontology_id == ontology_id)
                | (OntologyRelationConstraint.target_ontology_id == ontology_id)
            )
        )
        await db.delete(ont)
        await db.commit()
        return True

    @staticmethod
    async def batch_create_ontologies(db: AsyncSession, category_id: str, items: list) -> list[dict]:
        out = []
        for idx, item in enumerate(items):
            ont = Ontology(
                category_id=category_id, name=item.name.strip(),
                description=(item.description or "").strip(),
                color=item.color, sort_order=item.sort_order if item.sort_order is not None else idx,
            )
            db.add(ont)
            await db.flush()
            out.append({"id": ont.id, "name": ont.name})
        await db.commit()
        return out

    # ===== 本体属性 CRUD =====

    @staticmethod
    async def list_attributes(db: AsyncSession, ontology_id: str) -> list[dict]:
        result = await db.execute(
            select(OntologyAttribute)
            .where(OntologyAttribute.ontology_id == ontology_id)
            .order_by(OntologyAttribute.sort_order)
        )
        return [_serialize_attribute(a) for a in result.scalars().all()]

    @staticmethod
    async def create_attribute(db: AsyncSession, ontology_id: str, req) -> dict:
        # 校验编码唯一性
        code = (req.code or "").strip() or None
        if code:
            existing = await db.execute(
                select(OntologyAttribute.id).where(
                    OntologyAttribute.ontology_id == ontology_id,
                    OntologyAttribute.code == code,
                )
            )
            if existing.scalar_one_or_none():
                raise ValueError(f'编码 "{code}" 在该本体中已存在')
        attr = OntologyAttribute(
            ontology_id=ontology_id, name=req.name.strip(), code=code,
            data_type=req.data_type, description=(req.description or "").strip(),
            is_required=int(req.is_required), default_value=req.default_value,
            sort_order=req.sort_order,
        )
        db.add(attr)
        await db.commit()
        await db.refresh(attr)
        return _serialize_attribute(attr)

    @staticmethod
    async def update_attribute(db: AsyncSession, attr_id: str, req) -> dict | None:
        result = await db.execute(select(OntologyAttribute).where(OntologyAttribute.id == attr_id))
        attr = result.scalar_one_or_none()
        if not attr:
            return None
        if req.name is not None:
            attr.name = req.name.strip()
        if req.code is not None:
            code = req.code.strip() or None
            if code and code != attr.code:
                existing = await db.execute(
                    select(OntologyAttribute.id).where(
                        OntologyAttribute.ontology_id == attr.ontology_id,
                        OntologyAttribute.code == code,
                        OntologyAttribute.id != attr_id,
                    )
                )
                if existing.scalar_one_or_none():
                    raise ValueError(f'编码 "{code}" 在该本体中已存在')
            attr.code = code
        if req.data_type is not None:
            attr.data_type = req.data_type
        if req.description is not None:
            attr.description = req.description.strip()
        if req.is_required is not None:
            attr.is_required = int(req.is_required)
        if req.default_value is not None:
            attr.default_value = req.default_value
        if req.sort_order is not None:
            attr.sort_order = req.sort_order
        attr.updated_at = datetime.now().isoformat()
        await db.commit()
        return _serialize_attribute(attr)

    @staticmethod
    async def delete_attribute(db: AsyncSession, attr_id: str) -> bool:
        result = await db.execute(select(OntologyAttribute).where(OntologyAttribute.id == attr_id))
        attr = result.scalar_one_or_none()
        if not attr:
            return False
        await db.delete(attr)
        await db.commit()
        return True

    @staticmethod
    async def batch_save_attributes(db: AsyncSession, ontology_id: str, attributes: list) -> dict:
        # 校验编码唯一性
        codes = [(a.code or "").strip() for a in attributes if (a.code or "").strip()]
        dupes = {c for c in codes if codes.count(c) > 1}
        if dupes:
            raise ValueError(f'编码重复：{", ".join(sorted(dupes))}')
        await db.execute(
            delete(OntologyAttribute).where(OntologyAttribute.ontology_id == ontology_id)
        )
        for idx, a in enumerate(attributes):
            code = (a.code or "").strip() or None
            db.add(OntologyAttribute(
                ontology_id=ontology_id, name=a.name.strip(), code=code,
                data_type=a.data_type, description=(a.description or "").strip(),
                is_required=int(a.is_required), default_value=a.default_value,
                sort_order=a.sort_order if a.sort_order is not None else idx,
            ))
        await db.commit()
        return {"ontology_id": ontology_id, "count": len(attributes)}

    # ===== 模块三：关系字典 CRUD =====

    @staticmethod
    async def list_relations(db: AsyncSession, category_id: str) -> list[dict]:
        result = await db.execute(
            select(OntologyRelation)
            .where(OntologyRelation.category_id == category_id)
            .order_by(OntologyRelation.created_at)
        )
        return [
            {"id": r.id, "category_id": r.category_id, "name": r.name, "code": r.code,
             "description": r.description or "", "created_at": r.created_at}
            for r in result.scalars().all()
        ]

    @staticmethod
    async def create_relation(db: AsyncSession, category_id: str, name: str, code: str | None = None, description: str = "") -> dict:
        code = (code or "").strip() or None
        if code:
            existing = await db.execute(
                select(OntologyRelation.id).where(
                    OntologyRelation.category_id == category_id,
                    OntologyRelation.code == code,
                )
            )
            if existing.scalar_one_or_none():
                raise ValueError(f'编码 "{code}" 在该类别中已存在')
        rel = OntologyRelation(category_id=category_id, name=name.strip(), code=code, description=(description or "").strip())
        db.add(rel)
        await db.commit()
        await db.refresh(rel)
        return {"id": rel.id, "name": rel.name, "code": rel.code, "description": rel.description}

    @staticmethod
    async def update_relation(db: AsyncSession, relation_id: str, name: str | None, code: str | None, description: str | None) -> dict | None:
        result = await db.execute(select(OntologyRelation).where(OntologyRelation.id == relation_id))
        rel = result.scalar_one_or_none()
        if not rel:
            return None
        if name is not None:
            rel.name = name.strip()
        if code is not None:
            new_code = code.strip() or None
            if new_code and new_code != (rel.code or "").strip():
                dup = await db.execute(
                    select(OntologyRelation.id).where(
                        OntologyRelation.category_id == rel.category_id,
                        OntologyRelation.code == new_code,
                        OntologyRelation.id != relation_id,
                    )
                )
                if dup.scalar_one_or_none():
                    raise ValueError(f'编码 "{new_code}" 在该类别中已存在')
            rel.code = new_code
        if description is not None:
            rel.description = description.strip()
        rel.updated_at = datetime.now().isoformat()
        await db.commit()
        return {"id": rel.id, "name": rel.name, "code": rel.code, "description": rel.description}

    @staticmethod
    async def delete_relation(db: AsyncSession, relation_id: str) -> bool:
        result = await db.execute(select(OntologyRelation).where(OntologyRelation.id == relation_id))
        rel = result.scalar_one_or_none()
        if not rel:
            return False
        # 级联：删除引用它的三元组
        await db.execute(
            delete(OntologyRelationConstraint).where(OntologyRelationConstraint.relation_id == relation_id)
        )
        await db.delete(rel)
        await db.commit()
        return True

    @staticmethod
    async def batch_create_relations(db: AsyncSession, category_id: str, items: list) -> list[dict]:
        out = []
        seen_codes: set[str] = set()
        for item in items:
            code = (getattr(item, 'code', None) or '').strip() or None
            if code:
                if code in seen_codes:
                    raise ValueError(f'编码 "{code}" 重复')
                existing = await db.execute(
                    select(OntologyRelation.id).where(
                        OntologyRelation.category_id == category_id,
                        OntologyRelation.code == code,
                    )
                )
                if existing.scalar_one_or_none():
                    raise ValueError(f'编码 "{code}" 在该类别中已存在')
                seen_codes.add(code)
            rel = OntologyRelation(category_id=category_id, name=item.name.strip(),
                                   code=code, description=(getattr(item, 'description', '') or '').strip())
            db.add(rel)
            await db.flush()
            out.append({"id": rel.id, "name": rel.name, "code": rel.code})
        await db.commit()
        return out

    # ===== 模块四：三元组约束 CRUD =====

    @staticmethod
    async def list_constraints(db: AsyncSession, category_id: str) -> list[dict]:
        result = await db.execute(
            select(OntologyRelationConstraint)
            .where(OntologyRelationConstraint.category_id == category_id)
            .order_by(OntologyRelationConstraint.created_at)
        )
        return [await _serialize_constraint(db, c) for c in result.scalars().all()]

    @staticmethod
    async def create_constraint(
        db: AsyncSession, category_id: str, source_ontology_id: str,
        relation_id: str, target_ontology_id: str, description: str = "",
    ) -> dict:
        # 校验：两个本体之间只能建立唯一的关系（同一对 source-target 不可重复）
        existing = await db.execute(
            select(OntologyRelationConstraint.id).where(
                OntologyRelationConstraint.category_id == category_id,
                OntologyRelationConstraint.source_ontology_id == source_ontology_id,
                OntologyRelationConstraint.target_ontology_id == target_ontology_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("这两个本体之间已存在关系约束，每对本体只能建立一个关系")
        c = OntologyRelationConstraint(
            category_id=category_id, source_ontology_id=source_ontology_id,
            relation_id=relation_id, target_ontology_id=target_ontology_id,
            description=(description or "").strip(),
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return await _serialize_constraint(db, c)

    @staticmethod
    async def update_constraint(db: AsyncSession, constraint_id: str, req) -> dict | None:
        result = await db.execute(
            select(OntologyRelationConstraint).where(OntologyRelationConstraint.id == constraint_id)
        )
        c = result.scalar_one_or_none()
        if not c:
            return None
        new_source = req.source_ontology_id if req.source_ontology_id is not None else c.source_ontology_id
        new_target = req.target_ontology_id if req.target_ontology_id is not None else c.target_ontology_id
        # 校验：两个本体之间只能建立唯一的关系（同一对 source-target 不可重复）
        if req.source_ontology_id is not None or req.target_ontology_id is not None:
            existing = await db.execute(
                select(OntologyRelationConstraint.id).where(
                    OntologyRelationConstraint.category_id == c.category_id,
                    OntologyRelationConstraint.source_ontology_id == new_source,
                    OntologyRelationConstraint.target_ontology_id == new_target,
                    OntologyRelationConstraint.id != constraint_id,
                )
            )
            if existing.scalar_one_or_none():
                raise ValueError("这两个本体之间已存在关系约束，每对本体只能建立一个关系")
        if req.source_ontology_id is not None:
            c.source_ontology_id = req.source_ontology_id
        if req.relation_id is not None:
            c.relation_id = req.relation_id
        if req.target_ontology_id is not None:
            c.target_ontology_id = req.target_ontology_id
        if req.description is not None:
            c.description = req.description.strip()
        await db.commit()
        return await _serialize_constraint(db, c)

    @staticmethod
    async def delete_constraint(db: AsyncSession, constraint_id: str) -> bool:
        result = await db.execute(
            select(OntologyRelationConstraint).where(OntologyRelationConstraint.id == constraint_id)
        )
        c = result.scalar_one_or_none()
        if not c:
            return False
        await db.delete(c)
        await db.commit()
        return True

    @staticmethod
    async def batch_create_constraints(db: AsyncSession, category_id: str, items: list) -> list[dict]:
        out = []
        for item in items:
            c = OntologyRelationConstraint(
                category_id=category_id, source_ontology_id=item.source_ontology_id,
                relation_id=item.relation_id, target_ontology_id=item.target_ontology_id,
                description=(item.description or "").strip(),
            )
            db.add(c)
            await db.flush()
            out.append({"id": c.id})
        await db.commit()
        return out

    # ===== 知识库绑定 =====

    @staticmethod
    async def get_kb_binding(db: AsyncSession, kb_id: str) -> dict | None:
        result = await db.execute(
            select(KbOntologyBinding).where(KbOntologyBinding.kb_id == kb_id)
        )
        b = result.scalar_one_or_none()
        if not b:
            return None
        cat_result = await db.execute(
            select(OntologyCategory).where(OntologyCategory.id == b.category_id)
        )
        cat = cat_result.scalar_one_or_none()
        return {
            "kb_id": b.kb_id,
            "category_id": b.category_id,
            "category_name": cat.name if cat else None,
            "created_at": b.created_at,
        }

    @staticmethod
    async def bind_kb(db: AsyncSession, kb_id: str, category_id: str) -> dict:
        # UNIQUE(kb_id)：先删旧绑定再插新
        await db.execute(delete(KbOntologyBinding).where(KbOntologyBinding.kb_id == kb_id))
        binding = KbOntologyBinding(kb_id=kb_id, category_id=category_id)
        db.add(binding)
        await db.commit()
        return {"kb_id": kb_id, "category_id": category_id}

    @staticmethod
    async def unbind_kb(db: AsyncSession, kb_id: str) -> bool:
        result = await db.execute(
            delete(KbOntologyBinding).where(KbOntologyBinding.kb_id == kb_id)
        )
        await db.commit()
        return result.rowcount > 0

    # ===== 模块五：属性模板 CRUD =====

    @staticmethod
    async def list_templates(db: AsyncSession, q: str = "") -> list[dict]:
        stmt = select(OntologyAttributeTemplate).order_by(OntologyAttributeTemplate.created_at)
        if q:
            stmt = stmt.where(OntologyAttributeTemplate.name.contains(q))
        result = await db.execute(stmt)
        out = []
        for t in result.scalars().all():
            cnt = await db.execute(
                select(OntologyTemplateAttribute).where(OntologyTemplateAttribute.template_id == t.id)
            )
            out.append({
                "id": t.id, "name": t.name, "description": t.description or "",
                "is_system": bool(t.is_system),
                "attribute_count": len(cnt.scalars().all()),
                "created_at": t.created_at,
            })
        return out

    @staticmethod
    async def get_template_detail(db: AsyncSession, template_id: str) -> dict | None:
        result = await db.execute(
            select(OntologyAttributeTemplate).where(OntologyAttributeTemplate.id == template_id)
        )
        t = result.scalar_one_or_none()
        if not t:
            return None
        attrs_result = await db.execute(
            select(OntologyTemplateAttribute)
            .where(OntologyTemplateAttribute.template_id == template_id)
            .order_by(OntologyTemplateAttribute.sort_order)
        )
        attrs = [_serialize_attribute(a, source="template") for a in attrs_result.scalars().all()]
        return {
            "id": t.id, "name": t.name, "description": t.description or "",
            "is_system": bool(t.is_system), "created_at": t.created_at,
            "attributes": attrs,
        }

    @staticmethod
    async def create_template(db: AsyncSession, name: str, description: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("模板名称不能为空")
        
        # 检查名称是否已存在
        existing = await db.execute(
            select(OntologyAttributeTemplate).where(OntologyAttributeTemplate.name == name)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f'模板名称 "{name}" 已存在')
        
        t = OntologyAttributeTemplate(name=name, description=(description or "").strip())
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return {"id": t.id, "name": t.name, "description": t.description}

    @staticmethod
    async def update_template(db: AsyncSession, template_id: str, name: str | None, description: str | None) -> dict | None:
        result = await db.execute(
            select(OntologyAttributeTemplate).where(OntologyAttributeTemplate.id == template_id)
        )
        t = result.scalar_one_or_none()
        if not t:
            return None
        if name is not None:
            new_name = name.strip()
            if new_name != t.name:
                # 检查新名称是否已被其他模板使用
                existing = await db.execute(
                    select(OntologyAttributeTemplate).where(
                        OntologyAttributeTemplate.name == new_name,
                        OntologyAttributeTemplate.id != template_id
                    )
                )
                if existing.scalar_one_or_none():
                    raise ValueError(f'模板名称 "{new_name}" 已存在')
            t.name = new_name
        if description is not None:
            t.description = description.strip()
        t.updated_at = datetime.now().isoformat()
        await db.commit()
        return {"id": t.id, "name": t.name, "description": t.description}

    @staticmethod
    async def delete_template(db: AsyncSession, template_id: str) -> bool:
        result = await db.execute(
            select(OntologyAttributeTemplate).where(OntologyAttributeTemplate.id == template_id)
        )
        t = result.scalar_one_or_none()
        if not t:
            return False
        if t.is_system:
            return False  # 系统内置不可删
        # 解除所有本体引用 + 删模板属性
        await db.execute(
            delete(OntologyTemplateBinding).where(OntologyTemplateBinding.template_id == template_id)
        )
        await db.execute(
            delete(OntologyTemplateAttribute).where(OntologyTemplateAttribute.template_id == template_id)
        )
        await db.delete(t)
        await db.commit()
        return True

    @staticmethod
    async def create_template_attribute(db: AsyncSession, template_id: str, req) -> dict:
        code = (req.code or "").strip() or None
        if code:
            existing = await db.execute(
                select(OntologyTemplateAttribute.id).where(
                    OntologyTemplateAttribute.template_id == template_id,
                    OntologyTemplateAttribute.code == code,
                )
            )
            if existing.scalar_one_or_none():
                raise ValueError(f'编码 "{code}" 在该模板中已存在')
        attr = OntologyTemplateAttribute(
            template_id=template_id, name=req.name.strip(), code=code,
            data_type=req.data_type, description=(req.description or "").strip(),
            is_required=int(req.is_required), default_value=req.default_value,
            sort_order=req.sort_order,
        )
        db.add(attr)
        await db.commit()
        await db.refresh(attr)
        return _serialize_attribute(attr, source="template")

    @staticmethod
    async def update_template_attribute(db: AsyncSession, attr_id: str, req) -> dict | None:
        result = await db.execute(
            select(OntologyTemplateAttribute).where(OntologyTemplateAttribute.id == attr_id)
        )
        attr = result.scalar_one_or_none()
        if not attr:
            return None
        if req.name is not None:
            attr.name = req.name.strip()
        if req.code is not None:
            code = req.code.strip() or None
            if code and code != attr.code:
                existing = await db.execute(
                    select(OntologyTemplateAttribute.id).where(
                        OntologyTemplateAttribute.template_id == attr.template_id,
                        OntologyTemplateAttribute.code == code,
                        OntologyTemplateAttribute.id != attr_id,
                    )
                )
                if existing.scalar_one_or_none():
                    raise ValueError(f'编码 "{code}" 在该模板中已存在')
            attr.code = code
        if req.data_type is not None:
            attr.data_type = req.data_type
        if req.description is not None:
            attr.description = req.description.strip()
        if req.is_required is not None:
            attr.is_required = int(req.is_required)
        if req.default_value is not None:
            attr.default_value = req.default_value
        if req.sort_order is not None:
            attr.sort_order = req.sort_order
        attr.updated_at = datetime.now().isoformat()
        await db.commit()
        return _serialize_attribute(attr, source="template")

    @staticmethod
    async def delete_template_attribute(db: AsyncSession, attr_id: str) -> bool:
        result = await db.execute(
            select(OntologyTemplateAttribute).where(OntologyTemplateAttribute.id == attr_id)
        )
        attr = result.scalar_one_or_none()
        if not attr:
            return False
        await db.delete(attr)
        await db.commit()
        return True

    @staticmethod
    async def batch_save_template_attributes(db: AsyncSession, template_id: str, attributes: list) -> dict:
        codes = [(a.code or "").strip() for a in attributes if (a.code or "").strip()]
        dupes = {c for c in codes if codes.count(c) > 1}
        if dupes:
            raise ValueError(f'编码重复：{", ".join(sorted(dupes))}')
        await db.execute(
            delete(OntologyTemplateAttribute).where(OntologyTemplateAttribute.template_id == template_id)
        )
        for idx, a in enumerate(attributes):
            code = (a.code or "").strip() or None
            db.add(OntologyTemplateAttribute(
                template_id=template_id, name=a.name.strip(), code=code,
                data_type=a.data_type, description=(a.description or "").strip(),
                is_required=int(a.is_required), default_value=a.default_value,
                sort_order=a.sort_order if a.sort_order is not None else idx,
            ))
        await db.commit()
        return {"template_id": template_id, "count": len(attributes)}

    # ===== 本体引用模板（多对多）+ 属性合并 =====

    @staticmethod
    async def list_ontology_templates(db: AsyncSession, ontology_id: str) -> list[dict]:
        result = await db.execute(
            select(OntologyTemplateBinding)
            .where(OntologyTemplateBinding.ontology_id == ontology_id)
            .order_by(OntologyTemplateBinding.sort_order)
        )
        out = []
        for b in result.scalars().all():
            t_result = await db.execute(
                select(OntologyAttributeTemplate).where(OntologyAttributeTemplate.id == b.template_id)
            )
            t = t_result.scalar_one_or_none()
            out.append({
                "template_id": b.template_id,
                "sort_order": b.sort_order,
                "name": t.name if t else None,
                "description": t.description if t else "",
            })
        return out

    @staticmethod
    async def set_ontology_templates(db: AsyncSession, ontology_id: str, template_ids: list[str]) -> dict:
        await db.execute(
            delete(OntologyTemplateBinding).where(OntologyTemplateBinding.ontology_id == ontology_id)
        )
        for idx, tid in enumerate(template_ids):
            db.add(OntologyTemplateBinding(
                ontology_id=ontology_id, template_id=tid, sort_order=idx,
            ))
        await db.commit()
        return {"ontology_id": ontology_id, "template_ids": template_ids}

    @staticmethod
    async def get_merged_attributes(db: AsyncSession, ontology_id: str) -> dict | None:
        ont_result = await db.execute(select(Ontology).where(Ontology.id == ontology_id))
        ont = ont_result.scalar_one_or_none()
        if not ont:
            return None

        # 模板属性合并（按 binding.sort_order，再按 attribute.sort_order）
        bindings_result = await db.execute(
            select(OntologyTemplateBinding)
            .where(OntologyTemplateBinding.ontology_id == ontology_id)
            .order_by(OntologyTemplateBinding.sort_order)
        )
        merged: dict[str, dict] = {}
        order: list[str] = []
        for b in bindings_result.scalars().all():
            attrs_result = await db.execute(
                select(OntologyTemplateAttribute)
                .where(OntologyTemplateAttribute.template_id == b.template_id)
                .order_by(OntologyTemplateAttribute.sort_order)
            )
            for a in attrs_result.scalars().all():
                d = _serialize_attribute(a, source=f"template:{b.template_id}")
                if a.name not in merged:
                    merged[a.name] = d
                    order.append(a.name)

        # 自有属性覆盖（同名冲突时自有优先）
        conflicts: list[str] = []
        own_result = await db.execute(
            select(OntologyAttribute)
            .where(OntologyAttribute.ontology_id == ontology_id)
            .order_by(OntologyAttribute.sort_order)
        )
        for a in own_result.scalars().all():
            d = _serialize_attribute(a, source="own")
            if a.name in merged:
                conflicts.append(a.name)
            merged[a.name] = d
            if a.name not in order:
                order.append(a.name)

        return {
            "ontology_id": ontology_id,
            "ontology_name": ont.name,
            "attributes": [merged[name] for name in order],
            "conflicts": conflicts,
        }

    # ===== 抽取约束加载（供 file_service / graph_extraction_service 使用）=====

    @staticmethod
    async def get_kb_extraction_constraints(db: AsyncSession, kb_id: str) -> dict | None:
        """加载某知识库绑定的本体类别下的全部抽取约束。

        返回结构（无绑定时返回 None，抽取服务回退到自由抽取模式）：
        {
            "category_id": "...",
            "category_name": "...",
            "ontologies": [
                {
                    "id": "...", "name": "人物",
                    "attributes": [{"name":"姓名","data_type":"string","is_required":true,...}, ...],
                },
                ...
            ],
            "ontology_by_name": {"人物": {...}, "组织": {...}},  # 便于按名查找
            "relations": ["任职于", "持有", ...],                # 关系字典名列表
            "constraints": [
                {"source":"人物","relation":"任职于","target":"组织"},
                ...
            ],
            "constraint_set": {("人物","任职于","组织"), ...},   # 集合，便于 O(1) 校验
        }
        """
        binding = await OntologyService.get_kb_binding(db, kb_id)
        if not binding:
            return None
        category_id = binding["category_id"]

        ontologies = await OntologyService.list_ontologies(db, category_id)
        # 每个本体取合并后的完整属性
        ontology_list = []
        ontology_by_name: dict[str, dict] = {}
        for ont in ontologies:
            merged = await OntologyService.get_merged_attributes(db, ont["id"])
            attrs = merged.get("attributes", []) if merged else ont.get("attributes", [])
            # 精简属性，只保留抽取 Prompt 与后处理校验需要的字段
            slim_attrs = [
                {
                    "name": a["name"],
                    "code": a.get("code"),
                    "data_type": a["data_type"],
                    "is_required": a.get("is_required", False),
                    "description": a.get("description", ""),
                }
                for a in attrs
            ]
            entry = {
                "id": ont["id"],
                "name": ont["name"],
                "attributes": slim_attrs,
            }
            ontology_list.append(entry)
            ontology_by_name[ont["name"]] = entry

        relations = await OntologyService.list_relations(db, category_id)
        relation_names = [r["name"] for r in relations]
        relation_id_by_name = {r["name"]: r["id"] for r in relations}
        relation_code_by_name = {r["name"]: (r.get("code") or "") for r in relations}

        constraints_raw = await OntologyService.list_constraints(db, category_id)
        constraints = []
        constraint_set: set[tuple[str, str, str]] = set()
        for c in constraints_raw:
            src_name = c.get("source_ontology_name")
            rel_name = c.get("relation_name")
            tgt_name = c.get("target_ontology_name")
            if not (src_name and rel_name and tgt_name):
                continue
            triple = {"source": src_name, "relation": rel_name, "target": tgt_name}
            constraints.append(triple)
            constraint_set.add((src_name, rel_name, tgt_name))

        return {
            "category_id": category_id,
            "category_name": binding.get("category_name"),
            "ontologies": ontology_list,
            "ontology_by_name": ontology_by_name,
            "relation_names": relation_names,
            "relation_id_by_name": relation_id_by_name,
            "relation_code_by_name": relation_code_by_name,
            "constraints": constraints,
            "constraint_set": constraint_set,
        }


# ===== 模块六：本体建议（动态生成 + 审核）=====

class OntologySuggestionService:

    @staticmethod
    def _to_dict(s: OntologySuggestion) -> dict:
        try:
            data = json.loads(s.suggestion_data or "{}")
        except Exception:
            data = {}
        return {
            "id": s.id,
            "kb_id": s.kb_id,
            "file_id": s.file_id,
            "status": s.status,
            "source_mode": s.source_mode,
            "score": s.score,
            "review_notes": s.review_notes or "",
            "created_at": s.created_at,
            "reviewed_at": s.reviewed_at,
            "reviewer": s.reviewer,
            "suggestion_data": data,
        }

    @staticmethod
    async def list_suggestions(db: AsyncSession, kb_id: str | None = None, status: str | None = None) -> list[dict]:
        stmt = select(OntologySuggestion).order_by(OntologySuggestion.created_at.desc())
        if kb_id:
            stmt = stmt.where(OntologySuggestion.kb_id == kb_id)
        if status:
            stmt = stmt.where(OntologySuggestion.status == status)
        result = await db.execute(stmt)
        return [OntologySuggestionService._to_dict(s) for s in result.scalars().all()]

    @staticmethod
    async def get_suggestion(db: AsyncSession, suggestion_id: str) -> dict | None:
        result = await db.execute(select(OntologySuggestion).where(OntologySuggestion.id == suggestion_id))
        s = result.scalar_one_or_none()
        return OntologySuggestionService._to_dict(s) if s else None

    @staticmethod
    async def create_suggestion(db: AsyncSession, kb_id: str, file_id: str | None,
                                suggestion_data: dict, source_mode: str = "free_extraction",
                                score: float = 0.0) -> dict:
        s = OntologySuggestion(
            kb_id=kb_id,
            file_id=file_id,
            status="ready",
            source_mode=source_mode,
            suggestion_data=json.dumps(suggestion_data, ensure_ascii=False),
            score=score,
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return OntologySuggestionService._to_dict(s)

    @staticmethod
    async def update_suggestion(db: AsyncSession, suggestion_id: str,
                                suggestion_data: dict | None = None,
                                status: str | None = None,
                                review_notes: str | None = None,
                                score: float | None = None) -> dict | None:
        result = await db.execute(select(OntologySuggestion).where(OntologySuggestion.id == suggestion_id))
        s = result.scalar_one_or_none()
        if not s:
            return None
        if suggestion_data is not None:
            s.suggestion_data = json.dumps(suggestion_data, ensure_ascii=False)
        if status:
            s.status = status
        if review_notes is not None:
            s.review_notes = review_notes
        if score is not None:
            s.score = score
        s.reviewed_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(s)
        return OntologySuggestionService._to_dict(s)

    @staticmethod
    async def delete_suggestion(db: AsyncSession, suggestion_id: str) -> bool:
        result = await db.execute(select(OntologySuggestion).where(OntologySuggestion.id == suggestion_id))
        s = result.scalar_one_or_none()
        if not s:
            return False
        await db.delete(s)
        await db.commit()
        return True

    @staticmethod
    async def reject_suggestion(db: AsyncSession, suggestion_id: str,
                                reviewer: str | None = None,
                                review_notes: str = "") -> dict | None:
        return await OntologySuggestionService.update_suggestion(
            db, suggestion_id, status="rejected", review_notes=review_notes,
        )

    @staticmethod
    async def approve_suggestion(db: AsyncSession, suggestion_id: str,
                                 reviewer: str | None = None) -> dict:
        """审核通过：将 suggestion 内容写入正式本体表 + 自动绑定到知识库。

        智能合并策略：
        - KB 已绑定类别 → 合并到已有类别（跳过同名本体/关系，补充新增的属性）
        - KB 未绑定类别 → 创建新类别
        """
        result = await db.execute(select(OntologySuggestion).where(OntologySuggestion.id == suggestion_id))
        s = result.scalar_one_or_none()
        if not s:
            raise ValueError("建议不存在")
        if s.status == "approved":
            raise ValueError("该建议已审核通过")

        try:
            data = json.loads(s.suggestion_data or "{}")
        except Exception:
            data = {}

        cat_info = data.get("category") or {}
        cat_name = (cat_info.get("name") or "").strip()
        if not cat_name:
            raise ValueError("类别名称不能为空")
        cat_desc = (cat_info.get("description") or "").strip()

        # ====== 判断是合并还是新建 ======
        existing_binding = await db.execute(
            select(KbOntologyBinding).where(KbOntologyBinding.kb_id == s.kb_id)
        )
        binding = existing_binding.scalar_one_or_none()
        merge_target_category_id = binding.category_id if binding else None

        if merge_target_category_id:
            # --- 合并模式：使用已有类别 ---
            category_id = merge_target_category_id
            # 加载已有本体/关系/属性，用于去重
            existing_onts = await db.execute(
                select(Ontology).where(Ontology.category_id == category_id)
            )
            existing_ont_names: set[str] = set()
            existing_ont_id_by_name: dict[str, str] = {}
            for eo in existing_onts.scalars().all():
                existing_ont_names.add(eo.name)
                existing_ont_id_by_name[eo.name] = eo.id

            existing_rels = await db.execute(
                select(OntologyRelation).where(OntologyRelation.category_id == category_id)
            )
            existing_rel_names: set[str] = set()
            existing_rel_id_by_name: dict[str, str] = {}
            for er in existing_rels.scalars().all():
                existing_rel_names.add(er.name)
                existing_rel_id_by_name[er.name] = er.id

            # 加载已有属性（按 ontology_id 分组）
            existing_attrs_by_ont: dict[str, set[str]] = {}
            for ont_id in existing_ont_id_by_name.values():
                attr_rows = await db.execute(
                    select(OntologyAttribute.name).where(OntologyAttribute.ontology_id == ont_id)
                )
                existing_attrs_by_ont[ont_id] = {row[0] for row in attr_rows.scalars().all()}

            # 加载已有约束
            existing_constraints_rows = await db.execute(
                select(OntologyRelationConstraint).where(OntologyRelationConstraint.category_id == category_id)
            )
            existing_constraint_keys: set[tuple[str, str, str]] = set()
            for ec in existing_constraints_rows.scalars().all():
                src_name = None
                tgt_name = None
                src_row = await db.execute(select(Ontology.name).where(Ontology.id == ec.source_ontology_id))
                tgt_row = await db.execute(select(Ontology.name).where(Ontology.id == ec.target_ontology_id))
                if src_row.scalar_one_or_none():
                    src_name = src_row.scalar_one_or_none()[0] if hasattr(src_row.scalar_one_or_none(), '__getitem__') else getattr(src_row.scalar_one_or_none(), 'name', None)
                if tgt_row.scalar_one_or_none():
                    tgt_name = tgt_row.scalar_one_or_none()[0] if hasattr(tgt_row.scalar_one_or_none(), '__getitem__') else getattr(tgt_row.scalar_one_or_none(), 'name', None)
                if src_name and tgt_name:
                    existing_constraint_keys.add((src_name, ec.relation_id, tgt_name))

            # 补充关系ID映射（用已有 + 新建的）
            rel_id_by_name = dict(existing_rel_id_by_name)

            skipped_ontologies = 0
            skipped_relations = 0
            skipped_constraints = 0
            added_ontologies = 0
            added_relations = 0
            added_constraints = 0
            added_attributes = 0

        else:
            # --- 新建模式 ---
            category = OntologyCategory(name=cat_name, description=cat_desc)
            db.add(category)
            await db.flush()
            category_id = category.id
            existing_ont_names = set()
            existing_ont_id_by_name = {}
            existing_attrs_by_ont = {}
            existing_rel_names = set()
            rel_id_by_name = {}
            existing_constraint_keys = set()
            skipped_ontologies = 0
            skipped_relations = 0
            skipped_constraints = 0
            added_ontologies = 0
            added_relations = 0
            added_constraints = 0
            added_attributes = 0

        # ====== 统一处理：本体 + 属性 + 关系 + 约束 ======
        ont_id_by_name = dict(existing_ont_id_by_name)

        # --- 本体 + 属性 ---
        ont_list = data.get("ontologies") or []
        for ont in ont_list:
            ont_name = (ont.get("name") or "").strip()
            if not ont_name:
                continue
            if ont_name in existing_ont_names:
                # 同名本体已存在 → 补充属性
                skipped_ontologies += 1
                ont_id = existing_ont_id_by_name[ont_name]
                existing_attr_names = existing_attrs_by_ont.get(ont_id, set())
                attrs = ont.get("attributes") or []
                for i, at in enumerate(attrs):
                    a_name = (at.get("name") or "").strip()
                    if not a_name or a_name in existing_attr_names:
                        continue
                    a_code = (at.get("code") or "").strip() or None
                    a = OntologyAttribute(
                        ontology_id=ont_id, name=a_name, code=a_code,
                        data_type=(at.get("data_type") or "string").strip() or "string",
                        description=(at.get("description") or "").strip(),
                        is_required=int(bool(at.get("is_required", False))),
                        sort_order=len(existing_attr_names) + i,
                    )
                    db.add(a)
                    existing_attr_names.add(a_name)
                    existing_attrs_by_ont.setdefault(ont_id, set()).add(a_name)
                    added_attributes += 1
            else:
                # 新本体 → 创建
                o = Ontology(
                    category_id=category_id, name=ont_name,
                    description=(ont.get("description") or "").strip(),
                )
                db.add(o)
                await db.flush()
                ont_id_by_name[ont_name] = o.id
                existing_ont_names.add(ont_name)
                existing_attrs_by_ont[o.id] = set()
                added_ontologies += 1
                attrs = ont.get("attributes") or []
                seen_codes: set = set()
                for i, at in enumerate(attrs):
                    a_name = (at.get("name") or "").strip()
                    if not a_name:
                        continue
                    a_code = (at.get("code") or "").strip() or None
                    if a_code:
                        if a_code in seen_codes:
                            a_code = None
                        else:
                            seen_codes.add(a_code)
                    a = OntologyAttribute(
                        ontology_id=o.id, name=a_name, code=a_code,
                        data_type=(at.get("data_type") or "string").strip() or "string",
                        description=(at.get("description") or "").strip(),
                        is_required=int(bool(at.get("is_required", False))),
                        sort_order=i,
                    )
                    db.add(a)
                    existing_attrs_by_ont.setdefault(o.id, set()).add(a_name)
                    added_attributes += 1

        # --- 关系字典 ---
        rel_list = data.get("relations") or []
        seen_rel_codes: set = set()
        for rn_code in rel_id_by_name.values():
            c = rn_code
            if c:
                seen_rel_codes.add(c)
        for rel in rel_list:
            r_name = (rel.get("name") or "").strip()
            if not r_name:
                continue
            if r_name in existing_rel_names:
                skipped_relations += 1
                continue
            r_code = (rel.get("code") or "").strip() or None
            if r_code:
                if r_code in seen_rel_codes:
                    r_code = None
                else:
                    seen_rel_codes.add(r_code)
            r = OntologyRelation(
                category_id=category_id, name=r_name, code=r_code,
                description=(rel.get("description") or "").strip(),
            )
            db.add(r)
            await db.flush()
            rel_id_by_name[r_name] = r.id
            existing_rel_names.add(r_name)
            added_relations += 1

        # --- 三元组约束 ---
        cons_list = data.get("constraints") or []
        for c in cons_list:
            src_ont = (c.get("source") or "").strip()
            rel_name = (c.get("relation") or "").strip()
            tgt_ont = (c.get("target") or "").strip()
            if not (src_ont and rel_name and tgt_ont):
                continue
            src_id = ont_id_by_name.get(src_ont)
            tgt_id = ont_id_by_name.get(tgt_ont)
            rel_id = rel_id_by_name.get(rel_name)
            if not (src_id and tgt_id and rel_id):
                skipped_constraints += 1
                continue
            # 去重检查（仅新建模式或合并到不同类别时可能重复）
            cons_key = (src_ont, rel_id, tgt_ont)
            if cons_key in existing_constraint_keys:
                skipped_constraints += 1
                continue
            cons = OntologyRelationConstraint(
                category_id=category_id,
                source_ontology_id=src_id, relation_id=rel_id, target_ontology_id=tgt_id,
                description=(c.get("description") or "").strip(),
            )
            db.add(cons)
            existing_constraint_keys.add(cons_key)
            added_constraints += 1

        # --- 绑定到知识库 ---
        if not merge_target_category_id:
            db.add(KbOntologyBinding(kb_id=s.kb_id, category_id=category_id))

        # --- 更新建议状态 ---
        s.status = "approved"
        s.reviewed_at = datetime.now().isoformat()
        s.reviewer = reviewer or "system"
        await db.commit()
        await db.refresh(s)

        ret = OntologySuggestionService._to_dict(s)
        ret["approved_category_id"] = category_id
        ret["mode"] = "merge" if merge_target_category_id else "create"
        ret["ontology_count"] = added_ontologies
        ret["skipped_ontologies"] = skipped_ontologies
        ret["relation_count"] = added_relations
        ret["skipped_relations"] = skipped_relations
        ret["constraint_count"] = added_constraints
        ret["skipped_constraints"] = skipped_constraints
        ret["added_attributes"] = added_attributes

        # ====== 回填 Kùzu 实体 ontology_id + 写入 SQLite ======
        try:
            backfilled = await OntologySuggestionService._backfill_entities_after_approval(
                db, s.kb_id, ont_id_by_name
            )
            ret["backfilled_entities"] = backfilled
        except Exception as e:
            logger.exception("回填实体 ontology_id 失败: %s", e)
            ret["backfilled_entities"] = 0

        return ret

    @staticmethod
    async def _backfill_entities_after_approval(
        db: AsyncSession,
        kb_id: str,
        ont_id_by_name: dict[str, str],
    ) -> int:
        """审批后将 Kùzu 中 ontology_id 为空的实体回填 ontology_id 并写入 SQLite。

        匹配策略：Kùzu Entity.entity_type == Ontology.name（精确匹配）。
        返回成功回填的实体数量。
        """
        if not ont_id_by_name:
            return 0

        from providers.graph_store import _get_adapter

        adapter = _get_adapter()

        # 1. 从 Kùzu 查询该 KB 下 ontology_id 为空的实体
        try:
            orphan_rows = adapter._execute_dict(
                """
                MATCH (e:Entity {kb_id: $kb_id})
                WHERE e.ontology_id = '' OR e.ontology_id IS NULL
                RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type,
                       e.description AS description, e.properties AS properties
                """,
                {"kb_id": kb_id},
            )
        except Exception:
            logger.exception("查询 Kùzu 孤儿实体失败")
            return 0

        if not orphan_rows:
            return 0

        # 2. 建立 entity_type → ontology_id 的映射（精确匹配本体名）
        type_to_ont = {}
        for ont_name, ont_id in ont_id_by_name.items():
            type_to_ont[ont_name] = ont_id

        backfilled = 0
        for row in orphan_rows:
            et = (row.get("entity_type") or "").strip()
            ont_id = type_to_ont.get(et)
            if not ont_id:
                continue  # 该实体类型没有对应本体

            entity_graph_id = row.get("entity_id") or ""
            entity_name = (row.get("name") or "").strip()
            entity_desc = (row.get("description") or "").strip()
            entity_props = (row.get("properties") or "").strip()

            # 3. 更新图库中实体的 ontology_id
            try:
                adapter._execute(
                    """
                    MATCH (e:Entity {id: $entity_id})
                    SET e.ontology_id = $ontology_id
                    """,
                    {"entity_id": entity_graph_id, "ontology_id": ont_id},
                )
            except Exception:
                logger.exception("更新图库实体 ontology_id 失败: %s", entity_graph_id)

            # 4. 写入 SQLite entities 表（upsert 语义）
            try:
                props_dict = None
                if entity_props:
                    try:
                        props_dict = json.loads(entity_props)
                    except (json.JSONDecodeError, TypeError):
                        props_dict = None

                # 先检查是否已存在
                existing = await db.execute(
                    select(Entity).where(
                        Entity.kb_id == kb_id,
                        Entity.entity_type == et,
                        Entity.name == entity_name,
                    )
                )
                ent = existing.scalar_one_or_none()
                if ent is None:
                    ent = Entity(
                        id=entity_graph_id if entity_graph_id else None,
                        kb_id=kb_id,
                        ontology_id=ont_id,
                        entity_type=et,
                        name=entity_name,
                        description=entity_desc,
                        properties=json.dumps(props_dict, ensure_ascii=False) if props_dict else None,
                    )
                    db.add(ent)
                else:
                    if not ent.ontology_id:
                        ent.ontology_id = ont_id
                backfilled += 1
            except Exception:
                logger.exception("写入 SQLite 实体失败: %s", entity_name)

        if backfilled > 0:
            try:
                await db.commit()
            except Exception:
                logger.exception("回填 commit 失败")
                await db.rollback()

        logger.info("回填实体完成: kb_id=%s, 回填数量=%d", kb_id, backfilled)
        return backfilled
