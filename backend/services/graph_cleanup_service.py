"""图谱清洗服务：对已抽取的图提出「合并 / 删除」建议并执行。

设计要点：
- 建议为确定性启发式（名称相似度聚簇 + 低价值/孤岛识别 + 通用关系黑名单），不入库；
  由前端审核后显式 apply。
- 合并 / 删除均复用 ``EntityService``，Kùzu 同步经 ``providers.graph_store`` 公开函数，
  不与具体图库绑定（现 Kùzu，将来可换 Neo4j）。
"""

from __future__ import annotations

import difflib
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Entity, Ontology, Relation
from services.entity_service import EntityService
from services.graph_extraction_service import (
    _generic_relation_blocklist,
    _is_low_value_entity_name,
)

logger = logging.getLogger(__name__)

_NAME_SIM_THRESHOLD = 0.72      # 名称相似度（SequenceMatcher ratio）>= 此值视为可能重复
_MAX_PAIRWISE_PER_TYPE = 600    # 单类型超过此数量则跳过该类型合并建议，避免 O(n²) 爆炸


def _norm_name(s: str) -> str:
    return (s or "").strip().lower()


def _name_similarity(a: str, b: str) -> float:
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# 末尾 ASCII 型号编码（如 享界S9 的 "s9"、问界M9 的 "m9"、iPhone15 的 "15"）
_TAIL_CODE = re.compile(r"([A-Za-z0-9]+)\s*$")


def _brand_and_code(name: str) -> tuple[str, str]:
    """拆出 (品牌前缀, 末尾型号编码)。型号编码须含数字或为≥2字母词，否则视为无编码。"""
    n = _norm_name(name)
    m = _TAIL_CODE.search(n)
    if not m:
        return n, ""
    code = m.group(1)
    if not re.search(r"\d", code) and not (len(code) >= 2 and code.isalpha()):
        return n, ""
    return n[: m.start()].strip(), code


def _are_distinct_products(a: str, b: str) -> bool:
    """品牌相同但型号编码不同 → 视为不同产品，不应合并（享界S9 / 享界G9）。"""
    brand_a, code_a = _brand_and_code(a)
    brand_b, code_b = _brand_and_code(b)
    if code_a and code_b and brand_a and brand_a == brand_b and code_a != code_b:
        return True
    return False


