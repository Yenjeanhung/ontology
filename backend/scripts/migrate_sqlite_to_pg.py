"""SQLite → PostgreSQL 一次性数据迁移脚本。

用途：
    将 ./data/knowsource.db（SQLite）中的全部业务表数据迁入
    backend/.env 中 DATABASE_URL 指向的 PostgreSQL 数据库。

流程：
    1. 目标库执行 init_db()（建表 + 幂等迁移）；
    2. 复制源库 _migrations 记录（防止旧迁移在新库重复执行）；
    3. 逐表批量拷贝数据（INSERT ... ON CONFLICT DO NOTHING，可重复执行）。

用法：
    cd backend
    python scripts/migrate_sqlite_to_pg.py [--sqlite ./data/knowsource.db]

注意：
    - 迁移前请先 docker compose up -d postgres；
    - 脚本只做"新增/跳过"，不删除目标库已有数据，可安全重复执行。
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

# 保证可以从 scripts/ 目录导入 backend 包内模块
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from database import engine, init_db  # noqa: E402

BATCH_SIZE = 500  # 每批插入行数


def discover_tables(sqlite_path: str) -> list[str]:
    """发现 SQLite 中所有业务表（排除内部表）。"""
    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def fetch_table(conn: sqlite3.Connection, table: str):
    """读取一张表的全部数据与列名。"""
    cur = conn.execute(f'SELECT * FROM "{table}"')  # noqa: S608（表名来自 sqlite_master，本地可信）
    cols = [d[0] for d in cur.description]
    while True:
        rows = cur.fetchmany(BATCH_SIZE)
        if not rows:
            break
        yield cols, rows


async def migrate_table(table: str, cols: list[str], rows: list[tuple]) -> int:
    """将一批行写入 PostgreSQL（冲突跳过）。返回实际插入行数。"""
    col_list = ", ".join(f'"{c}"' for c in cols)
    params = ", ".join(f":p{i}" for i in range(len(cols)))
    stmt = text(
        f'INSERT INTO "{table}" ({col_list}) VALUES ({params}) '  # noqa: S608
        "ON CONFLICT DO NOTHING"
    )
    inserted = 0
    async with engine.begin() as conn:
        for row in rows:
            bound = {f"p{i}": v for i, v in enumerate(row)}
            result = await conn.execute(stmt, bound)
            inserted += result.rowcount if result.rowcount > 0 else 0
    return inserted


async def target_tables() -> set[str]:
    """获取目标库（当前 schema）中已存在的表名集合。"""
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ))
        return {r[0] for r in rows}


async def sync_columns(
    conn, table: str, src_cols: list[tuple]
) -> list[str]:
    """把源库存在、目标库缺失的列补上（schema.sql 滞后于真实库结构时兜底）。

    src_cols 为 PRAGMA table_info 行：(
        cid, name, type, notnull, dflt_value, pk)
    新增列一律按可空处理，避免 NOT NULL 与存量行冲突。返回新增列名列表。
    """
    rows = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :t"
    ), {"t": table})
    existing = {r[0] for r in rows}
    added = []
    for _, name, ctype, _notnull, _dflt, _pk in src_cols:
        if name in existing:
            continue
        col_type = (ctype or "TEXT").upper()
        if col_type.startswith(("INT", )):
            col_type = "INTEGER"
        elif col_type.startswith("REAL") or col_type.startswith("FLOA") or col_type.startswith("DOUB"):
            col_type = "DOUBLE PRECISION"
        elif col_type.startswith("VARCHAR"):
            pass  # 保留长度定义
        else:
            col_type = "TEXT"
        await conn.execute(text(
            f'ALTER TABLE "{table}" ADD COLUMN "{name}" {col_type}'  # noqa: S608
        ))
        added.append(name)
    return added


async def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    parser.add_argument(
        "--sqlite",
        default=str(BACKEND_DIR / "data" / "knowsource.db"),
        help="SQLite 源库路径（默认 ./data/knowsource.db）",
    )
    args = parser.parse_args()

    sqlite_path = args.sqlite
    if not Path(sqlite_path).exists():
        print(f"[错误] 源 SQLite 文件不存在：{sqlite_path}")
        sys.exit(1)
    if engine.dialect.name == "sqlite":
        print("[错误] 当前 DATABASE_URL 仍是 SQLite，请先在 .env 中切换为 PostgreSQL")
        sys.exit(1)

    print(f"源库：{sqlite_path}")
    print(f"目标：{engine.url.render_as_string(hide_password=True)}")

    print("\n[1/3] 目标库建表 + 幂等迁移 ...")
    await init_db()

    existing = await target_tables()

    src = sqlite3.connect(sqlite_path)
    try:
        # 迁移期间关闭外键/唯一性触发检查（POSTGRES_USER 为超级用户）：
        # 1) 源库表间存在外键依赖，按任意顺序插入都需要；
        # 2) 源库可能存在历史遗留的孤儿行（SQLite 从未开启外键约束校验）
        print("\n[*] 临时关闭外键约束检查（session_replication_role = replica）")
        async with engine.begin() as conn:
            await conn.execute(text("SET session_replication_role = replica"))

        print("\n[2/3] 复制 _migrations 记录 ...")
        try:
            _, rows = next(iter(list(fetch_table(src, "_migrations"))))
        except StopIteration:
            rows = []
        except sqlite3.OperationalError:
            rows = []
        if rows:
            n = await migrate_table("_migrations", ["version", "applied_at"], rows)
            print(f"  _migrations：插入 {n} 条")
        else:
            print("  _migrations：源库无记录，跳过")

        print("\n[3/3] 迁移业务表数据 ...")
        tables = discover_tables(sqlite_path)
        total_rows = 0
        for table in tables:
            if table == "_migrations":
                continue
            if table not in existing:
                print(f"  {table}：跳过（目标库无此表，疑似旧备份表）")
                continue
            # 列结构同步：schema.sql 滞后于源库真实结构时，自动补齐缺失列
            src_info = src.execute(f'PRAGMA table_info("{table}")').fetchall()  # noqa: S608
            async with engine.begin() as conn:
                added = await sync_columns(conn, table, src_info)
            if added:
                print(f"  {table}：补齐缺失列 {added}")
            count = 0
            for cols, batch in fetch_table(src, table):
                count += await migrate_table(table, cols, batch)
            total_rows += count
            print(f"  {table}：{count} 行")
        print(f"\n完成！共迁移 {total_rows} 行数据。")
    finally:
        src.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
