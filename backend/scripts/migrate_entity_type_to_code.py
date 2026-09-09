"""
数据迁移：把 Entity.entity_type 由本体名（Ontology.name）改为本体编码（Ontology.code）。

背景：
  此前抽取与回填逻辑按本体名（中文如「机型」）写入 Neo4j Entity 节点
  和 SQLite entities 表的 entity_type 字段。本体重命名后会导致 entity_type 失配。
  本次起统一改为 ontology.code（稳定 API 名，与 Palantir API Name 同语义）。

操作：
  - SQLite entities.entity_type：按 entities.ontology_id → ontologies.code 改写
  - Neo4j Entity 节点 entity_type：MATCH 后 SET
  - Kùzu（若后端未启 Neo4j 而是 Kùzu）：同样通过 provider adapter 执行

幂等性：
  仅当当前 entity_type 等于 Ontology.name 时才改写为 Ontology.code，
  已经存 code 的不动；ontology_id 为空的实体跳过（待业务回填）。

用法：
  python scripts/migrate_entity_type_to_code.py [--dry-run] [--only sqlite|graph]

依赖：项目根目录有可用的 backend/ 配置（DATABASE_URL / Neo4j / Kùzu）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_entity_type_to_code")


def _sqlite_path() -> Path | None:
    """从环境推断 SQLite 数据库路径；找不到返回 None。"""
    env_url = os.environ.get("DATABASE_URL", "") or os.environ.get("SQLITE_PATH", "")
    if env_url:
        if env_url.startswith("sqlite:///"):
            return Path(env_url[len("sqlite:///"):])
        if env_url.endswith(".db") or env_url.endswith(".sqlite"):
            return Path(env_url)
    # 默认 backend/data/ontology.db
    default = Path(__file__).resolve().parent.parent / "data" / "ontology.db"
    return default if default.exists() else None


def migrate_sqlite(dry: bool) -> tuple[int, int]:
    """SQLite：把 entity_type 由 Ontology.name 改为 Ontology.code。"""
    path = _sqlite_path()
    if path is None or not path.exists():
        log.warning("SQLite 数据库不存在，跳过")
        return (0, 0)

    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # 关联本体表：id, name, code
        rows = cur.execute(
            """
            SELECT e.id AS eid, e.entity_type AS old_et, e.ontology_id AS oid,
                   o.name AS oname, o.code AS ocode
            FROM entities e
            LEFT JOIN ontologies o ON o.id = e.ontology_id
            WHERE e.ontology_id IS NOT NULL AND e.ontology_id <> ''
            """
        ).fetchall()
        scanned = 0
        updated = 0
        skipped = 0
        for r in rows:
            scanned += 1
            old = (r["old_et"] or "").strip()
            oname = (r["oname"] or "").strip()
            ocode = (r["ocode"] or "").strip()
            if not ocode:
                # 本体尚未配置 code，不动旧数据（避免脏改）
                skipped += 1
                continue
            if old == ocode:
                # 已是 code，无需处理
                skipped += 1
                continue
            if old != oname:
                # entity_type 既不是 name 也不是 code，说明脏数据；不动
                log.info("跳过脏数据 entity.id=%s entity_type=%r oname=%r ocode=%r",
                         r["eid"], old, oname, ocode)
                skipped += 1
                continue
            if dry:
                log.info("[DRY] would update entity.id=%s  %r -> %r",
                         r["eid"], old, ocode)
            else:
                cur.execute(
                    "UPDATE entities SET entity_type = ? WHERE id = ?",
                    (ocode, r["eid"]),
                )
            updated += 1
        if not dry:
            conn.commit()
        log.info("SQLite: scanned=%d updated=%d skipped=%d", scanned, updated, skipped)
        return (scanned, updated)
    finally:
        conn.close()


def migrate_graph(dry: bool) -> int:
    """Neo4j / Kùzu：把 Entity 节点 entity_type 由本体名改为本体 code。"""
    try:
        # 复用项目内的 provider 抽象，避免重复实现 Cypher
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from providers.graph_store import _get_adapter  # noqa: WPS433
    except Exception as e:
        log.warning("无法加载 graph_store adapter，跳过图库迁移: %s", e)
        return 0

    adapter = _get_adapter()
    provider_name = type(adapter).__name__
    log.info("图库 provider: %s", provider_name)

    # Neo4j 与 Kùzu 的查询语法相近（adapter 内部已经做方言适配）
    try:
        # 1. 拉取 ontology 信息：id -> (name, code)
        rows = adapter._execute_dict(
            """
            MATCH (o:Ontology)
            RETURN o.id AS id, o.name AS name, o.code AS code
            """,
        )
    except Exception:
        log.exception("查询 Ontology 节点失败")
        return 0

    ont_by_id: dict[str, tuple[str, str]] = {}
    for r in rows or []:
        ont_by_id[r.get("id") or ""] = (
            (r.get("name") or "").strip(),
            (r.get("code") or "").strip(),
        )

    # 2. 拉取未设置 ontology_id 的实体（entity_type 是 name 的来源）：
    #    仅在需要回填的实体范围内修改（按 ontology_id 关联本体）。
    try:
        ents = adapter._execute_dict(
            """
            MATCH (e:Entity)
            WHERE e.ontology_id IS NOT NULL AND e.ontology_id <> ''
            RETURN e.id AS eid, e.entity_type AS et, e.ontology_id AS oid
            """,
        )
    except Exception:
        log.exception("查询 Entity 节点失败")
        return 0

    updated = 0
    for r in ents or []:
        et = (r.get("et") or "").strip()
        oid = r.get("oid") or ""
        eid = r.get("eid") or ""
        if not oid or oid not in ont_by_id:
            continue
        oname, ocode = ont_by_id[oid]
        if not ocode or et == ocode or et != oname:
            continue
        if dry:
            log.info("[DRY] would update Entity.id=%s  %r -> %r", eid, et, ocode)
        else:
            try:
                adapter._execute(
                    """
                    MATCH (e:Entity {id: $eid})
                    SET e.entity_type = $et
                    """,
                    {"eid": eid, "et": ocode},
                )
                updated += 1
            except Exception:
                log.exception("更新 Entity 失败: %s", eid)

    log.info("Graph: updated=%d", updated)
    return updated


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    p.add_argument("--only", choices=["sqlite", "graph"], help="只跑某一边")
    args = p.parse_args()

    if args.only != "graph":
        migrate_sqlite(args.dry_run)
    if args.only != "sqlite":
        migrate_graph(args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())