class GraphCleanupService:
    """图谱清洗：建议（只读）+ 执行（写入，复用 EntityService）。"""

    @staticmethod
    async def suggest_cleanup(
        db: AsyncSession,
        kb_id: str | None = None,
        category_id: str | None = None,
        ontology_id: str | None = None,
    ) -> dict:
        """对指定知识库或本体类别的实体/关系给出清洗建议，纯查询、不写库。"""
        # 收集目标本体 ID
        ontology_ids = []
        if ontology_id:
            ontology_ids = [ontology_id]
        elif category_id:
            rows = await db.execute(
                select(Ontology.id).where(Ontology.category_id == category_id)
            )
            ontology_ids = rows.scalars().all()

        ent_q = select(Entity)
        rel_q = select(Relation)
        if kb_id:
            ent_q = ent_q.where(Entity.kb_id == kb_id)
            rel_q = rel_q.where(Relation.kb_id == kb_id)
        if ontology_ids:
            ent_q = ent_q.where(Entity.ontology_id.in_(ontology_ids))
            rel_q = rel_q.where(Relation.source_entity_id.in_(
                select(Entity.id).where(Entity.ontology_id.in_(ontology_ids))
            ))

        ent_row = await db.execute(ent_q)
        ents = ent_row.scalars().all()
        rel_row = await db.execute(rel_q)
        rels = rel_row.scalars().all()

        # 度数（关系数）
        degree: dict[str, int] = {}
        for r in rels:
            degree[r.source_entity_id] = degree.get(r.source_entity_id, 0) + 1
            degree[r.target_entity_id] = degree.get(r.target_entity_id, 0) + 1

        def deg(eid: str) -> int:
            return degree.get(eid, 0)

        # ----- 合并建议：按 entity_type 分组，组内按名称相似度聚簇 -----
        by_type: dict[str, list[Entity]] = {}
        for e in ents:
            by_type.setdefault((e.entity_type or "UNKNOWN"), []).append(e)

        merge_groups: list[dict] = []
        for etype, group in by_type.items():
            if len(group) < 2 or len(group) > _MAX_PAIRWISE_PER_TYPE:
                continue
            parent = {e.id: e.id for e in group}

            def find(x: str) -> str:
                while parent[x] != x:  # noqa: E731 — 闭包绑定到本次循环的 parent
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: str, b: str) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    if (
                        _name_similarity(group[i].name, group[j].name) >= _NAME_SIM_THRESHOLD
                        and not _are_distinct_products(group[i].name, group[j].name)
                    ):
                        union(group[i].id, group[j].id)

            clusters: dict[str, list[Entity]] = {}
            for e in group:
                clusters.setdefault(find(e.id), []).append(e)
            for members in clusters.values():
                if len(members) < 2:
                    continue
                # canonical：度数最高，并列取名字最短
                canonical = sorted(members, key=lambda e: (-deg(e.id), len(e.name or "")))[0]
                merge_groups.append({
                    "canonical_id": canonical.id,
                    "canonical_name": canonical.name,
                    "entity_type": etype,
                    "members": [
                        {"id": m.id, "name": m.name, "degree": deg(m.id)} for m in members
                    ],
                    "reason": "名称高度相似",
                })

        # ----- 删除实体建议：仅低价值实体名（日期/数值/整句/URL/版本号）。
        # 刻意「不」建议删除度数=0 的孤岛节点：删通用关系后大量实体会临时变成孤岛，
        # 若再建议删孤岛会在反复「全部清洗」时级联清空整个图谱（曾导致数据全丢）。
        # 孤岛节点非错误数据，如需清理由用户在实体页手动删除。-----
        delete_entities: list[dict] = []
        for e in ents:
            if _is_low_value_entity_name(e.name):
                delete_entities.append({
                    "id": e.id, "name": e.name,
                    "entity_type": e.entity_type, "degree": deg(e.id),
                    "reason": "低价值实体名(日期/数值/整句等)",
                })

        # ----- 删除关系建议：无语义通用关系 -----
        block = _generic_relation_blocklist()
        delete_relations: list[dict] = []
        for r in rels:
            if r.relation_type in block:
                delete_relations.append({
                    "id": r.id, "relation_type": r.relation_type,
                    "source_entity_id": r.source_entity_id,
                    "target_entity_id": r.target_entity_id,
                })

        return {
            "kb_id": kb_id or "",
            "category_id": category_id or "",
            "ontology_id": ontology_id or "",
            "merge_groups": merge_groups,
            "delete_entities": delete_entities,
            "delete_relations": delete_relations,
            "summary": {
                "entity_total": len(ents),
                "relation_total": len(rels),
                "merge_group_count": len(merge_groups),
                "delete_entity_count": len(delete_entities),
                "delete_relation_count": len(delete_relations),
            },
        }

    @staticmethod
    async def apply_cleanup(
        db: AsyncSession,
        *,
        kb_id: str = "",
        category_id: str = "",
        ontology_id: str = "",
        merges: list[dict] | None = None,
        delete_entity_ids: list[str] | None = None,
        delete_relation_ids: list[str] | None = None,
    ) -> dict:
        """执行清洗：逐组合并、批量删除关系/实体。复用 EntityService，自动同步 Kùzu。"""
        merges = merges or []
        delete_entity_ids = delete_entity_ids or []
        delete_relation_ids = delete_relation_ids or []

        # 安全护栏：单次清洗删除占比超过阈值则中止，避免误操作清空整个图谱。
        # 合并操作的 merged_ids 实体最终也会被删，计入待删实体数。
        max_ratio = getattr(settings, "GRAPH_CLEANUP_MAX_DELETE_RATIO", 0.5)
        ent_total = await db.scalar(
            select(func.count()).select_from(Entity).where(Entity.kb_id == kb_id)
        ) or 0
        rel_total = await db.scalar(
            select(func.count()).select_from(Relation).where(Relation.kb_id == kb_id)
        ) or 0
        will_del_ents = len(delete_entity_ids) + sum(
            len(item.get("merged_ids") or []) for item in merges
        )
        will_del_rels = len(delete_relation_ids)
        if ent_total and will_del_ents / ent_total > max_ratio:
            raise ValueError(
                f"安全限制：本次将删除 {will_del_ents}/{ent_total} 个实体"
                f"（超过 {int(max_ratio * 100)}%），已中止。请减少勾选或分批清洗。"
            )
        if rel_total and will_del_rels / rel_total > max_ratio:
            raise ValueError(
                f"安全限制：本次将删除 {will_del_rels}/{rel_total} 条关系"
                f"（超过 {int(max_ratio * 100)}%），已中止。请减少勾选或分批清洗。"
            )

        merged_total = relations_rewired = relations_dropped = 0
        for item in (merges or []):
            res = await EntityService.merge_entities(
                db,
                canonical_id=item["canonical_id"],
                merged_ids=item.get("merged_ids") or [],
                kb_id=kb_id,
            )
            merged_total += res.get("merged_count", 0)
            relations_rewired += res.get("relations_rewired", 0)
            relations_dropped += res.get("relations_dropped", 0)
        # 先删关系、再删实体，避免关系端点先于关系本身失效
        rel_deleted = await EntityService.delete_relations(db, delete_relation_ids or [])
        ent_deleted = await EntityService.delete_entities(db, delete_entity_ids or [])
        return {
            "kb_id": kb_id,
            "merged": merged_total,
            "relations_rewired": relations_rewired,
            "relations_dropped": relations_dropped,
            "entities_deleted": ent_deleted,
            "relations_deleted": rel_deleted,
        }
