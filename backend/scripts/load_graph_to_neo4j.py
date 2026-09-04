#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 build_aviation_graph.py 生成的 CSV 导入 Neo4j，并做图计算前置校验。

设计要点：
  - 所有节点统一带 :AviationEntity 标签并在其 id 上建唯一约束，
    这样关系导入时可以 O(1) 定位端点，不受具体标签影响。
  - 刻意**不复用**应用运行时使用的 :Entity 标签：业务文档图谱（KnowledgeBase/
    Document/Chunk/Entity/Relation）与这套"领域底图"是两套数据，混用同一标签会
    造成约束冲突，也会让业务侧按 kb_id 的清理逻辑误伤领域数据。
  - 按标签 / 关系类型分批 UNWIND 写入，避免逐条提交带来的往返开销。
  - 关系类型来自 CSV 的 type 列，属于本脚本可控的白名单，用字符串拼接 Cypher 安全。

用法：
  python load_graph_to_neo4j.py                          # 导入默认数据集
  python load_graph_to_neo4j.py --reset                  # 先清空再导入
  python load_graph_to_neo4j.py --verify-only            # 只做校验，不导入
  python load_graph_to_neo4j.py --uri bolt://host:7687 --password xxx
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "ontology123"
BATCH_SIZE = 2000


def _load_env_defaults() -> tuple[str, str, str]:
    """尽量复用 backend/.env 中的配置，避免两处维护。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    uri, user, pwd = DEFAULT_URI, DEFAULT_USER, DEFAULT_PASSWORD
    if not env_path.exists():
        return uri, user, pwd
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "NEO4J_URI" and v:
                uri = v
            elif k == "NEO4J_USER" and v:
                user = v
            elif k == "NEO4J_PASSWORD" and v:
                pwd = v
    except OSError:
        pass
    return uri, user, pwd


def read_nodes(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            props = json.loads(r["props_json"] or "{}")
            props["id"] = r["node_id"]
            props["name"] = r["name"]
            rows.append({"label": r["label"], "props": props})
    return rows


def read_edges(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "start": r["start_id"],
                "end": r["end_id"],
                "type": r["type"],
                "props": json.loads(r["props_json"] or "{}"),
            })
    return rows


def _sanitize_label(label: str) -> str:
    """标签必须是合法 Cypher 标识符。"""
    if not label.replace("_", "a").isalnum():
        raise ValueError(f"非法节点标签: {label}")
    return label


def _sanitize_rel_type(rtype: str) -> str:
    if not rtype.replace("_", "a").isalnum():
        raise ValueError(f"非法关系类型: {rtype}")
    return rtype


class Neo4jLoader:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        try:
            from neo4j import GraphDatabase
        except ImportError:
            print("缺少 neo4j 驱动，请先执行： pip install neo4j", file=sys.stderr)
            raise SystemExit(2)

        self._driver_cls = GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    # ───────── 基础操作 ─────────

    def verify_connectivity(self) -> bool:
        try:
            self.driver.verify_connectivity()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"无法连接 Neo4j：{e}", file=sys.stderr)
            return False

    def close(self):
        try:
            self.driver.close()
        except Exception:
            pass

    def reset(self):
        print("清空现有图数据 ...")
        with self.driver.session(database=self.database) as s:
            total = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            deleted = 0
            while True:
                res = s.run("MATCH (n) WITH n LIMIT 50000 DETACH DELETE n RETURN count(n) AS c").single()
                n = res["c"]
                deleted += n
                if n == 0:
                    break
            print(f"  已删除 {deleted} 个节点（原有 {total}）")

    def ensure_constraints(self):
        print("创建约束与索引 ...")
        with self.driver.session(database=self.database) as s:
            s.run(
                "CREATE CONSTRAINT aviation_entity_id IF NOT EXISTS "
                "FOR (n:AviationEntity) REQUIRE n.id IS UNIQUE"
            )
            for label in ("Aircraft", "Component", "WorkOrder", "FaultMode", "AirworthinessDirective"):
                s.run(
                    f"CREATE INDEX idx_{label.lower()}_name IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.name)"
                )

    def import_nodes(self, nodes: list[dict[str, Any]]):
        by_label: dict[str, list[dict]] = {}
        for n in nodes:
            by_label.setdefault(n["label"], []).append(n["props"])

        print(f"导入节点（{len(nodes):,} 个，{len(by_label)} 类）...")
        t0 = time.time()
        with self.driver.session(database=self.database) as s:
            for label, props_list in by_label.items():
                lbl = _sanitize_label(label)
                cypher = (
                    f"UNWIND $rows AS row "
                    f"CREATE (n:{lbl}:AviationEntity) SET n = row"
                )
                for i in range(0, len(props_list), BATCH_SIZE):
                    s.run(cypher, rows=props_list[i:i + BATCH_SIZE])
                print(f"  {lbl:<24} {len(props_list):>8,}")
        print(f"  节点导入耗时 {time.time() - t0:.1f}s")

    def import_edges(self, edges: list[dict[str, Any]]):
        by_type: dict[str, list[dict]] = {}
        for e in edges:
            by_type.setdefault(e["type"], []).append(e)

        print(f"导入关系（{len(edges):,} 条，{len(by_type)} 类）...")
        t0 = time.time()
        with self.driver.session(database=self.database) as s:
            for rtype, rows in by_type.items():
                rt = _sanitize_rel_type(rtype)
                cypher = (
                    "UNWIND $rows AS row "
                    "MATCH (a:AviationEntity {id: row.start}), (b:AviationEntity {id: row.end}) "
                    f"CREATE (a)-[r:{rt}]->(b) SET r = row.props"
                )
                for i in range(0, len(rows), BATCH_SIZE):
                    s.run(cypher, rows=rows[i:i + BATCH_SIZE])
                print(f"  {rt:<24} {len(rows):>8,}")
        print(f"  关系导入耗时 {time.time() - t0:.1f}s")

    # ───────── 校验 ─────────

    def stats(self):
        print("\n" + "=" * 62)
        print("图数据库现状统计")
        print("=" * 62)
        with self.driver.session(database=self.database) as s:
            nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            print(f"节点总数：{nodes:,}    关系总数：{rels:,}\n")

            print("节点构成：")
            for rec in s.run(
                "MATCH (n) RETURN labels(n) AS ls, count(*) AS c ORDER BY c DESC"
            ):
                lbl = [x for x in rec["ls"] if x != "AviationEntity"]
                print(f"  {'/'.join(lbl) or 'AviationEntity':<28} {rec['c']:>8,}")

            print("\n关系构成：")
            for rec in s.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
            ):
                print(f"  {rec['t']:<28} {rec['c']:>8,}")

    def sanity_checks(self):
        """数据真实性 / 完整性抽查，帮助发现导入问题。"""
        print("\n" + "=" * 62)
        print("数据抽查")
        print("=" * 62)
        with self.driver.session(database=self.database) as s:
            # 1. 每架飞机的发动机型号是否一致（真实飞机不混装不同型号）
            mixed = s.run("""
                MATCH (a:Aircraft)-[:INSTALLED_ENGINE]->(e:Engine)
                WITH a, collect(DISTINCT e.model) AS models
                WHERE size(models) > 1
                RETURN count(a) AS c
            """).single()["c"]
            print(f"1) 混装不同型号发动机的飞机数：{mixed}（应为 0）")

            # 2. 飞行小时 < 飞行循环 的不合理部件数
            bad = s.run("""
                MATCH (c:Component)
                WHERE c.tsn < c.csn
                RETURN count(c) AS c
            """).single()["c"]
            print(f"2) 飞行小时少于飞行循环的部件数：{bad}（应为 0）")

            # 3. 孤立节点（没有任何关系）——通常应为 0
            orphan = s.run("""
                MATCH (n) WHERE NOT (n)--() RETURN count(n) AS c
            """).single()["c"]
            print(f"3) 孤立节点数：{orphan}")

            # 4. NFF 占比
            rec = s.run("""
                MATCH (w:WorkOrder)
                RETURN count(w) AS total,
                       sum(CASE WHEN w.is_nff THEN 1 ELSE 0 END) AS nff
            """).single()
            if rec["total"]:
                rate = rec["nff"] / rec["total"] * 100
                print(f"4) NFF（无故障发现）工单占比：{rate:.1f}%（行业典型 15%~25%）")

            # 5. 故障高频部件 Top5 —— 图计算的典型入口
            print("\n5) 故障工单最多的部件类型 Top5：")
            for r in s.run("""
                MATCH (w:WorkOrder)-[:REPORTS_FAULT]->(:FaultMode)-[:OCCURS_AT]->(ct:ComponentType)
                RETURN ct.name AS part, count(w) AS wo_count
                ORDER BY wo_count DESC LIMIT 5
            """):
                print(f"   {r['part']:<28} {r['wo_count']:>6} 条工单")

    def gds_check(self):
        """检查 Neo4j GDS 是否可用，并为图计算准备一个内存图投影。"""
        print("\n" + "=" * 62)
        print("图计算（GDS）可用性")
        print("=" * 62)
        with self.driver.session(database=self.database) as s:
            try:
                rows = list(s.run(
                    "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'gds.' RETURN count(*) AS c"
                ))
                gds_count = rows[0]["c"] if rows else 0
            except Exception as e:  # noqa: BLE001
                print(f"无法查询 GDS 过程：{e}")
                return

            if not gds_count:
                print("未检测到 GDS 插件。docker-compose 已配置自动安装，")
                print("若容器是先于配置启动的，请执行： docker compose restart neo4j")
                return

            print(f"GDS 过程数：{gds_count}")

            # 建立一个用于故障传播分析的图投影
            name = "aviationFaultGraph"
            existing = s.run(
                "CALL gds.graph.exists($name) YIELD exists RETURN exists",
                name=name,
            ).single()["exists"]
            if existing:
                s.run("CALL gds.graph.drop($name)", name=name)
                print(f"已删除旧投影 {name}")

            try:
                s.run("""
                    CALL gds.graph.project($name, ['ComponentType','FaultMode','System'], {
                        OCCURS_AT:   {orientation:'UNDIRECTED'},
                        MAY_CAUSE:   {orientation:'NATURAL'},
                        PART_OF:     {orientation:'UNDIRECTED'}
                    })
                """, name=name)
                info = s.run("""
                    CALL gds.graph.list($name) YIELD nodeCount, relationshipCount
                    RETURN nodeCount, relationshipCount
                """, name=name).single()
                print(f"图投影 {name} 已创建：{info['nodeCount']:,} 节点 / {info['relationshipCount']:,} 关系")
                print("\n后续可直接跑的图算法示例：")
                print("  CALL gds.pageRank.stream('aviationFaultGraph') YIELD nodeId, score")
                print("  RETURN gds.util.asNode(nodeId).name AS part, score ORDER BY score DESC LIMIT 10")
            except Exception as e:  # noqa: BLE001
                print(f"创建图投影失败：{e}")


def main() -> int:
    env_uri, env_user, env_pwd = _load_env_defaults()

    ap = argparse.ArgumentParser(description="导入民航维修图谱到 Neo4j")
    ap.add_argument("--uri", default=env_uri)
    ap.add_argument("--user", default=env_user)
    ap.add_argument("--password", default=env_pwd)
    ap.add_argument("--database", default="neo4j")
    ap.add_argument("--data-dir", default=None, help="数据集目录，默认 backend/data/graph_dataset")
    ap.add_argument("--reset", action="store_true", help="导入前清空图库")
    ap.add_argument("--verify-only", action="store_true", help="只校验不导入")
    ap.add_argument("--skip-gds", action="store_true", help="跳过 GDS 图投影")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir) if args.data_dir else base / "data" / "graph_dataset"
    nodes_path = data_dir / "graph_nodes.csv"
    edges_path = data_dir / "graph_edges.csv"

    if not args.verify_only and not (nodes_path.exists() and edges_path.exists()):
        print(f"数据集不存在：{data_dir}", file=sys.stderr)
        print("请先执行： python scripts/build_aviation_graph.py --fleet 370", file=sys.stderr)
        return 2

    loader = Neo4jLoader(args.uri, args.user, args.password, args.database)
    try:
        if not loader.verify_connectivity():
            print("\n请确认：")
            print("  1) 已安装 Docker Desktop 并启动")
            print("  2) 在项目根目录执行过 docker compose up -d")
            print("  3) .env 中 NEO4J_URI / NEO4J_PASSWORD 与 docker-compose 一致")
            return 1

        if args.verify_only:
            loader.stats()
            loader.sanity_checks()
            if not args.skip_gds:
                loader.gds_check()
            return 0

        if args.reset:
            loader.reset()

        print(f"读取数据集：{data_dir}")
        nodes = read_nodes(nodes_path)
        edges = read_edges(edges_path)
        print(f"  {len(nodes):,} 节点 / {len(edges):,} 关系\n")

        loader.ensure_constraints()
        loader.import_nodes(nodes)
        loader.import_edges(edges)

        loader.stats()
        loader.sanity_checks()
        if not args.skip_gds:
            loader.gds_check()
        return 0
    finally:
        loader.close()


if __name__ == "__main__":
    raise SystemExit(main())
