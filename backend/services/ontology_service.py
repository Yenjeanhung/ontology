from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    KbOntologyBinding,
    Ontology,
    OntologyAttribute,
    OntologyAttributeTemplate,
    OntologyCategory,
    OntologyRelation,
    OntologyRelationConstraint,
    OntologyTemplateAttribute,
    OntologyTemplateBinding,
)


# ---------- 辅助序列化 ----------

def _prepare_enum_values(enum_values) -> str | None:
    if enum_values is None:
        return None
    return json.dumps(list(enum_values), ensure_ascii=False)


def _parse_enum_values(raw) -> list[str] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _serialize_attribute(attr, source: str = "own") -> dict:
    return {
        "id": attr.id,
        "name": attr.name,
        "data_type": attr.data_type,
        "description": attr.description or "",
        "is_required": bool(attr.is_required),
        "default_value": attr.default_value,
        "enum_values": _parse_enum_values(attr.enum_values),
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
        stmt = select(OntologyCategory).order_by(OntologyCategory.created_at)
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
        ontology_list = []
        for ont in ontologies_result.scalars().all():
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
        result = await db.execute(
            select(Ontology)
            .where(Ontology.category_id == category_id)
            .order_by(Ontology.sort_order, Ontology.created_at)
        )
        out = []
        for ont in result.scalars().all():
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
            out.append({
                "id": ont.id,
                "category_id": ont.category_id,
                "name": ont.name,
                "description": ont.description or "",
                "color": ont.color,
                "sort_order": ont.sort_order,
                "attributes": attrs,
                "template_ids": template_ids,
                "created_at": ont.created_at,
            })
        return out

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
        # 级联：属性、模板绑定、引用它的三元组
        await db.execute(
            delete(OntologyAttribute).where(OntologyAttribute.ontology_id == ontology_id)
        )
        await db.execute(
            delete(OntologyTemplateBinding).where(OntologyTemplateBinding.ontology_id == ontology_id)
        )
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
        attr = OntologyAttribute(
            ontology_id=ontology_id, name=req.name.strip(), data_type=req.data_type,
            description=(req.description or "").strip(), is_required=int(req.is_required),
            default_value=req.default_value, enum_values=_prepare_enum_values(req.enum_values),
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
        if req.data_type is not None:
            attr.data_type = req.data_type
        if req.description is not None:
            attr.description = req.description.strip()
        if req.is_required is not None:
            attr.is_required = int(req.is_required)
        if req.default_value is not None:
            attr.default_value = req.default_value
        if req.enum_values is not None:
            attr.enum_values = _prepare_enum_values(req.enum_values)
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
        await db.execute(
            delete(OntologyAttribute).where(OntologyAttribute.ontology_id == ontology_id)
        )
        for idx, a in enumerate(attributes):
            db.add(OntologyAttribute(
                ontology_id=ontology_id, name=a.name.strip(), data_type=a.data_type,
                description=(a.description or "").strip(), is_required=int(a.is_required),
                default_value=a.default_value, enum_values=_prepare_enum_values(a.enum_values),
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
            {"id": r.id, "category_id": r.category_id, "name": r.name,
             "description": r.description or "", "created_at": r.created_at}
            for r in result.scalars().all()
        ]

    @staticmethod
    async def create_relation(db: AsyncSession, category_id: str, name: str, description: str = "") -> dict:
        rel = OntologyRelation(category_id=category_id, name=name.strip(), description=(description or "").strip())
        db.add(rel)
        await db.commit()
        await db.refresh(rel)
        return {"id": rel.id, "name": rel.name, "description": rel.description}

    @staticmethod
    async def update_relation(db: AsyncSession, relation_id: str, name: str | None, description: str | None) -> dict | None:
        result = await db.execute(select(OntologyRelation).where(OntologyRelation.id == relation_id))
        rel = result.scalar_one_or_none()
        if not rel:
            return None
        if name is not None:
            rel.name = name.strip()
        if description is not None:
            rel.description = description.strip()
        rel.updated_at = datetime.now().isoformat()
        await db.commit()
        return {"id": rel.id, "name": rel.name, "description": rel.description}

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
        for item in items:
            rel = OntologyRelation(category_id=category_id, name=item.name.strip(),
                                   description=(item.description or "").strip())
            db.add(rel)
            await db.flush()
            out.append({"id": rel.id, "name": rel.name})
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
        t = OntologyAttributeTemplate(name=name.strip(), description=(description or "").strip())
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
            t.name = name.strip()
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
        attr = OntologyTemplateAttribute(
            template_id=template_id, name=req.name.strip(), data_type=req.data_type,
            description=(req.description or "").strip(), is_required=int(req.is_required),
            default_value=req.default_value, enum_values=_prepare_enum_values(req.enum_values),
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
        if req.data_type is not None:
            attr.data_type = req.data_type
        if req.description is not None:
            attr.description = req.description.strip()
        if req.is_required is not None:
            attr.is_required = int(req.is_required)
        if req.default_value is not None:
            attr.default_value = req.default_value
        if req.enum_values is not None:
            attr.enum_values = _prepare_enum_values(req.enum_values)
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
        await db.execute(
            delete(OntologyTemplateAttribute).where(OntologyTemplateAttribute.template_id == template_id)
        )
        for idx, a in enumerate(attributes):
            db.add(OntologyTemplateAttribute(
                template_id=template_id, name=a.name.strip(), data_type=a.data_type,
                description=(a.description or "").strip(), is_required=int(a.is_required),
                default_value=a.default_value, enum_values=_prepare_enum_values(a.enum_values),
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
                    "data_type": a["data_type"],
                    "is_required": a.get("is_required", False),
                    "enum_values": a.get("enum_values"),
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
            "constraints": constraints,
            "constraint_set": constraint_set,
        }
