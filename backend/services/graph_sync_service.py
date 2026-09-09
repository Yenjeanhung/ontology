"""图迁入服务：PostgreSQL 权威本体/实体数据 → Neo4j 分析图（按本体类别）。

设计来源：《图迁入与图计算推理_功能设计》4 节。
算法蓝本：``scripts/sync_seed_to_neo4j.py``（分页读取 + 按类型分组 + UNWIND 批量），
本服务将其产品化：任务表进度、护栏（互斥/预检/空库拒绝）、迁入后自动重建 GDS 投影。

数据流原则（与全项目一致）
==========================
- PostgreSQL 为权威存储，单向流入 Neo4j 分析图；分析图可随时全量重建；
- 分析图 schema（沿用脚本已验证结构）：
  - 节点 ``(:Entity:<本体编码> {id, kb_id, category_id, name, entity_type, ontology_id, description, properties})``
    标签用稳定编码 ``Ontology.code``（如 Flight），本体重命名不再导致图标签漂移；
    ``entity_type`` 属性仍存显示名，供图分析 label_filter 与列表展示。
    未定义编码的本体回落显示名（兼容历史数据）。
  - 关系 ``[:<关系编码> {relation_id, kb_id, category_id}]``
    关系类型用 ``OntologyRelation.code``（如 HAS_LEG），未定义编码时回落关系名。
- ``category_id`` 属性是分析图的圈定标记（M0 投影与查询都靠它），
  运行时业务抽取图（KB 维度）不写该属性，两图天然隔离。

阻塞说明
========
Neo4j 批量写入是同步阻塞调用（10 万级 1~2 分钟），统一放在
``asyncio.to_thread`` 工作线程中执行，避免卡死事件循环；
进度通过线程共享 dict 回传，由独立协程定期 flush 到 ``graph_sync_runs``。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from database import async_session
from models import (
    Entity,
    GraphSyncRun,
    Ontology,
    OntologyCategory,
    OntologyRelation,
    Relation,
)
from providers.graph_store import gds

logger = logging.getLogger(__name__)

# 每次从 PostgreSQL 分页拉取的行数（10 万行不能一次性 load 进内存）
READ_CHUNK = 2000
# Neo4j 分批删除的批大小（避免大事务）
DELETE_BATCH = 5000


def _esc(identifier: str) -> str:
    """标签/关系类型转义：含非 ASCII 或特殊字符时用反引号包裹。"""
    ident = (identifier or "").strip()
    if not ident:
        raise ValueError("空的标签/关系类型")
    return f"`{ident.replace('`', '')}`"


def _props(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    return str(raw)


def _now() -> str:
    return datetime.now().isoformat()


def _run_to_dict(run: GraphSyncRun) -> dict:
    return {
        "id": run.id,
        "category_id": run.category_id,
        "mode": run.mode,
        "status": run.status,
        "dry_run": bool(run.dry_run),
        "entity_count": run.entity_count or 0,
        "relation_count": run.relation_count or 0,
        "total_entities": run.total_entities or 0,
        "total_relations": run.total_relations or 0,
        "projection": json.loads(run.projection) if run.projection else None,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


def _ontology_ids_subq(category_id: str):
    return select(Ontology.id).where(Ontology.category_id == category_id)


def _category_entity_ids_subq(category_id: str):
    return select(Entity.id).where(Entity.ontology_id.in_(_ontology_ids_subq(category_id)))


class GraphSyncService:
    """图迁入（只做 PostgreSQL → Neo4j 单向搬运；写回审核闭环另见推理设计 6.4）。"""

    # ───────────────────────── 类别列表（迁入管理页） ─────────────────────────

    @staticmethod
    async def list_sync_categories(db: AsyncSession) -> dict:
        """可迁入类别列表：PG 统计 + 最近一次迁入 + Neo4j 侧图/投影状态。"""
        categories = (await db.execute(
            select(OntologyCategory).order_by(OntologyCategory.created_at)
        )).scalars().all()

        # PG 实体数：entities JOIN ontologies 按 category_id 聚合
        ent_rows = (await db.execute(
            select(Ontology.category_id, func.count(Entity.id))
            .join(Entity, Entity.ontology_id == Ontology.id, isouter=True)
            .group_by(Ontology.category_id)
        )).all()
        entity_counts = {cid: cnt for cid, cnt in ent_rows}

        # PG 关系数：relations 经 source 实体归属类别聚合
        rel_rows = (await db.execute(
            select(Ontology.category_id, func.count(Relation.id))
            .join(Entity, Entity.id == Relation.source_entity_id, isouter=True)
            .join(Ontology, Ontology.id == Entity.ontology_id, isouter=True)
            .group_by(Ontology.category_id)
        )).all()
        relation_counts = {cid: cnt for cid, cnt in rel_rows}

        # 最近一次迁入（每类别一条）
        last_runs: dict[str, GraphSyncRun] = {}
        for run in (await db.execute(
            select(GraphSyncRun)
            .where(GraphSyncRun.dry_run == 0)
            .order_by(GraphSyncRun.created_at.desc())
            .limit(200)
        )).scalars().all():
            last_runs.setdefault(run.category_id, run)

        # Neo4j 侧：一次查询拿到全类别节点/关系分布 + GDS 投影状态
        graph_dist = await asyncio.to_thread(_fetch_graph_distribution)

        items = []
        for cat in categories:
            cid = cat.id
            last = last_runs.get(cid)
            g = graph_dist.get(cid) or {}
            entity_count = entity_counts.get(cid, 0)
            graph_nodes = g.get("nodes", 0)
            projection = g.get("projection")
            items.append({
                "id": cid,
                "name": cat.name,
                "description": cat.description or "",
                "is_system": bool(cat.is_system),
                "entity_count": entity_count,
                "relation_count": relation_counts.get(cid, 0),
                "last_run": _run_to_dict(last) if last else None,
                "graph": {
                    "nodes": graph_nodes,
                    "relations": g.get("relations", 0),
                    # 图内节点数与权威库实体数不一致 → 迁入已过期
                    "stale": last is not None and graph_nodes != entity_count,
                    "projection": projection,
                },
            })

        return {
            "gds_available": gds.gds_available(),
            "graph_provider": settings.GRAPH_STORE_PROVIDER,
            "items": items,
        }

    # ───────────────────────── 预检 / 启动迁入 ─────────────────────────

    @staticmethod
    async def precheck(db: AsyncSession, category_id: str) -> dict:
        """dry_run 预检：将迁入的数量统计（不落任务表、不动图）。"""
        total_entities = await db.scalar(
            select(func.count(Entity.id)).where(Entity.ontology_id.in_(_ontology_ids_subq(category_id)))
        ) or 0
        total_relations = await db.scalar(
            select(func.count(Relation.id))
            .where(Relation.source_entity_id.in_(_category_entity_ids_subq(category_id)))
        ) or 0
        ontology_count = await db.scalar(
            select(func.count(Ontology.id)).where(Ontology.category_id == category_id)
        ) or 0
        return {
            "category_id": category_id,
            "ontology_count": ontology_count,
            "total_entities": total_entities,
            "total_relations": total_relations,
        }

    @staticmethod
    async def start_run(
        db: AsyncSession, category_id: str, mode: str = "full", dry_run: bool = False,
    ) -> dict:
        """创建迁入任务（护栏校验后落 ``graph_sync_runs``），返回 run 摘要。

        护栏：
        - 类别必须存在且有本体/实体（实体数为 0 拒绝执行）；
        - 同类别已有 pending/running 任务时拒绝（互斥）；
        - incremental 模式 P2 提供（决策点 2：迁入默认全量重建）。
        """
        cat = await db.get(OntologyCategory, category_id)
        if not cat:
            raise ValueError(f"本体类别不存在：{category_id}")
        if mode not in ("full", "incremental"):
            raise ValueError("mode 仅支持 full / incremental")
        if mode == "incremental":
            raise ValueError("增量迁入将在 P2 提供，当前请使用全量迁入")

        stats = await GraphSyncService.precheck(db, category_id)
        if stats["ontology_count"] == 0:
            raise ValueError("该类别下没有本体，无可迁入数据")
        if stats["total_entities"] == 0:
            raise ValueError("该类别下实体数为 0，拒绝执行迁入")

        if dry_run:
            return {"dry_run": True, **stats}

        running = (await db.execute(
            select(GraphSyncRun)
            .where(
                GraphSyncRun.category_id == category_id,
                GraphSyncRun.status.in_(("pending", "running")),
            )
            .limit(1)
        )).scalar_one_or_none()
        if running:
            raise PermissionError(f"该类别已有进行中的迁入任务（{running.id}），请等待完成")

        run = GraphSyncRun(
            category_id=category_id,
            mode=mode,
            status="pending",
            total_entities=stats["total_entities"],
            total_relations=stats["total_relations"],
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return _run_to_dict(run)

    @staticmethod
    async def get_run(db: AsyncSession, run_id: str) -> dict | None:
        run = await db.get(GraphSyncRun, run_id)
        return _run_to_dict(run) if run else None

    @staticmethod
    async def list_runs(db: AsyncSession, category_id: str, limit: int = 20) -> list[dict]:
        runs = (await db.execute(
            select(GraphSyncRun)
            .where(GraphSyncRun.category_id == category_id)
            .order_by(GraphSyncRun.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return [_run_to_dict(r) for r in runs]

    # ───────────────────────── 后台执行 ─────────────────────────

    @staticmethod
    async def execute_run(run_id: str, category_id: str) -> None:
        """BackgroundTasks 入口：置 running → 线程中迁入 → 重建投影 → done/failed。

        进度：工作线程写共享 ``progress`` dict，独立协程每 2s flush 到任务表。
        """
        from services.notification_hub import hub

        progress = {"entities": 0, "relations": 0}

        async with async_session() as db:
            run = await db.get(GraphSyncRun, run_id)
            if not run or run.status != "pending":
                logger.error("迁入任务 %s 不存在或状态异常，跳过执行", run_id)
                return
            run.status = "running"
            run.started_at = _now()
            await db.commit()

        async def _flush() -> None:
            while True:
                await asyncio.sleep(2)
                try:
                    async with async_session() as db:
                        r = await db.get(GraphSyncRun, run_id)
                        if r and r.status == "running":
                            r.entity_count = progress["entities"]
                            r.relation_count = progress["relations"]
                            await db.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("刷新迁入进度失败（run=%s）", run_id)

        flusher = asyncio.create_task(_flush())
        started = time.time()
        try:
            if settings.GRAPH_STORE_PROVIDER != "neo4j":
                raise RuntimeError("图存储不是 Neo4j，无法执行图迁入")

            # 同步迁入跑在工作线程（内部自建 event loop 做 PG 分页读）
            stats = await asyncio.to_thread(_run_full_sync, category_id, progress)
            logger.info(
                "迁入写入完成（cid=%s）：实体 %d 类 / 关系 %d 类 / 耗时 %ss",
                category_id, len(stats["entity_types"]), len(stats["relation_types"]),
                stats["elapsed_seconds"],
            )
            # 迁入后自动重建投影（M0）；GDS 不可用时记录原因但不失败
            projection: dict | None = None
            projection_error = ""
            try:
                projection = await asyncio.to_thread(gds.ensure_category_projection, category_id)
            except Exception as exc:  # noqa: BLE001
                projection_error = str(exc)
                logger.warning("迁入完成但投影重建失败（%s）：%s", category_id, exc)

            async with async_session() as db:
                r = await db.get(GraphSyncRun, run_id)
                r.status = "done"
                r.entity_count = progress["entities"]
                r.relation_count = progress["relations"]
                r.projection = json.dumps(
                    {"result": projection, "error": projection_error}, ensure_ascii=False
                )
                r.finished_at = _now()
                r.error = None
                await db.commit()
            logger.info(
                "图迁入完成 run=%s cid=%s 实体=%d 关系=%d 耗时=%.1fs",
                run_id, category_id, progress["entities"], progress["relations"],
                time.time() - started,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("图迁入失败 run=%s", run_id)
            async with async_session() as db:
                r = await db.get(GraphSyncRun, run_id)
                if r:
                    r.status = "failed"
                    r.entity_count = progress["entities"]
                    r.relation_count = progress["relations"]
                    r.error = str(exc)[:4000]
                    r.finished_at = _now()
                    await db.commit()
        finally:
            flusher.cancel()
            try:
                hub.notify()
            except Exception:  # noqa: BLE001
                pass


# ───────────────────────── Neo4j 侧（同步，跑在工作线程） ─────────────────────────


def _neo4j_driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _fetch_graph_distribution() -> dict[str, dict]:
    """Neo4j 侧各 category 的节点/关系分布 + 投影状态（一次往返；库不可用返回空）。"""
    dist: dict[str, dict] = {}
    try:
        driver = _neo4j_driver()
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as s:
                for rec in s.run(
                    "MATCH (e:Entity) WHERE e.category_id IS NOT NULL "
                    "RETURN e.category_id AS cid, count(e) AS nodes"
                ):
                    dist.setdefault(rec["cid"], {"nodes": 0, "relations": 0})["nodes"] = rec["nodes"]
                for rec in s.run(
                    "MATCH ()-[r]->() WHERE r.category_id IS NOT NULL "
                    "RETURN r.category_id AS cid, count(r) AS rels"
                ):
                    dist.setdefault(rec["cid"], {"nodes": 0, "relations": 0})["relations"] = rec["rels"]
                if gds.gds_available():
                    for cid in list(dist.keys()):
                        dist[cid]["projection"] = gds.projection_stats(gds.category_projection_name(cid))
        finally:
            driver.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Neo4j 图分布失败：%s", exc)
    return dist


def _clear_category_graph(s, category_id: str) -> int:
    """全量模式清理：分批 DETACH DELETE 该类别旧子图（天然对账，无残留旧标签）。"""
    deleted = 0
    while True:
        cnt = s.run(
            "MATCH (n:Entity {category_id: $cid}) "
            "WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS c",
            cid=category_id, batch=DELETE_BATCH,
        ).single()["c"]
        deleted += cnt
        if cnt < DELETE_BATCH:
            return deleted


def _run_full_sync(category_id: str, progress: dict[str, int]) -> dict:
    """全量迁入主流程（工作线程内执行，自建 event loop 分页读 PG）。

    MERGE 语义：命中业务图已有同 id 实体节点时补分析图属性/标签（同一实体，
    双图共存）；未命中时创建。全量模式先按 category_id 清图，MERGE 只会命中
    业务图残留节点，幂等且不会撞 :Entity.id 唯一约束。
    """
    return asyncio.run(_import_category(category_id, progress))


async def _import_category(category_id: str, progress: dict[str, int]) -> dict:
    """分页读取 PostgreSQL → 按类型分组 → UNWIND 批量写入 Neo4j。"""
    driver = _neo4j_driver()
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        driver.close()
        raise RuntimeError(f"无法连接 Neo4j：{exc}") from exc

    # 工作线程内自建 event loop（asyncio.run）读 PG：全局 async_session 的连接池
    # 绑定主线程 loop，跨 loop 使用会报 "attached to a different loop"——必须配
    # 线程私有的 AsyncEngine，用完即销毁。
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    t0 = time.time()
    label_counts: dict[str, int] = {}
    rel_counts: dict[str, int] = {}
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            # 唯一约束（幂等）：业务图通常已建 Entity.id 唯一约束，先查再建，
            # 避免 IF NOT EXISTS 撞等价约束时的 "has no effect" SCHEMA 通知刷日志
            has_id_unique = s.run(
                "SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties "
                "WHERE type = 'UNIQUENESS' AND entityType = 'NODE' "
                "AND labelsOrTypes = ['Entity'] AND properties = ['id'] "
                "RETURN count(*) AS c"
            ).single()["c"]
            if not has_id_unique:
                s.run(
                    "CREATE CONSTRAINT analysis_entity_id "
                    "FOR (n:Entity) REQUIRE n.id IS UNIQUE"
                )

            _clear_category_graph(s, category_id)

            async with Session() as db:
                # 标签/关系类型编码映射：图内标识用稳定编码（Ontology.code /
                # OntologyRelation.code），未定义编码时回落名称。
                ont_rows = (await db.execute(
                    select(Ontology.id, Ontology.code, Ontology.name)
                    .where(Ontology.category_id == category_id)
                )).all()
                label_by_ont = {rid: (code or name) for rid, code, name in ont_rows}
                # 二级回落：ontology_id 悬空的实体（历史数据）按类型名解析编码
                label_by_name = {name: (code or name) for _rid, code, name in ont_rows}
                reldef_rows = (await db.execute(
                    select(OntologyRelation.id, OntologyRelation.code, OntologyRelation.name)
                    .where(OntologyRelation.category_id == category_id)
                )).all()
                rtype_by_def = {rid: (code or name) for rid, code, name in reldef_rows}
                rtype_by_name = {name: (code or name) for _rid, code, name in reldef_rows}

                # ── 实体：游标分页读 PG，按本体编码分组 UNWIND 写入 ──
                last_id = ""
                while True:
                    rows = (await db.execute(
                        select(Entity)
                        .where(
                            Entity.ontology_id.in_(_ontology_ids_subq(category_id)),
                            Entity.id > last_id,
                        )
                        .order_by(Entity.id)
                        .limit(READ_CHUNK)
                    )).scalars().all()
                    if not rows:
                        break

                    by_label: dict[str, list[dict[str, Any]]] = {}
                    for ent in rows:
                        by_label.setdefault(
                            label_by_ont.get(ent.ontology_id)
                            or label_by_name.get(ent.entity_type)
                            or ent.entity_type,
                            [],
                        ).append({
                            "id": ent.id,
                            "kb_id": ent.kb_id,
                            "category_id": category_id,
                            "name": ent.name,
                            "entity_type": ent.entity_type,
                            "ontology_id": ent.ontology_id or "",
                            "description": ent.description or "",
                            "properties": _props(ent.properties),
                        })
                        last_id = ent.id

                    for label, payloads in by_label.items():
                        lbl = _esc(label)
                        # MERGE 只按 :Entity {id}（命中业务图残留节点时不能因缺第二标签
                        # 而走 CREATE，否则撞 id 唯一约束）；第二标签随后 SET 补打，幂等
                        s.run(
                            f"UNWIND $rows AS row "
                            f"MERGE (n:Entity {{id: row.id}}) "
                            f"ON CREATE SET n += row "
                            f"ON MATCH SET n.category_id = row.category_id, "
                            f"        n.name = row.name, n.entity_type = row.entity_type, "
                            f"        n.ontology_id = row.ontology_id, "
                            f"        n.description = row.description, "
                            f"        n.properties = row.properties, n.kb_id = row.kb_id "
                            f"SET n:{lbl}",
                            rows=payloads,
                        )
                        label_counts[label] = label_counts.get(label, 0) + len(payloads)
                        progress["entities"] += len(payloads)

                # ── 关系：source 实体圈定，UNWIND 批量 MERGE ──
                last_rid = ""
                while True:
                    rrows = (await db.execute(
                        select(Relation)
                        .where(
                            Relation.source_entity_id.in_(_category_entity_ids_subq(category_id)),
                            Relation.id > last_rid,
                        )
                        .order_by(Relation.id)
                        .limit(READ_CHUNK)
                    )).scalars().all()
                    if not rrows:
                        break

                    by_type: dict[str, list[dict[str, Any]]] = {}
                    for rel in rrows:
                        by_type.setdefault(
                            rtype_by_def.get(rel.relation_def_id)
                            or rtype_by_name.get(rel.relation_type)
                            or rel.relation_type,
                            [],
                        ).append({
                            "start": rel.source_entity_id,
                            "end": rel.target_entity_id,
                            "relation_id": rel.id,
                            "kb_id": rel.kb_id,
                            "category_id": category_id,
                            "relation_def_id": rel.relation_def_id or "",
                        })
                        last_rid = rel.id

                    for rtype, payloads in by_type.items():
                        rt = _esc(rtype)
                        # 目标实体不在本类别图中（跨类别/缺失）时 MATCH 落空自动跳过
                        s.run(
                            f"UNWIND $rows AS row "
                            f"MATCH (a:Entity {{id: row.start}}), (b:Entity {{id: row.end}}) "
                            f"MERGE (a)-[r:{rt} {{relation_id: row.relation_id}}]->(b) "
                            f"SET r.kb_id = row.kb_id, r.category_id = row.category_id, "
                            f"    r.relation_def_id = row.relation_def_id",
                            rows=payloads,
                        )
                        rel_counts[rtype] = rel_counts.get(rtype, 0) + len(payloads)
                        progress["relations"] += len(payloads)
    finally:
        driver.close()
        await engine.dispose()

    return {
        "entity_types": label_counts,
        "relation_types": rel_counts,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
