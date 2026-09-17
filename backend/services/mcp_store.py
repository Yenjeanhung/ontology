# -*- coding: utf-8 -*-
"""MCP 工具服务器注册中心（配置入库持久化 + CRUD + 校验）。

mcp_servers 表是外部工具服务器的唯一事实源（注册中心可视化管理）：
- PlatformTools 每次构建时热加载 enabled=1 的服务器清单（services/tool_registry.py），
  在管理界面增删改后下次团队运行即生效，无需重启进程；
- 库表为空时回退读取环境变量 MCP_SERVERS（向后兼容既有部署，见 tool_registry.load_mcp_servers）；
- name 会拼进工具前缀 mcp_<name>_，限 [a-zA-Z0-9_-] 且全局唯一（唯一索引兜底）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import McpServer

VALID_TRANSPORTS = ("stdio", "streamable_http")
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")

_FIELDS = ("id", "name", "transport", "command", "url", "description",
           "enabled", "sort_order", "created_at", "updated_at")


def _load_json(raw: str, fallback):
    try:
        obj = json.loads(raw or "")
        return obj if isinstance(obj, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def _dump(row: McpServer) -> dict:
    data = {k: getattr(row, k) for k in _FIELDS}
    data["args"] = _load_json(row.args, [])
    data["env"] = _load_json(row.env, {})
    return data


def _runtime(row_like: dict) -> dict:
    """存储形态 → tool_registry 运行形态（args/env 已是解析后的 list/dict）。"""
    return {
        "name": row_like["name"],
        "transport": row_like["transport"],
        "command": row_like.get("command") or "",
        "args": row_like.get("args") or [],
        "env": row_like.get("env") or {},
        "url": row_like.get("url") or "",
    }


def validate_server(data: dict) -> list[str]:
    """配置校验，返回错误消息列表（空 = 通过）。create/update/test 共用。"""
    errors: list[str] = []
    name = str(data.get("name") or "").strip()
    if not name:
        errors.append("name 不能为空")
    elif not _NAME_RE.match(name):
        errors.append("name 仅限字母/数字/下划线/中划线（1~32 位），将用作工具前缀 mcp_<name>_")
    transport = str(data.get("transport") or "stdio").strip().lower()
    if transport not in VALID_TRANSPORTS:
        errors.append(f"transport 仅支持 {' / '.join(VALID_TRANSPORTS)}")
    if transport == "stdio" and not str(data.get("command") or "").strip():
        errors.append("stdio 传输必须填写 command（可执行命令）")
    if transport in ("streamable_http",) and not str(data.get("url") or "").strip():
        errors.append("streamable_http 传输必须填写 url")
    url = str(data.get("url") or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        errors.append("url 必须以 http:// 或 https:// 开头")
    args, env = data.get("args"), data.get("env")
    if args is not None and not isinstance(args, list):
        errors.append("args 必须是字符串数组")
    if env is not None and not isinstance(env, dict):
        errors.append("env 必须是键值对象")
    return errors


async def list_servers(db: AsyncSession) -> list[dict]:
    """注册表全量列表（含停用项），管理界面展示用。"""
    rows = (await db.execute(
        select(McpServer).order_by(McpServer.sort_order, McpServer.created_at)
    )).scalars().all()
    return [_dump(r) for r in rows]


async def has_any_server(db: AsyncSession) -> bool:
    """库里是否配置过服务器（含停用）——热加载时决定「库为准」还是「env 兜底」。"""
    return (await db.execute(select(McpServer.id).limit(1))).first() is not None


async def load_enabled_servers(db: AsyncSession) -> list[dict]:
    """启用的服务器清单（运行形态），tool_registry 热加载入口。"""
    rows = (await db.execute(
        select(McpServer)
        .where(McpServer.enabled == 1)
        .order_by(McpServer.sort_order, McpServer.created_at)
    )).scalars().all()
    return [_runtime(_dump(r)) for r in rows]


async def _name_taken(db: AsyncSession, name: str, exclude_id: str = "") -> bool:
    stmt = select(McpServer.id).where(McpServer.name == name).limit(1)
    if exclude_id:
        stmt = stmt.where(McpServer.id != exclude_id)
    return (await db.execute(stmt)).first() is not None


async def create_server(db: AsyncSession, data: dict) -> dict:
    """新增服务器；校验失败/重名抛 ValueError（路由层转 422）。"""
    data = dict(data)
    data["name"] = str(data.get("name") or "").strip()
    errors = validate_server(data)
    if errors:
        raise ValueError("；".join(errors))
    if await _name_taken(db, data["name"]):
        raise ValueError(f"服务器名 {data['name']} 已存在")
    row = McpServer(
        name=data["name"],
        transport=str(data.get("transport") or "stdio").strip().lower(),
        command=str(data.get("command") or ""),
        args=json.dumps(data.get("args") or [], ensure_ascii=False),
        env=json.dumps(data.get("env") or {}, ensure_ascii=False),
        url=str(data.get("url") or ""),
        description=str(data.get("description") or ""),
        enabled=1 if data.get("enabled", True) else 0,
    )
    db.add(row)
    await db.commit()
    return _dump(row)


async def update_server(db: AsyncSession, server_id: str, data: dict) -> Optional[dict]:
    """部分更新（缺省字段保留现值），整体校验后落库；不存在返回 None。"""
    row = await db.get(McpServer, server_id)
    if not row:
        return None
    merged = {
        "name": str(data.get("name", row.name) or "").strip(),
        "transport": str(data.get("transport", row.transport) or "").strip().lower(),
        "command": str(data.get("command", row.command) or ""),
        "args": data.get("args", _load_json(row.args, [])),
        "env": data.get("env", _load_json(row.env, {})),
        "url": str(data.get("url", row.url) or ""),
        "description": str(data.get("description", row.description) or ""),
        "enabled": bool(data.get("enabled", row.enabled == 1)),
    }
    errors = validate_server(merged)
    if errors:
        raise ValueError("；".join(errors))
    if await _name_taken(db, merged["name"], exclude_id=server_id):
        raise ValueError(f"服务器名 {merged['name']} 已存在")
    row.name = merged["name"]
    row.transport = merged["transport"]
    row.command = merged["command"]
    row.args = json.dumps(merged["args"], ensure_ascii=False)
    row.env = json.dumps(merged["env"], ensure_ascii=False)
    row.url = merged["url"]
    row.description = merged["description"]
    row.enabled = 1 if merged["enabled"] else 0
    row.updated_at = datetime.now().isoformat()
    await db.commit()
    return _dump(row)


async def delete_server(db: AsyncSession, server_id: str) -> bool:
    row = await db.get(McpServer, server_id)
    if not row:
        return False
    await db.delete(row)
    await db.commit()
    return True
