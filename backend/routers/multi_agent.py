# -*- coding: utf-8 -*-
"""
多智能体通用 API（业务无关，面向场景注册表编程）。

- GET  /api/agent/multi/scenarios                                场景列表
- GET  /api/agent/multi/scenarios/{sid}/targets                  场景目标列表（通用目标卡）
- POST /api/agent/multi/scenarios/{sid}/targets/{tid}/run        对目标发起研判（SSE）
- POST /api/agent/multi/scenarios/{sid}/run                      自由任务研判（SSE，仅 adhoc 场景；
                                                                 body.task + body.agents 自由指定团队编制）
- GET/POST /api/agent/multi/tasks · PUT/DELETE /tasks/{tid}      任务库 CRUD（可配置任务提示词模板）
- GET  /api/agent/multi/tools                                    Function Calling 工具清单（内置 + MCP 状态）
- GET/POST /api/agent/multi/mcp/servers · PUT/DELETE /servers/{sid}   MCP 注册中心：服务器 CRUD（配置入库，热生效）
- POST /api/agent/multi/mcp/test                                 MCP 服务器连接测试（不落库，试连 + 拉工具清单）
- POST /api/agent/multi/mcp/inspect                              已启用服务器状态巡检（实连 + 工具清单）

SSE 事件契约（team / plan / node_start / node_done / evidence / fact /
conflict / token / error / done，`data: [DONE]` 收尾）与业务场景无关，
定义见 doc/智能体/多智能体场景.md §4。新增业务场景只需在
services/multi_agent/scenarios/ 注册适配器，本路由零改动。
"""

import asyncio
import inspect
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.multi_agent import task_store
from services.multi_agent.scenarios import MultiAgentScenario, get_scenario, list_scenarios

router = APIRouter(prefix="/agent/multi", tags=["agent-multi"])


def _sse_evt(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/scenarios")
async def multi_scenarios():
    """已注册场景列表（id / name / business / description / adhoc）。"""
    return list_scenarios()


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


class TaskBody(BaseModel):
    task: str = ""
    agents: list[str] = []   # 自由编制：可选能力智能体 id 列表（空 = 场景默认编制）


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario_task(scenario_id: str, body: TaskBody):
    """自由任务研判（adhoc 场景）：任务文本 + 智能体编制 → 动态建团，SSE 返回。"""
    scenario: MultiAgentScenario | None = get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(404, f"场景 {scenario_id} 未注册")
    if not getattr(scenario, "adhoc", False):
        raise HTTPException(400, f"场景 {scenario_id} 不支持自由任务输入，请走目标卡接口")
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(422, "task 不能为空")
    # 构建以 awaitable 传入：SSE 先开流，Planner 规划（LLM 调用）期间前端可见「规划中」
    return _stream_engine(engine_source=scenario.build_engine_from_task(task, agents=body.agents))


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


def _stream_engine(engine_source) -> StreamingResponse:
    """引擎执行 → SSE 事件流（team 首帧 / 过程事件 / done 收尾，公共实现）。

    engine_source 可以是引擎实例，也可以是「返回引擎的 awaitable」（自由任务
    模式下 Planner 要先调 LLM 规划，最长 PLAN_TIMEOUT 秒）。传 awaitable 时
    构建在流内进行——SSE 立即开流并先下发 planner node_start，规划期间的
    等待对前端可见（此前规划在开流前完成，前端最长 15s 只见「团队运行中」
    而无任何过程输出）。
    """

    async def _stream():
        engine = engine_source
        if inspect.isawaitable(engine):
            yield _sse_evt({"type": "node_start", "node": "planner",
                            "role": "planner", "goal": "任务规划中（LLM 分解子任务）…"})
            try:
                engine = await engine
            except Exception as exc:  # 构建失败：走事件通道报告，不让前端干等
                yield _sse_evt({"type": "error", "content": f"团队构建失败：{exc}"})
                yield _sse_evt({"type": "done", "conclusion": "", "elapsed_ms": 0})
                yield "data: [DONE]\n\n"
                return

        await engine.q.put({"type": "team", **engine.team_info()})

        final: dict = {}

        async def _run():
            try:
                final.update(await engine.run())
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
                yield _sse_evt(evt)
        finally:
            if not task.done():
                task.cancel()

        yield _sse_evt({
            "type": "done",
            "conclusion": final.get("conclusion", ""),
            "elapsed_ms": final.get("elapsed_ms", 0),
        })
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
