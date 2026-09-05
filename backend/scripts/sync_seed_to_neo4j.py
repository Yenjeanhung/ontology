#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 seed_aviation_entities.py 生成的民航维修实体/关系批量同步到 Neo4j，供图推理与图计算使用。

为什么单独写一个脚本
====================
1. 运行时 adapter（providers/graph_store）的 upsert_entity / upsert_relation 是
   **逐条 MERGE**，写 10 万节点 + 33 万关系会产生 43 万次事务往返，慢到不可用。
   这里改用 UNWIND 批量提交，按标签/关系类型分组，通常 1~2 分钟写完。
2. 图计算（GDS / 自定义算法）需要**语义化的标签与关系类型**才能按子图投影。
   因此这里除了通用的 :Entity 标签，还额外打上本体类型标签（如 :部件 / :维修工单），
   关系也直接用工体语义名（如 :涉及故障 / :发生于航空器），而不是统一的 :RELATES。
3. 与主业务图谱（文件抽取产生的 Chunk/Document 图）用不同标签，互不干扰。

产出（Neo4j）
============
- 节点：``(:Entity:<本体类型> {id, kb_id, name, entity_type, ontology_id, description, properties})``
- 关系：``(a)-[:<语义关系名> {relation_id, kb_id}]->(b)``
- GDS 投影（若插件可用）：``aviationMaintenance``，供 PageRank / 社区发现 / 中心性 / 路径分析

用法（从 backend 目录）
====================
    python scripts/sync_seed_to_neo4j.py
    python scripts/sync_seed_to_neo4j.py --kb-id 72f1ecec567e      # 指定知识库
    python scripts/sync_seed_to_neo4j.py --skip-gds                # 不建 GDS 投影
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import func, select

from config import settings
from database import async_session, engine, init_db
from models import Entity, KnowledgeBase, Relation

BATCH_SIZE = 2000
# 每次从关系库分页拉取的行数（读取是一等公民：10 万行不能一次性 load 进内存）
READ_CHUNK = 2000


# ───────────────────────── Neo4j 连接 ─────────────────────────

def get_driver():
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("缺少 neo4j 驱动，请先执行： pip install neo4j", file=sys.stderr)
        raise SystemExit(2)
    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _esc(identifier: str) -> str:
    """标签 / 关系类型转义：Neo4j 标识符含非 ASCII 或特殊字符时用反引号包裹。"""
    ident = (identifier or "").strip()
    if not ident:
        raise ValueError("空的标签/关系类型")
    safe = ident.replace("`", "")
    return f"`{safe}`"


# ───────────────────────── 关系型库读取 ─────────────────────────

