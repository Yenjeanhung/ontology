"""图计算任务服务：GDS 算法编排（P1 五算法）+ 结果写回 Neo4j 分析图。

设计来源：《图迁入与图计算推理_功能设计》5 节。
- 任务生命周期：POST → graph_analysis_tasks(pending) → BackgroundTasks：
  护栏（投影缺失则重建，持 ``_gds_lock``）→ gds.<algo>.stream/write → top-N 榜单 → done；
- 结果去向（P1 两条）：榜单（results JSON 前端直出）+ 写回节点属性
  （pagerank_score / betweenness_score / community_id）——分值只写分析图
  派生数据，**不回 PostgreSQL 权威库**（权威库只收关系事实，决策点 6）；
  node_similarity 的「相似于」建议落 relation_suggestions 属 P2（建议表共建后接）；
- 并发：读投影的算法不加锁（社区版 GDS 自排队）；投影重建持 ``_gds_lock``；
  degree 算法纯 Cypher 聚合，不依赖投影与 GDS 插件。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from models import GraphAnalysisTask, GraphSyncRun, OntologyCategory
from providers.graph_store import gds
from providers.graph_store.gds import EXCLUDED_REL_TYPES

logger = logging.getLogger(__name__)

# 榜单落库上限（设计 5.3：results 存 top 100；社区取前 50，成员样例 12）
MAX_TOP_N = 100
MAX_COMMUNITIES = 50
COMMUNITY_SAMPLE = 12

# 写回分析图的节点属性名（可重建的派生数据）
WRITE_PROPS = {"pagerank": "pagerank_score", "betweenness": "betweenness_score",
               "louvain": "community_id"}

# betweenness 精确算法 O(V·E)：投影节点数超阈值自动切 GDS 近似模式（MSBRA 采样；
# seed 固定保证同图重跑结果可复现）。经验值：1 万采样在 20 万节点图分钟级可完成。
BETWEENNESS_SAMPLE_THRESHOLD = 50_000
BETWEENNESS_SAMPLE_SIZE = 10_000
BETWEENNESS_SEED = 42

# 算法注册表：元数据供前端算法卡片与参数表单直出
ALGORITHMS: dict[str, dict[str, Any]] = {
    "pagerank": {
        "name": "PageRank 中心性",
        "semantic": "关键节点：故障网络最中心的部件 / 故障模式",
        "result": "榜单 + 写回 pagerank_score",
        "kind": "ranking", "score_label": "PageRank",
        "needs_gds": True, "supports_write": True,
    },
    "betweenness": {
        "name": "介数中心性",
        "semantic": "桥接节点：掐断即可阻断传播的关键环节",
        "result": "榜单 + 写回 betweenness_score",
        "kind": "ranking", "score_label": "介数",
        "needs_gds": True, "supports_write": True,
    },
    "louvain": {
        "name": "Louvain 社区发现",
        "semantic": "故障综合征：互相诱发的故障聚类",
        "result": "社区列表 + 写回 community_id",
        "kind": "communities", "score_label": "规模",
        "needs_gds": True, "supports_write": True,
    },
    "node_similarity": {
        "name": "节点相似度",
        "semantic": "相似故障 / 相似件号（Jaccard，共享邻居）",
        "result": "相似对榜单（「相似于」建议产出属 P2）",
        "kind": "pairs", "score_label": "相似度",
        "needs_gds": True, "supports_write": False,
        "extra_params": [{"key": "similarity_cutoff", "label": "相似度下限（0~1，默认 0）",
                          "type": "number", "default": 0}],
    },
    "degree": {
        "name": "度中心性",
        "semantic": "高频拆换 / 高频故障粗排（Cypher 聚合，GDS 不可用也能跑）",
        "result": "榜单",
        "kind": "ranking", "score_label": "度数",
        "needs_gds": False, "supports_write": False,
    },
}


def _now() -> str:
    return datetime.now().isoformat()


def _task_to_dict(t: GraphAnalysisTask) -> dict:
    return {
        "id": t.id,
        "category_id": t.category_id,
        "kind": t.kind,
        "algorithm": t.algorithm,
        "algorithm_name": ALGORITHMS.get(t.algorithm, {}).get("name", t.algorithm),
        "params": json.loads(t.params) if t.params else {},
        "status": t.status,
        "stats": json.loads(t.stats) if t.stats else {},
        "results": json.loads(t.results) if t.results else None,
        "error": t.error,
        "started_at": t.started_at,
        "finished_at": t.finished_at,
        "created_at": t.created_at,
    }


class GraphAnalysisService:
    """图计算任务编排：创建（护栏）→ 后台执行 → 榜单/写回落地。"""

    # ───────────────────────── 算法元数据 / 查询 ─────────────────────────

    @staticmethod
    def list_algorithms() -> list[dict]:
        """算法卡片元数据（前端 Tab2 直出）。"""
        return [{"key": k, **meta} for k, meta in ALGORITHMS.items()]

    @staticmethod
    async def get_task(db: AsyncSession, task_id: str) -> dict | None:
        t = await db.get(GraphAnalysisTask, task_id)
        return _task_to_dict(t) if t else None

    @staticmethod
    async def list_tasks(db: AsyncSession, category_id: str, limit: int = 20) -> list[dict]:
        tasks = (await db.execute(
            select(GraphAnalysisTask)
            .where(GraphAnalysisTask.category_id == category_id)
            .order_by(GraphAnalysisTask.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return [_task_to_dict(t) for t in tasks]

    # ───────────────────────── 创建任务（护栏校验） ─────────────────────────

    @staticmethod
    async def start_task(
        db: AsyncSession, category_id: str, algorithm: str,
        top_n: int = 20, write_back: bool = False, label_filter: str = "",
        similarity_cutoff: float = 0.0,
    ) -> dict:
        """创建计算任务（护栏校验后落 ``graph_analysis_tasks``），返回任务摘要。

        护栏：
        - 算法必须在注册表内；write_back 仅对支持的算法生效；
        - 类别必须存在；
        - needs_gds 的算法要求 GDS 可用；degree 例外（纯 Cypher）；
        - 分析图该类别必须有节点（否则提示先迁入）；
        - 同类别迁入 pending/running 时拒绝（清图期间计算无意义）；
        - 同类别已有 pending/running 计算任务时拒绝（社区版 GDS 并发 = 1）。
        """
        if algorithm not in ALGORITHMS:
            raise ValueError(f"未知算法：{algorithm}")
        cat = await db.get(OntologyCategory, category_id)
        if not cat:
            raise ValueError(f"本体类别不存在：{category_id}")

        if ALGORITHMS[algorithm]["needs_gds"] and not gds.gds_available():
            raise RuntimeError("Neo4j 未安装 GDS 插件，无法执行该算法")

        top_n = max(1, min(int(top_n or 20), MAX_TOP_N))
        label_filter = (label_filter or "").strip()
        if not ALGORITHMS[algorithm]["supports_write"]:
            write_back = False

        running_sync = (await db.execute(
            select(GraphSyncRun.id).where(
                GraphSyncRun.category_id == category_id,
                GraphSyncRun.status.in_(("pending", "running")),
            ).limit(1)
        )).scalar()
        if running_sync:
            raise PermissionError("该类别迁入进行中，请等待完成后再计算")

        running_task = (await db.execute(
            select(GraphAnalysisTask.id).where(
                GraphAnalysisTask.category_id == category_id,
                GraphAnalysisTask.status.in_(("pending", "running")),
            ).limit(1)
        )).scalar()
        if running_task:
            raise PermissionError("该类别已有计算任务进行中（GDS 单并发，请排队）")

        # 分析图该类别必须有数据（degree 也要图数据）
        graph_nodes = _count_category_nodes(category_id)
        if graph_nodes <= 0:
            raise ValueError("分析图中该类别暂无数据，请先在「迁入管理」执行全量迁入")

        params = {"top_n": top_n, "write_back": bool(write_back),
                  "label_filter": label_filter}
        if algorithm == "node_similarity":
            params["similarity_cutoff"] = max(0.0, min(float(similarity_cutoff or 0), 1.0))

        task = GraphAnalysisTask(
            category_id=category_id, kind="algorithm", algorithm=algorithm,
            params=json.dumps(params, ensure_ascii=False), status="pending",
            stats=json.dumps({"graph_nodes": graph_nodes}),
        )
        db.add(task)
        await db.commit()
        logger.info("图计算任务已创建：%s", json.dumps(_task_to_dict(task), ensure_ascii=False))
        return _task_to_dict(task)

    # ───────────────────────── 后台执行 ─────────────────────────

    @staticmethod
    async def execute_task(task_id: str, category_id: str, algorithm: str,
                           params_json: str) -> None:
        """BackgroundTasks 入口：置 running → 线程中执行算法 → done/failed。"""
        from services.notification_hub import hub

        params: dict = json.loads(params_json or "{}")
        async with async_session() as db:
            t = await db.get(GraphAnalysisTask, task_id)
            if not t or t.status != "pending":
                logger.error("计算任务 %s 不存在或状态异常，跳过执行", task_id)
                return
            t.status = "running"
            t.started_at = _now()
            await db.commit()

        started = time.time()
        try:
            if settings.GRAPH_STORE_PROVIDER != "neo4j":
                raise RuntimeError("图存储不是 Neo4j，无法执行图计算")

            stats, results = await asyncio.to_thread(
                _execute_algorithm, category_id, algorithm, params,
            )
            stats["elapsed_seconds"] = round(time.time() - started, 1)

            async with async_session() as db:
                t = await db.get(GraphAnalysisTask, task_id)
                t.status = "done"
                t.stats = json.dumps(stats, ensure_ascii=False)
                t.results = json.dumps(results, ensure_ascii=False)
                t.finished_at = _now()
                t.error = None
                await db.commit()
            logger.info(
                "图计算完成 task=%s cid=%s algo=%s 耗时=%ss",
                task_id, category_id, algorithm, stats["elapsed_seconds"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("图计算失败 task=%s", task_id)
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


# ───────────────────── 算法实现（同步，跑在工作线程） ─────────────────────


def _driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _count_category_nodes(category_id: str) -> int:
    driver = _driver()
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            return s.run(
                "MATCH (e:Entity {category_id: $cid}) RETURN count(e) AS c",
                cid=category_id,
            ).single()["c"]
    finally:
        driver.close()


def _execute_algorithm(category_id: str, algorithm: str, params: dict) -> tuple[dict, dict]:
    """调度到具体算法；返回 (stats, results)。"""
    meta = ALGORITHMS[algorithm]
    top_n = int(params.get("top_n") or 20)
    label_filter = (params.get("label_filter") or "").strip()
    write_back = bool(params.get("write_back")) and meta["supports_write"]

    projection = None
    if meta["needs_gds"]:
        # 投影护栏：缺失则重建（ensure 内部持 _gds_lock；重建后与最新迁入数据一致）
        name = gds.category_projection_name(category_id)
        if not gds.projection_exists(name):
            projection = gds.ensure_category_projection(category_id)
        else:
            projection = gds.projection_stats(name)
        if not projection or not projection.get("node_count"):
            raise ValueError("GDS 投影为空，请先在「迁入管理」执行全量迁入")
        # 透传投影规模给 runner（betweenness 大图自动切近似模式的依据）
        params["_projection_nodes"] = projection.get("node_count") or 0

    runner = {
        "pagerank": _run_centrality,
        "betweenness": _run_centrality,
        "louvain": _run_louvain,
        "node_similarity": _run_node_similarity,
        "degree": _run_degree,
    }[algorithm]

    stats, results = runner(category_id, algorithm, top_n, label_filter, write_back,
                            params)
    stats["projection"] = projection
    stats["write_back"] = write_back
    return stats, results


def _run_centrality(category_id: str, algorithm: str, top_n: int, label_filter: str,
                    write_back: bool, _params: dict) -> tuple[dict, dict]:
    """pagerank / betweenness：榜单 + 可选写回分值属性。

    write 模式只跑一次算法（write 过程）+ Cypher 读 top-N；
    stream 模式直接流式聚合 top-N。
    betweenness 精确算法 O(V·E)，大图不可行 → 超过阈值自动切 GDS 近似模式
    （MSBRA 采样，samplingSize 固定 / samplingSeed 固定保证可复现）。
    """
    proc = {"pagerank": "pageRank", "betweenness": "betweenness"}[algorithm]
    prop = WRITE_PROPS[algorithm]
    meta = ALGORITHMS[algorithm]

    # 大图 betweenness 近似模式配置（Cypher map 字面量，数值安全内联）
    nodes = int(_params.get("_projection_nodes") or 0)
    approximate = algorithm == "betweenness" and nodes > BETWEENNESS_SAMPLE_THRESHOLD
    algo_cfg = (
        f", {{samplingSize: {BETWEENNESS_SAMPLE_SIZE}, "
        f"samplingSeed: {BETWEENNESS_SEED}}}" if approximate else ""
    )

    driver = _driver()
    rows: list[dict] = []
    written = 0
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            name = gds.category_projection_name(category_id)
            if write_back:
                rec = s.run(
                    f"CALL gds.{proc}.write($p, {{writeProperty: $wp}}{algo_cfg}) "
                    "YIELD nodePropertiesWritten",
                    p=name, wp=prop,
                ).single()
                written = rec["nodePropertiesWritten"] if rec else 0
                for r in s.run(
                    f"MATCH (e:Entity {{category_id: $cid}}) "
                    f"WHERE e[{prop!r}] IS NOT NULL "
                    f"AND ($lf = '' OR e.entity_type = $lf) "
                    f"RETURN e.id AS id, e.name AS name, e.entity_type AS type, "
                    f"e[{prop!r}] AS score ORDER BY score DESC LIMIT $top",
                    cid=category_id, lf=label_filter, top=top_n,
                ):
                    rows.append({"id": r["id"], "name": r["name"],
                                 "entity_type": r["type"], "score": round(float(r["score"]), 6)})
            else:
                for r in s.run(
                    f"CALL gds.{proc}.stream($p{algo_cfg}) YIELD nodeId, score "
                    "WITH gds.util.asNode(nodeId) AS n, score "
                    "WHERE $lf = '' OR n.entity_type = $lf "
                    "RETURN n.id AS id, n.name AS name, n.entity_type AS type, score "
                    "ORDER BY score DESC LIMIT $top",
                    p=name, lf=label_filter, top=top_n,
                ):
                    rows.append({"id": r["id"], "name": r["name"],
                                 "entity_type": r["type"], "score": round(float(r["score"]), 6)})
    finally:
        driver.close()

    stats = {"total_rows": len(rows), "written": written}
    if approximate:
        stats["approximate"] = True
        stats["sampling_size"] = BETWEENNESS_SAMPLE_SIZE
    results = {"kind": "ranking", "algorithm": algorithm,
               "score_label": meta["score_label"], "rows": rows}
    return stats, results


def _run_louvain(category_id: str, algorithm: str, top_n: int, label_filter: str,
                 write_back: bool, _params: dict) -> tuple[dict, dict]:
    """Louvain 社区发现：社区列表（规模 + 成员样例）+ 可选写回 community_id。

    label_filter 过滤参与分组的节点（如只看「故障模式」的社区结构）。
    """
    prop = WRITE_PROPS["louvain"]
    driver = _driver()
    written = 0
    members_by_cid: dict[int, list[dict]] = {}
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            name = gds.category_projection_name(category_id)
            if write_back:
                rec = s.run(
                    "CALL gds.louvain.write($p, {writeProperty: $wp}) "
                    "YIELD nodePropertiesWritten, communityCount, modularity",
                    p=name, wp=prop,
                ).single()
                if rec:
                    written = rec["nodePropertiesWritten"]
                query = (
                    f"MATCH (e:Entity {{category_id: $cid}}) "
                    f"WHERE e[{prop!r}] IS NOT NULL "
                    f"AND ($lf = '' OR e.entity_type = $lf) "
                    f"RETURN e[{prop!r}] AS cid, collect({{id: e.id, name: e.name, "
                    f"entity_type: e.entity_type}}) AS members"
                )
                params = {"cid": category_id, "lf": label_filter}
            else:
                query = (
                    "CALL gds.louvain.stream($p) YIELD nodeId, communityId "
                    "WITH gds.util.asNode(nodeId) AS n, communityId "
                    "WHERE $lf = '' OR n.entity_type = $lf "
                    "RETURN communityId AS cid, collect({id: n.id, name: n.name, "
                    "entity_type: n.entity_type}) AS members"
                )
                params = {"p": name, "lf": label_filter}
            for rec in s.run(query, **params):
                members_by_cid[int(rec["cid"])] = list(rec["members"])
    finally:
        driver.close()

    communities = [
        {"community_id": cid, "size": len(members), "members": members[:COMMUNITY_SAMPLE]}
        for cid, members in sorted(members_by_cid.items(), key=lambda kv: -len(kv[1]))
    ][:MAX_COMMUNITIES]
    stats = {"total_rows": len(communities), "written": written,
             "community_count": len(members_by_cid)}
    results = {"kind": "communities", "algorithm": "louvain", "rows": communities}
    return stats, results


def _run_node_similarity(category_id: str, algorithm: str, top_n: int,
                         label_filter: str, _write_back: bool,
                         params: dict) -> tuple[dict, dict]:
    """节点相似度（Jaccard）：相似对榜单。「相似于」建议落 relation_suggestions 属 P2。"""
    cutoff = float(params.get("similarity_cutoff") or 0.0)
    config: dict[str, Any] = {}
    if cutoff > 0:
        config["similarityCutoff"] = cutoff
    driver = _driver()
    rows = []
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            name = gds.category_projection_name(category_id)
            for r in s.run(
                "CALL gds.nodeSimilarity.stream($p, $config) "
                "YIELD node1, node2, similarity "
                "WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity "
                "WHERE ($lf = '' OR a.entity_type = $lf) AND ($lf = '' OR b.entity_type = $lf) "
                "RETURN a.id AS sid, a.name AS sname, a.entity_type AS stype, "
                "       b.id AS tid, b.name AS tname, b.entity_type AS ttype, similarity "
                "ORDER BY similarity DESC LIMIT $top",
                p=name, config=config, lf=label_filter, top=top_n,
            ):
                rows.append({
                    "source_id": r["sid"], "source_name": r["sname"], "source_type": r["stype"],
                    "target_id": r["tid"], "target_name": r["tname"], "target_type": r["ttype"],
                    "similarity": round(float(r["similarity"]), 6),
                })
    finally:
        driver.close()
    stats = {"total_rows": len(rows), "similarity_cutoff": cutoff}
    results = {"kind": "pairs", "algorithm": "node_similarity", "rows": rows}
    return stats, results


def _run_degree(category_id: str, algorithm: str, top_n: int, label_filter: str,
                _write_back: bool, _params: dict) -> tuple[dict, dict]:
    """度中心性：纯 Cypher 无向聚合（不依赖 GDS，GDS 不可用也可用）。"""
    driver = _driver()
    rows = []
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            for r in s.run(
                "MATCH (e:Entity {category_id: $cid})-[r]-(o) "
                "WHERE NOT type(r) IN $excluded AND o.category_id = $cid "
                "  AND ($lf = '' OR e.entity_type = $lf) "
                "RETURN e.id AS id, e.name AS name, e.entity_type AS type, "
                "       count(r) AS degree "
                "ORDER BY degree DESC LIMIT $top",
                cid=category_id, excluded=list(EXCLUDED_REL_TYPES),
                lf=label_filter, top=top_n,
            ):
                rows.append({"id": r["id"], "name": r["name"],
                             "entity_type": r["type"], "score": int(r["degree"])})
    finally:
        driver.close()
    stats = {"total_rows": len(rows)}
    results = {"kind": "ranking", "algorithm": "degree",
               "score_label": ALGORITHMS["degree"]["score_label"], "rows": rows}
    return stats, results
