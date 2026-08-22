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

from models import (
    Entity,
    File,
    Ontology,
    OntologyAttribute,
    OntologyRelation,
    OntologyService,
    OntologyTemplateAttribute,
    OntologyTemplateBinding,
    Relation,
)
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


def _format_property_preview(props: dict, limit: int = 3) -> str:
    """实体列表的属性概要：仅取前 N 个字段的 key: value，值截断避免过长。"""
    if not props:
        return ""
    parts = []
    for k, v in list(props.items())[:limit]:
        s = str(v)
        if len(s) > 24:
            s = s[:24] + "…"
        parts.append(f"{k}: {s}")
    return " · ".join(parts)


def _merge_properties(old_raw: str | None, new_props: dict | None) -> dict:
    """按字段合并实体属性，保证「增量累积、不丢旧值」。

    规则（对应产品诉求：新属性更丰富则更新，不要把旧属性改没，没有的新增）：
    - 以旧属性为基底；
    - 新值非空 → 覆盖/新增该字段；
    - 新值为空 → 保留旧值，不动；
    - 仅旧值有的字段 → 原样保留。
    """
    merged = _parse_properties(old_raw)
    for key, value in (new_props or {}).items():
        if value is None or str(value).strip() == "":
            continue  # 新值为空：不覆盖旧值
        merged[key] = value
    return merged


def _normalize_entity_value(value: str, default: str = "") -> str:
    return (value or "").strip() or default


def _entity_key(value: str) -> str:
    return (value or "").strip().lower()


