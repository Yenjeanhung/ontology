# -*- coding: utf-8 -*-
"""
多智能体通用 API（业务无关，面向场景注册表编程）。

- GET  /api/agent/multi/scenarios                                场景列表
- GET  /api/agent/multi/scenarios/{sid}/targets                  场景目标列表（通用目标卡）
- POST /api/agent/multi/scenarios/{sid}/targets/{tid}/run        对目标发起研判（SSE）
- POST /api/agent/multi/scenarios/{sid}/run                      自由任务研判（SSE，仅 adhoc 场景；
                                                                 body.task + body.agents 自由指定智能体组合；
                                                                 body.session_id 续聊协作会话，不传自动新建）
- GET/POST /api/agent/multi/tasks · PUT/DELETE /tasks/{tid}      任务库 CRUD（可配置任务提示词模板）
- GET  /api/agent/multi/sessions · GET /sessions/{sid}/messages  协作会话：列表 / 历史消息（回放）
- POST /api/agent/multi/sessions/{sid}/rename · DELETE /sessions/{sid}   会话重命名 / 删除
- GET  /api/agent/multi/tools                                    Function Calling 工具清单（内置 + MCP 状态）
- GET/POST /api/agent/multi/mcp/servers · PUT/DELETE /servers/{sid}   MCP 注册中心：服务器 CRUD（配置入库，热生效）
- POST /api/agent/multi/mcp/test                                 MCP 服务器连接测试（不落库，试连 + 拉工具清单）
- POST /api/agent/multi/mcp/inspect                              已启用服务器状态巡检（实连 + 工具清单）

SSE 事件契约（session / team / step_done / plan / node_start / node_done /
evidence / fact / conflict / token / error / done，`data: [DONE]` 收尾）与业务场景无关，
定义见 doc/智能体/多智能体场景.md §4。新增业务场景只需在
services/multi_agent/scenarios/ 注册适配器，本路由零改动。
"""

import asyncio
import inspect
import json
import logging
import math
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import get_current_user_id
from database import get_db
from schemas import ChatSessionRename
from services.chat_service import ChatService
from services.multi_agent import task_store
from services.multi_agent.scenarios import MultiAgentScenario, get_scenario, list_scenarios

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/multi", tags=["agent-multi"])

MULTI_SCENE = "multi"   # chat_sessions.scene 取值：多智能体协作会话（空串 = 智能体问答）

# SSE 内落库用的 async session 工厂（请求级 db 已随响应释放，落库须新开会话）；None = 全局 async_session，测试可注入
_SESSION_FACTORY = None


def _sse_evt(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 路由 mode → 过程面板可读标签（team 预告帧用）
_ROUTE_LABELS = {"data": "数据查询", "graph": "图谱核查", "kb": "知识库问答", "chat": "问答直答"}


def _route_label(route: dict) -> str:
    return _ROUTE_LABELS.get((route or {}).get("mode") or "", "通用研判")


@router.get("/scenarios")
async def multi_scenarios():
    """已注册场景列表（id / name / business / description / adhoc）。

    universal 场景额外注入 agents.custom：智能体配置页的自定义智能体
    （启用中、非内置）→ 前端组队勾选可直接选中（custom:{id} 透传后端装配）。
    """
    data = list_scenarios()
    custom = await _list_custom_roster()
    for item in data:
        if item.get("id") == "universal":
            item.setdefault("agents", {})["custom"] = custom
    return data


async def _list_custom_roster() -> list[dict]:
    """自定义智能体名册（组队勾选用）：启用中、非内置 preset。失败降级为空。"""
    from sqlalchemy import select
    from database import async_session
    from models import Agent

    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(Agent).where(Agent.is_enabled == 1, Agent.is_preset == 0)
                .order_by(Agent.name)
            )).scalars().all()
        return [{
            "id": f"custom:{r.id}",
            "name": r.name,
            "desc": (r.description or "自定义智能体（人设 + 绑定知识库）").strip()[:60],
        } for r in rows]
    except Exception:
        return []


