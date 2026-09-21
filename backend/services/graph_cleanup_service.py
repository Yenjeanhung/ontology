"""图谱清洗服务：对已抽取的图提出「合并 / 删除」建议并执行。

设计要点：
- 建议为确定性启发式（名称相似度聚簇 + 低价值/孤岛识别 + 通用关系黑名单），不入库；
  由前端审核后显式 apply。
- 合并建议含两条通道：
  * 字面通道——SequenceMatcher 名称相似度聚簇（近形变体）；
  * 语义通道——「实体名+描述」embedding 近邻比对（简称/全称/别名等语义同、字面远的重复），
    向量缓存在 entity_vectors 派生表，仅增量编码，可整表重建（见本文件 _semantic_merge_suggestions）。
- 合并 / 删除均复用 ``EntityService``，图库同步经 ``providers.graph_store`` 公开函数，
  不与具体图库绑定（Kùzu / Neo4j 均可）。
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import logging
import re
from datetime import datetime

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Entity, EntityVector, Ontology, Relation
from providers.embedding import create_embeddings
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


# ===== 语义实体对齐：embedding 缓存 + blocking + verification =====
# 设计见 doc/知识库/实体语义对齐与知识入库流程.md §4-§5
# - 缓存：entity_vectors 派生表存「实体名+描述」向量（float32→base64，方言无关），
#   content_hash（模型|文本）惰性失效，可整表 DROP 重建；
# - 增量编码：仅对无有效缓存的实体调用嵌入模型（编码是主要成本），
#   首轮全量，之后每轮只算新增/变更部分，单轮成本只随增量线性增长；
# - blocking：比对在向量缓存之上按类型做矩阵乘取 top-k 近邻为候选——常规类型全量比对
#   （建议与字面通道一致地可重复出现），单类型超过矩阵上限才退化为只比增量侧，
#   十万级实体按类型分桶后单类型矩阵乘毫秒~百毫秒级；
# - verification：候选对过余弦阈值 + 防误合规则（_are_distinct_products）后聚簇出建议。


def _entity_embed_text(name: str, description: str) -> str:
    """参与编码的实体文本：名称 + 截断后的描述。"""
    limit = settings.GRAPH_CLEANUP_SEMANTIC_TEXT_MAXLEN
    return f"{name or ''}\n{(description or '')[:limit]}"


def _embed_content_hash(text: str) -> str:
    """缓存失效指纹：provider/模型名参与哈希，换嵌入模型自动全量失效。"""
    basis = f"{settings.EMBEDDING_PROVIDER}|{settings.EMBEDDING_MODEL}|{text}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _embedding_model_tag() -> str:
    return f"{settings.EMBEDDING_PROVIDER}:{settings.EMBEDDING_MODEL}"


def _encode_vector(vec) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode("ascii")


def _decode_vector(raw: str):
    """base64 → float32 ndarray；损坏/为空返回 None（调用方视为过期重算）。"""
    try:
        arr = np.frombuffer(base64.b64decode(raw), dtype=np.float32)
        return arr if arr.size else None
    except Exception:
        return None


def _make_union_find(ids: list[str]):
    """并查集（路径压缩）：返回 (find, union)，闭包共享同一 parent 表。"""
    parent = {x: x for x in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    return find, union


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
                    "source": "literal",
                })

        # ----- 合并建议（语义通道）：语义同、字面远的重复（简称/全称/别名）。
        # 与字面通道去重：语义组若与已有建议存在成员交集则整组跳过——
        # 宁少勿重，避免同一实体出现在多个建议组导致 apply 时二次合并踩已删实体；
        # 被跳过的组合并执行后实体消失，后续轮次自然收敛。-----
        semantic_added = 0
        if merge_groups or ents:
            semantic_groups = await GraphCleanupService._semantic_merge_suggestions(
                db, ents, degree
            )
            covered: set[str] = set()
            for g in merge_groups:
                covered.add(g["canonical_id"])
                covered.update(m["id"] for m in g["members"])
            for g in semantic_groups:
                if covered & {m["id"] for m in g["members"]}:
                    continue
                merge_groups.append(g)
                semantic_added += 1

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
                "semantic_merge_group_count": semantic_added,
                "delete_entity_count": len(delete_entities),
                "delete_relation_count": len(delete_relations),
            },
        }

    @staticmethod
    async def _semantic_merge_suggestions(
        db: AsyncSession,
        ents: list[Entity],
        degree: dict[str, int],
    ) -> list[dict]:
        """语义通道：对「语义同、字面远」的重复实体（简称/全称/别名）生成合并建议。

        增量编码：entity_vectors 中无有效缓存（缺失 / content_hash 过期 / 解码失败）的实体
        视为本轮增量，仅对增量调用嵌入模型（编码是主要成本，首轮全量、之后线性增长）；
        比对在向量缓存之上按类型做矩阵乘：常规类型全量比对（建议可重复出现，与字面通道
        行为一致），超过矩阵上限的超大类型只比增量侧。

        写路径仅涉及派生的 entity_vectors 缓存表（建议本身仍不入库），不触碰业务表；
        缓存写入失败只降级为「本轮不产出语义建议」，不影响清洗主流程。
        """
        if not settings.GRAPH_CLEANUP_SEMANTIC_ENABLED or len(ents) < 2:
            return []
        if len(ents) > settings.GRAPH_CLEANUP_SEMANTIC_MAX_ENTITIES:
            logger.warning(
                "[语义对齐] 实体数 %d 超过上限 %d，本轮跳过语义通道"
                "（如需放开可调大 GRAPH_CLEANUP_SEMANTIC_MAX_ENTITIES）",
                len(ents), settings.GRAPH_CLEANUP_SEMANTIC_MAX_ENTITIES,
            )
            return []

        threshold = settings.GRAPH_CLEANUP_SEMANTIC_THRESHOLD
        topk = max(1, settings.GRAPH_CLEANUP_SEMANTIC_TOPK)

        # 1. 文本与内容指纹（模型名参与哈希，换模型自动全量失效）
        texts = {e.id: _entity_embed_text(e.name, e.description or "") for e in ents}
        hashes = {eid: _embed_content_hash(t) for eid, t in texts.items()}
        model_tag = _embedding_model_tag()

        # 2. 加载向量缓存（IN 分批，兼容 SQLite 变量数上限）
        cached: dict[str, EntityVector] = {}
        ent_ids = [e.id for e in ents]
        for i in range(0, len(ent_ids), 500):
            rows = await db.execute(
                select(EntityVector).where(EntityVector.entity_id.in_(ent_ids[i:i + 500]))
            )
            for row in rows.scalars():
                cached[row.entity_id] = row

        # 3. 分桶：有效缓存 / 待编码（缺失、过期、模型不匹配、解码失败）
        valid_vecs: dict[str, np.ndarray] = {}
        stale_ids: list[str] = []
        for e in ents:
            row = cached.get(e.id)
            arr = _decode_vector(row.vec) if row else None
            if (
                row is not None
                and arr is not None
                and row.model == model_tag
                and row.content_hash == hashes[e.id]
            ):
                valid_vecs[e.id] = arr
            else:
                stale_ids.append(e.id)

        # 4. 增量编码并回写缓存（同步阻塞调用，与项目内其他 embed 用法一致；
        #    merge 按主键 upsert，重复运行幂等）
        new_vecs: dict[str, np.ndarray] = {}
        if stale_ids:
            try:
                embeddings = create_embeddings()
            except Exception as exc:
                logger.warning("[语义对齐] 嵌入模型不可用，跳过语义通道: %s", exc)
                return []
            kb_of = {e.id: (e.kb_id or "") for e in ents}
            batch = 64
            for i in range(0, len(stale_ids), batch):
                part = stale_ids[i:i + batch]
                try:
                    vectors = embeddings.embed_documents([texts[eid] for eid in part])
                except Exception as exc:
                    logger.warning("[语义对齐] 向量编码失败，跳过语义通道: %s", exc)
                    return []
                for eid, vec in zip(part, vectors):
                    new_vecs[eid] = np.asarray(vec, dtype=np.float32)
            try:
                for eid, arr in new_vecs.items():
                    await db.merge(EntityVector(
                        entity_id=eid,
                        kb_id=kb_of.get(eid, ""),
                        vec=_encode_vector(arr),
                        dim=int(arr.size),
                        model=model_tag,
                        content_hash=hashes[eid],
                        updated_at=datetime.now().isoformat(),
                    ))
                await db.commit()
            except Exception as exc:
                logger.warning("[语义对齐] 向量缓存回写失败（不影响建议生成，下轮重算）: %s", exc)
                await db.rollback()

        all_vecs = {**valid_vecs, **new_vecs}
        if len(all_vecs) < 2:
            return []

        # 5. 逐类型 blocking + verification（同类型才可能同实体）
        # 编码只做增量（步骤 4），比对尽量全量：向量已缓存后 n×n 只是矩阵乘（毫秒~百毫秒级），
        # 保证建议与字面通道一致地「每次都给」（用户未处理也不消失）；
        # 单类型超过矩阵上限时退化为只比增量侧，防超大类型的 n² 矩阵内存。
        id2ent = {e.id: e for e in ents}
        by_type: dict[str, list[Entity]] = {}
        for e in ents:
            by_type.setdefault(e.entity_type or "UNKNOWN", []).append(e)

        type_limit = max(2, settings.GRAPH_CLEANUP_SEMANTIC_TYPE_MATRIX_LIMIT)
        suggestions: list[dict] = []
        for etype, group in by_type.items():
            gid = [e.id for e in group if e.id in all_vecs]
            if len(gid) < 2:
                continue
            new_in_group = [eid for eid in gid if eid in new_vecs]
            query_ids = gid if len(gid) <= type_limit else new_in_group
            if not query_ids:
                continue
            idx_of = {eid: k for k, eid in enumerate(gid)}
            mat = np.stack([all_vecs[eid] for eid in gid]).astype(np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            mat /= norms  # 归一化后点积即余弦相似度
            sims = mat[[idx_of[eid] for eid in query_ids]] @ mat.T  # (查询侧, 全体)

            find, union = _make_union_find(gid)
            for r, src in enumerate(query_ids):
                row = sims[r]
                row[idx_of[src]] = -1.0  # 排除自身
                k = min(topk, row.size - 1)
                if k <= 0:
                    continue
                for c in np.argpartition(-row, k)[:k]:
                    if float(row[c]) < threshold:
                        continue
                    dst = gid[int(c)]
                    if _are_distinct_products(id2ent[src].name, id2ent[dst].name):
                        continue
                    union(src, dst)

            clusters: dict[str, list[str]] = {}
            for eid in gid:
                clusters.setdefault(find(eid), []).append(eid)
            for members in clusters.values():
                if len(members) < 2:
                    continue
                # canonical 与字面通道同规则：度数最高，并列取名字最短
                canonical = sorted(
                    members, key=lambda eid: (-degree.get(eid, 0), len(id2ent[eid].name or ""))
                )[0]
                suggestions.append({
                    "canonical_id": canonical,
                    "canonical_name": id2ent[canonical].name,
                    "entity_type": etype,
                    "members": [
                        {"id": m, "name": id2ent[m].name, "degree": degree.get(m, 0)}
                        for m in members
                    ],
                    "reason": "语义相似(名称+描述向量)",
                    "source": "semantic",
                })
        return suggestions

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

        # 顺手清理已删实体的向量缓存（派生表，避免残留孤儿行；合并的 merged_ids 实体同样已删）
        removed_ids: set[str] = set(delete_entity_ids or [])
        for item in (merges or []):
            removed_ids.update(item.get("merged_ids") or [])
        if removed_ids:
            removed_list = list(removed_ids)
            for i in range(0, len(removed_list), 500):
                await db.execute(
                    delete(EntityVector).where(EntityVector.entity_id.in_(removed_list[i:i + 500]))
                )
            await db.commit()

        return {
            "kb_id": kb_id,
            "merged": merged_total,
            "relations_rewired": relations_rewired,
            "relations_dropped": relations_dropped,
            "entities_deleted": ent_deleted,
            "relations_deleted": rel_deleted,
        }