def _props(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    return str(raw)


# ───────────────────────── 主流程 ─────────────────────────

def clear_kb(driver, kb_id: str, database: str):
    print(f"清理 Neo4j 中该知识库的旧数据（kb_id={kb_id}）...")
    t0 = time.time()
    with driver.session(database=database) as s:
        total = s.run(
            "MATCH (n) WHERE n.kb_id = $kb_id RETURN count(n) AS c", kb_id=kb_id
        ).single()["c"]
        deleted = 0
        while True:
            res = s.run(
                "MATCH (n {kb_id: $kb_id}) WITH n LIMIT 5000 DETACH DELETE n RETURN count(n) AS c",
                kb_id=kb_id,
            ).single()["c"]
            deleted += res
            if res == 0:
                break
    print(f"  已删除 {deleted:,} 个节点（原有 {total:,}），耗时 {time.time() - t0:.1f}s")


def ensure_constraints(driver, database: str, labels: list[str]):
    print("创建约束与索引 ...")
    with driver.session(database=database) as s:
        s.run(
            "CREATE CONSTRAINT aviation_entity_id IF NOT EXISTS "
            "FOR (n:Entity) REQUIRE n.id IS UNIQUE"
        )
        for lbl in labels:
            s.run(
                f"CREATE INDEX idx_{abs(hash(lbl)) % (10 ** 8)} IF NOT EXISTS "
                f"FOR (n:{_esc(lbl)}) ON (n.name)"
            )


async def import_entities(driver, database: str, session, kb_id: str) -> Counter:
    """流式导入实体：按 id 游标分页读，每页按标签分组后立刻 UNWIND 写 Neo4j。

    不把 10 万条一次性堆在内存里，内存占用与库大小解耦。
    """
    total = await session.scalar(
        select(func.count(Entity.id)).where(Entity.kb_id == kb_id)
    )
    print(f"导入实体（{total:,} 个）...")

    counts: Counter = Counter()
    last_id = ""
    processed = 0
    t0 = time.time()

    with driver.session(database=database) as s:
        while True:
            rows = (await session.execute(
                select(Entity)
                .where(Entity.kb_id == kb_id, Entity.id > last_id)
                .order_by(Entity.id)
                .limit(READ_CHUNK)
            )).scalars().all()
            if not rows:
                break

            by_label: dict[str, list[dict[str, Any]]] = {}
            for ent in rows:
                by_label.setdefault(ent.entity_type, []).append({
                    "id": ent.id,
                    "kb_id": ent.kb_id,
                    "name": ent.name,
                    "entity_type": ent.entity_type,
                    "ontology_id": ent.ontology_id or "",
                    "description": ent.description or "",
                    "properties": _props(ent.properties),
                })
                last_id = ent.id

            for label, payloads in by_label.items():
                lbl = _esc(label)
                cypher = f"UNWIND $rows AS row CREATE (n:Entity:{lbl}) SET n = row"
                s.run(cypher, rows=payloads)
                counts[label] += len(payloads)

            processed += len(rows)
            if processed % (READ_CHUNK * 10) < READ_CHUNK:
                print(f"  已写入 {processed:,}/{total:,}", flush=True)
    print()

    print("  实体构成：")
    for k, v in counts.most_common():
        print(f"    {k:<20} {v:>8,}")
    print(f"  实体导入耗时 {time.time() - t0:.1f}s")
    return counts


async def import_relations(driver, database: str, session, kb_id: str) -> Counter:
    """流式导入关系：分页读，每页按关系类型分组后立刻 UNWIND 写入。"""
    total = await session.scalar(
        select(func.count(Relation.id)).where(Relation.kb_id == kb_id)
    )
    print(f"导入关系（{total:,} 条）...")

    counts: Counter = Counter()
    last_id = ""
    processed = 0
    t0 = time.time()

    with driver.session(database=database) as s:
        while True:
            rows = (await session.execute(
                select(Relation)
                .where(Relation.kb_id == kb_id, Relation.id > last_id)
                .order_by(Relation.id)
                .limit(READ_CHUNK)
            )).scalars().all()
            if not rows:
                break

            by_type: dict[str, list[dict[str, Any]]] = {}
            for rel in rows:
                by_type.setdefault(rel.relation_type, []).append({
                    "start": rel.source_entity_id,
                    "end": rel.target_entity_id,
                    "relation_id": rel.id,
                    "kb_id": rel.kb_id,
                    "relation_def_id": rel.relation_def_id or "",
                })
                last_id = rel.id

            for rtype, payloads in by_type.items():
                rt = _esc(rtype)
                cypher = (
                    "UNWIND $rows AS row "
                    "MATCH (a:Entity {id: row.start}), (b:Entity {id: row.end}) "
                    f"CREATE (a)-[r:{rt}]->(b) "
                    "SET r.relation_id = row.relation_id, r.kb_id = row.kb_id, "
                    "    r.relation_def_id = row.relation_def_id"
                )
                s.run(cypher, rows=payloads)
                counts[rtype] += len(payloads)

            processed += len(rows)
            if processed % (READ_CHUNK * 10) < READ_CHUNK:
                print(f"  已写入 {processed:,}/{total:,}", flush=True)
    print()

    print("  关系构成：")
    for k, v in counts.most_common():
        print(f"    {k:<20} {v:>8,}")
    print(f"  关系导入耗时 {time.time() - t0:.1f}s")
    return counts


def gds_project(driver, database: str, node_labels: list[str], rel_types: list[str]):
    """创建 GDS 内存图投影，供 PageRank / 社区发现 / 中心性等图算法直接调用。"""
    print("\n" + "=" * 62)
    print("GDS 图投影")
    print("=" * 62)
    name = "aviationMaintenance"
    with driver.session(database=database) as s:
        try:
            cnt = s.run(
                "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'gds.' RETURN count(*) AS c"
            ).single()["c"]
        except Exception as e:  # noqa: BLE001
            print(f"无法查询 GDS 过程：{e}")
            return
        if not cnt:
            print("未检测到 GDS 插件，跳过投影。")
            print("docker-compose 已配置自动安装；若容器先于配置启动，请执行：")
            print("  docker compose restart neo4j")
            return

        print(f"GDS 过程数：{cnt:,}")
        if s.run("CALL gds.graph.exists($name) YIELD exists RETURN exists", name=name).single()["exists"]:
            s.run("CALL gds.graph.drop($name)", name=name)
            print(f"已删除旧投影 {name}")

        # 只投影"故障传播 / 根因分析"相关的核心子图，避免把 10 万节点全塞进内存。
        # 组成 用 REVERSE：让"父系统<-子部件"在投影中指向父，PageRank 才能把
        # 结构重要性汇聚到系统层（已实测：起落架收放作动筒/燃油计量组件等排前）。
        core_labels = [l for l in node_labels if l in ("部件", "故障模式", "故障原因", "系统")]
        rel_orient = {
            "表现为": "NATURAL", "征兆为": "NATURAL", "发生于": "NATURAL",
            "归属系统": "NATURAL", "上报代码": "NATURAL", "组成": "REVERSE",
        }
        core_rels = [r for r in rel_types if r in rel_orient]
        if not core_labels or not core_rels:
            print("核心标签/关系不足，跳过投影。")
            return

        rel_map = ", ".join(
            f"{_esc(r)}: {{orientation: '{rel_orient[r]}'}}" for r in core_rels
        )
        try:
            s.run(
                f"CALL gds.graph.project($name, $labels, {{{rel_map}}})",
                name=name, labels=core_labels,
            )
            info = s.run(
                "CALL gds.graph.list($name) YIELD nodeCount, relationshipCount "
                "RETURN nodeCount, relationshipCount", name=name
            ).single()
            print(f"图投影 {name} 已创建：{info['nodeCount']:,} 节点 / {info['relationshipCount']:,} 关系")
            print("\n可直接运行的图算法示例：")
            print("  CALL gds.pageRank.stream('aviationMaintenance') YIELD nodeId, score")
            print("  RETURN gds.util.asNode(nodeId).name AS node, score ORDER BY score DESC LIMIT 10")
            print("  CALL gds.louvain.stream('aviationMaintenance') YIELD nodeId, communityId")
            print("  RETURN communityId, count(*) AS size ORDER BY size DESC LIMIT 10")
        except Exception as e:  # noqa: BLE001
            print(f"创建图投影失败：{e}")


def sanity_checks(driver, database: str, kb_id: str):
    print("\n" + "=" * 62)
    print("图数据库抽查")
    print("=" * 62)
    with driver.session(database=database) as s:
        n = s.run("MATCH (n) WHERE n.kb_id = $kb_id RETURN count(n) AS c", kb_id=kb_id).single()["c"]
        r = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"节点总数：{n:,}    关系总数：{r:,}\n")

        print("故障工单最多的部件 Top8（图计算典型入口）：")
        for rec in s.run(
            "MATCH (w:`维修工单`)-[:`涉及部件`]->(c:`部件`) "
            "RETURN c.name AS part, count(w) AS wo_count "
            "ORDER BY wo_count DESC LIMIT 8"
        ):
            print(f"   {str(rec['part'])[:36]:<38} {rec['wo_count']:>6} 条工单")

        print("\n各机型工单分布 Top8：")
        for rec in s.run(
            "MATCH (w:`维修工单`)-[:`发生于航空器`]->(a:`航空器`) "
            "RETURN a.name AS ac, count(w) AS c ORDER BY c DESC LIMIT 8"
        ):
            print(f"   {str(rec['ac'])[:36]:<38} {rec['c']:>6} 条工单")

        orphan = s.run(
            "MATCH (n) WHERE n.kb_id = $kb_id AND NOT (n)--() RETURN count(n) AS c",
            kb_id=kb_id,
        ).single()["c"]
        print(f"\n孤立节点数：{orphan:,}")


