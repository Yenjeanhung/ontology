# -*- coding: utf-8 -*-
"""智能助手（全局浮标）单智能体服务：LLM + Function Calling 工具循环。

与多智能体协作链路（services/multi_agent）完全解耦：
- 一个 LLM + 工具循环（run_tool_loop），无 Planner / 路由 / 团队编排；
- 智能体身份 = 内置 preset「智能助手」（agent_assistant），技能 / 工具白名单 /
  人设 / KB 绑定均在智能体配置页（/agent/configs）维护，保存后浮标即时生效；
- 工具 = PlatformTools 白名单构建：内置 kb_search/graph_search/data_query +
  MCP 注册中心（按 "mcp:<server>" 服务器级勾选）；
- 会话留痕复用 chat_sessions/chat_messages（scene="assistant"），滚动摘要与
  mem0 长期记忆与问答页同口径。

SSE 事件（data: {json}\n\n，data: [DONE] 收尾）：
    session {session_id, title}          会话锚点（续聊/留痕）
    tools   {tools, mcp_status}          可用工具清单（本轮实际构建的注册表）
    token   {content[, reasoning]}       正文/思考链流式增量（reasoning=true 为思考链）
    tool_call    {name, arguments}       工具调用开始
    tool_result  {name, ok, duration_ms, summary}
    tool_calls   {calls:[...]}           全部调用汇总（轻量，无 raw）
    done    {conclusion, elapsed_ms} / error {content}
"""
import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Optional

from database import get_db
from services.agent_service import ASSISTANT_AGENT_ID, AgentService, ensure_assistant_agent
from services.chat_service import ChatService

logger = logging.getLogger(__name__)

ASSISTANT_SCENE = "assistant"   # chat_sessions.scene 取值：浮标会话与问答/协作隔离


class AssistantUnavailable(Exception):
    """智能助手不可用（未 seed 或已被禁用）。"""


def _sse(evt: dict) -> str:
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


