from __future__ import annotations

import asyncio
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

SQL_DIR = Path(__file__).parent / "sql"

_DB_TROUBLESHOOT = (
    "排查步骤：\n"
    "  1) 启动数据库：在本仓库根目录执行 `docker-compose up -d postgres`\n"
    "  2) 核对 backend/.env 的 DATABASE_URL（主机/端口/库名/账号密码）\n"
    "  3) 确认容器已在运行：docker ps | findstr ontology-postgres\n"
    "  4) 若用本地 PostgreSQL，请确认服务已启动且端口未被防火墙拦截"
)


class DatabaseUnavailableError(RuntimeError):
    """数据库不可达（未启动 / 地址错 / 认证失败），用于向用户输出友好提示。"""

    def __init__(self, target: str, reason: str):
        self.target = target
        self.reason = reason
        super().__init__(f"数据库不可达：{target}（{reason}）")

    def friendly_message(self) -> str:
        return (
            "============================================================\n"
            "数据库未启动或无法连接，服务已中止启动。\n"
            f"  目标：{self.target}\n"
            f"  原因：{self.reason}\n"
            f"{_DB_TROUBLESHOOT}\n"
            "============================================================"
        )


def masked_database_url() -> str:
    """连接串脱敏：隐藏密码，便于安全打印。"""
    url = str(settings.DATABASE_URL)
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)


def _summarize_error(exc: BaseException) -> str:
    msg = str(exc).strip()
    first = msg.splitlines()[0] if msg else ""
    if not first:
        # 部分 OSError（如 ConnectionRefusedError）str 为空，用 errno/winerror 兜底
        errno = getattr(exc, "errno", None)
        first = f"errno={errno}" if errno is not None else exc.__class__.__name__
    return f"{exc.__class__.__name__}: {first[:200]}"


async def check_database_connectivity(timeout: float = 5.0) -> None:
    """启动前探活：连不上或超时即抛 DatabaseUnavailableError，不暴露原始堆栈。"""
    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except DatabaseUnavailableError:
        raise
    except TimeoutError as exc:  # asyncio.timeout 在 3.11+ 抛 TimeoutError
        raise DatabaseUnavailableError(
            masked_database_url(), f"连接超时（超过 {timeout:g} 秒未响应）"
        ) from exc
    except Exception as exc:  # ConnectionRefusedError / OSError / asyncpg 认证错误等
        raise DatabaseUnavailableError(masked_database_url(), _summarize_error(exc)) from exc


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
            # 去除版本标记行（整行，不管后面有无 : 描述），再过滤注释行
            code_lines = [ln for ln in part.splitlines()
                         if not re.match(r"^--\s*migration_\w+", ln)
                         and not ln.strip().startswith("--")]
            stmt = "\n".join(code_lines).strip()
            if stmt:
                migrations.append((version, stmt))
    return migrations


async def init_db():
    """Create data directories, tables, and run migrations."""
    Path("./data").mkdir(exist_ok=True)
    Path(settings.UPLOAD_DIR).mkdir(exist_ok=True)
    (Path(settings.UPLOAD_DIR) / "_assets").mkdir(parents=True, exist_ok=True)
    Path(settings.CHUNK_DIR).mkdir(exist_ok=True)
    # 仅在使用嵌入式 Kùzu 后端时才创建其数据目录（Neo4j 由 docker-compose 管理）
    if settings.GRAPH_STORE_PROVIDER == "kuzu":
        Path(settings.KUZU_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    # 先探活：数据库没启动时给出友好提示，而不是抛原始堆栈
    await check_database_connectivity()

    async with engine.begin() as conn:
        # 全量建表（IF NOT EXISTS，逐条执行）
        # 注意：schema.sql 中部分 CREATE TABLE 前有 `--` 注释行，
        # 简单的 `stmt.startswith("--")` 会把"注释 + CREATE"整段跳过，
        # 导致全新数据库上漏建表。这里先剔除注释行再判断。
        schema_file = SQL_DIR / "schema.sql"
        if schema_file.exists():
            raw = schema_file.read_text(encoding="utf-8")
            for stmt in raw.split(";"):
                # 移除整行注释，保留语句本身
                code_lines = [
                    ln for ln in stmt.splitlines()
                    if not ln.strip().startswith("--")
                ]
                clean = "\n".join(code_lines).strip()
                if clean:
                    await conn.execute(text(clean))

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
                            # 方言兼容：SQLite 用 PRAGMA，PostgreSQL 用 information_schema
                            if engine.dialect.name == "sqlite":
                                cols = await conn.execute(
                                    text(f"PRAGMA table_info('{table}')")
                                )
                                existing = {row[1] for row in cols}
                            else:
                                cols = await conn.execute(
                                    text("SELECT column_name FROM information_schema.columns "
                                         "WHERE table_schema = current_schema() AND table_name = :t"),
                                    {"t": table},
                                )
                                existing = {row[0] for row in cols}
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
