"""图推理服务：规则推理（建议落审核闭环）+ 场景化传播分析（即时查询）。

设计来源：《图迁入与图计算推理_功能设计》6 节。
- 规则推理是长任务：复用 ``graph_analysis_tasks``（kind='inference', algorithm='rules_v1'），
  候选 Cypher 物化（闸1 在图上挡存量）→ Python 过白名单/tombstone/待审重复（闸2/3/5）
  → 置信度闸门（闸4，规则级）→ 落 ``relation_suggestions``（source='rule'，evidence 记推理路径）；
- 传播分析 4 个同步 API：propagation/impact/similar/path，统一返回 ``{nodes, edges, chains}``，
  纯 Cypher 不依赖 GDS（similar 例外，需投影护栏）；
- 审核闭环：批准走 ``EntityService.create_relation`` 双写链路（与手工建关系一致，PG 权威先行）
  + 顺带写一条到分析图（同语义关系，批准即时可见，增量迁入随后对齐）；拒绝写 tombstone。
- 并发约定：规则/传播 Cypher 在工作线程执行（不碰 PG）；闸门与落库在主 loop（async_session）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from models import (Entity, GraphAnalysisTask, GraphSyncRun, OntologyCategory,
                    OntologyRelation, OntologyRelationConstraint, RelationSuggestion,
                    RelationSuggestionTombstone)
from providers.graph_store import gds
from services.graph_analysis_service import _count_category_nodes, _driver
from services.graph_sync_service import _esc

logger = logging.getLogger(__name__)

PROPAGATION_DECAY = 0.8        # 传播链每多一跳置信度 ×0.8
PROPAGATION_CHAIN_LIMIT = 50   # 传播链返回上限（防组合爆炸）
IMPACT_MAX_HOPS = 5            # 影响范围向上遍历跳数
PATH_MAX_HOPS = 6              # 两点路径查询最大跳数
SUGGESTION_LIST_LIMIT = 200

# 规则推理引擎 v1：内置规则集（不建配置表，v2 再外置）。
# 每条规则：源类型限定 + 两跳关系路径 + 推出关系 + 置信度（闸4 默认 ≥0.7 才跑）。
INFERENCE_RULES: list[dict] = [
    {"key": "compose_trans", "src_type": "部件",
     "hops": ["组成", "组成"], "rel": "组成", "conf": 0.95,
     "desc": "部件树传递：A 组成 B、B 组成 C ⇒ A 组成 C"},
    {"key": "fault_rollup", "src_type": "故障模式",
     "hops": ["发生于", "组成"], "rel": "发生于", "conf": 0.90,
     "desc": "IPC 上滚：子件故障 ⇒ 父件也受其影响"},
    {"key": "cause_symptom", "src_type": "故障原因",
     "hops": ["导致", "表现为"], "rel": "表现为", "conf": 0.80,
     "desc": "根因链传导：原因导致故障且故障表现为症状 ⇒ 原因表现为症状"},
    {"key": "cause_rollup", "src_type": "故障原因",
     "hops": ["发生于", "组成"], "rel": "发生于", "conf": 0.85,
     "desc": "原因发生于子件 ⇒ 原因发生于父件"},
    {"key": "wo_part", "src_type": "维修工单",
     "hops": ["涉及故障", "发生于"], "rel": "涉及部件", "conf": 0.70,
     "desc": "工单涉及故障且故障发生于部件 ⇒ 工单涉及部件"},
]


def _now() -> str:
    return datetime.now().isoformat()


def _run_query(query: str, **params) -> list[dict]:
    driver = _driver()
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            return [dict(r) for r in s.run(query, **params)]
    finally:
        driver.close()


def _entity_meta_cols() -> str:
    return ("{id: n.id, name: n.name, entity_type: n.entity_type}")


def _sug_to_dict(s: RelationSuggestion, names: dict[str, str]) -> dict:
    try:
        ev = json.loads(s.evidence) if s.evidence else {}
    except Exception:  # noqa: BLE001
        ev = {}
    return {
        "id": s.id, "kb_id": s.kb_id, "category_id": s.category_id, "source": s.source,
        "source_entity_id": s.source_entity_id,
        "source_entity_name": names.get(s.source_entity_id, s.source_entity_id),
        "target_entity_id": s.target_entity_id,
        "target_entity_name": names.get(s.target_entity_id, s.target_entity_id),
        "suggested_relation_type": s.suggested_relation_type,
        "score": s.score or 0, "confidence": s.confidence or 0,
        "evidence": ev, "reason": s.reason or "", "status": s.status,
        "created_at": s.created_at, "reviewed_at": s.reviewed_at, "reviewer": s.reviewer,
    }


class GraphInferenceService:
    """规则推理任务编排 + 传播分析即时查询 + 建议审核闭环。"""

    # ───────────────────── 规则推理任务 ─────────────────────

    @staticmethod
    async def start_inference_task(db: AsyncSession, category_id: str) -> dict:
        """创建规则推理任务（护栏同计算任务：迁入互斥 / 同类别任务互斥 / 图非空）。"""
        cat = await db.get(OntologyCategory, category_id)
        if not cat:
            raise ValueError(f"本体类别不存在：{category_id}")

        running_sync = (await db.execute(
            select(GraphSyncRun.id).where(
                GraphSyncRun.category_id == category_id,
                GraphSyncRun.status.in_(("pending", "running")),
            ).limit(1)
        )).scalar()
        if running_sync:
            raise PermissionError("该类别迁入进行中，请等待完成后再推理")

        running_task = (await db.execute(
            select(GraphAnalysisTask.id).where(
                GraphAnalysisTask.category_id == category_id,
                GraphAnalysisTask.status.in_(("pending", "running")),
            ).limit(1)
        )).scalar()
        if running_task:
            raise PermissionError("该类别已有计算/推理任务进行中，请排队")

        graph_nodes = await asyncio.to_thread(_count_category_nodes, category_id)
        if graph_nodes <= 0:
            raise ValueError("分析图中该类别暂无数据，请先在「迁入管理」执行全量迁入")

        task = GraphAnalysisTask(
            category_id=category_id, kind="inference", algorithm="rules_v1",
            params="{}", status="pending",
            stats=json.dumps({"graph_nodes": graph_nodes}),
        )
        db.add(task)
        await db.commit()
        logger.info("规则推理任务已创建：cid=%s task=%s", category_id, task.id)
        return {"id": task.id, "category_id": category_id, "kind": "inference",
                "algorithm": "rules_v1", "status": task.status}

    @staticmethod
    async def execute_inference_task(task_id: str, category_id: str) -> None:
        """BackgroundTasks 入口：闸门预取 → 线程跑规则 Cypher → 过闸落建议 → done。"""
        from services.notification_hub import hub

        async with async_session() as db:
            t = await db.get(GraphAnalysisTask, task_id)
            if not t or t.status != "pending":
                logger.error("推理任务 %s 不存在或状态异常，跳过执行", task_id)
                return
            t.status = "running"
            t.started_at = _now()
            await db.commit()

        started = time.time()
        try:
            gate = await _load_gates(category_id)

            candidates: list[dict] = []
            per_rule: dict[str, int] = {}
            rules_run = 0
            for rule in INFERENCE_RULES:
                if rule["conf"] < settings.INFERENCE_MIN_CONFIDENCE:  # 闸4：规则级
                    per_rule[rule["key"]] = -1
                    continue
                rules_run += 1
                found = await asyncio.to_thread(_collect_rule_candidates, category_id, rule)
                per_rule[rule["key"]] = len(found)
                candidates.extend(found)

            stats = await _filter_and_persist(category_id, candidates, gate)
            stats.update({
                "rules_total": len(INFERENCE_RULES), "rules_run": rules_run,
                "per_rule": per_rule,
                "elapsed_seconds": round(time.time() - started, 1),
            })

            async with async_session() as db:
                t = await db.get(GraphAnalysisTask, task_id)
                t.status = "done"
                t.stats = json.dumps(stats, ensure_ascii=False)
                t.results = json.dumps(
                    {"kind": "suggestions", "created": stats["created"],
                     "candidates": stats["candidates"]}, ensure_ascii=False)
                t.finished_at = _now()
                await db.commit()
            logger.info("规则推理完成 task=%s cid=%s：%s", task_id, category_id, stats)
        except Exception as exc:  # noqa: BLE001
            logger.exception("规则推理失败 task=%s", task_id)
            async with async_session() as db:
                t = await db.get(GraphAnalysisTask, task_id)
                if t:
                    t.status = "failed"
                    t.error = str(exc)[:4000]
                    t.finished_at = _now()
                    await db.commit()
        finally:
            try:
                hub.notify()
            except Exception:  # noqa: BLE001
                pass

    # ───────────────────── 传播分析（同步 API，秒级） ─────────────────────

    @staticmethod
    async def propagation(category_id: str, entity_id: str, max_hops: int = 3) -> dict:
        """故障往下会引发什么：沿 ``导致`` 变长路径，置信度随跳数衰减。"""
        hops = max(1, min(int(max_hops or 3), 5))
        rel = _esc("导致")
        rows = await asyncio.to_thread(
            _run_query,
            f"MATCH path = (a:Entity {{category_id: $cid, id: $eid}})-[{rel}*1..{hops}]->"
            f"(b:Entity {{category_id: $cid}}) "
            "WITH path, size(relationships(path)) AS len "
            f"ORDER BY len LIMIT {PROPAGATION_CHAIN_LIMIT} "
            "RETURN [n IN nodes(path) | " + _entity_meta_cols() + "] AS ns, "
            "[r IN relationships(path) | "
            "{source: startNode(r).id, target: endNode(r).id, type: type(r)}] AS es",
            cid=category_id, eid=entity_id,
        )
        chains, nodes, edges = [], {}, {}
        for r in rows:
            n = len(r["ns"])
            chains.append({
                "nodes": r["ns"], "edges": r["es"], "hops": n - 1,
                "confidence": round(PROPAGATION_DECAY ** (n - 1), 3),
                "label": " → ".join(x["name"] for x in r["ns"]),
            })
            for x in r["ns"]:
                nodes[x["id"]] = x
            for e in r["es"]:
                edges[(e["source"], e["target"], e["type"])] = e
        return {"query": "propagation", "nodes": list(nodes.values()),
                "edges": list(edges.values()), "chains": chains}

    @staticmethod
    async def impact(category_id: str, entity_id: str) -> dict:
        """部件出问题影响哪些系统/机型：组成向上 ∪ 装于链 ∪ 发生于反向（故障清单）。"""
        comp, inst, occur = _esc("组成"), _esc("装于"), _esc("发生于")
        up = await asyncio.to_thread(
            _run_query,
            f"MATCH (p:Entity {{category_id: $cid, id: $eid}})"
            f"-[:{comp}|{inst}*1..{IMPACT_MAX_HOPS}]->(s:Entity {{category_id: $cid}}) "
            "RETURN DISTINCT " + _entity_meta_cols().replace("n.", "s.") + " AS n",
            cid=category_id, eid=entity_id,
        )
        faults = await asyncio.to_thread(
            _run_query,
            f"MATCH (f:Entity {{category_id: $cid}})-[:{occur}]->"
            f"(p:Entity {{category_id: $cid, id: $eid}}) "
            "RETURN DISTINCT " + _entity_meta_cols().replace("n.", "f.") + " AS n",
            cid=category_id, eid=entity_id,
        )
        ups = [r["n"] for r in up]
        flts = [r["n"] for r in faults]
        return {
            "query": "impact",
            "nodes": ups + flts,
            "edges": [],  # 清单视图为主，无需边
            "groups": {"upstream": ups, "faults": flts},
            "chains": [{"label": x["name"], "nodes": [x], "edges": [], "hops": 0,
                        "confidence": 1} for x in ups],
        }

    @staticmethod
    async def similar(category_id: str, entity_id: str, top_k: int = 10) -> dict:
        """相似故障案例（排故参考）：nodeSimilarity 流式 + 投影护栏。"""
        if not gds.gds_available():
            raise RuntimeError("Neo4j 未安装 GDS 插件，无法执行相似度查询")
        top_k = max(1, min(int(top_k or 10), 50))

        def _q() -> list[dict]:
            name = gds.category_projection_name(category_id)
            if not gds.projection_exists(name):
                gds.ensure_category_projection(category_id)
            return _run_query(
                "CALL gds.nodeSimilarity.stream($p) YIELD node1, node2, similarity "
                "WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity "
                "WHERE a.id = $eid OR b.id = $eid "
                "RETURN CASE WHEN a.id = $eid THEN a ELSE b END AS self, "
                "       CASE WHEN a.id = $eid THEN b ELSE a END AS other, similarity "
                "ORDER BY similarity DESC LIMIT $k",
                p=name, eid=entity_id, k=top_k,
            )

        rows = await asyncio.to_thread(_q)
        others = [{"id": r["other"]["id"], "name": r["other"]["name"],
                   "entity_type": r["other"]["entity_type"],
                   "similarity": round(float(r["similarity"]), 4)} for r in rows]
        return {"query": "similar", "nodes": others, "edges": [], "rows": others,
                "chains": [{"label": f"{o['name']}（{o['similarity']}）",
                            "nodes": [o], "edges": [], "hops": 0,
                            "confidence": o["similarity"]} for o in others]}

    @staticmethod
    async def path(category_id: str, source_id: str, target_id: str) -> dict:
        """两实体的关系链（如 AD→部件→故障）：shortestPath 全语义关系。"""
        from providers.graph_store.gds import EXCLUDED_REL_TYPES

        def _q() -> list[dict]:
            return _run_query(
                "MATCH (a:Entity {category_id: $cid, id: $sid}), "
                "      (b:Entity {category_id: $cid, id: $tid}) "
                "MATCH p = shortestPath((a)-[*..%d]-(b)) "
                "WHERE all(r IN relationships(p) WHERE NOT type(r) IN $excluded) "
                "RETURN [n IN nodes(p) | %s] AS ns, "
                "[r IN relationships(p) | "
                "{source: startNode(r).id, target: endNode(r).id, type: type(r)}] AS es"
                % (PATH_MAX_HOPS, _entity_meta_cols()),
                cid=category_id, sid=source_id, tid=target_id,
                excluded=list(EXCLUDED_REL_TYPES),
            )

        rows = await asyncio.to_thread(_q)
        if not rows:
            return {"query": "path", "nodes": [], "edges": [], "chains": []}
        r = rows[0]
        return {
            "query": "path", "nodes": r["ns"], "edges": r["es"],
            "chains": [{"nodes": r["ns"], "edges": r["es"], "hops": len(r["es"]),
                        "confidence": 1,
                        "label": " - ".join(
                            x["name"] for x in r["ns"])}],
        }

    @staticmethod
    async def search_entities(category_id: str, q: str, limit: int = 10) -> list[dict]:
        """分析图内实体搜索（Tab3 查询器下拉数据源）。"""
        limit = max(1, min(int(limit or 10), 30))
        rows = await asyncio.to_thread(
            _run_query,
            "MATCH (n:Entity {category_id: $cid}) "
            "WHERE $q = '' OR n.name CONTAINS $q "
            "RETURN " + _entity_meta_cols() + " AS n, n.name AS sort_key "
            "ORDER BY sort_key LIMIT $l",
            cid=category_id, q=(q or "").strip(), l=limit,
        )
        return [r["n"] for r in rows]

    # ───────────────────── 建议审核闭环 ─────────────────────

    @staticmethod
    async def list_suggestions(db: AsyncSession, category_id: str,
                               status: str = "pending") -> list[dict]:
        """建议列表（联 PG entities 补实体名）。"""
        rows = (await db.execute(
            select(RelationSuggestion)
            .where(RelationSuggestion.category_id == category_id,
                   RelationSuggestion.status == status)
            .order_by(RelationSuggestion.confidence.desc())
            .limit(SUGGESTION_LIST_LIMIT)
        )).scalars().all()
        names = await _entity_names(
            db, [e for s in rows for e in (s.source_entity_id, s.target_entity_id)])
        return [_sug_to_dict(s, names) for s in rows]

    @staticmethod
    async def approve_suggestion(db: AsyncSession, suggestion_id: str,
                                 reviewer: str = "") -> dict:
        """批准：走 create_relation 双写链路（PG 权威先行 + 业务图）+ 顺带写分析图。"""
        from services.entity_service import EntityService

        sug = await db.get(RelationSuggestion, suggestion_id)
        if not sug:
            raise ValueError("建议不存在")
        if sug.status != "pending":
            raise PermissionError(f"建议已审核过（{sug.status}）")

        # 防御终检：关系类型必须仍是该类别本体定义的关系
        rel_def = (await db.execute(
            select(OntologyRelation).where(
                OntologyRelation.category_id == sug.category_id,
                OntologyRelation.name == sug.suggested_relation_type,
            ).limit(1)
        )).scalar_one_or_none()
        if not rel_def:
            raise ValueError(
                f"关系类型「{sug.suggested_relation_type}」不在本体定义中，无法批准")

        payload = await EntityService.create_relation(
            db,
            kb_id=sug.kb_id,
            relation_def_id=rel_def.id,
            relation_type=sug.suggested_relation_type,
            source_entity_id=sug.source_entity_id,
            target_entity_id=sug.target_entity_id,
            description=f"图推理建议批准（{sug.reason}）",
        )

        # 顺带写分析图（同语义关系；批准即时可见，增量迁入随后对齐）
        try:
            await asyncio.to_thread(
                _write_analysis_relation, sug.category_id, sug.suggested_relation_type,
                sug.source_entity_id, sug.target_entity_id,
                str(payload.get("id", "")), sug.kb_id, rel_def.id,
            )
        except Exception:  # noqa: BLE001
            logger.warning("分析图写关系失败（增量迁入会对齐）：%s", sug.id, exc_info=True)

        sug.status = "approved"
        sug.reviewed_at = _now()
        sug.reviewer = reviewer or "manual"
        await db.commit()
        return {"ok": True, "relation": payload}

    @staticmethod
    async def reject_suggestion(db: AsyncSession, suggestion_id: str,
                                reviewer: str = "") -> dict:
        """拒绝：置 rejected + 写 tombstone（之后不再重推该组合）。"""
        sug = await db.get(RelationSuggestion, suggestion_id)
        if not sug:
            raise ValueError("建议不存在")
        if sug.status != "pending":
            raise PermissionError(f"建议已审核过（{sug.status}）")

        sug.status = "rejected"
        sug.reviewed_at = _now()
        sug.reviewer = reviewer or "manual"
        db.add(RelationSuggestionTombstone(
            kb_id=sug.kb_id, source_entity_id=sug.source_entity_id,
            target_entity_id=sug.target_entity_id,
            suggested_relation_type=sug.suggested_relation_type,
            category_id=sug.category_id,
        ))
        await db.commit()
        return {"ok": True}


# ─────────────────── 闸门数据 / 候选收集 / 过滤落库 ───────────────────


async def _load_gates(category_id: str) -> dict:
    """预取闸门数据：白名单三元组 / tombstones / 待审建议组合 / 关系定义 id 映射。"""
    async with async_session() as db:
        rel_defs = (await db.execute(
            select(OntologyRelation)
            .where(OntologyRelation.category_id == category_id)
        )).scalars().all()
        rel_def_by_name = {r.name: r.id for r in rel_defs}
        rel_name_by_id = {r.id: r.name for r in rel_defs}

        constraints = (await db.execute(
            select(OntologyRelationConstraint.source_ontology_id,
                   OntologyRelationConstraint.relation_id,
                   OntologyRelationConstraint.target_ontology_id)
            .where(OntologyRelationConstraint.category_id == category_id)
        )).all()
        whitelist = {(src, rel_name_by_id.get(rid, ""), tgt)
                     for src, rid, tgt in constraints}

        tombstones = set((await db.execute(
            select(RelationSuggestionTombstone.kb_id,
                   RelationSuggestionTombstone.source_entity_id,
                   RelationSuggestionTombstone.target_entity_id,
                   RelationSuggestionTombstone.suggested_relation_type)
            .where(RelationSuggestionTombstone.category_id == category_id)
        )).all())

        pendings = set((await db.execute(
            select(RelationSuggestion.kb_id,
                   RelationSuggestion.source_entity_id,
                   RelationSuggestion.target_entity_id,
                   RelationSuggestion.suggested_relation_type)
            .where(RelationSuggestion.category_id == category_id,
                   RelationSuggestion.status == "pending")
        )).all())

    return {"rel_def_by_name": rel_def_by_name, "whitelist": whitelist,
            "tombstones": tombstones, "pendings": pendings}


def _collect_rule_candidates(category_id: str, rule: dict) -> list[dict]:
    """单规则 Cypher 物化（线程）：两跳路径 + 闸1（已存在同向同类型关系）挡存量。

    每个候选行附 _rule（规则 dict），供后续过滤与 evidence 使用。
    """
    r1, r2 = _esc(rule["hops"][0]), _esc(rule["hops"][1])
    rel = _esc(rule["rel"])
    src_type = rule["src_type"].replace("'", "")
    query = (
        "MATCH (a:Entity {category_id: $cid}) "
        f"WHERE a.entity_type = '{src_type}' "
        f"MATCH (a)-[r1:{r1}]->(m:Entity {{category_id: $cid}}) "
        f"MATCH (m)-[r2:{r2}]->(b:Entity {{category_id: $cid}}) "
        f"WHERE a.id <> b.id AND NOT (a)-[:{rel}]->(b) "
        "RETURN a.id AS sid, a.name AS sname, a.ontology_id AS sont, a.kb_id AS skb, "
        "       b.id AS tid, b.name AS tname, b.ontology_id AS tont, "
        "       [a.name, m.name, b.name] AS chain, [type(r1), type(r2)] AS hops "
        f"LIMIT {int(settings.INFERENCE_RULE_LIMIT)}"
    )
    driver = _driver()
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            rows = [dict(r) for r in s.run(query, cid=category_id)]
    finally:
        driver.close()
    for row in rows:
        row["_rule"] = rule
    return rows


async def _filter_and_persist(category_id: str, candidates: list[dict],
                              gate: dict) -> dict:
    """闸2（白名单）/ 闸3（tombstone）/ 闸5（待审重复）过滤后落 relation_suggestions。"""
    blocked = {"constraint": 0, "tombstone": 0, "pending": 0, "no_relation_def": 0}
    to_create: list[RelationSuggestion] = []
    seen: set = set()

    for c in candidates:
        rule: dict = c["_rule"]
        rel_name = rule["rel"]
        key = (c["skb"], c["sid"], c["tid"], rel_name)
        if key in seen:
            continue
        seen.add(key)

        rel_def_id = gate["rel_def_by_name"].get(rel_name)
        if not rel_def_id:
            blocked["no_relation_def"] += 1
            continue
        if (c["sont"], rel_name, c["tont"]) not in gate["whitelist"]:
            blocked["constraint"] += 1
            continue
        if key in gate["tombstones"]:
            blocked["tombstone"] += 1
            continue
        if key in gate["pendings"]:
            blocked["pending"] += 1
            continue

        to_create.append(RelationSuggestion(
            kb_id=c["skb"], category_id=category_id,
            source_entity_id=c["sid"], target_entity_id=c["tid"],
            suggested_relation_type=rel_name, relation_def_id=rel_def_id,
            source="rule", score=0, confidence=float(rule["conf"]),
            evidence=json.dumps(
                {"rule": rule["key"], "rule_desc": rule["desc"],
                 "chain": c["chain"], "hops": c["hops"]},
                ensure_ascii=False),
            reason=rule["desc"], status="pending",
        ))

    if to_create:
        async with async_session() as db:
            db.add_all(to_create)
            await db.commit()

    return {"candidates": len(candidates), "created": len(to_create),
            "blocked_constraint": blocked["constraint"],
            "blocked_tombstone": blocked["tombstone"],
            "blocked_pending": blocked["pending"],
            "blocked_no_relation_def": blocked["no_relation_def"]}


async def _entity_names(db: AsyncSession, ids: list[str]) -> dict[str, str]:
    """批量取实体名（PG 权威库；建议列表展示用）。"""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    rows = (await db.execute(
        select(Entity.id, Entity.name).where(Entity.id.in_(ids))
    )).all()
    return {rid: name for rid, name in rows}


def _write_analysis_relation(category_id: str, rel_type: str, source_id: str,
                             target_id: str, relation_id: str, kb_id: str,
                             relation_def_id: str) -> None:
    """批准建议后写分析图关系（与迁入写入格式一致，动态类型过 _esc 转义）。"""
    rt = _esc(rel_type)
    _run_query(
        "MATCH (a:Entity {id: $sid}), (b:Entity {id: $tid}) "
        f"MERGE (a)-[r:{rt} {{relation_id: $rid}}]->(b) "
        "SET r.kb_id = $kb, r.category_id = $cid, r.relation_def_id = $rdid",
        sid=source_id, tid=target_id, rid=relation_id,
        kb=kb_id, cid=category_id, rdid=relation_def_id,
    )