@router.get("/tools")
async def multi_tools():
    """Function Calling 工具清单：内置平台工具规范 + MCP 外部工具接入状态。

    ToolAgent 组队时的可观测入口——不跑流水线也能确认工具链是否就绪
    （MCP 服务器连接失败时在 mcp_status 中给出 error，不抛 500）。
    """
    from config import settings
    from services.tool_registry import PlatformTools

    async with PlatformTools() as pt:
        return {
            "tools": pt.registry.describe(),
            "mcp_status": pt.mcp_status,
            "mcp_configured": bool((getattr(settings, "MCP_SERVERS", "") or "").strip()),
        }


@router.get("/scenarios/{scenario_id}/targets")
async def scenario_targets(scenario_id: str):
    """场景下的可研判目标列表（通用目标卡，含 runnable / 克制边界提示）。"""
    scenario: MultiAgentScenario | None = get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(404, f"场景 {scenario_id} 未注册")
    return {"scenario": scenario_id, "targets": await scenario.list_targets()}


@router.post("/scenarios/{scenario_id}/targets/{target_id}/run")
async def run_scenario_target(scenario_id: str, target_id: str):
    """对指定目标发起多智能体研判，SSE 流式返回过程事件与结论。"""
    scenario: MultiAgentScenario | None = get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(404, f"场景 {scenario_id} 未注册")
    try:
        engine = await scenario.build_engine(target_id)
    except KeyError:
        raise HTTPException(404, f"目标 {target_id} 在场景 {scenario_id} 中不存在")
    return _stream_engine(engine)


@router.get("/datasources")
async def list_datasources(db: AsyncSession = Depends(get_db)):
    """DataAgent 可选数据源清单 = 挂了数据源的类别（多选来源，勾选维度 = 类别 id）。

    对应关系：一个类别 = 一个库的 schema 映射，通过 datasource_id 单选引用数据源
    注册表（数据源管理页维护）；连接信息经 resolve_category_datasource 解析
    （注册表优先，内联字段兜底），dsn 掩码返回；解析不出连接的类别不出现在清单里。
    """
    from sqlalchemy import select, func, or_
    from models import OntologyCategory, Ontology
    from services.datasource_service import mask_dsn, resolve_category_datasource
    cats = (await db.execute(
        select(OntologyCategory)
        .where(or_(OntologyCategory.datasource_dialect != "",
                   OntologyCategory.datasource_id != ""))
        .where(OntologyCategory.datasource_dialect.isnot(None))
        .order_by(OntologyCategory.created_at)
    )).scalars().all()
    counts = dict((await db.execute(
        select(Ontology.category_id, func.count())
        .select_from(Ontology).group_by(Ontology.category_id)
    )).all())
    out = []
    for c in cats:
        dialect, dsn = await resolve_category_datasource(db, c)
        if not dsn:
            continue                    # 位置不明（未绑定且内联空），不进清单
        out.append({
            "id": c.id, "name": c.name,
            "dialect": dialect,
            "dsn": mask_dsn(dsn),
            "tables": int(counts.get(c.id, 0)),
            "description": (c.description or "")[:120],
        })
    return out