def _short(text: Any, limit: int = 120) -> str:
    s = str(text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


async def prepare_turn(db, query: str, session_id: Optional[str], user_id: str) -> dict:
    """端点期（db 可用）：解析助手智能体 + 会话锚定 + 历史/记忆预载。

    db 会话在响应返回后即释放，这里预载全部上下文；返回的 ctx 由
    stream_turn() 消费（生成器内不再持有 db）。
    """
    query = (query or "").strip()
    agent = await AgentService.resolve(db, ASSISTANT_AGENT_ID)
    if agent is None:
        # 库为旧版本未 seed / 被手工删除：现场补种兜底一次
        await ensure_assistant_agent(db)
        agent = await AgentService.resolve(db, ASSISTANT_AGENT_ID)
    if agent is None:
        raise AssistantUnavailable("智能助手未启用（请在智能体配置页检查「智能助手」状态）")

    from services.skill_service import SkillService
    skills = await SkillService.resolve(db, agent["skill_ids"])

    # 会话（scene=assistant）：传 session_id 续聊（校验属主 + 归属），否则新建
    session = None
    if session_id:
        session = await ChatService.get_owned(db, session_id, user_id)
        if session and (session.agent_id or "") != ASSISTANT_AGENT_ID:
            session = None   # 归属智能体不符：自动开新会话，前端以 session 事件为准
    if session is None:
        session = await ChatService.create_session(
            db, agent_id=ASSISTANT_AGENT_ID, kb_id="", user_id=user_id,
            title=query[:50], scene=ASSISTANT_SCENE)
    await ChatService.append_message(db, session.id, "user", query)

    hist = await ChatService.load_history(db, session.id)

    memories: list[str] = []
    from services.memory_store import MemoryStore
    if MemoryStore.available():
        try:
            memories = await MemoryStore.search(query, agent_id=ASSISTANT_AGENT_ID,
                                                user_id=user_id)
        except Exception:
            logger.exception("assistant memory search failed")

    return {
        "query": query,
        "user_id": user_id,
        "session": session,
        "agent": agent,
        "skills": skills,
        "history": hist["history"],
        "summary": hist["summary"],
        "memories": memories,
    }


async def stream_turn(ctx: dict) -> AsyncGenerator[str, None]:
    """SSE 主流程：工具循环流式执行（事件经桥接队列即时下发），结束后落库留痕。"""
    from services.oag_service import (
        CHAT_SYSTEM_PROMPT, _TOOL_MODE_HEADER, _augment_system_prompt,
        _history_messages, build_system_prompt,
    )
    from providers.llm import create_llm

    t0 = time.monotonic()
    session = ctx["session"]
    agent = ctx["agent"]
    query = ctx["query"]

    yield _sse({"type": "session", "session_id": session.id, "title": session.title})

    # system：人设（空=问答页默认人设）+ 技能指令 + 会话摘要 + 长期记忆 + 工具口径
    base_prompt = agent["system_prompt"] or CHAT_SYSTEM_PROMPT
    system = build_system_prompt(ctx["skills"], base_prompt=base_prompt)
    system = _augment_system_prompt(system, summary=ctx["summary"], memories=ctx["memories"])

    use_tools = bool(agent.get("use_tools"))
    if use_tools:
        system += _TOOL_MODE_HEADER.format(
            facts="（无）", sources="（未预取检索上下文，需要时用工具获取）")
        if not agent["tool_names"]:
            system += "\n当前未勾选任何工具白名单，默认开放全部内置工具与已启用 MCP 服务器。"

    queue: asyncio.Queue = asyncio.Queue()

    def _on_event(evt: dict) -> None:
        queue.put_nowait(evt)

    async def _drive() -> tuple[str, list[dict]]:
        """执行主体，返回 (conclusion, 轻量调用记录)；事件全部进队列。"""
        answer_parts: list[str] = []
        calls: list[dict] = []
        if not use_tools:
            # 未启用工具循环：纯 LLM 单轮流式（保留思考链外抛）
            from langchain_core.messages import HumanMessage
            llm = create_llm()
            history = _history_messages(ctx["history"])
            async for chunk in llm.astream([*history, HumanMessage(content=query)]):
                from providers.llm import chunk_reasoning, chunk_text
                reasoning = chunk_reasoning(chunk)
                if reasoning:
                    queue.put_nowait({"type": "token", "content": reasoning, "reasoning": True})
                text = chunk_text(chunk)
                if text:
                    answer_parts.append(text)
                    queue.put_nowait({"type": "token", "content": text})
            return "".join(answer_parts), calls

        from services.agent_loop import run_tool_loop
        from services.tool_registry import PlatformTools

        include = agent["tool_names"] or None   # 空 = 全部可用
        async with PlatformTools(include=include) as pt:
            queue.put_nowait({"type": "tools", "tools": pt.registry.describe(),
                              "mcp_status": pt.mcp_status})
            result = await run_tool_loop(
                create_llm(), pt.registry, system, query,
                history=_history_messages(ctx["history"]),
                on_event=_on_event, stream_tokens=True,
            )
            if result.final_text:
                answer_parts.append(result.final_text)
            calls = [{"name": c.name, "ok": c.ok,
                      "duration_ms": c.duration_ms,
                      "summary": _short(c.result_text)} for c in result.calls]
            if result.degraded and result.degrade_note:
                queue.put_nowait({"type": "tool_degrade", "note": result.degrade_note})
        return "".join(answer_parts), calls

    task = asyncio.create_task(_drive())
    conclusion = ""
    calls: list[dict] = []
    failed = False
    while not task.done() or not queue.empty():
        try:
            evt = await asyncio.wait_for(queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            continue
        yield _sse(evt)
    try:
        conclusion, calls = await task
    except RuntimeError as exc:      # LLM 未配置等致命错误
        failed = True
        yield _sse({"type": "error", "content": str(exc)})
    except Exception:
        logger.exception("assistant turn failed")
        failed = True
        yield _sse({"type": "error", "content": "智能助手执行出错，请稍后重试"})

    if calls:
        yield _sse({"type": "tool_calls", "calls": calls})
    if not failed:
        yield _sse({"type": "done",
                    "conclusion": conclusion,
                    "elapsed_ms": int((time.monotonic() - t0) * 1000)})
    yield "data: [DONE]\n\n"

    # ── 留痕（db 已释放，自开会话；尽力而为不影响已下发内容）──
    await _persist_turn(ctx, conclusion)


async def _persist_turn(ctx: dict, answer: str) -> None:
    """助手消息落库 + 滚动摘要（后台）+ mem0 长期记忆（后台）。"""
    try:
        async for db in get_db():
            await ChatService.append_message(db, ctx["session"].id, "assistant", answer)
            break
        _summarize_background(ctx["session"].id)
    except Exception:
        logger.exception("assistant 会话消息落库失败: session=%s", ctx["session"].id)
    try:
        from services.memory_store import MemoryStore
        MemoryStore.add_background(ctx["query"], answer, agent_id=ASSISTANT_AGENT_ID,
                                   user_id=ctx["user_id"], session_id=ctx["session"].id)
    except Exception:
        logger.exception("assistant 长期记忆写入失败")


def _summarize_background(session_id: str) -> None:
    """滚动摘要后台任务（与问答页同口径）。"""
    async def _run() -> None:
        try:
            async for db in get_db():
                await ChatService.maybe_summarize(db, session_id)
                break
        except Exception:
            logger.exception("assistant 会话摘要失败: session=%s", session_id)
    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass
