"""MCP 注册中心 API 冒烟测试（路由层契约：CRUD / 校验 422 / 404 / test 短路）。

运行：cd backend && python -m pytest test/test_mcp_api.py -q
不连真实数据库：aiosqlite 内存库 + FastAPI dependency_overrides。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database
from models import McpServer
from routers.multi_agent import router

STDIO_OK = {"name": "fs", "transport": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"], "env": {}, "url": ""}


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(McpServer.__table__.create)

    asyncio.run(_create())

    async def _override_get_db():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix="/api")   # router 自带 /agent/multi 前缀，与 server.py 挂载方式一致
    app.dependency_overrides[database.get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    asyncio.run(engine.dispose())


def test_crud_roundtrip(client):
    # create → list
    row = client.post("/api/agent/multi/mcp/servers", json=STDIO_OK)
    assert row.status_code == 200, row.text
    body = row.json()
    assert body["name"] == "fs" and body["enabled"] == 1
    assert body["args"] == STDIO_OK["args"]
    sid = body["id"]

    listed = client.get("/api/agent/multi/mcp/servers").json()["servers"]
    assert [s["name"] for s in listed] == ["fs"]

    # update（部分字段）
    upd = client.put(f"/api/agent/multi/mcp/servers/{sid}",
                     json={"description": "文件系统", "enabled": False})
    assert upd.status_code == 200
    assert upd.json()["description"] == "文件系统" and upd.json()["enabled"] == 0
    assert upd.json()["command"] == "npx"          # 未提供字段保留

    # delete → 404 再删
    assert client.delete(f"/api/agent/multi/mcp/servers/{sid}").json() == {"ok": True}
    assert client.delete(f"/api/agent/multi/mcp/servers/{sid}").status_code == 404


def test_validation_and_conflict(client):
    # 缺 command → 422
    bad = client.post("/api/agent/multi/mcp/servers",
                      json={"name": "x", "transport": "stdio", "command": ""})
    assert bad.status_code == 422 and "command" in bad.json()["detail"]
    # 非法 name → 422
    bad = client.post("/api/agent/multi/mcp/servers", json={**STDIO_OK, "name": "非法名!"})
    assert bad.status_code == 422
    # 重名 → 422
    assert client.post("/api/agent/multi/mcp/servers", json=STDIO_OK).status_code == 200
    dup = client.post("/api/agent/multi/mcp/servers", json={**STDIO_OK, "command": "python"})
    assert dup.status_code == 422 and "已存在" in dup.json()["detail"]
    # 更新不存在 → 404
    assert client.put("/api/agent/multi/mcp/servers/nope", json=STDIO_OK).status_code == 404


def test_test_endpoint_short_circuits_on_invalid(client):
    """test 接口对非法配置不实连：直接返回 200 + ok:false + 校验消息。"""
    res = client.post("/api/agent/multi/mcp/test",
                      json={"name": "x", "transport": "ws", "command": ""})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False and "transport" in body["error"]


def test_inspect_empty(client):
    res = client.post("/api/agent/multi/mcp/inspect")
    assert res.status_code == 200 and res.json() == {"servers": []}


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
