"""MCP 注册中心单元测试（mcp_store CRUD + 热加载回退 + probe_server + PlatformTools 接入）。

运行：cd backend && python -m pytest test/test_mcp_registry.py -q
不依赖真实 MCP 服务器 / LLM / 主库：aiosqlite 内存库 + 脚本化假连接。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from models import McpServer
from services import mcp_store, tool_registry
from services.tool_registry import PlatformTools, Tool, load_mcp_servers, probe_server

STDIO_OK = {"name": "fs", "transport": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/data"],
            "env": {}, "url": "", "description": "文件系统", "enabled": True}
HTTP_OK = {"name": "corp", "transport": "streamable_http",
           "url": "http://10.0.0.8:9001/mcp", "description": "企业工具", "enabled": True}


@pytest.fixture()
def db_factory():
    """aiosqlite 内存库（仅 mcp_servers 表），返回 session 工厂。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(McpServer.__table__.create)

    asyncio.run(_create())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


# ───────────────────────── validate_server ─────────────────────────

def test_validate_ok_stdio_and_http():
    assert mcp_store.validate_server(STDIO_OK) == []
    assert mcp_store.validate_server(HTTP_OK) == []


def test_validate_name_rules():
    assert any("name" in e for e in mcp_store.validate_server({**STDIO_OK, "name": ""}))
    assert any("name" in e for e in mcp_store.validate_server({**STDIO_OK, "name": "中文名"}))
    assert mcp_store.validate_server({**STDIO_OK, "name": "a" * 33}) != []


def test_validate_transport_and_required_fields():
    assert any("transport" in e for e in mcp_store.validate_server({**STDIO_OK, "transport": "ws"}))
    assert any("command" in e for e in mcp_store.validate_server({**STDIO_OK, "command": ""}))
    assert any("url" in e for e in mcp_store.validate_server({**HTTP_OK, "url": ""}))
    assert any("url" in e for e in mcp_store.validate_server({**HTTP_OK, "url": "ftp://x"}))


def test_validate_args_env_types():
    assert any("args" in e for e in mcp_store.validate_server({**STDIO_OK, "args": "not-a-list"}))
    assert any("env" in e for e in mcp_store.validate_server({**STDIO_OK, "env": [1]}))


# ───────────────────────── CRUD ─────────────────────────

def test_create_and_list_roundtrip(db_factory):
    async def _flow():
        async with db_factory() as db:
            row = await mcp_store.create_server(db, STDIO_OK)
            await mcp_store.create_server(db, HTTP_OK)
            servers = await mcp_store.list_servers(db)
        return row, servers

    row, servers = _run(_flow())
    assert row["id"] and row["enabled"] == 1
    assert {s["name"] for s in servers} == {"fs", "corp"}
    fs = next(s for s in servers if s["name"] == "fs")
    assert fs["args"] == STDIO_OK["args"]          # JSON 列解析还原为 list
    assert isinstance(fs["env"], dict)


def test_create_duplicate_name_rejected(db_factory):
    async def _flow():
        async with db_factory() as db:
            await mcp_store.create_server(db, STDIO_OK)
            with pytest.raises(ValueError, match="已存在"):
                await mcp_store.create_server(db, {**STDIO_OK, "command": "python"})

    _run(_flow())


def test_update_partial_and_validate(db_factory):
    async def _flow():
        async with db_factory() as db:
            row = await mcp_store.create_server(db, STDIO_OK)
            # 部分更新：只改描述，其余保留
            updated = await mcp_store.update_server(db, row["id"], {"description": "新描述"})
            # 非法更新：整体校验（transport 变 http 但无 url）→ ValueError
            with pytest.raises(ValueError, match="url"):
                await mcp_store.update_server(db, row["id"], {"transport": "streamable_http"})
            assert not await mcp_store.update_server(db, "nope", {"name": "x"})
            return updated

    updated = _run(_flow())
    assert updated["description"] == "新描述"
    assert updated["command"] == "npx"               # 未提供的字段保留现值


def test_delete_server(db_factory):
    async def _flow():
        async with db_factory() as db:
            row = await mcp_store.create_server(db, STDIO_OK)
            assert await mcp_store.delete_server(db, row["id"])
            assert not await mcp_store.delete_server(db, row["id"])
            return await mcp_store.list_servers(db)

    assert _run(_flow()) == []


# ───────────────────────── 热加载（load_mcp_servers） ─────────────────────────

