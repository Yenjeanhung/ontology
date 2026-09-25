"""外部数据源注册表服务：连接配置 CRUD / DSN 拼装 / 试连 / 类别引用解析。

设计：doc/本体管理/数据源管理/00-数据源管理设计方案.md
- 连接解析唯一入口 resolve_category_datasource：注册表引用优先，类别内联字段兜底
  （存量 migration_042 类别继续可用），NL2SQL 链与清单接口共用，杜绝口径漂移；
- 安全口径：password 明文存库（单机内网系统），接口一律不回传 password，
  dsn 掩码输出；test 失败返回 200 + ok:false（对齐 MCP 注册中心，不抛 500）。
"""
import re
import time
from urllib.parse import quote_plus

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import DataSource, OntologyCategory

DIALECTS = ("postgres", "mysql", "sqlite")


def mask_dsn(dsn: str) -> str:
    """连接串掩码：隐藏密码段（://user:***@），空串安全。"""
    return re.sub(r"://([^:/@]+):[^@/]+@", r"://\1:***@", (dsn or "").strip())


def build_dsn(dialect: str, host: str = "", port: int = 0, dbname: str = "",
              username: str = "", password: str = "") -> str:
    """结构化字段 → 连接串。sqlite：文件路径 → file:...?mode=ro（NL2SQL 只读口径一致）。"""
    dialect = (dialect or "").strip()
    if dialect == "sqlite":
        path = (dbname or "").strip()
        if not path:
            return ""
        return path if path.startswith("file:") else f"file:{path}?mode=ro"
    if not host or not dbname:
        return ""
    auth = ""
    if username:
        auth = quote_plus(username)
        if password:
            auth += ":" + quote_plus(password)
        auth += "@"
    port_part = f":{int(port)}" if port else ""
    if dialect == "mysql":
        return f"mysql+pymysql://{auth}{host}{port_part}/{dbname}"
    return f"postgresql://{auth}{host}{port_part}/{dbname}"


def effective_dsn(ds: DataSource) -> str:
    """记录生效连接串：直填 dsn 优先，否则结构化字段拼装。"""
    return (ds.dsn or "").strip() or build_dsn(
        ds.dialect, ds.host or "", ds.port or 0, ds.dbname or "",
        ds.username or "", ds.password or "")


def serialize(ds: DataSource, used_by: int | None = None) -> dict:
    out = {
        "id": ds.id, "name": ds.name, "dialect": ds.dialect,
        "host": ds.host or "", "port": ds.port or 0, "dbname": ds.dbname or "",
        "username": ds.username or "",
        "dsn": mask_dsn(effective_dsn(ds)),          # 掩码，不泄密码
        "description": ds.description or "",
        "enabled": bool(ds.enabled),
        "created_at": ds.created_at, "updated_at": ds.updated_at,
    }
    if used_by is not None:
        out["used_by"] = used_by
    return out


async def list_datasources(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(DataSource).order_by(DataSource.created_at))).scalars().all()
    counts = dict((await db.execute(
        select(OntologyCategory.datasource_id, func.count())
        .where(OntologyCategory.datasource_id != "")
        .group_by(OntologyCategory.datasource_id))).all())
    return [serialize(ds, int(counts.get(ds.id, 0))) for ds in rows]


async def get_datasource(db: AsyncSession, ds_id: str) -> DataSource | None:
    return (await db.execute(
        select(DataSource).where(DataSource.id == ds_id))).scalar_one_or_none()


def _validate(data: dict) -> str:
    """轻校验，返回错误信息（空串 = 通过）。"""
    name = (data.get("name") or "").strip()
    dialect = (data.get("dialect") or "").strip()
    if not name:
        return "名称不能为空"
    if len(name) > 60:
        return "名称不能超过 60 字符"
    if dialect not in DIALECTS:
        return f"不支持的数据库类型：{dialect}（可选 {'/'.join(DIALECTS)}）"
    if dialect == "sqlite":
        if not (data.get("dbname") or "").strip() and not (data.get("dsn") or "").strip():
            return "sqlite 需填写数据库文件路径（或直填 DSN）"
    else:
        if not (data.get("dsn") or "").strip() and \
                not ((data.get("host") or "").strip() and (data.get("dbname") or "").strip()):
            return "需填写主机与数据库名（或直填 DSN）"
    return ""


