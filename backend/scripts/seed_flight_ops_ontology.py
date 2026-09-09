"""Seed 民航航班运行监控本体（定义层）到 KnowSource。

用法：
    cd backend
    python scripts/seed_flight_ops_ontology.py          # 首次生成
    python scripts/seed_flight_ops_ontology.py --force  # 删除同名类别后重建

数据定义见 flight_ops_domain.py。生成后可运行
seed_flight_ops_entities.py 写入示例实体与关系。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, init_db
from models import (
    Ontology,
    OntologyAttribute,
    OntologyAttributeTemplate,
    OntologyCategory,
    OntologyRelation,
    OntologyRelationConstraint,
    OntologyTemplateAttribute,
    OntologyTemplateBinding,
)
from flight_ops_domain import (
    ATTRIBUTE_TEMPLATES,
    ATTRIBUTES,
    CATEGORY_DESC,
    CATEGORY_NAME,
    COLORS,
    CONSTRAINTS,
    ONTOLOGIES,
    RELATIONS,
    TEMPLATE_BINDINGS,
)

SORT_BASE = {
    "航班运行核心类": 100,
    "运行状态事件类": 200,
    "延误异常类": 300,
    "监控告警类": 400,
    "保障资源类": 500,
}


async def _clear_category(db: AsyncSession, cat_id: str) -> None:
    result = await db.execute(select(Ontology.id).where(Ontology.category_id == cat_id))
    ont_ids = [r[0] for r in result.all()]
    if ont_ids:
        await db.execute(delete(OntologyTemplateBinding).where(
            OntologyTemplateBinding.ontology_id.in_(ont_ids)))
        await db.execute(delete(OntologyAttribute).where(
            OntologyAttribute.ontology_id.in_(ont_ids)))
        await db.execute(delete(Ontology).where(Ontology.category_id == cat_id))
    await db.execute(delete(OntologyRelationConstraint).where(
        OntologyRelationConstraint.category_id == cat_id))
    await db.execute(delete(OntologyRelation).where(
        OntologyRelation.category_id == cat_id))
    await db.execute(delete(OntologyCategory).where(OntologyCategory.id == cat_id))


async def seed(force: bool = False) -> None:
    await init_db()

    async with async_session() as db:
        existing_cat = (await db.execute(
            select(OntologyCategory).where(OntologyCategory.name == CATEGORY_NAME)
        )).scalar_one_or_none()

        if existing_cat and not force:
            print(f"本体类别「{CATEGORY_NAME}」已存在（id={existing_cat.id}）。")
            print("如需重新生成，请使用 --force 参数。")
            return
        if existing_cat and force:
            print(f"强制重新生成：删除类别 {existing_cat.id} 及其从属数据...")
            await _clear_category(db, existing_cat.id)
            await db.commit()

        # 1. 类别
        category = OntologyCategory(name=CATEGORY_NAME, description=CATEGORY_DESC)
        db.add(category)
        await db.flush()
        await db.refresh(category)
        cat_id = category.id
        print(f"已创建本体类别：{CATEGORY_NAME}（id={cat_id}）")

        # 2. 属性模板（全局）
        template_id_by_name: dict[str, str] = {}
        for tdef in ATTRIBUTE_TEMPLATES:
            existing_tpl = (await db.execute(
                select(OntologyAttributeTemplate).where(
                    OntologyAttributeTemplate.name == tdef["name"])
            )).scalar_one_or_none()
            if existing_tpl and not force:
                print(f"  属性模板「{tdef['name']}」已存在，跳过。")
                template_id_by_name[tdef["name"]] = existing_tpl.id
                continue
            if existing_tpl and force:
                await db.execute(delete(OntologyTemplateBinding).where(
                    OntologyTemplateBinding.template_id == existing_tpl.id))
                await db.execute(delete(OntologyTemplateAttribute).where(
                    OntologyTemplateAttribute.template_id == existing_tpl.id))
                await db.execute(delete(OntologyAttributeTemplate).where(
                    OntologyAttributeTemplate.id == existing_tpl.id))
            template = OntologyAttributeTemplate(
                name=tdef["name"], description=tdef["description"])
            db.add(template)
            await db.flush()
            await db.refresh(template)
            template_id_by_name[tdef["name"]] = template.id
            for idx, adef in enumerate(tdef["attributes"]):
                db.add(OntologyTemplateAttribute(
                    template_id=template.id,
                    name=adef["name"],
                    code=adef.get("code"),
                    data_type=adef["data_type"],
                    description=adef.get("description", ""),
                    is_required=int(adef.get("is_required", False)),
                    default_value=adef.get("default_value"),
                    sort_order=idx,
                ))
            print(f"  已创建属性模板：{tdef['name']}（{len(tdef['attributes'])} 个属性）")

        # 3. 本体
        ontology_id_by_name: dict[str, str] = {}
        for idx, odef in enumerate(ONTOLOGIES):
            ontology = Ontology(
                category_id=cat_id,
                name=odef["name"],
                code=odef.get("code"),
                description=odef["description"],
                color=COLORS[odef["group"]],
                sort_order=SORT_BASE[odef["group"]] + idx % 100,
            )
            db.add(ontology)
            await db.flush()
            await db.refresh(ontology)
            ontology_id_by_name[odef["name"]] = ontology.id
        print(f"已创建 {len(ONTOLOGIES)} 个本体")

        # 4. 属性
        attr_count = 0
        for ont_name, attrs in ATTRIBUTES.items():
            ont_id = ontology_id_by_name[ont_name]
            for idx, adef in enumerate(attrs):
                db.add(OntologyAttribute(
                    ontology_id=ont_id,
                    name=adef["name"],
                    code=adef.get("code"),
                    data_type=adef["data_type"],
                    description=adef.get("description", ""),
                    is_required=int(adef.get("is_required", False)),
                    default_value=adef.get("default_value"),
                    sort_order=idx,
                ))
                attr_count += 1
        print(f"已创建 {attr_count} 个本体属性")

        # 5. 模板绑定
        binding_count = 0
        for ont_name, template_names in TEMPLATE_BINDINGS.items():
            ont_id = ontology_id_by_name.get(ont_name)
            if not ont_id:
                continue
            for tname in template_names:
                tid = template_id_by_name.get(tname)
                if not tid:
                    continue
                db.add(OntologyTemplateBinding(
                    ontology_id=ont_id, template_id=tid, sort_order=binding_count))
                binding_count += 1
        print(f"已创建 {binding_count} 条模板绑定")

        # 6. 关系字典
        relation_id_by_name: dict[str, str] = {}
        for rdef in RELATIONS:
            relation = OntologyRelation(
                category_id=cat_id,
                name=rdef["name"],
                code=rdef.get("code"),
                description=rdef.get("description", ""),
                inverse_name=rdef.get("inverse", ""),
            )
            db.add(relation)
            await db.flush()
            await db.refresh(relation)
            relation_id_by_name[rdef["name"]] = relation.id
        print(f"已创建 {len(RELATIONS)} 个关系定义")

        # 7. 三元组约束
        constraint_count = 0
        for src_name, rel_name, tgt_name in CONSTRAINTS:
            src_id = ontology_id_by_name.get(src_name)
            rel_id = relation_id_by_name.get(rel_name)
            tgt_id = ontology_id_by_name.get(tgt_name)
            if not (src_id and rel_id and tgt_id):
                print(f"  [跳过] 无效约束：({src_name}, {rel_name}, {tgt_name})")
                continue
            db.add(OntologyRelationConstraint(
                category_id=cat_id,
                source_ontology_id=src_id,
                relation_id=rel_id,
                target_ontology_id=tgt_id,
            ))
            constraint_count += 1
        print(f"已创建 {constraint_count} 条三元组约束")

        await db.commit()
        print(f"\n[完成] {CATEGORY_NAME} 定义层生成完毕："
              f"本体 {len(ONTOLOGIES)} / 属性 {attr_count} / 关系 {len(RELATIONS)} / "
              f"约束 {constraint_count} / 模板 {len(template_id_by_name)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 民航航班运行监控本体")
    parser.add_argument("--force", action="store_true", help="若类别已存在则删除重建")
    args = parser.parse_args()
    asyncio.run(seed(args.force))


if __name__ == "__main__":
    main()
