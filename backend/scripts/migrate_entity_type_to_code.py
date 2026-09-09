"""
数据迁移：把 Entity.entity_type 由 Ontology.name 改为 Ontology.code。

背景：
  此前抽取与回填逻辑按本体名（中文如「机型」）写入 Neo4j Entity 节点
  和 SQLite/PostgreSQL entities 表的 entity_type 字段。本体重命名后会导致
  entity_type 失配。改为 ontology.code（稳定 API 名）后不再受影响。

操作：
  - PostgreSQL/SQLite entities.entity_type：按 entities.ontology_id → ontologies.code 改写
  - Neo4j Entity 节点 entity_type：MATCH 后 SET
  - Kùzu（若启用）：同样通过 provider adapter 执行

适配矩阵：
  DATABASE_URL=postgresql+...  → asyncpg
  DATABASE_URL=sqlite:///...   → sqlite3
  NEO4J_URI=bolt://...         → neo4j driver
  GRAPH_PROVIDER=kuzu          → kuzu provider

幂等：
  - 仅当 entity_type 等于 Ontology.name 时才改写
  - 已等于 ontology.code 的不动
  - ontology_id 为空或本体缺 code 的跳过
  - 找不到本体映射的（脏数据）跳过 + 日志

用法：
  python scripts/migrate_entity_type_to_code.py --dry-run
  python scripts/migrate_entity_type_to_code.py
  python scripts/migrate_entity_type_to_code.py --only sqlite|postgres|graph
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_entity_type_to_code")


def _load_env() -> dict:
    """从 .env（如果存在）加载，避免重复依赖项目配置。"""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return dict(os.environ)
    raw = env_file.read_text(encoding="utf-8")
    env = dict(os.environ)
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env()


# ────────────────────────────────────── PostgreSQL ──────────────────────────────────────

def _pg_dsn() -> str | None:
    """从 DATABASE_URL 抽取 PostgreSQL DSN，去掉 SQLAlchemy 驱动前缀。"""
    url = ENV.get("DATABASE_URL") or ""
    if "postgresql" not in url:
        return None
    # postgresql+asyncpg://... → postgres://...
    if "+asyncpg" in url:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    elif "+psycopg" in url:
        url = url.replace("postgresql+psycopg://", "postgresql://")
    return url


def migrate_postgres(dry: bool) -> tuple[int, int]:
    import asyncio
    import asyncpg

    dsn = _pg_dsn()
    if not dsn:
        log.info("PostgreSQL: 未配置 DATABASE_URL，跳过")
        return (0, 0)

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT e.id AS eid, e.entity_type AS old_et, e.ontology_id AS oid,
                       o.name AS oname, o.code AS ocode
                FROM entities e
                JOIN ontologies o ON o.id = e.ontology_id
                WHERE e.ontology_id IS NOT NULL AND e.ontology_id <> ''
                  AND o.code IS NOT NULL AND o.code <> ''
                  AND e.entity_type = o.name
                """
            )
            scanned = len(rows)
            updated = 0
            skipped_no_code = 0
            for r in rows:
                old_et = (r["old_et"] or "").strip()
                ocode = (r["ocode"] or "").strip()
                if old_et == ocode:
                    skipped_no_code += 1
                    continue
                if dry:
                    log.info("[DRY] PG update entity.id=%s  %r -> %r", r["eid"], old_et, ocode)
                else:
                    await conn.execute(
                        "UPDATE entities SET entity_type = $1 WHERE id = $2",
                        ocode, r["eid"],
                    )
                updated += 1
            log.info(
                "PostgreSQL: scanned=%d updated=%d skipped_unchanged=%d",
                scanned, updated, skipped_no_code,
            )
            return (scanned, updated)
        finally:
            await conn.close()

    return asyncio.run(_run())


# ────────────────────────────────────── SQLite ──────────────────────────────────────

def _sqlite_path() -> Path | None:
    env_url = ENV.get("DATABASE_URL") or ENV.get("SQLITE_PATH") or ""
    if env_url:
        if env_url.startswith("sqlite:///"):
            return Path(env_url[len("sqlite:///"):])
        if env_url.endswith(".db") or env_url.endswith(".sqlite"):
            return Path(env_url)
    default = Path(__file__).resolve().parent.parent / "data" / "knowsource.db"
    return default if default.exists() else None