class TaskBody(BaseModel):
    task: str = ""
    agents: list[str] = []         # 自由组合：可选能力智能体 id 列表（空 = 场景默认组合）
    session_id: str | None = None  # 协作会话 id：传了续聊（校验属主+场景），不传自动新建
    clarified: bool = False        # True = 澄清补充后的重发，跳过澄清判定（防循环）
    deep: bool = False             # 深度模式：DeepAgents 自主规划+多轮取证（需 DEEP_AGENT_ENABLED 总闸开）
    data_sources: list[str] = []   # DataAgent 可操作数据源 = 本体类别 id 多选（空 = 自动检索全部数据源类别）


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario_task(scenario_id: str, body: TaskBody,
                            db: AsyncSession = Depends(get_db),
                            user_id: str = Depends(get_current_user_id)):
    """自由任务研判（adhoc 场景）：任务文本 + 智能体组合 → 动态建团，SSE 返回。

    会话留痕（协作历史）：user 任务在开流前落库，assistant 成果与过程摘要
    在流结束（done）后落库；SSE 首帧 session 事件回传会话锚点。
    """
    scenario: MultiAgentScenario | None = get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(404, f"场景 {scenario_id} 未注册")
    if not getattr(scenario, "adhoc", False):
        raise HTTPException(400, f"场景 {scenario_id} 不支持自由任务输入，请走目标卡接口")
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(422, "task 不能为空")

    # ── 会话：续聊校验（不存在/非本人/非协作场景一律 404，不暴露存在性）；否则新建 ──
    if body.session_id:
        session = await _get_multi_session(db, body.session_id, user_id)
        if not session:
            raise HTTPException(404, "会话不存在或已被删除")
    else:
        session = await ChatService.create_session(db, user_id=user_id,
                                                   title=task[:50], scene=MULTI_SCENE)
    await ChatService.append_message(db, session.id, "user", task)

    # 构建以工厂传入：SSE 先开流并先跑意图路由（预告帧即时可见）；工厂接收
    # on_step 进度回调（Planner 规划 / NL2Filter 每步完成即时外抛事件）
    # 深度模式（body.deep + DEEP_AGENT_ENABLED 双确认）：走 DeepAgents 第二
    # 执行路径（services/multi_agent/deep_agent.py），同契约适配零改动；
    # 总闸关闭或未安装 deepagents 时回落普通团队路径（行为与旧版一致）。
    from config import settings as _settings
    use_deep = bool(body.deep) and bool(getattr(_settings, "DEEP_AGENT_ENABLED", False))
    if use_deep:
        from services.multi_agent.deep_agent import build_deep_engine
        engine_source = lambda route, on_step=None: build_deep_engine(task)  # noqa: E731
    else:
        engine_source = lambda route, on_step=None: scenario.build_engine_from_task(
            task, agents=body.agents, route=route, on_step=on_step,
            data_sources=body.data_sources or None)                          # noqa: E731
    return _stream_engine(
        engine_source,
        session=session, task_text=task, clarified=body.clarified,
        team_label="深度智能体" if use_deep else "",
        manual_agents=list(body.agents) if (body.agents and not use_deep) else None)


# ─────────────────────── MCP 注册中心（工具服务器管理） ───────────────────────


class McpServerBody(BaseModel):
    name: str = ""
    transport: str = "stdio"      # stdio | streamable_http
    command: str = ""             # stdio：可执行命令
    args: list = []               # stdio：命令参数（字符串数组）
    env: dict = {}                # stdio：环境变量（键值对象）
    url: str = ""                 # streamable_http：MCP 端点
    description: str = ""
    enabled: bool = True


@router.get("/mcp/servers")
async def list_mcp_servers(db: AsyncSession = Depends(get_db)):
    """MCP 服务器注册表列表（含停用项；配置入库持久化）。"""
    from services.mcp_store import list_servers
    return {"servers": await list_servers(db)}


@router.post("/mcp/servers")
async def create_mcp_server(req: McpServerBody, db: AsyncSession = Depends(get_db)):
    """新增 MCP 服务器；校验失败 / 重名抛 422。保存后下次团队运行即热生效。"""
    from services.mcp_store import create_server
    try:
        return await create_server(db, req.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.put("/mcp/servers/{server_id}")
async def update_mcp_server(server_id: str, req: McpServerBody, db: AsyncSession = Depends(get_db)):
    """更新 MCP 服务器（缺省字段保留现值，整体校验）；不存在返回 404。"""
    from services.mcp_store import update_server
    try:
        row = await update_server(db, server_id, req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not row:
        raise HTTPException(404, "MCP 服务器不存在")
    return row


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str, db: AsyncSession = Depends(get_db)):
    """删除 MCP 服务器（下次团队运行不再接入其工具）。"""
    from services.mcp_store import delete_server
    if not await delete_server(db, server_id):
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True}


@router.post("/mcp/test")
async def test_mcp_server(req: McpServerBody):
    """连接测试（不落库）：试连 + initialize 握手 + 拉工具清单。

    任何失败都返回 200 + ok:false + error 文本（管理界面直接展示，不抛 500）。
    """
    from services.mcp_store import validate_server
    from services.tool_registry import probe_server
    data = req.model_dump()
    errors = validate_server(data)
    if errors:
        return {"ok": False, "tools": [], "elapsed_ms": 0, "error": "；".join(errors)}
    return await probe_server(data)


@router.post("/mcp/inspect")
async def inspect_mcp_servers(db: AsyncSession = Depends(get_db)):
    """状态巡检：对已启用的服务器逐一实连并返回各自工具清单（管理界面「巡检全部」）。"""
    from services.mcp_store import load_enabled_servers
    from services.tool_registry import probe_server
    results = []
    for server in await load_enabled_servers(db):
        probe = await probe_server(server)
        results.append({"server": server["name"], **probe})
    return {"servers": results}