async def run(args) -> int:
    database = settings.NEO4J_DATABASE

    async with async_session() as session:
        # 定位知识库
        if args.kb_id:
            kb_id = args.kb_id
        else:
            kb_id = await session.scalar(
                select(KnowledgeBase.id).where(KnowledgeBase.name.like("%民航维修领域图谱%"))
            )
        if not kb_id:
            print("未找到种子知识库，请先执行： python scripts/seed_aviation_entities.py", file=sys.stderr)
            return 2
        print(f"种子知识库 id：{kb_id}")

        # 预读标签（用于建索引与 GDS 投影）
        labels = list((await session.execute(
            select(Entity.entity_type).where(Entity.kb_id == kb_id).distinct()
        )).scalars().all())

    driver = get_driver()
    try:
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        print(f"无法连接 Neo4j：{e}", file=sys.stderr)
        print("请确认 docker compose 中 neo4j 已启动，且 .env 的 NEO4J_* 配置一致。", file=sys.stderr)
        return 1

    try:
        clear_kb(driver, kb_id, database)
        ensure_constraints(driver, database, labels)

        async with async_session() as session:
            ent_counts = await import_entities(driver, database, session, kb_id)
            rel_counts = await import_relations(driver, database, session, kb_id)

        print("\n" + "=" * 62)
        print("同步完成")
        print("=" * 62)
        print(f"实体：{sum(ent_counts.values()):,}（{len(ent_counts)} 类）")
        print(f"关系：{sum(rel_counts.values()):,}（{len(rel_counts)} 类）")

        sanity_checks(driver, database, kb_id)
        if not args.skip_gds:
            gds_project(driver, database, labels, list(rel_counts.keys()))
        return 0
    finally:
        driver.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="同步民航维修种子实体到 Neo4j")
    ap.add_argument("--kb-id", default=None, help="知识库 id；默认取名为「民航维修领域图谱（种子数据）」的库")
    ap.add_argument("--skip-gds", action="store_true", help="跳过 GDS 图投影")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
