"""
数据清理：把「非知识库抽取来源」实体的 kb_id 清空。

背景：
  kb_id 表示实体来自哪个知识库的文本抽取。此前手动创建实体会被错误地
  打上当前筛选的知识库 id（SQL entities 表 + 图节点均有），语义不符。

判据：
  source_file_id 为空的实体必然不是文本抽取产生（抽取流程总是携带
  source_file_id / source_chunk_id），其 kb_id 应清空。

操作：
  - SQLite/PostgreSQL entities 表：kb_id 置空
  - Neo4j Entity 节点（或 Kùzu adapter）：按实体 id 批量 REMOVE kb_id

幂等：
  - SQL 侧仅更新 kb_id 非空且 source_file_id 为空的行
  - 图侧仅对收集到的 id 执行 REMOVE（无则跳过）

用法：
  python scripts/cleanup_entity_kb_id.py --dry-run
  python scripts/cleanup_entity_kb_id.py
  python scripts/cleanup_entity_kb_id.py --only sqlite|postgres|graph
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("cleanup_entity_kb_id")

# 与 migrate_entity_type_to_code.py 相同的环境加载方式
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> dict:
    """从 .env（如果存在）加载，避免重复依赖项目配置。"""
    if ENV_FILE.exists():
        raw = ENV_FILE.read_text(encoding="utf-8")
        env = dict(os.environ)
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
        return env
    return dict(os.environ)


ENV = _load_env()

# 非抽取来源（source_file_id 为空）但 kb_id 非空的实体
_SELECT_SQL = """
SELECT id FROM entities
WHERE kb_id IS NOT NULL AND kb_id <> ''
  AND (source_file_id IS NULL OR source_file_id = '')
"""
_CLEAR_SQL = "UPDATE entities SET kb_id = '' WHERE id = ?"  # SQLite 占位符


# ────────────────────────────────────── PostgreSQL ──────────────────────────────────────

def _pg_dsn() -> str | None:
    url = ENV.get("DATABASE_URL") or ""
    if "postgresql" not in url:
        return None
    if "+asyncpg" in url:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    elif "+psycopg" in url:
        url = url.replace("postgresql+psycopg://", "postgresql://")
    return url


def cleanup_postgres(dry: bool) -> list[str]:
    dsn = _pg_dsn()
    if not dsn:
        log.info("PostgreSQL: 未配置 DATABASE_URL，跳过")
        return []

    import asyncio

    async def _run() -> list[str]:
        import asyncpg

        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(_SELECT_SQL)
            ids = [r["id"] for r in rows]
            if dry:
                for eid in ids[:20]:
                    log.info("[DRY] PG 清空 kb_id: entity.id=%s", eid)
                if len(ids) > 20:
                    log.info("[DRY] PG 其余 %d 条略", len(ids) - 20)
            else:
                await conn.execute(
                    "UPDATE entities SET kb_id = '' "
                    "WHERE kb_id IS NOT NULL AND kb_id <> '' "
                    "  AND (source_file_id IS NULL OR source_file_id = '')"
                )
            log.info("PostgreSQL: 待清理 %d 条", len(ids))
            return ids
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


def cleanup_sqlite(dry: bool) -> list[str]:
    path = _sqlite_path()
    if path is None or not path.exists():
        log.info("SQLite: 数据库不存在，跳过")
        return []

    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        ids = [r[0] for r in cur.execute(_SELECT_SQL).fetchall()]
        if dry:
            for eid in ids[:20]:
                log.info("[DRY] SQLite 清空 kb_id: entity.id=%s", eid)
            if len(ids) > 20:
                log.info("[DRY] SQLite 其余 %d 条略", len(ids) - 20)
        else:
            for eid in ids:
                cur.execute(_CLEAR_SQL, (eid,))
            conn.commit()
        log.info("SQLite: 待清理 %d 条", len(ids))
        return ids
    finally:
        conn.close()


# ────────────────────────────────────── 图库 ──────────────────────────────────────

def _graph_conn():
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
            return ("driver", GraphDatabase.driver(uri, auth=(user, password)))
        except Exception as ee:
            log.warning("无法加载 neo4j driver: %s", ee)
            return ("none", None)


def cleanup_graph(ids: list[str], dry: bool) -> int:
    """按实体 id 批量 REMOVE 图节点的 kb_id 属性。"""
    if not ids:
        log.info("Graph: 无待清理实体，跳过")
        return 0

    kind, conn = _graph_conn()
    if kind == "none" or conn is None:
        log.info("Graph: 未配置 Neo4j / Kùzu，跳过")
        return 0
    log.info("Graph: 使用 %s", "provider adapter" if kind == "adapter" else "原生 neo4j driver")

    def _run_cypher(query: str, params: dict):
        if kind == "adapter":
            return conn._execute(query, params)
        with conn.session() as s:  # type: ignore[union-attr]
            return s.run(query, params)

    removed = 0
    batch = 200
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        if dry:
            log.info("[DRY] Graph REMOVE kb_id: %d 个节点（id %s…）", len(chunk), chunk[0])
            removed += len(chunk)
            continue
        summary = _run_cypher(
            "UNWIND $ids AS eid MATCH (e:Entity {id: eid}) WHERE e.kb_id IS NOT NULL "
            "REMOVE e.kb_id RETURN count(e) AS n",
            {"ids": chunk},
        )
        try:
            record = next(iter(summary))
            removed += int(record["n"])
        except (StopIteration, TypeError, KeyError):
            removed += len(chunk)
    log.info("Graph: %s %d 个节点的 kb_id", "将清理" if dry else "已清理", removed)
    return removed


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

    ids: list[str] = []
    if "postgres" in targets:
        ids.extend(cleanup_postgres(args.dry_run))
    if "sqlite" in targets:
        ids.extend(cleanup_sqlite(args.dry_run))
    if "graph" in targets:
        cleanup_graph(sorted(set(ids)), args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
