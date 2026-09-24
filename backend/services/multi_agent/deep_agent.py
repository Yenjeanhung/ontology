# -*- coding: utf-8 -*-
"""DeepAgents 深度模式桥接层：多智能体模块的第二执行路径（增量，非替换）。

DeepAgents（LangChain 官方 agent harness，deepagents>=0.7）在 LangGraph 运行时
之上提供「LLM 自主规划（write_todos）+ 虚拟文件系统（上下文卸载）+ 可选子智能体
（上下文隔离）」的深度任务能力。本模块把它接入现有协作链路：

- 同契约适配：DeepAgentRunner 实现 MultiAgentEngine 的三个消费面成员
  （q / team_info() / run()），路由层 _stream_engine 零改动——SSE 事件、
  协作会话留痕、前端渲染全部复用（《智能体交互.md》事件契约不变）；
- 取数同源：deep_kb_search / deep_graph_search / deep_data_query 直接复用
  universal 场景的取证函数（NL2SQL → NL2Filter → 词频老路三级链、跨库向量
  检索、图谱关键词检索），口径与 ToolAgent / 取证节点完全一致；
- 事件契约不变：node_start / node_done / fact / token / error——前端零改动
  （工具调用呈过程节点，事实卡进「工具产出」tab，AI 文本流式进正文气泡）；
- 与现有引擎的分工：常规任务走 StateGraph 团队（确定性编排、快），复杂多阶段
  任务由前端显式勾选「深度模式」（请求体 deep=true）进入本路径（LLM 自主多轮）。

开关（config.py）：DEEP_AGENT_ENABLED 总闸（默认 False，前端勾选 + 总闸双确认）；
DEEP_AGENT_TIMEOUT 整轮超时；DEEP_AGENT_SUBAGENTS 子智能体模式（默认 False =
主代理直带工具，启用后主代理经 task 工具派发给隔离上下文的子研究员）。
"""
import asyncio
import json
import logging
import time
from typing import Any, Optional

from config import settings
from core.otel import async_span

logger = logging.getLogger(__name__)

TEAM_NAME = "深度智能体（DeepAgents）"

_DEEP_SYSTEM = (
    "你是平台的深度研究员，处理需要多步骤取证的复杂任务。\n"
    "工作方式：先用 write_todos 把任务拆成待办清单（3~6 项），再逐项执行——\n"
    "· 查文档、制度、规范、事实描述 → deep_kb_search；\n"
    "· 查实体之间的关联关系 → deep_graph_search；\n"
    "· 查数量、统计、台账明细（真实业务数据）→ deep_data_query；\n"
    "必要时对同一问题换措辞多查几次、或用多工具结果交叉印证。\n"
    "硬约束：平台内数据（台账数值、实体、关系）必须以工具返回为准，绝不编造；"
    "每项待办完成后可在清单中标记进度。最终回答用中文、结构化呈现"
    "（分点/小标题），并注明关键结论来自哪次工具调用。"
)