def _serialize_entity(
    ent: Entity,
    *,
    ontology_name: str | None = None,
    include_properties: bool = True,
) -> dict:
    props = _parse_properties(ent.properties)
    return {
        "id": ent.id,
        "kb_id": ent.kb_id,
        "ontology_id": ent.ontology_id,
        "ontology_name": ontology_name,
        "entity_type": ent.entity_type,
        "name": ent.name,
        "description": ent.description or "",
        "properties": props if include_properties else None,
        "property_count": len(props),
        "property_preview": _format_property_preview(props),
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
        category_id: str | None = None,
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
        if category_id:
            filters.append(
                Entity.ontology_id.in_(
                    select(Ontology.id).where(Ontology.category_id == category_id)
                )
            )
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

        # 批量统计：关系度数 + 服务数（继承/自定义）
        ent_ids = [r.id for r in rows]

        relation_counts: dict[str, int] = {}
        if ent_ids:
            for col in (Relation.source_entity_id, Relation.target_entity_id):
                cnt_rows = await db.execute(
                    select(col, func.count()).where(col.in_(ent_ids)).group_by(col)
                )
                for eid, c in cnt_rows:
                    relation_counts[eid] = relation_counts.get(eid, 0) + c

        service_custom: dict[str, int] = {}
        service_inherited: dict[str, int] = {}
        if ent_ids:
            svc_rows = await db.execute(
                select(OntologyService.entity_id, func.count())
                .where(
                    OntologyService.entity_id.in_(ent_ids),
                    OntologyService.owner_type == "entity",
                    OntologyService.is_enabled == 1,
                )
                .group_by(OntologyService.entity_id)
            )
            service_custom = {eid: c for eid, c in svc_rows}
        if ont_ids:
            ont_svc_rows = await db.execute(
                select(OntologyService.ontology_id, func.count())
                .where(
                    OntologyService.ontology_id.in_(ont_ids),
                    OntologyService.owner_type == "ontology",
                    OntologyService.is_enabled == 1,
                )
                .group_by(OntologyService.ontology_id)
            )
            service_inherited = {oid: c for oid, c in ont_svc_rows}

        # 本体属性名/编码（自有 + 模板），用于判断实体属性是否「继承自本体」
        inherited_keys: dict[str, set[str]] = {}
        if ont_ids:
            own_rows = await db.execute(
                select(OntologyAttribute.ontology_id, OntologyAttribute.name, OntologyAttribute.code)
                .where(OntologyAttribute.ontology_id.in_(ont_ids))
            )
            for oid, name, code in own_rows:
                s = inherited_keys.setdefault(oid, set())
                if name:
                    s.add(name.strip().lower())
                if code:
                    s.add(code.strip().lower())
            tpl_rows = await db.execute(
                select(OntologyTemplateBinding.ontology_id, OntologyTemplateAttribute.name, OntologyTemplateAttribute.code)
                .join(OntologyTemplateAttribute, OntologyTemplateBinding.template_id == OntologyTemplateAttribute.template_id)
                .where(OntologyTemplateBinding.ontology_id.in_(ont_ids))
            )
            for oid, name, code in tpl_rows:
                s = inherited_keys.setdefault(oid, set())
                if name:
                    s.add(name.strip().lower())
                if code:
                    s.add(code.strip().lower())

        items = []
        for r in rows:
            props = _parse_properties(r.properties)
            prop_keys = {k.strip().lower() for k in props if k}
            inherited = sum(1 for k in prop_keys if k in inherited_keys.get(r.ontology_id, set()))
            custom_svc = service_custom.get(r.id, 0)
            inherited_svc = service_inherited.get(r.ontology_id, 0)

            it = _serialize_entity(r, ontology_name=ont_map.get(r.ontology_id), include_properties=False)
            it["relation_count"] = relation_counts.get(r.id, 0)
            it["property_inherited_count"] = inherited
            it["property_custom_count"] = len(props) - inherited
            it["service_count"] = custom_svc + inherited_svc
            it["service_inherited_count"] = inherited_svc
            it["service_custom_count"] = custom_svc
            items.append(it)
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

        if ent.source_file_id:
            file_row = await db.execute(select(File.name).where(File.id == ent.source_file_id))
            result["source_file_name"] = file_row.scalar_one_or_none()

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
        normalized_name = _normalize_entity_value(name)
        normalized_type = _normalize_entity_value(entity_type, "UNKNOWN")
        existing_row = await db.execute(
            select(Entity).where(
                Entity.kb_id == kb_id,
                func.lower(Entity.entity_type) == normalized_type.lower(),
                func.lower(Entity.name) == normalized_name.lower(),
            )
        )
        ent = existing_row.scalar_one_or_none()
        if ent is None:
            ent = Entity(
                kb_id=kb_id,
                ontology_id=ontology_id,
                entity_type=normalized_type,
                name=normalized_name,
                description=(description or "").strip(),
                properties=_dump_properties(properties),
                source_file_id=source_file_id,
                source_chunk_id=source_chunk_id,
            )
            db.add(ent)
            await db.flush()
        else:
            # upsert：更新本体归属、名称；描述与属性走「增量累积」语义。
            # 描述：新值更丰富（更长）才覆盖，避免把更完整的旧描述改没；
            # 属性：按字段合并（_merge_properties），旧字段不丢、新字段新增。
            ent.ontology_id = ontology_id
            ent.entity_type = normalized_type
            ent.name = normalized_name
            if description is not None:
                old_desc = ent.description or ""
                new_desc = description.strip()
                ent.description = new_desc if len(new_desc) >= len(old_desc) else old_desc
            if properties is not None:
                ent.properties = _dump_properties(_merge_properties(ent.properties, properties))
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

        # 级联删除：该实体的自定义服务
        from services.ontology_action_service import OntologyServiceService

        await OntologyServiceService.delete_for_entities(db, [entity_id])

        await db.delete(ent)
        await db.commit()

        # Kùzu 同步：delete_entity 会级联删除该实体参与的 Kùzu Relation 节点
        _sync_delete_entity(entity_id)
        return True

    @staticmethod
    async def merge_entities(
        db: AsyncSession,
        *,
        canonical_id: str,
        merged_ids: list[str],
        kb_id: str,
    ) -> dict:
        """把 merged_ids 的实体并入 canonical_id（同知识库内）。

        - 重写它们参与的关系端点 -> canonical；
        - 丢弃自环(两端皆 canonical)与重复(同 source+relation_type+target)关系；
        - 合并属性(_merge_properties，旧字段不丢)、描述取更长；
        - 被合并实体的旧名写入 canonical.properties.aliases（软别名，免迁移）；
        - 删除被合并实体；SQLite 为权威，Kùzu best-effort 同步（经 providers.graph_store 公开函数）。

        关键：Kùzu 的 delete_entity 会级联删触及该实体的 Relation 节点，
        故必须「先改端点 / 删关系 -> 后删实体」，否则会误删本应改挂到 canonical 的关系。
        """
        merged_ids = [mid for mid in (merged_ids or []) if mid and mid != canonical_id]
        if not canonical_id or not merged_ids:
            return {"canonical_id": canonical_id, "merged_count": 0,
                    "relations_rewired": 0, "relations_dropped": 0}

        canonical = await db.get(Entity, canonical_id)
        if not canonical:
            raise ValueError("canonical entity not found")
        merged_map: dict[str, Entity] = {}
        for mid in merged_ids:
            e = await db.get(Entity, mid)
            if e:
                merged_map[mid] = e
        if not merged_map:
            return {"canonical_id": canonical_id, "merged_count": 0,
                    "relations_rewired": 0, "relations_dropped": 0}
        for e in [canonical, *merged_map.values()]:
            if e.kb_id != kb_id:
                raise ValueError("all entities must belong to the same kb")

        id_remap = {mid: canonical_id for mid in merged_map}

        # 1. 取所有触及 merged 的关系，计算新端点、判自环
        touched_row = await db.execute(
            select(Relation).where(
                or_(Relation.source_entity_id.in_(merged_ids),
                    Relation.target_entity_id.in_(merged_ids))
            )
        )
        candidates: list[tuple[Relation, str, str]] = []  # (rel, new_src, new_tgt)
        to_drop_rels: list[Relation] = []
        touched_ids: set[str] = set()
        for rel in touched_row.scalars().all():
            touched_ids.add(rel.id)
            ns = id_remap.get(rel.source_entity_id, rel.source_entity_id)
            nt = id_remap.get(rel.target_entity_id, rel.target_entity_id)
            if ns == nt:  # 两端都并入 canonical -> 自环，丢弃
                to_drop_rels.append(rel)
                continue
            candidates.append((rel, ns, nt))

        # 2. 把「本就指向 canonical」的关系也纳入，用于 5 元组去重
        canon_row = await db.execute(
            select(Relation).where(
                or_(Relation.source_entity_id == canonical_id,
                    Relation.target_entity_id == canonical_id)
            )
        )
        for rel in canon_row.scalars().all():
            if rel.id in touched_ids:
                continue
            candidates.append((rel, rel.source_entity_id, rel.target_entity_id))

        # 3. 按「计算出的新端点」(new_src, relation_type, new_tgt) 去重，保留 created_at 最早。
        #    关键：必须用计算值（而非改后的 ORM 字段）去重，并先删重复/自环再 flush，
        #    然后才改存活端点——否则 commit 时 UPDATE 会先于 DELETE 落库，
        #    与尚未删除的重复行短暂撞上 relations 的 (kb,src,type,tgt) 唯一约束。
        candidates.sort(key=lambda t: (t[0].created_at or ""))
        keep_rels: list[tuple[Relation, str, str]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for rel, ns, nt in candidates:
            key = (ns, rel.relation_type, nt)
            if key in seen_keys:
                if rel not in to_drop_rels:
                    to_drop_rels.append(rel)
            else:
                seen_keys.add(key)
                keep_rels.append((rel, ns, nt))

        # 4. 先物理删除自环 / 重复关系并 flush，腾出唯一键空间
        dropped_rel_ids = [rel.id for rel in to_drop_rels]
        for rel in to_drop_rels:
            await db.delete(rel)
        if to_drop_rels:
            await db.flush()

        # 5. 再改存活关系的端点（此时已无重复行占位，UPDATE 不会撞唯一键）
        rewired_rels: list[Relation] = []
        for rel, ns, nt in keep_rels:
            changed = False
            if rel.source_entity_id != ns:
                rel.source_entity_id = ns
                changed = True
            if rel.target_entity_id != nt:
                rel.target_entity_id = nt
                changed = True
            if changed:
                rewired_rels.append(rel)

        # 6. 合并属性 / 描述 / 软别名
        merged_props = _parse_properties(canonical.properties)
        for e in merged_map.values():
            merged_props = _merge_properties(
                _dump_properties(merged_props), _parse_properties(e.properties)
            )
            if (e.description or "") and len(e.description) > len(canonical.description or ""):
                canonical.description = e.description
        aliases = list(merged_props.get("aliases") or [])
        for e in merged_map.values():
            if e.name and e.name != canonical.name and e.name not in aliases:
                aliases.append(e.name)
        if aliases:
            merged_props["aliases"] = aliases
        canonical.properties = _dump_properties(merged_props)
        canonical.updated_at = datetime.now().isoformat()

        # 7. 删除被合并实体（SQLite 权威）+ 级联删除其自定义服务
        from services.ontology_action_service import OntologyServiceService

        await OntologyServiceService.delete_for_entities(db, list(merged_map.keys()))
        for e in merged_map.values():
            await db.delete(e)
        await db.commit()
        await db.refresh(canonical)

        # 8. Kùzu 同步（best-effort）：先刷新存活关系、删丢弃关系，最后删被合并实体
        _sync_upsert_entity(canonical)
        for rel in rewired_rels:
            _sync_upsert_relation(rel)
        for rid in dropped_rel_ids:
            _sync_delete_relation(rid)
        for mid in merged_map:
            _sync_delete_entity(mid)

        return {
            "canonical_id": canonical_id,
            "merged_count": len(merged_map),
            "relations_rewired": len(rewired_rels),
            "relations_dropped": len(to_drop_rels),
        }

    @staticmethod
    async def delete_entities(db: AsyncSession, entity_ids: list[str]) -> int:
        n = 0
        for eid in (entity_ids or []):
            if await EntityService.delete_entity(db, eid):
                n += 1
        return n

    @staticmethod
    async def delete_relations(db: AsyncSession, relation_ids: list[str]) -> int:
        n = 0
        for rid in (relation_ids or []):
            if await EntityService.delete_relation(db, rid):
                n += 1
        return n

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