# ─────────────────────── 任务库（可配置任务提示词） ───────────────────────


class TaskCreate(BaseModel):
    name: str = ""
    prompt: str = ""
    agents: list[str] = []


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    agents: Optional[list[str]] = None
    sort_order: Optional[int] = None


@router.get("/tasks")
async def list_multi_tasks(db: AsyncSession = Depends(get_db)):
    """任务库列表：可配置/可编辑的任务提示词模板（首次使用自动播种预置任务）。"""
    return await task_store.list_tasks(db)


@router.post("/tasks")
async def create_multi_task(req: TaskCreate, db: AsyncSession = Depends(get_db)):
    name = (req.name or "").strip()
    prompt = (req.prompt or "").strip()
    if not name:
        raise HTTPException(422, "任务名称不能为空")
    if not prompt:
        raise HTTPException(422, "任务提示词不能为空")
    return await task_store.create_task(db, name, prompt, req.agents)


@router.put("/tasks/{task_id}")
async def update_multi_task(task_id: str, req: TaskUpdate, db: AsyncSession = Depends(get_db)):
    data = req.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "没有需要更新的字段")
    updated = await task_store.update_task(db, task_id, data)
    if not updated:
        raise HTTPException(404, "任务不存在")
    return updated


@router.delete("/tasks/{task_id}")
async def delete_multi_task(task_id: str, db: AsyncSession = Depends(get_db)):
    ok = await task_store.delete_task(db, task_id)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


# ─────────────────────── 协作会话（协作历史 / 短期记忆载体） ───────────────────────