def migrate_sqlite(dry: bool) -> tuple[int, int]:
    path = _sqlite_path()
    if path is None or not path.exists():
        log.info("SQLite: 数据库不存在，跳过")
        return (0, 0)

    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT e.id AS eid, e.entity_type AS old_et, e.ontology_id AS oid,
                   o.name AS oname, o.code AS ocode
            FROM entities e
            JOIN ontologies o ON o.id = e.ontology_id
            WHERE e.ontology_id IS NOT NULL AND e.ontology_id <> ''
              AND o.code IS NOT NULL AND o.code <> ''
              AND e.entity_type = o.name
            """
        ).fetchall()
        scanned = len(rows)
        updated = 0
        for r in rows:
            old = (r["old_et"] or "").strip()
            ocode = (r["ocode"] or "").strip()
            if old == ocode:
                continue
            if dry:
                log.info("[DRY] SQLite update entity.id=%s  %r -> %r", r["eid"], old, ocode)
            else:
                cur.execute(
                    "UPDATE entities SET entity_type = ? WHERE id = ?",
                    (ocode, r["eid"]),
                )
            updated += 1
        if not dry:
            conn.commit()
        log.info("SQLite: scanned=%d updated=%d", scanned, updated)
        return (scanned, updated)
    finally:
        conn.close()


# ────────────────────────────────────── 共享：本体名 → code 映射 ──────────────────────────────────────

def load_ontology_mapping() -> dict[str, str]:
    """从 PostgreSQL ontologies 表加载 {name: code} 映射（Neo4j 没写 Ontology 节点）。"""
    import asyncio
    import asyncpg
    dsn = _pg_dsn()
    if not dsn:
        return {}

    async def _run() -> dict[str, str]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "SELECT name, code FROM ontologies WHERE code IS NOT NULL AND code <> ''"
            )
            return {(r["name"] or "").strip(): (r["code"] or "").strip() for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_run())


# ────────────────────────────────────── Neo4j ──────────────────────────────────────

def _neo4j_driver_or_adapter():
    """优先用项目 provider 抽象；不行再退化到 neo4j 原生 driver。"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from providers.graph_store import _get_adapter  # noqa: WPS433
        return ("adapter", _get_adapter())
    except Exception as e:
        log.debug("加载 provider adapter 失败，退到原生 driver: %s", e)
        try:
            from neo4j import GraphDatabase  # type: ignore
            uri = ENV.get("NEO4J_URI")
            user = ENV.get("NEO4J_USER") or "neo4j"
            password = ENV.get("NEO4J_PASSWORD") or ""
            if not uri:
                return ("none", None)
            driver = GraphDatabase.driver(uri, auth=(user, password))
            return ("driver", driver)
        except Exception as ee:
            log.warning("无法加载 neo4j driver: %s", ee)
            return ("none", None)


def migrate_graph(dry: bool) -> int:
    kind, conn = _neo4j_driver_or_adapter()
    if kind == "none" or conn is None:
        log.info("Graph: 未配置 Neo4j / Kùzu，跳过")
        return 0
    log.info("Graph: 使用 %s", "provider adapter" if kind == "adapter" else "原生 neo4j driver")

    def _exec(query: str, params: dict | None = None):
        if kind == "adapter":
            return conn._execute(query, params or {})
        else:
            with conn.session() as s:
                return s.run(query, params or {})

    def _exec_dict(query: str, params: dict | None = None):
        if kind == "adapter":
            return conn._execute_dict(query, params or {})
        else:
            with conn.session() as s:
                r = s.run(query, params or {})
                return [dict(rec) for rec in r]

    # 1. 拉 Ontology 映射（Neo4j 没写 Ontology 节点，从 PostgreSQL ontologies 表读）
    ont_by_name: dict[str, str] = load_ontology_mapping()
    if not ont_by_name:
        try:
            rows = _exec_dict(
                """
                MATCH (o:Ontology)
                RETURN o.id AS id, o.name AS name, o.code AS code
                """,
            )
            for r in rows or []:
                code = (r.get("code") or "").strip()
                name = (r.get("name") or "").strip()
                if name and code:
                    ont_by_name[name] = code
        except Exception:
            log.debug("Neo4j Ontology 节点不存在或查询失败，使用 PG 映射")
    log.info("Graph: 载入 %d 个 ontology (name→code)", len(ont_by_name))

    # 2. 批量改写 Entity.entity_type：用 CALL {...} 子查询一次性回填
    #    仅当 entity_type 命中 ontology.name 且该本体 code 非空时改写
    if not ont_by_name:
        return 0

    updated = 0
    if dry:
        # 统计 + 抽样
        for old_et, code in list(ont_by_name.items())[:10]:
            n = 0
            try:
                res = _exec_dict(
                    "MATCH (e:Entity) WHERE e.entity_type = $et RETURN count(e) AS n",
                    {"et": old_et},
                )
                n = (res[0]["n"] if res else 0) or 0
            except Exception:
                continue
            if n:
                log.info("[DRY] Graph would update %d entities: %r -> %r", n, old_et, code)
                updated += n
        log.info("Graph [DRY sample]: 估计改写 ≥%d 节点", updated)
        return updated

    # 真正执行：逐个 ontology.name 做 SET
    for old_et, code in ont_by_name.items():
        try:
            res = _exec_dict(
                """
                MATCH (e:Entity {entity_type: $et})
                SET e.entity_type = $code
                RETURN count(e) AS n
                """,
                {"et": old_et, "code": code},
            )
            n = (res[0]["n"] if res else 0) or 0
            if n:
                log.info("Graph: %d nodes  %r -> %r", n, old_et, code)
                updated += int(n)
        except Exception:
            log.exception("Graph 改写失败 entity_type=%r", old_et)

    log.info("Graph: 总改写 %d 节点", updated)
    return updated


# ────────────────────────────────────── main ──────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    p.add_argument(
        "--only",
        choices=["postgres", "sqlite", "graph"],
        help="只跑某一边（默认都跑）",
    )
    args = p.parse_args()

    targets = {args.only} if args.only else {"postgres", "sqlite", "graph"}

    if "postgres" in targets:
        migrate_postgres(args.dry_run)
    if "sqlite" in targets:
        migrate_sqlite(args.dry_run)
    if "graph" in targets:
        migrate_graph(args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())