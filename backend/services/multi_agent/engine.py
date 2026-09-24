# -*- coding: utf-8 -*-
"""
多智能体通用执行引擎（业务无关）。

引擎不知道任何业务概念，只负责三件事（对应《多智能体交互.md》§4/§5）：
1. 编排：planner 分解 → plan 中声明为并行角色（retriever / worker /
   graph_agent / data_agent）的节点在同一 superstep 自动并行 → critic
   （可选组合）→ synthesizer；
2. 事件外抛：节点通过 engine.emit() 把过程事件（node_start / node_done /
   evidence / fact / conflict / token / error）推入队列，路由层转成 SSE；
3. 公共设施：LLM 单例、流式调用（带单节点超时）、并行写 state 的 reducer。

业务逻辑全部下沉到场景适配器（见 scenarios/）：适配器提供团队信息、任务计划、
节点实现与初始上下文，引擎按 plan 声明的角色调度。新增业务场景 = 新写一个
适配器并注册，引擎与前端页面零改动。
"""

import asyncio
from datetime import datetime
from pathlib import Path
from langgraph.graph.state import CompiledStateGraph
import time
from typing import Annotated, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from config import settings
from core.otel import async_span
from providers.llm import create_llm

NODE_TIMEOUT = 30.0  # 单节点 LLM 调用超时（秒），超时由节点自行降级

# 节点函数：async (state) -> 部分更新 dict
NodeFunc = Callable[[dict], Awaitable[dict]]


def merge_evidence(left: dict, right: dict) -> dict:
    """并行节点写同一 evidence 键时的 reducer：按域合并证据卡。"""
    out = dict(left or {})
    for domain, cards in (right or {}).items():
        out.setdefault(domain, [])
        out[domain] = out[domain] + list(cards)
    return out


def merge_facts(left: list, right: list) -> list:
    """并行节点写 facts 时的 reducer：顺序拼接事实卡（图谱/数据查询可并行入黑板）。"""
    return list(left or []) + list(right or [])


# ── LangGraph Checkpointer（断点恢复/回放，选型文档 P0） ────────────────────
#
# AsyncSqliteSaver 进程级单例：独立库文件（不混业务库/主库/otel 库），每个
# superstep 落一行 checkpoint。进程崩溃/节点异常后，同 thread_id 用 ainvoke(None)
# 从最后 checkpoint 续跑——已完成节点不重复执行（1.2.11 实测：aget_state 精确
# 给出 next 待续节点，恢复仅重试 pending 任务）。
#
# 同库维护 agent_runs 登记表：一轮运行一行，记录 status 与重建引擎所需的全部
# 静态材料（plan/context/team_info/nl_filter/rewrite_gate）——checkpoint 只存
# 黑板状态不存图结构，恢复端点据此重建同构引擎。

_SAVER = None        # AsyncSqliteSaver | None（惰性创建，随进程生命周期）
_SAVER_LOCK: asyncio.Lock | None = None


def _checkpoint_db_path() -> str:
    return settings.MULTI_AGENT_CHECKPOINT_DB or "./data/multi_agent_checkpoints.db"