async def create_datasource(db: AsyncSession, data: dict) -> tuple[dict | None, str]:
    err = _validate(data)
    if err:
        return None, err
    name = data["name"].strip()
    dup = (await db.execute(
        select(DataSource).where(DataSource.name == name))).scalars().first()
    if dup:
        return None, f"名称已存在：{name}"
    ds = DataSource(
        name=name, dialect=data["dialect"].strip(),
        host=(data.get("host") or "").strip(),
        port=int(data.get("port") or 0),
        dbname=(data.get("dbname") or "").strip(),
        username=(data.get("username") or "").strip(),
        password=data.get("password") or "",
        dsn=(data.get("dsn") or "").strip(),
        description=(data.get("description") or "").strip(),
        enabled=1 if data.get("enabled", True) else 0,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return serialize(ds), ""


async def update_datasource(db: AsyncSession, ds_id: str, data: dict) -> tuple[dict | None, str]:
    ds = await get_datasource(db, ds_id)
    if not ds:
        return None, "数据源不存在"
    err = _validate({**serialize(ds), **data})
    if err:
        return None, err
    if "name" in data and (data["name"] or "").strip() != ds.name:
        name = data["name"].strip()
        dup = (await db.execute(
            select(DataSource).where(DataSource.name == name))).scalars().first()
        if dup:
            return None, f"名称已存在：{name}"
        ds.name = name
    for k in ("dialect", "host", "dbname", "username", "description"):
        if k in data and data[k] is not None:
            setattr(ds, k, (str(data[k])).strip())
    if "port" in data:
        ds.port = int(data.get("port") or 0)
    # password 传空串 = 保持原值不回传场景；显式 None 跳过
    if "password" in data and data["password"]:
        ds.password = data["password"]
    if "dsn" in data:
        ds.dsn = (data.get("dsn") or "").strip()
    if "enabled" in data and data["enabled"] is not None:
        ds.enabled = 1 if data["enabled"] else 0
    from datetime import datetime
    ds.updated_at = datetime.now().isoformat()
    await db.commit()
    await db.refresh(ds)
    return serialize(ds), ""


async def delete_datasource(db: AsyncSession, ds_id: str) -> tuple[bool, str]:
    ds = await get_datasource(db, ds_id)
    if not ds:
        return False, "数据源不存在"
    used = (await db.execute(
        select(func.count()).select_from(OntologyCategory)
        .where(OntologyCategory.datasource_id == ds_id))).scalar() or 0
    if int(used) > 0:
        return False, f"该数据源被 {int(used)} 个本体类别引用，请先在类别编辑中解绑"
    await db.delete(ds)
    await db.commit()
    return True, ""


async def test_connection(data: dict, db: AsyncSession | None = None) -> dict:
    """不落库试连（SELECT 1，3s 超时）。永不抛异常，失败也是 200 + ok:false。

    data.id 提供且 db 可用时以库中配置为底（表单密码留空场景），
    表单里显式给的新值（除空密码）覆盖库值。
    """
    t0 = time.perf_counter()
    out = {"ok": False, "elapsed_ms": 0, "error": ""}
    ds_id = (data.get("id") or "").strip()
    if ds_id and db is not None:
        ds = await get_datasource(db, ds_id)
        if ds is None:
            out["error"] = "数据源不存在"
            return out
        stored = {"dialect": ds.dialect, "host": ds.host or "", "port": ds.port or 0,
                  "dbname": ds.dbname or "", "username": ds.username or "",
                  "password": ds.password or "", "dsn": ds.dsn or ""}
        for k in ("dialect", "host", "dbname", "username", "dsn"):
            if (data.get(k) or "").strip():
                stored[k] = data[k].strip()
        if data.get("port"):
            stored["port"] = data["port"]
        if data.get("password"):
            stored["password"] = data["password"]
        data = stored
    dialect = (data.get("dialect") or "").strip()
    dsn = (data.get("dsn") or "").strip() or build_dsn(
        dialect, data.get("host") or "", int(data.get("port") or 0),
        data.get("dbname") or "", data.get("username") or "", data.get("password") or "")
    if not dsn:
        out["error"] = "连接信息不完整（无 DSN）"
        return out
    try:
        if dialect == "sqlite":
            path = dsn[5:].split("?")[0] if dsn.startswith("file:") else dsn
            import os
            import sqlite3
            if not os.path.exists(path):
                out["error"] = f"文件不存在：{path}"
            else:
                conn = sqlite3.connect(path, timeout=3)
                try:
                    conn.execute("SELECT 1")
                    out["ok"] = True
                finally:
                    conn.close()
        elif dialect == "mysql":
            import pymysql
            conn = pymysql.connect(
                host=data.get("host") or "", port=int(data.get("port") or 3306),
                user=data.get("username") or "", password=data.get("password") or "",
                database=data.get("dbname") or "", connect_timeout=3)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    out["ok"] = True
            finally:
                conn.close()
        else:  # postgres：与 NL2SQL 执行器同驱动 asyncpg
            import asyncpg
            raw = dsn.replace("postgresql+asyncpg://", "postgresql://")
            conn = await __import__("asyncio").wait_for(
                asyncpg.connect(raw, timeout=3), 3)
            try:
                await conn.fetch("SELECT 1")
                out["ok"] = True
            finally:
                await conn.close()
    except Exception as e:
        out["error"] = str(e)[:200]
    out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


async def resolve_category_datasource(db: AsyncSession, cat: OntologyCategory) -> tuple[str, str]:
    """类别生效连接解析（全链路唯一入口）：注册表引用优先，内联字段兜底。

    返回 (dialect, dsn)，二者任一为空 = 数据源位置不明，调用方回落老链。
    """
    ds_id = (cat.datasource_id or "").strip()
    if ds_id:
        ds = await get_datasource(db, ds_id)
        if ds is not None:
            if not ds.enabled:
                return "", ""        # 明确停用：切断（优先于内联兜底，停用 = 立即失效）
            dsn = effective_dsn(ds)
            if dsn:
                return ds.dialect, dsn
        # 记录不存在（被删但类别残留引用）→ 回落内联存量字段
    return (cat.datasource_dialect or "").strip(), (cat.datasource_dsn or "").strip()
