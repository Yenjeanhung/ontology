#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理 Neo4j 中的孤儿图数据：与 PostgreSQL 知识库对账，删除已无对应 KB 的图数据。

背景
====
图数据库没有外键约束：在 PG 里删除 KnowledgeBase 时，运行时若未级联清理
Neo4j（或历史脚本直接写图、绕过 kb_id 规范），就会留下孤儿节点/关系。
本项目 Neo4j 中曾发现三类残留：

1. 历史脚本 ``build_aviation_graph.py`` 写入的英文标签图
   （:MaintenanceTask / :Component ...），节点无 kb_id，约 10 万节点 + 百万关系；
2. 旧版运行时 adapter 的 ``(:Relation)`` 中间节点模式产物（无 kb_id）；
3. PG 中已删除知识库（kb_id 不在 knowledge_bases 表）对应的 Entity/Relation。

清理规则（保守，只删"确认无主"的数据）
====================================
删除同时满足以下条件的节点（DETACH DELETE，连带其关系）：

- ``n.kb_id IS NULL`` 或为空串，**且** 该节点不带任何"运行时还会再写"的标签
  （:KnowledgeBase / :Document / :Chunk 属于文档图管线，同样以 kb_id 判定，
  这里一并对账，无 kb_id 的也删）；或
- ``n.kb_id`` 不在 PG ``knowledge_bases.id`` 集合中。

保留：所有 kb_id 指向存活知识库的节点。

用法（从 backend 目录）
====================
    python scripts/cleanup_neo4j_orphans.py --dry-run   # 只统计，不删除
    python scripts/cleanup_neo4j_orphans.py             # 执行清理
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _BACKEND_DIR)

from neo4j import GraphDatabase
from sqlalchemy import select

from config import settings
from database import async_session
from models import KnowledgeBase

DELETE_BATCH = 10_000

# 这些标签若缺 kb_id 一律视为孤儿（图数据必须可归属到某个知识库）
_ORPHAN_COND = "n.kb_id IS NULL OR n.kb_id = ''"


async def alive_kb_ids() -> set[str]:
    async with async_session() as s:
        rows = (await s.execute(select(KnowledgeBase.id))).scalars().all()
    return set(rows)


def count_orphans(s, alive: set[str]) -> dict:
    """统计将被删除的节点，按标签分组（用于 dry-run 与删除前确认）。"""
    stats = {}
    q = (
        f"MATCH (n) WHERE {_ORPHAN_COND} OR NOT n.kb_id IN $alive "
        "RETURN labels(n) AS lbls, count(*) AS c ORDER BY c DESC"
    )
    for rec in s.run(q, alive=list(alive)):
        key = "+".join(rec["lbls"]) or "(无标签)"
        stats[key] = rec["c"]
    return stats


def delete_orphans(driver, database: str, alive: set[str]) -> tuple[int, int]:
    """分批 DETACH DELETE 孤儿节点，返回（删除节点数, 删除关系数）。

    关系数用全库前后差值计算：DETACH DELETE 之后再数关系已不可得。
    """
    with driver.session(database=database) as s:
        rels_before = s.run("MATCH ()-[x]->() RETURN count(x) AS c").single()["c"]

    nodes = 0
    t0 = time.time()
    with driver.session(database=database) as s:
        while True:
            deleted = s.run(
                f"MATCH (n) WHERE {_ORPHAN_COND} OR NOT n.kb_id IN $alive "
                "WITH n LIMIT $batch DETACH DELETE n RETURN count(n) AS c",
                alive=list(alive),
                batch=DELETE_BATCH,
            ).single()["c"]
            nodes += deleted
            if deleted == 0:
                break
            print(f"  已删除 {nodes:,} 节点（{time.time() - t0:.0f}s）", flush=True)

    with driver.session(database=database) as s:
        rels_after = s.run("MATCH ()-[x]->() RETURN count(x) AS c").single()["c"]
    return nodes, rels_before - rels_after


def final_stats(driver, database: str):
    with driver.session(database=database) as s:
        n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r = s.run("MATCH ()-[x]->() RETURN count(x) AS c").single()["c"]
        print(f"\n清理后全库规模：{n:,} 节点 / {r:,} 关系")
        print("按 kb_id 分布：")
        for rec in s.run(
            "MATCH (n) UNWIND CASE WHEN n.kb_id IS NULL THEN ['<NULL>'] ELSE [n.kb_id] END AS kb "
            "RETURN kb, count(*) AS c ORDER BY c DESC"
        ):
            print(f"  {rec['kb']:<40} {rec['c']:>8,}")


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 Neo4j 孤儿图数据（与 PG 知识库对账）")
    ap.add_argument("--dry-run", action="store_true", help="只统计将删除的数据，不执行")
    args = ap.parse_args()

    alive = asyncio.run(alive_kb_ids())
    print(f"PG 中存活知识库 {len(alive)} 个：{', '.join(sorted(alive))}")

    driver = GraphDatabase.driver(
        settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    try:
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        print(f"无法连接 Neo4j：{e}", file=sys.stderr)
        return 1

    try:
        with driver.session(database=settings.NEO4J_DATABASE) as s:
            stats = count_orphans(s, alive)
        if not stats:
            print("没有孤儿数据，图与 PG 一致。")
            return 0

        print("\n将删除的孤儿节点（按标签）：")
        for k, v in stats.items():
            print(f"  {k:<44} {v:>8,}")
        total = sum(stats.values())
        print(f"  合计 {total:,} 节点（及其全部关系）")

        if args.dry_run:
            print("\n[dry-run] 未执行删除。去掉 --dry-run 重新运行以实际清理。")
            return 0

        nodes, rels = delete_orphans(driver, settings.NEO4J_DATABASE, alive)
        print(f"\n完成：删除 {nodes:,} 节点 / {rels:,} 关系")
        final_stats(driver, settings.NEO4J_DATABASE)
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