def test_load_db_priority_and_enabled_filter(db_factory, monkeypatch):
    """库为准：有记录时读库且只取 enabled=1，env 配置被忽略。"""
    monkeypatch.setattr("database.async_session", db_factory)
    monkeypatch.setattr(settings, "MCP_SERVERS",
                        json.dumps([{"name": "env_only", "transport": "stdio", "command": "echo"}]))

    async def _flow():
        async with db_factory() as db:
            await mcp_store.create_server(db, STDIO_OK)
            await mcp_store.create_server(db, {**HTTP_OK, "enabled": False})

    _run(_flow())
    servers = _run(load_mcp_servers())
    assert [s["name"] for s in servers] == ["fs"]
    assert servers[0]["args"] == STDIO_OK["args"]     # 运行形态


def test_load_env_fallback_when_db_empty(db_factory, monkeypatch):
    """库空 → 环境变量兜底（向后兼容既有部署）。"""
    monkeypatch.setattr("database.async_session", db_factory)
    env_servers = [{"name": "env_only", "transport": "stdio", "command": "echo"}]
    monkeypatch.setattr(settings, "MCP_SERVERS", json.dumps(env_servers))
    assert _run(load_mcp_servers()) == env_servers


def test_load_db_cleared_means_off(monkeypatch):
    """界面删光服务器 = 明确不接入：env 配置不复活（库有行即库为准，enabled 过滤后为空）。"""
    async def _fake_has(db):
        return True

    async def _fake_enabled(db):
        return []

    monkeypatch.setattr("services.mcp_store.has_any_server", _fake_has)
    monkeypatch.setattr("services.mcp_store.load_enabled_servers", _fake_enabled)
    monkeypatch.setattr(settings, "MCP_SERVERS", json.dumps([{"name": "x", "command": "echo"}]))
    assert _run(load_mcp_servers()) == []


def test_load_db_unavailable_falls_back_to_env(monkeypatch):
    """库不可用（脚本/测试场景）→ env 兜底，不抛异常。"""
    async def _boom():
        raise RuntimeError("db down")

    class _BrokenCtx:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("database.async_session", lambda: _BrokenCtx())
    monkeypatch.setattr(settings, "MCP_SERVERS",
                        json.dumps([{"name": "env_only", "transport": "stdio", "command": "echo"}]))
    assert [s["name"] for s in _run(load_mcp_servers())] == ["env_only"]


# ───────────────────────── probe_server / PlatformTools ─────────────────────────

class FakeConn:
    """脚本化假 MCP 连接：name=bad 的服务器连接失败，其余返回一个 echo 工具。"""
    instances: list = []

    def __init__(self, server):
        self.server = server
        self.closed = False
        FakeConn.instances.append(self)

    async def connect(self):
        if self.server.get("name") == "bad":
            raise RuntimeError("connection refused")

    async def list_tools(self):
        async def _echo(**kwargs):
            return {"echo": kwargs}
        return [Tool(name=f"mcp_{self.server['name']}_echo", description="回声工具",
                     parameters={"type": "object", "properties": {}},
                     handler=_echo, source=f"mcp:{self.server['name']}")]

    async def close(self):
        self.closed = True


@pytest.fixture()
def fake_conn(monkeypatch):
    FakeConn.instances = []
    monkeypatch.setattr(tool_registry, "McpConnection", FakeConn)
    return FakeConn


def test_probe_server_ok(fake_conn):
    probe = _run(probe_server({"name": "fs", "transport": "stdio", "command": "npx"}))
    assert probe["ok"] and probe["error"] == ""
    assert probe["tools"] == [{"name": "mcp_fs_echo", "description": "回声工具"}]
    assert probe["elapsed_ms"] >= 0
    assert fake_conn.instances[0].closed             # 探测后必须关闭连接


def test_probe_server_fail_wrapped(fake_conn):
    probe = _run(probe_server({"name": "bad", "transport": "stdio", "command": "npx"}))
    assert not probe["ok"]
    assert "connection refused" in probe["error"]
    assert probe["tools"] == []


def test_platform_tools_registers_mcp_and_degrades(fake_conn):
    """远程工具与内置工具同表注册；单台失败降级记 mcp_status 不阻断。"""
    async def _fake_load():
        return [{"name": "good", "transport": "stdio", "command": "npx"},
                {"name": "bad", "transport": "stdio", "command": "npx"}]

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tool_registry, "load_mcp_servers", _fake_load)
    try:
        async def _flow():
            async with PlatformTools() as pt:
                return pt.registry.names(), pt.mcp_status

        names, status = _run(_flow())
    finally:
        monkey.undo()

    assert "kb_search" in names and "graph_search" in names and "data_query" in names
    assert "mcp_good_echo" in names                  # 远程工具进同一注册表
    assert "mcp_bad_echo" not in names
    by_name = {s["server"]: s for s in status}
    assert by_name["good"]["ok"] and by_name["good"]["tools"] == 1
    assert not by_name["bad"]["ok"] and "connection refused" in by_name["bad"]["error"]
    assert all(c.closed for c in fake_conn.instances)   # 上下文退出统一关闭


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
