"""多智能体协作会话（协作历史 / 短期记忆）API 测试。

覆盖：
- 会话列表场景过滤（仅本人、仅 scene=multi）
- 历史消息接口（meta JSON 解析、轮次素材）
- 重命名 / 删除；属主隔离与场景隔离（非本人 404、普通问答会话 404）
- run 接口会话分支：空任务 422 · 未知会话 404 · 场景不符 404 ·
  正常运行自动建会话（SSE session 事件 + user 消息落库 + 会话标题取任务）

运行：cd backend && python -m pytest test/test_multi_agent_session.py -q
不连真实数据库：aiosqlite 内存库 + FastAPI dependency_overrides；不做真实 LLM 调用
（自由任务场景的引擎构建在 SSE 流内失败，走 error 事件兜底，不影响会话分支断言）。
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
from core.deps import get_current_user_id
from models import ChatMessage, ChatSession
from routers.multi_agent import MULTI_SCENE, router
from services.chat_service import ChatService

UID = "u-test"
OTHER = "u-other"

TASK = "梳理知识图谱构建的技术路线与风险点"


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(ChatSession.__table__.create)
            await conn.run_sync(ChatMessage.__table__.create)

    asyncio.run(_create())

    async def _override_get_db():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix="/api")   # 与 server.py 挂载方式一致（/api/agent/multi/*）
    app.dependency_overrides[database.get_db] = _override_get_db
    app.dependency_overrides[get_current_user_id] = lambda: UID
    # SSE 内落库（_persist）不走请求级 db，注入同库工厂避免连真实数据库
    import routers.multi_agent as ra
    ra._SESSION_FACTORY = factory
    with TestClient(app) as c:
        yield c, factory
    ra._SESSION_FACTORY = None
    asyncio.run(engine.dispose())


def _seed(factory, *, user_id: str, scene: str, title: str, with_msg: bool = False):
    async def _run():
        async with factory() as db:
            s = await ChatService.create_session(db, user_id=user_id, title=title, scene=scene)
            if with_msg:
                await ChatService.append_message(db, s.id, "user", "第一轮任务")
                await ChatService.append_message(
                    db, s.id, "assistant", "第一轮成果结论",
                    meta={"task": "第一轮任务", "team": "t", "members": [],
                          "plan": [], "nodes": [], "domains": [],
                          "conflicts": [], "verdict": None, "elapsed_ms": 12},
                )
            return s.id
    return asyncio.run(_run())


def test_sessions_list_filters_owner_and_scene(client):
    c, factory = client
    _seed(factory, user_id=UID, scene=MULTI_SCENE, title="图谱路线协作")
    _seed(factory, user_id=OTHER, scene=MULTI_SCENE, title="别人的协作")
    _seed(factory, user_id=UID, scene="", title="普通问答会话")

    rows = c.get("/api/agent/multi/sessions").json()
    # 仅本人 + 仅协作场景
    assert [r["title"] for r in rows] == ["图谱路线协作"]
    assert rows[0]["scene"] == MULTI_SCENE


def test_messages_rename_delete_and_isolation(client):
    c, factory = client
    mine = _seed(factory, user_id=UID, scene=MULTI_SCENE, title="我的协作", with_msg=True)
    plain = _seed(factory, user_id=UID, scene="", title="普通会话")
    others = _seed(factory, user_id=OTHER, scene=MULTI_SCENE, title="他人协作")

    # 历史消息：meta 解析回 dict
    detail = c.get(f"/api/agent/multi/sessions/{mine}/messages")
    assert detail.status_code == 200
    body = detail.json()
    assert body["title"] == "我的协作"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["meta"]["elapsed_ms"] == 12

    # 场景隔离：普通问答会话走 multi 接口 → 404
    assert c.get(f"/api/agent/multi/sessions/{plain}/messages").status_code == 404
    # 属主隔离：他人会话 → 404（不暴露存在性）
    assert c.get(f"/api/agent/multi/sessions/{others}/messages").status_code == 404

    # 重命名
    renamed = c.post(f"/api/agent/multi/sessions/{mine}/rename", json={"title": "改名后"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "改名后"
    assert c.post(f"/api/agent/multi/sessions/{others}/rename",
                  json={"title": "x"}).status_code == 404

    # 删除 → 再访问 404
    assert c.delete(f"/api/agent/multi/sessions/{mine}").status_code == 200
    assert c.get(f"/api/agent/multi/sessions/{mine}/messages").status_code == 404
    assert c.delete(f"/api/agent/multi/sessions/{mine}").status_code == 404


def test_run_auto_creates_session_and_persists_user_turn(client):
    c, factory = client
    # 流式请求：无需真实 LLM——引擎构建失败走 error 事件兜底，但会话分支照常生效
    with c.stream("POST", "/api/agent/multi/scenarios/universal/run",
                  json={"task": TASK}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert '"type": "session"' in body or '"type":"session"' in body

    rows = c.get("/api/agent/multi/sessions").json()
    assert len(rows) == 1
    assert rows[0]["title"] == TASK[:50]
    detail = c.get(f"/api/agent/multi/sessions/{rows[0]['id']}/messages").json()
    roles = [m["role"] for m in detail["messages"]]
    # user 任务必定留痕；assistant 成果是否存在取决于环境（无 LLM 时引擎构建失败
    # 不落成果，有 LLM 时完整留痕并携带过程摘要 meta）
    assert roles[0] == "user" and detail["messages"][0]["content"] == TASK
    assert roles[1:] in ([], ["assistant"])
    if roles[1:]:
        assert detail["messages"][1]["meta"] is not None


def test_run_session_validation_branches(client):
    c, factory = client
    mine = _seed(factory, user_id=UID, scene=MULTI_SCENE, title="续聊目标")
    plain = _seed(factory, user_id=UID, scene="", title="普通会话")

    # 空任务 → 422（会话校验之前）
    assert c.post("/api/agent/multi/scenarios/universal/run",
                  json={"task": "", "session_id": mine}).status_code == 422
    # 未知会话 → 404
    assert c.post("/api/agent/multi/scenarios/universal/run",
                  json={"task": TASK, "session_id": "nope"}).status_code == 404
    # 场景隔离：普通问答会话不可作为协作会话续聊 → 404
    assert c.post("/api/agent/multi/scenarios/universal/run",
                  json={"task": TASK, "session_id": plain}).status_code == 404

    # 非自由任务场景 → 400（存在非 adhoc 场景时）
    from routers.multi_agent import list_scenarios

    fixed = next((s["id"] for s in list_scenarios() if not s.get("adhoc")), None)
    if fixed:
        assert c.post(f"/api/agent/multi/scenarios/{fixed}/run",
                      json={"task": TASK}).status_code == 400