def _short(text: Any, limit: int = 60) -> str:
    s = str(text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


class DeepAgentRunner:
    """与 MultiAgentEngine 同契约（q / team_info() / run()）的深度模式适配器。

    路由层 _stream_engine 只消费这三个成员：q 事件队列转 SSE、team_info() 出
    团队帧、run() 返回 {"conclusion", "elapsed_ms"}。结论口径与前端一致：
    run 内流出的全部 AI 文本（token 累积）即 conclusion。
    """

    def __init__(self, task: str, kb_id: str = "") -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self._task = (task or "").strip()
        self._kb_id = kb_id or ""
        self._facts = 0                                  # 事实卡编号（fact-deep-N）
        self._node_t0: dict[str, float] = {}             # 节点计时（与 engine.emit 同口径）
        self._pending_tools: dict[str, str] = {}         # tool_call_id → 工具名
        self._t0 = time.monotonic()

    # ── 事件外抛（node_done 就地补齐 elapsed_ms，与 engine.emit 一致） ──

    def emit(self, evt: dict) -> None:
        t = evt.get("type")
        if t == "node_start":
            self._node_t0.setdefault(evt.get("node", ""), time.perf_counter())
        elif t == "node_done":
            t0 = self._node_t0.setdefault(evt.get("node", ""), time.perf_counter())
            evt["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        self.q.put_nowait(evt)

    def team_info(self) -> dict:
        return {
            "team": TEAM_NAME,
            "members": [
                {"node": "deep_agent", "role": "deep", "name": "深度研究员",
                 "goal": "自主规划 + 多轮工具取证"},
            ],
        }

    def _emit_fact(self, cards: list[dict]) -> None:
        """工具产出的结构化结果 → 事实卡（grade=tool_result，前端「工具产出」tab）。"""
        out = []
        for c in cards or []:
            if not isinstance(c, dict):
                continue
            self._facts += 1
            out.append({
                "id": f"fact-deep-{self._facts}",
                "grade": "tool_result",
                "title": str(c.get("title") or "")[:80],
                "detail": str(c.get("detail") or "")[:300],
            })
        if out:
            self.emit({"type": "fact", "facts": out})

    # ── 工具集：与取证节点同源取数，结果文本化给 LLM + 结构化卡外抛 ──

    def _build_tools(self) -> list:
        from langchain_core.tools import tool

        runner = self

        @tool
        async def deep_kb_search(query: str) -> str:
            """跨全部知识库做语义检索，返回最相关的文档片段。凡涉及文档、制度、
            规范、报告、事实描述类信息时使用；query 用一个聚焦的查询短语。"""
            from services.multi_agent.scenarios.universal import _search_kb_chunks
            try:
                chunks = await _search_kb_chunks(query, runner._kb_id)
            except Exception as exc:
                return f"（检索失败：{type(exc).__name__}: {exc}）"
            if not chunks:
                return "（知识库无命中；可换措辞重试，或改用其他工具）"
            runner._emit_fact([{
                "title": f"知识库《{c.get('kb_name', '')}》· {c.get('file_name', '')}"
                         + (f" 第{c.get('page')}页" if c.get("page") else ""),
                "detail": str(c.get("text") or "")[:200],
            } for c in chunks[:4]])
            return "\n\n".join(
                f"[{i}] 《{c.get('file_name', '')}》"
                + (f" 第{c.get('page')}页" if c.get("page") else "")
                + f"（相关度 {c.get('score', 0):.2f}）\n{str(c.get('text') or '')[:350]}"
                for i, c in enumerate(chunks[:4], 1))

        @tool
        async def deep_graph_search(query: str) -> str:
            """实体图谱关键词检索：返回命中实体及其关系链。凡涉及「谁与谁有关联、
            依赖、上下游、组成」类问题使用。"""
            from services.multi_agent.scenarios.universal import _graph_facts
            facts = await _graph_facts(query)
            if not facts:
                return "（图谱无命中实体；可尝试只给实体名的短查询）"
            runner._emit_fact(facts[:6])
            return "\n".join(f"· {f.get('title', '')}：{f.get('detail', '')}"
                             for f in facts[:6])

        @tool
        async def deep_data_query(query: str) -> str:
            """实体台账结构化查询：返回统计计数、聚合与最新明细（真实业务数据）。
            凡涉及「多少条 / 数量 / 统计 / 台账明细」类问题使用。"""
            from services.multi_agent.scenarios.universal import _data_facts
            facts = await _data_facts(query)
            if not facts:
                return "（台账未命中；可换更具体的实体/指标措辞重试）"
            runner._emit_fact(facts[:8])
            return "\n".join(f"· {f.get('title', '')}：{f.get('detail', '')}"
                             for f in facts[:8])

        return [deep_kb_search, deep_graph_search, deep_data_query]

    # ── agent 构建 ────────────────────────────────────────────

    def _build_agent(self):
        try:
            from deepagents import create_deep_agent
            from langchain.agents.middleware import TodoListMiddleware
        except ImportError as exc:
            raise RuntimeError(
                "deepagents 未安装（conda 环境 pip install deepagents）") from exc

        from providers.llm import create_llm
        llm = create_llm()
        if llm is None:
            raise RuntimeError("LLM 未配置（llm_configs 无启用配置）")

        kwargs: dict = {
            "model": llm,
            "tools": self._build_tools(),
            "system_prompt": _DEEP_SYSTEM,
            "middleware": [TodoListMiddleware()],   # v0.7 起规划 opt-in，显式启用
        }
        if bool(getattr(settings, "DEEP_AGENT_SUBAGENTS", False)):
            tools = kwargs["tools"]
            kwargs["subagents"] = [
                {"name": "kb_researcher",
                 "description": "知识库检索专员：查文档、制度、规范、事实描述",
                 "tools": [tools[0]],
                 "system_prompt": "你是知识库检索专员，只做 deep_kb_search 并如实汇总命中片段。"},
                {"name": "graph_researcher",
                 "description": "图谱分析专员：查实体之间的关联关系",
                 "tools": [tools[1]],
                 "system_prompt": "你是图谱分析专员，只做 deep_graph_search 并如实汇总实体关系。"},
                {"name": "data_analyst",
                 "description": "数据分析专员：查台账统计与明细（真实业务数据）",
                 "tools": [tools[2]],
                 "system_prompt": "你是数据分析专员，只做 deep_data_query 并如实汇总台账数据。"},
            ]
        return create_deep_agent(**kwargs)

    # ── 执行：astream 双流消费 → 既有事件契约 ─────────────────

    async def run(self) -> dict:
        from langchain_core.messages import AIMessage, ToolMessage

        timeout = float(getattr(settings, "DEEP_AGENT_TIMEOUT", 300.0))
        async with async_span(
            "agent.deep.run",
            {"agent.team": TEAM_NAME, "agent.task": _short(self._task, 200)},
        ) as span:
            self.emit({"type": "node_start", "node": "deep_agent", "role": "deep",
                       "goal": "自主规划 + 多轮工具取证"})
            text_parts: list[str] = []

            def _consume_update(update: Any) -> None:
                """updates 流：AIMessage.tool_calls → 工具节点开始；ToolMessage →
                节点完成。纯文本 AIMessage 已由 messages 流逐 token 外抛，不重复。"""
                if not isinstance(update, dict):
                    return
                for m in (update.get("messages") or []):
                    if isinstance(m, AIMessage):
                        for tc in (m.tool_calls or []):
                            name = str(tc.get("name") or "tool")
                            self._pending_tools[str(tc.get("id") or name)] = name
                            self.emit({
                                "type": "node_start", "node": f"tool:{name}",
                                "role": "deep", "goal": _tool_goal(name, tc.get("args")),
                            })
                    elif isinstance(m, ToolMessage):
                        name = self._pending_tools.pop(str(m.tool_call_id or ""), "tool")
                        ok = getattr(m, "status", "success") == "success"
                        summary = _short(m.content, 120)
                        self.emit({"type": "node_done", "node": f"tool:{name}",
                                   "summary": summary if ok else f"调用失败：{summary}"})

            async def _drive(agent) -> None:
                async for mode, payload in agent.astream(
                    {"messages": [{"role": "user", "content": self._task}]},
                    stream_mode=["messages", "updates"],
                ):
                    if mode == "messages":
                        chunk = payload[0] if isinstance(payload, tuple) else payload
                        text = _chunk_text(chunk)
                        if text:
                            text_parts.append(text)
                            self.emit({"type": "token", "content": text})
                    else:   # updates：{node_name: state_delta}
                        for update in (payload or {}).values():
                            _consume_update(update)

            conclusion = ""
            try:
                agent = self._build_agent()
                await asyncio.wait_for(_drive(agent), timeout=timeout)
                conclusion = "".join(text_parts).strip()
            except asyncio.TimeoutError:
                conclusion = "".join(text_parts).strip()
                note = f"深度模式整轮超时（>{int(timeout)}s），已基于已有产出收尾"
                logger.warning("[deep] %s task=%s", note, _short(self._task))
                self.emit({"type": "error", "content": note})
            except Exception as exc:
                logger.exception("[deep] 深度模式执行异常")
                self.emit({"type": "error",
                           "content": f"深度模式执行异常：{type(exc).__name__}: {exc}"})
            finally:
                self.emit({"type": "node_done", "node": "deep_agent",
                           "summary": f"产出 {len(conclusion)} 字 · 事实卡 {self._facts} 张"})

            elapsed = int((time.monotonic() - self._t0) * 1000)
            if span.is_recording():
                span.set_attribute("agent.elapsed_ms", elapsed)
                span.set_attribute("agent.facts", self._facts)
            return {"conclusion": conclusion, "elapsed_ms": elapsed}


def _tool_goal(name: str, args: Any) -> str:
    """工具节点目标摘要：取 args 中最有信息量的短字段（query/task 等）。"""
    brief = ""
    if isinstance(args, dict):
        for k in ("query", "task", "q", "input", "description"):
            if args.get(k):
                brief = _short(args[k], 48)
                break
    return f"{name}（{brief}）" if brief else name


def _chunk_text(chunk) -> str:
    """AIMessageChunk → 纯文本增量（兼容 string / 多模态列表两种 content）。"""
    if chunk is None or getattr(chunk, "tool_call_chunks", None):
        return ""     # 工具调用声明块不含正文
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict))
    return ""


async def build_deep_engine(task: str, route: Optional[dict] = None,
                            kb_id: str = "") -> DeepAgentRunner:
    """深度模式引擎工厂（与 scenario.build_engine_from_task 同形，供路由层分支）。"""
    task = (task or "").strip()
    if not task:
        raise ValueError("任务描述不能为空")
    if route:
        logger.info("[deep] 深度模式启动 route=%s task=%s",
                    route.get("mode"), _short(task))
    return DeepAgentRunner(task, kb_id=kb_id)
