# -*- coding: utf-8 -*-
"""
多智能体通用执行引擎（业务无关）。

引擎不知道任何业务概念，只负责三件事（对应《多智能体交互.md》§4/§5）：
1. 编排：planner 分解 → plan 中声明为并行角色（retriever / worker /
   graph_agent / data_agent）的节点在同一 superstep 自动并行 → critic
   （可选编制）→ synthesizer；
2. 事件外抛：节点通过 engine.emit() 把过程事件（node_start / node_done /
   evidence / fact / conflict / token / error）推入队列，路由层转成 SSE；
3. 公共设施：LLM 单例、流式调用（带单节点超时）、并行写 state 的 reducer。

业务逻辑全部下沉到场景适配器（见 scenarios/）：适配器提供团队信息、任务计划、
节点实现与初始上下文，引擎按 plan 声明的角色调度。新增业务场景 = 新写一个
适配器并注册，引擎与前端页面零改动。
"""

import asyncio
import time
from typing import Annotated, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

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
    # worker=纯模型执行，graph_agent=图谱事实，data_agent=台账数据查询；
    # 自由编制时由场景按所选智能体声明）
    PARALLEL_ROLES = {"retriever", "worker", "graph_agent", "data_agent"}

    def __init__(
        self,
        context: dict,
        team_info: dict,
        plan: list[dict],
        node_factory: Callable[["MultiAgentEngine"], dict[str, NodeFunc]],
        pacing: float = 0.05,
    ):
        self.context = context
        self._team_info = dict(team_info or {})
        self._plan = [dict(step) for step in plan]
        self._node_factory = node_factory
        self._pacing = pacing
        self._nodes: dict[str, NodeFunc] = {}
        self._llm = None
        self._llm_checked = False

        self.q: asyncio.Queue = asyncio.Queue()
        self._validate_plan()

    def _validate_plan(self) -> None:
        critics = [s for s in self._plan if s["role"] == "critic"]
        synths = [s for s in self._plan if s["role"] == "synthesizer"]
        if len(critics) > 1 or len(synths) != 1:
            raise ValueError(
                "plan 必须包含且仅包含一个 synthesizer，至多一个 critic 节点"
                "（critic 为可选编制，未选时并行节点直通 synthesizer）")

    # ── 基础设施 ──────────────────────────────────────────────

    def emit(self, evt: dict) -> None:
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

    def _build(self):
        self._nodes = dict(self._node_factory(self))
        unknown = [
            s["node"] for s in self._plan
            if s["role"] != "planner" and s["node"] not in self._nodes
        ]
        if unknown:
            raise ValueError(f"plan 中的节点缺少实现: {unknown}")

        critic = next((s["node"] for s in self._plan if s["role"] == "critic"), None)
        synth = next(s["node"] for s in self._plan if s["role"] == "synthesizer")

        g = StateGraph(MultiAgentState)
        g.add_node("planner", self._planner)
        for node, fn in self._nodes.items():
            g.add_node(node, fn)
        g.add_edge(START, "planner")

        # 汇聚点：有 critic 编制则并行节点汇入 critic，否则直通 synthesizer
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
        return g.compile()

    async def run(self) -> dict:
        """执行流水线，返回终态（含 elapsed_ms）。"""
        t0 = time.monotonic()
        final = await self._build().ainvoke({
            "context": self.context,
            "plan": [],
            "evidence": {},
            "facts": [],
            "verdict": {},
            "conclusion": "",
        })
        final["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return final
