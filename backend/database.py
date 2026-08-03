from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

SQL_DIR = Path(__file__).parent / "sql"


def _parse_migrations(sql_text: str) -> list[tuple[str, str]]:
    """Split migrations.sql into (version, sql) pairs by '-- migration_XXX' markers."""
    parts = re.split(r"(?=^-- migration_\w+)", sql_text, flags=re.MULTILINE)
    migrations = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^-- (migration_\w+)", part)
        if m:
            version = m.group(1)
            stmt = re.sub(r"-- migration_\w+\s*", "", part).strip()
            if stmt:
                migrations.append((version, stmt))
    return migrations


async def init_db():
    """Create data directories, tables, and run migrations."""
    Path("./data").mkdir(exist_ok=True)
    Path(settings.UPLOAD_DIR).mkdir(exist_ok=True)
    (Path(settings.UPLOAD_DIR) / "_assets").mkdir(parents=True, exist_ok=True)
    Path(settings.CHUNK_DIR).mkdir(exist_ok=True)
    Path(settings.KUZU_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        # 全量建表（IF NOT EXISTS，逐条执行）
        schema_file = SQL_DIR / "schema.sql"
        if schema_file.exists():
            raw = schema_file.read_text(encoding="utf-8")
            for stmt in raw.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    await conn.execute(text(stmt))

        # 迁移记录表
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "version VARCHAR PRIMARY KEY, applied_at VARCHAR)"
        ))

        # 执行增量迁移
        mig_file = SQL_DIR / "migrations.sql"
        if mig_file.exists():
            applied = set()
            rows = await conn.execute(text("SELECT version FROM _migrations"))
            for row in rows:
                applied.add(row[0])

            sql_text = mig_file.read_text(encoding="utf-8")
            for version, stmt_block in _parse_migrations(sql_text):
                if version not in applied:
                    for stmt in stmt_block.split(";"):
                        stmt = stmt.strip()
                        if not stmt:
                            continue
                        # ALTER TABLE ADD COLUMN -> skip if column already exists
                        m = re.match(
                            r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s",
                            stmt, re.IGNORECASE,
                        )
                        if m:
                            table, col = m.group(1), m.group(2)
                            cols = await conn.execute(
                                text(f"PRAGMA table_info('{table}')")
                            )
                            existing = {row[1] for row in cols}
                            if col in existing:
                                continue
                        await conn.execute(text(stmt))
                    from datetime import datetime
                    await conn.execute(
                        text("INSERT INTO _migrations (version, applied_at) VALUES (:v, :t)"),
                        {"v": version, "t": datetime.now().isoformat()},
                    )


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