def _session_dict(s) -> dict:
    return {
        "id": s.id,
        "session_id": s.id,
        "title": s.title,
        "scene": s.scene,
        "has_summary": bool(s.summary),
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


async def _get_multi_session(db: AsyncSession, session_id: str, user_id: str):
    """取属于当前用户的协作会话；不存在 / 非本人 / 非协作场景一律 None（对外统一 404，不暴露存在性）。"""
    session = await ChatService.get_owned(db, session_id, user_id)
    if not session or (session.scene or "") != MULTI_SCENE:
        return None
    return session


@router.get("/sessions")
async def list_multi_sessions(limit: int = Query(default=50, ge=1, le=200),
                              db: AsyncSession = Depends(get_db),
                              user_id: str = Depends(get_current_user_id)):
    """协作会话列表：仅本人、scene=multi，按最近更新倒序。"""
    rows = await ChatService.list_sessions(db, limit=limit, user_id=user_id, scene=MULTI_SCENE)
    return [_session_dict(s) for s in rows]


@router.get("/sessions/{session_id}/messages")
async def multi_session_messages(session_id: str, db: AsyncSession = Depends(get_db),
                                 user_id: str = Depends(get_current_user_id)):
    """会话消息（历史回放）：user 任务与 assistant 成果逐条返回，meta 为过程摘要。"""
    session = await _get_multi_session(db, session_id, user_id)
    if not session:
        raise HTTPException(404, "会话不存在或已被删除")
    messages = await ChatService.get_messages(db, session_id)

    out: list[dict] = []
    for m in messages:
        meta = None
        if m.meta:
            try:
                meta = json.loads(m.meta)
            except ValueError:
                meta = None
        out.append({
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "meta": meta,
            "created_at": m.created_at,
        })
    return {"session_id": session_id, "title": session.title, "messages": out}


@router.post("/sessions/{session_id}/rename")
async def rename_multi_session(session_id: str, req: ChatSessionRename,
                               db: AsyncSession = Depends(get_db),
                               user_id: str = Depends(get_current_user_id)):
    if not await _get_multi_session(db, session_id, user_id):
        raise HTTPException(404, "会话不存在或已被删除")
    session = await ChatService.rename(db, session_id, req.title)
    if not session:
        raise HTTPException(404, "会话不存在或已被删除")
    return _session_dict(session)


@router.delete("/sessions/{session_id}")
async def delete_multi_session(session_id: str, db: AsyncSession = Depends(get_db),
                               user_id: str = Depends(get_current_user_id)):
    if not await _get_multi_session(db, session_id, user_id):
        raise HTTPException(404, "会话不存在或已被删除")
    if not await ChatService.delete(db, session_id):
        raise HTTPException(404, "会话不存在或已被删除")
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/runs")
async def list_session_runs(session_id: str, db: AsyncSession = Depends(get_db),
                            user_id: str = Depends(get_current_user_id)):
    """会话的引擎运行登记（Checkpointer P0 诊断）：状态 / 错误 / 时间线。"""
    if not await _get_multi_session(db, session_id, user_id):
        raise HTTPException(404, "会话不存在或已被删除")
    from services.multi_agent.engine import runs_list, runs_latest_pending
    return {"runs": await runs_list(session_id),
            "pending": await runs_latest_pending(session_id)}


@router.post("/sessions/{session_id}/resume")
async def resume_session_run(session_id: str, db: AsyncSession = Depends(get_db),
                             user_id: str = Depends(get_current_user_id)):
    """断点续跑该会话最近一次未完成的运行（SSE，事件契约同 /scenarios/{id}/run）。

    Checkpointer P0：服务重启 / 节点异常后，从最后 checkpoint 恢复——已完成
    节点不重复执行（不重复取证、不重复花 token），pending 节点经同一 SSE 契约
    续推，成果照常落协作会话留痕。引擎按落库 plan/materials 重建（同构）。
    """
    session = await _get_multi_session(db, session_id, user_id)
    if not session:
        raise HTTPException(404, "会话不存在或已被删除")
    from services.multi_agent.engine import _get_checkpointer, runs_latest_pending

    if await _get_checkpointer() is None:
        raise HTTPException(409, "Checkpointer 未启用（MULTI_AGENT_CHECKPOINTER=false）")
    row = await runs_latest_pending(session_id)
    if not row:
        raise HTTPException(404, "该会话没有待恢复的运行（均已正常结束）")

    scenario = get_scenario("universal")
    if scenario is None:
        raise HTTPException(500, "universal 场景未注册")
    try:
        engine = scenario.build_engine_from_replay(
            row["context"], row["team_info"], row["plan"], row["materials"])
    except Exception as exc:
        raise HTTPException(500, f"恢复重建引擎失败：{exc}")
    engine.thread_id = row["thread_id"]
    return _stream_engine(engine, session=session, task_text=row["task"],
                          clarified=True, team_label="断点恢复",
                          resume_thread=row["thread_id"])


def _stream_engine(engine_source, session=None, task_text: str = "",
                   clarified: bool = False, team_label: str = "",
                   resume_thread: str = "",
                   manual_agents: list[str] | None = None) -> StreamingResponse:
    """引擎执行 → SSE 事件流（session 首帧 / team / 过程事件 / done 收尾，公共实现）。

    engine_source 可以是引擎实例，也可以是「返回引擎的 awaitable」（自由任务
    模式下 Planner 要先调 LLM 规划，最长 PLAN_TIMEOUT 秒）。传 awaitable 时
    构建在流内进行——SSE 立即开流并先下发 planner node_start，规划期间的
    等待对前端可见（此前规划在开流前完成，前端最长 15s 只见「团队运行中」
    而无任何过程输出）。

    session 非空时启用协作会话留痕：透传事件的同时把过程摘要收集进
    turn_meta（与前端渲染状态同构），done 后落库为一轮 assistant 消息
    （meta 即回放元数据）。落库失败只记日志，不影响 SSE 交付。
    """

    def _collect(turn_meta: dict, evt: dict) -> None:
        """过程事件 → 回放元数据（字段语义与前端 MultiAgentPage 渲染状态一致）。"""
        t = evt.get("type")
        if t == "team":
            turn_meta["team"] = evt.get("team", "")
            turn_meta["members"] = evt.get("members") or []
            turn_meta["route"] = evt.get("route")   # 两级路由决策（0.6B 耗时/置信/精简组合），回放链路耗时用
        elif t == "plan":
            turn_meta["plan"] = evt.get("plan") or []
        elif t == "step_done":
            # 构建期步骤（planner 规划 / nl2filter 抽取）即时外抛的耗时记录：
            # nl2filter 口径随完整 route 落库，这里补 planner 规划耗时供回放
            if evt.get("step") == "planner":
                turn_meta["plan_ms"] = evt.get("elapsed_ms")
                turn_meta["plan_summary"] = evt.get("summary", "")
        elif t == "node_done":
            turn_meta["nodes"].append(
                {"node": evt.get("node", ""), "summary": evt.get("summary", ""),
                 "elapsed_ms": evt.get("elapsed_ms")})   # 节点真实执行耗时（engine.emit 统一计时）
        elif t == "evidence":
            cards = [
                {"grade": c.get("grade", ""), "source": c.get("source", ""),
                 "title": c.get("title", ""), "summary": c.get("summary", ""),
                 "quote": c.get("quote", ""), "stance": c.get("stance", "")}
                for c in evt.get("cards") or []
            ]
            dom = next((d for d in turn_meta["domains"] if d["domain"] == evt.get("domain")), None)
            if dom:
                dom["cards"] = cards
            else:
                turn_meta["domains"].append({"domain": evt.get("domain", ""), "cards": cards})
        elif t == "fact":
            turn_meta["domains"].append({"domain": "__facts__", "cards": [
                {"grade": f.get("grade", ""), "source": "", "title": f.get("title", ""),
                 "summary": f.get("detail", ""), "quote": "", "stance": "fact",
                 "image": f.get("image", ""),   # 图表卡（chart_result）内嵌图片回放
                 "sql": f.get("sql", ""), "dialect": f.get("dialect", "")}  # NL2SQL 完整 SQL 回放
                for f in evt.get("facts") or []
            ]})
        elif t == "conflict":
            turn_meta["conflicts"] = evt.get("conflicts") or []
            # LLM 可能输出 NaN/字符串形式的置信度，落库前归一为 null，避免回放显示 NaN%
            _conf = evt.get("confidence")
            try:
                _conf = float(_conf)
                if not math.isfinite(_conf):
                    _conf = None
            except (TypeError, ValueError):
                _conf = None
            turn_meta["verdict"] = {
                "suggest_label": evt.get("suggest_label"),
                "confidence": _conf,
                "need_human": evt.get("need_human"),
                "comment": evt.get("comment"),
            }

    async def _persist(turn_meta: dict, conclusion: str) -> None:
        """成果落库：请求级 db 已随响应释放，SSE 生成器内新开独立会话。"""
        if session is None or not conclusion:
            return
        try:
            from database import async_session

            factory = _SESSION_FACTORY or async_session
            async with factory() as db:
                await ChatService.append_message(
                    db, session.id, "assistant", conclusion, meta=turn_meta)
        except Exception:
            logger.warning("协作会话成果落库失败: session=%s", session.id, exc_info=True)

    async def _stream():
        turn_meta: dict = {
            "task": task_text, "team": "", "members": [], "plan": [],
            "nodes": [], "domains": [], "conflicts": [], "verdict": None,
            "route": None, "elapsed_ms": 0, "plan_ms": None, "plan_summary": "",
        }
        # 首事件：会话锚点（新建会话时前端据此记录 session_id 并刷新会话列表）
        if session is not None:
            yield _sse_evt({"type": "session", "session_id": session.id, "title": session.title})

        engine = engine_source
        if callable(engine_source) and not inspect.isawaitable(engine_source):
            # 工厂形态（自由任务）：意图路由前置到开流之后——0.6B 决策一出结果
            # 立即发 team 预告帧（含 route），随后 LLM 规划（可达十余秒）期间前端
            # 可见团队与路由，不再长时间只见一行 planner 干等（过程不可见根因）。
            # route 传给工厂后 build 内不再重复路由（route is None 才自跑）。
            from services.multi_agent.router_service import routing_decision
            _rt0 = time.perf_counter()
            route = await routing_decision(task_text)
            # 0.6B 真实耗时在此补记：build 侧因 route 已就绪会跳过计时分支
            route["elapsed_ms"] = int((time.perf_counter() - _rt0) * 1000)
            # ── 澄清判定：任务缺关键信息先问清再开工（clarified=True 的补充重发跳过，
            #    防循环；chat 直答是闲聊/明确指令，无澄清价值，同样跳过） ──
            if not clarified and not route.get("chat_direct"):
                from services.multi_agent.router_service import clarify_check
                _clar = await clarify_check(task_text)
                if _clar:
                    yield _sse_evt({"type": "clarify", **_clar})
                    yield _sse_evt({"type": "done", "conclusion": "", "elapsed_ms": 0})
                    yield "data: [DONE]\n\n"
                    return
            yield _sse_evt({"type": "node_start", "node": "planner",
                            "role": "planner", "goal": "任务规划中（LLM 分解子任务）…"})
            manual = bool(manual_agents)
            if manual:
                # 预告帧先行于 build（manual 标记原本 build 内才打上）：
                # 手动组队时组合由用户指定，路由仅辅助 NL2Filter/改写门控，
                # 预告帧与 route 明细不得显示路由精简组合（曾误导用户以为
                # 勾选被路由覆盖——实际 caps 一直是手动清单，v6.4 修复；
                # 前端 routeSteps 按 rt.manual 显示「手动组队，组合由用户指定」）
                route["manual"] = True
            yield _sse_evt({"type": "team",
                            "team": (f"{team_label or '通用智能体团队'}（手动组队 · 子任务规划中…）"
                                     if manual else
                                     f"{team_label or '通用智能体团队'}（{_route_label(route)} · 子任务规划中…）"),
                            "members": [], "route": route})
            # 构建放后台任务并发执行：build 内每步（LLM 规划 / NL2Filter 抽取）
            # 一完成即经 on_step 把事件 put 进桥接队列，本生成器取出即刻 yield——
            # 构建期间每一步的结束与耗时实时可见，不再整体静默到 build 返回。
            build_q: asyncio.Queue = asyncio.Queue()

            async def _build():
                try:
                    eng = await engine_source(
                        route, on_step=lambda e: build_q.put_nowait(e))
                    await build_q.put(eng)
                except Exception as exc:
                    await build_q.put(exc)

            build_task = asyncio.create_task(_build())
            engine = None
            try:
                while True:
                    item = await build_q.get()
                    if isinstance(item, BaseException):
                        raise item
                    if not isinstance(item, dict):   # 引擎对象 = 构建完成
                        engine = item
                        break
                    yield _sse_evt(item)             # step_done 进度事件即时下发
            except Exception as exc:  # 构建失败：走事件通道报告，不让前端干等
                yield _sse_evt({"type": "error", "content": f"团队构建失败：{exc}"})
                yield _sse_evt({"type": "done", "conclusion": "", "elapsed_ms": 0})
                yield "data: [DONE]\n\n"
                return
            finally:
                if engine is None and not build_task.done():
                    build_task.cancel()
        elif inspect.isawaitable(engine):
            yield _sse_evt({"type": "node_start", "node": "planner",
                            "role": "planner", "goal": "任务规划中（LLM 分解子任务）…"})
            try:
                engine = await engine
            except Exception as exc:  # 构建失败：走事件通道报告，不让前端干等
                yield _sse_evt({"type": "error", "content": f"团队构建失败：{exc}"})
                yield _sse_evt({"type": "done", "conclusion": "", "elapsed_ms": 0})
                yield "data: [DONE]\n\n"
                return

        # Checkpointer thread（P0 断点恢复）：MultiAgentEngine 专有属性
        # （DeepAgentRunner 无 thread_id，hasattr 天然跳过）。新运行生成
        # "会话ID::随机token"（一轮一条）；断点恢复流沿用原 thread 不换号。
        if hasattr(engine, "thread_id"):
            engine.thread_id = resume_thread or (
                f"{(session.id if session else '')}::{uuid.uuid4().hex[:8]}")

        await engine.q.put({"type": "team", **engine.team_info()})

        final: dict = {}

        async def _run():
            try:
                # resume_thread 非空 = 断点恢复：从最后 checkpoint 续跑 pending 节点
                final.update(await (engine.resume() if resume_thread else engine.run()))
            except Exception as exc:  # 流水线级兜底：不让 SSE 断流
                await engine.q.put({"type": "error", "content": f"多智能体流水线异常：{exc}"})
            finally:
                await engine.q.put(None)

        task = asyncio.create_task(_run())
        try:
            while True:
                evt = await engine.q.get()
                if evt is None:
                    break
                _collect(turn_meta, evt)
                yield _sse_evt(evt)
        finally:
            if not task.done():
                task.cancel()

        conclusion = final.get("conclusion", "")
        turn_meta["elapsed_ms"] = final.get("elapsed_ms", 0)
        yield _sse_evt({"type": "done", "conclusion": conclusion,
                        "elapsed_ms": turn_meta["elapsed_ms"]})
        await _persist(turn_meta, conclusion)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