async def _get_checkpointer():
    """AsyncSqliteSaver 单例；总闸关闭返回 None（run 行为同无 checkpointer 旧版）。"""
    global _SAVER, _SAVER_LOCK
    if not settings.MULTI_AGENT_CHECKPOINTER:
        return None
    if _SAVER is None:
        if _SAVER_LOCK is None:
            _SAVER_LOCK = asyncio.Lock()
        async with _SAVER_LOCK:
            if _SAVER is None:
                import aiosqlite
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

                p = Path(_checkpoint_db_path())
                p.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(str(p))
                saver = AsyncSqliteSaver(conn)
                await saver.setup()          # checkpoint 表（幂等）
                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS agent_runs(
                        thread_id      TEXT PRIMARY KEY,
                        chat_id        TEXT NOT NULL DEFAULT '',
                        task           TEXT NOT NULL DEFAULT '',
                        status         TEXT NOT NULL DEFAULT 'running',
                        plan_json      TEXT NOT NULL DEFAULT '[]',
                        context_json   TEXT NOT NULL DEFAULT '{}',
                        team_json      TEXT NOT NULL DEFAULT '{}',
                        materials_json TEXT NOT NULL DEFAULT '{}',
                        error          TEXT NOT NULL DEFAULT '',
                        created_at     TEXT NOT NULL,
                        updated_at     TEXT NOT NULL)""")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_runs_chat"
                    " ON agent_runs(chat_id, updated_at)")
                await conn.commit()
                _SAVER = saver
    return _SAVER


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def runs_register(engine: "MultiAgentEngine") -> None:
    """登记一轮运行（run 开始时 upsert）：恢复重建材料随行落库。"""
    conn = _SAVER.conn if _SAVER is not None else None
    if conn is None or not engine.thread_id:
        return
    import json
    chat_id = engine.thread_id.split("::", 1)[0] if "::" in engine.thread_id else ""
    now = _now()
    await conn.execute(
        """INSERT INTO agent_runs(thread_id, chat_id, task, status, plan_json,
             context_json, team_json, materials_json, error, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(thread_id) DO UPDATE SET
             status='running', error='', updated_at=excluded.updated_at""",
        (engine.thread_id, chat_id, str(engine.context.get("task", ""))[:500],
         "running", json.dumps(engine._plan, ensure_ascii=False),
         json.dumps(engine.context, ensure_ascii=False, default=str),
         json.dumps(engine._team_info, ensure_ascii=False, default=str),
         json.dumps(getattr(engine, "replay_materials", {}) or {}, ensure_ascii=False, default=str),
         "", now, now))
    await conn.commit()


async def runs_set_status(thread_id: str, status: str, error: str = "") -> None:
    """更新运行终态：done / failed（崩溃残留 running 由恢复端点认领）。"""
    conn = _SAVER.conn if _SAVER is not None else None
    if conn is None or not thread_id:
        return
    await conn.execute(
        "UPDATE agent_runs SET status=?, error=?, updated_at=? WHERE thread_id=?",
        (status, error[:500], _now(), thread_id))
    await conn.commit()


async def runs_latest_pending(chat_id: str) -> dict | None:
    """该会话最近一条未正常结束的运行（running 崩溃残留 / failed 异常），供断点续跑。"""
    conn = _SAVER.conn if _SAVER is not None else None
    if conn is None:
        return None
    import json
    cur = await conn.execute(
        "SELECT thread_id, chat_id, task, status, plan_json, context_json,"
        " team_json, materials_json, error, updated_at FROM agent_runs"
        " WHERE chat_id=? AND status IN ('running','failed')"
        " ORDER BY updated_at DESC LIMIT 1", (chat_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    cols = ["thread_id", "chat_id", "task", "status", "plan", "context",
            "team_info", "materials", "error", "updated_at"]
    d = dict(zip(cols, row))
    for k in ("plan", "context", "team_info", "materials"):
        try:
            d[k] = json.loads(d[k] or ("[]" if k == "plan" else "{}"))
        except Exception:
            d[k] = [] if k == "plan" else {}
    return d


async def runs_list(chat_id: str, limit: int = 20) -> list[dict]:
    """该会话的运行登记（诊断列表，不含重建材料大字段）。"""
    conn = _SAVER.conn if _SAVER is not None else None
    if conn is None:
        return []
    cur = await conn.execute(
        "SELECT thread_id, task, status, error, created_at, updated_at"
        " FROM agent_runs WHERE chat_id=? ORDER BY updated_at DESC LIMIT ?",
        (chat_id, int(limit)))
    rows = await cur.fetchall()
    return [
        {"thread_id": r[0], "task": r[1], "status": r[2],
         "error": r[3], "created_at": r[4], "updated_at": r[5]}
        for r in rows
    ]


class MultiAgentState(TypedDict):
    """流水线共享状态（黑板模式，见设计文档 §4.1）。

    context 由场景适配器填充，引擎不解读其内容；其余键为各角色的标准产出。
    """

    context: dict                                                  # 场景上下文（只读，业务自定义）
    plan: list[dict]                                               # 任务计划
    evidence: Annotated[dict[str, list[dict]], merge_evidence]     # domain → 证据卡
    facts: Annotated[list[dict], merge_facts]                      # 图谱/结构化事实卡（并行可写）
    verdict: dict                                                  # critic 裁定
    conclusion: str                                                # synthesizer 结论


def chunk_text(chunk) -> str:
    """兼容 string / 多模态列表两种 content 形态，取出纯文本增量。"""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


class MultiAgentEngine:
    """业务无关的多智能体执行引擎。

    参数：
    - context:    场景上下文（业务自定义 dict，进入 state["context"]）
    - team_info:  {"team": 名称, "members": [{node, role, name}]}
    - plan:       [{id, node, role, goal}]，role ∈ parallel_roles / critic / synthesizer
    - node_factory: callable(engine) -> {node_id: NodeFunc}，
                    延迟到建图时调用，使节点函数可以闭包引用 engine（emit / llm_stream）
    - pacing:     演示节奏（planner 停顿时长，秒；生产可置 0）
    """

    # 并行角色：同一 superstep 自动并行执行（retriever=检索增强执行，
    # worker=纯模型执行，graph_agent=图谱事实，data_agent=台账数据查询，
    # tool_agent=Function Calling 工具调用取证，custom=自定义智能体
    # （智能体配置页人设 + 绑定知识库，custom:{id} 勾选接入）；
    # 自由组合时由场景按所选智能体声明）
    PARALLEL_ROLES = {"retriever", "worker", "graph_agent", "data_agent",
                      "tool_agent", "custom"}

    def __init__(
        self,
        context: dict,
        team_info: dict,
        plan: list[dict],
        node_factory: Callable[["MultiAgentEngine"], dict[str, NodeFunc]],
        pacing: float = 0.05,
    ) -> None:
        self.context = context
        self._team_info = dict(team_info or {})
        self._plan = [dict(step) for step in plan]
        self._node_factory = node_factory
        self._pacing = pacing
        self._nodes: dict[str, NodeFunc] = {}
        self._llm = None
        self._llm_checked = False
        # Checkpointer（断点恢复，见模块尾 _get_checkpointer）：thread_id 由路由层
        # 生成（f"{会话ID}::{随机token}"，一轮运行一条）；空串 = 不启用（零破坏兼容）。
        # replay_materials：重建引擎所需、但不进黑板状态的材料（nl_filter/rewrite_gate），
        # 由场景适配器在构造后填充，随 agent_runs 行落库供恢复端点重建同构节点闭包。
        self.thread_id: str = ""
        self.replay_materials: dict = {}

        self.q: asyncio.Queue = asyncio.Queue()
        self._node_t0: dict[str, float] = {}   # 节点计时起点（node_start 首次出现时刻）
        self._validate_plan()

    def _validate_plan(self) -> None:
        critics = [s for s in self._plan if s["role"] == "critic"]
        synths = [s for s in self._plan if s["role"] == "synthesizer"]
        if len(critics) > 1 or len(synths) != 1:
            raise ValueError(
                "plan 必须包含且仅包含一个 synthesizer，至多一个 critic 节点"
                "（critic 为可选组合，未选时并行节点直通 synthesizer）")

    # ── 基础设施 ──────────────────────────────────────────────

    def emit(self, evt: dict) -> None:
        """事件外抛统一出口：node_done 就地补齐 elapsed_ms（节点真实执行耗时），
        回放/链路耗时面板不再依赖前端现场计时。"""
        t = evt.get("type")
        if t == "node_start":
            self._node_t0.setdefault(evt.get("node", ""), time.perf_counter())
        elif t == "node_done":
            t0 = self._node_t0.setdefault(evt.get("node", ""), time.perf_counter())
            evt["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        self.q.put_nowait(evt)

    def team_info(self) -> dict:
        info = dict(self._team_info)
        info["members"] = [dict(m) for m in info.get("members", [])]
        return info

    def llm(self):
        """create_llm 延迟单例：未配置 API KEY 时返回 None（节点自行降级）。"""
        if not self._llm_checked:
            self._llm_checked = True
            try:
                self._llm = create_llm()
            except Exception:
                self._llm = None
        return self._llm

    async def llm_stream(self, system: str, user: str, on_token=None,
                         timeout: float | None = None) -> str:
        """流式调用 LLM：单节点超时兜底（默认 NODE_TIMEOUT，可放宽），逐 token 回调。

        失败（未配置 / 超时 / 异常）向上抛出，由场景节点决定降级方式；
        返回完整文本。注意思考型模型可见输出前有较长静默期，长文本合成
        节点应传更长的 timeout（如 SYNTH_TIMEOUT），否则整流会被掐断。"""
        llm = self.llm()
        if llm is None:
            raise RuntimeError("LLM 未配置")
        parts: list[str] = []

        async def _consume():
            async for chunk in llm.astream([
                ("system", system),
                ("human", user),
            ]):
                text = chunk_text(chunk)
                if text:
                    parts.append(text)
                    if on_token:
                        on_token(text)

        await asyncio.wait_for(_consume(), timeout=timeout or NODE_TIMEOUT)
        return "".join(parts).strip()

    # ── 默认 planner 节点（通用：规则分解，不走 LLM，快而稳） ──

    async def _planner(self, state: MultiAgentState) -> dict:
        self.emit({
            "type": "node_start",
            "node": "planner",
            "role": "planner",
            "goal": "分解任务",
        })
        if self._pacing:
            await asyncio.sleep(self._pacing)
        self.emit({"type": "plan", "plan": self._plan})
        self.emit({
            "type": "node_done",
            "node": "planner",
            "summary": f"拆解 {len(self._plan)} 项子任务",
        })
        return {"plan": self._plan}

    # ── 建图与执行 ────────────────────────────────────────────

    def _build(self, checkpointer=None) -> CompiledStateGraph[MultiAgentState, None, MultiAgentState, MultiAgentState]:
        self._nodes = dict(self._node_factory(self))
        unknown = [
            s["node"] for s in self._plan
            if s["role"] != "planner" and s["node"] not in self._nodes
        ]
        if unknown:
            raise ValueError(f"plan 中的节点缺少实现: {unknown}")

        critic = next((s["node"] for s in self._plan if s["role"] == "critic"), None)
        synth = next(s["node"] for s in self._plan if s["role"] == "synthesizer")

        role_by_node = {s["node"]: s["role"] for s in self._plan}

        def _wrap(node: str, fn: NodeFunc) -> NodeFunc:
            """节点级 OTel span：并行角色节点在瀑布图中呈同一父下的并列横条。

            与 emit(node_start/node_done) 事件并存——SSE 实时回放用事件，
            事后持久化调用树用 span，一次包装两份产出。
            """
            role = role_by_node.get(node, node)

            async def _wrapped(state):
                async with async_span(
                    f"agent.multi.node[{node}]",
                    {"agent.node": node, "agent.role": role},
                ):
                    return await fn(state)

            return _wrapped

        g = StateGraph(MultiAgentState)
        g.add_node("planner", _wrap("planner", self._planner))
        for node, fn in self._nodes.items():
            g.add_node(node, _wrap(node, fn))
        g.add_edge(START, "planner")

        # 汇聚点：有 critic 组合则并行节点汇入 critic，否则直通 synthesizer
        fan_in = critic or synth
        parallel_steps = [s for s in self._plan if s["role"] in self.PARALLEL_ROLES]
        if parallel_steps:
            for step in parallel_steps:
                g.add_edge("planner", step["node"])   # 同 superstep 自动并行
                g.add_edge(step["node"], fan_in)      # 并行全部完成后汇聚点才执行
        else:
            g.add_edge("planner", fan_in)
        if critic:
            g.add_edge(critic, synth)
        g.add_edge(synth, END)
        return g.compile(checkpointer=checkpointer) if checkpointer else g.compile()

    async def run(self) -> dict:
        """执行流水线，返回终态（含 elapsed_ms）。

        Checkpointer：thread_id 就绪时每个 superstep 落 checkpoint 并登记
        agent_runs 行——服务崩溃/异常后可经 resume() 断点续跑（已完成节点
        不重复执行，证据卡/事实卡等黑板状态从 checkpoint 原样恢复）。
        """
        t0 = time.monotonic()
        async with async_span(
            "agent.multi.run",
            {
                "agent.team": self._team_info.get("team", ""),
                "agent.task": str(self.context.get("task", ""))[:200],
                "agent.steps": len(self._plan),
            },
        ) as span:
            saver = await _get_checkpointer()
            checkpointer = saver if (saver and self.thread_id) else None
            cfg = ({"configurable": {"thread_id": self.thread_id}}
                   if checkpointer else None)
            if checkpointer:
                try:
                    await runs_register(self)
                except Exception:   # 登记失败不阻断运行（checkpoint 照写）
                    pass
            try:
                final = await self._build(checkpointer).ainvoke({
                    "context": self.context,
                    "plan": [],
                    "evidence": {},
                    "facts": [],
                    "verdict": {},
                    "conclusion": "",
                }, cfg)
                if checkpointer:
                    await runs_set_status(self.thread_id, "done")
            except Exception as exc:
                if checkpointer:
                    await runs_set_status(self.thread_id, "failed", str(exc))
                raise
            final["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            if span.is_recording():
                span.set_attribute("agent.elapsed_ms", final["elapsed_ms"])
        return final

    async def resume(self) -> dict:
        """断点续跑：从最后 checkpoint 恢复黑板状态，仅执行 pending 节点。

        适用：run() 中途服务重启 / 节点异常（agent_runs 行停在 running/failed）。
        图按当前 plan 与节点闭包重建（结构同构即可，checkpoint 只存黑板状态）；
        已完成节点不重复执行（不重复取证、不重复花 token），pending 节点重试。
        无待续节点（如已正常结束）时直接返回落盘终态，resumed=False 标识。
        """
        saver = await _get_checkpointer()
        if saver is None or not self.thread_id:
            raise RuntimeError("断点续跑不可用：未启用 Checkpointer 或缺少 thread_id")
        graph = self._build(checkpointer=saver)
        cfg = {"configurable": {"thread_id": self.thread_id}}
        st = await graph.aget_state(cfg)
        if not st.next:                     # 无待续节点：返回落盘终态
            await runs_set_status(self.thread_id, "done")
            return dict(st.values or {}) | {"resumed": False, "elapsed_ms": 0}
        t0 = time.monotonic()
        try:
            final = await graph.ainvoke(None, cfg)   # None 输入 = 从 checkpoint 继续
            final["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            final["resumed"] = True
            await runs_set_status(self.thread_id, "done")
            return final
        except Exception as exc:
            await runs_set_status(self.thread_id, "failed", str(exc))
            raise

    async def checkpoint_state(self) -> dict | None:
        """checkpoint 快照（诊断/回放入口）：待续节点、黑板键、历史步数。

        仅诊断用，不参与执行；未启用 checkpointer 返回 None。
        """
        saver = await _get_checkpointer()
        if saver is None or not self.thread_id:
            return None
        graph = self._build(checkpointer=saver)
        cfg = {"configurable": {"thread_id": self.thread_id}}
        st = await graph.aget_state(cfg)
        history = [h async for h in graph.aget_state_history(cfg)]
        return {
            "thread_id": self.thread_id,
            "next": list(st.next),                      # 待续节点（空 = 已到终点）
            "keys": sorted((st.values or {}).keys()),   # 黑板现有键（不导出内容）
            "history": len(history),                    # checkpoint 步数（时间旅行深度）
        }
