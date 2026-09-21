# -*- coding: utf-8 -*-
"""
通用智能体团队场景（adhoc · 默认场景，业务无关、任务类型无关）。

对标开源多智能体项目的「智能体组合」协作范式（AutoGen / CrewAI / MetaGPT /
Magentic-One）：
- 核心智能体内置：Planner（任务规划）与 Synthesizer（结果合成）是任何团队
  都必需的角色，固定内置；
- 能力智能体自由勾选：Retriever（知识库取证）/ DataAgent（台账数据查询）/
  GraphAgent（图谱事实）/ ToolAgent（Function Calling 工具调用，含 MCP 外部
  工具接入）/ Critic（评审质控）由用户按需组队，流水线按组合
  动态装配（不选 Critic 时并行节点直通 Synthesizer，引擎零特判）；
- 任务类型不限：研判、写作、总结、问答皆可——Planner 分解提示词随组合
  切换（选 Retriever → 检索式子任务；否则 → 由成员用模型知识直接执行）；
- 黑板协作：所有节点读写共享 state（evidence / facts / verdict），并行
  节点同一 superstep 执行，结果以素材卡/事实卡落黑板；
- 全链路降级：LLM / 向量库 / 图谱任一不可用都不中断流水线，裁定与模板
  兜底保证可复现。

本场景与引擎、路由、前端页面互不绑定；新业务如需专属编排，按
MultiAgentScenario 适配器范式另行接入。
"""

import asyncio
import json
import re
import time
from datetime import date, timedelta
from typing import Callable, Optional

from sqlalchemy import and_, func, or_, select

from providers.llm import create_llm
from ..engine import MultiAgentEngine
from ..router_service import nl2filter as _nl2filter_extract
from ..router_service import routing_decision
from . import MultiAgentScenario, register

# ── 智能体名册（前端组队选择器据此渲染） ──────────────────────

# 核心智能体：任何团队必需，内置不可取消
CORE_AGENTS = [
    {"id": "planner", "name": "Planner · 任务规划",
     "desc": "用 LLM 把任务分解为可并行子任务，产出执行计划"},
    {"id": "synthesizer", "name": "Synthesizer · 结果合成",
     "desc": "汇总共享黑板素材，流式交付最终成果"},
]

# 能力智能体：自由勾选组合，流水线按组合动态装配
OPTIONAL_AGENTS = [
    {"id": "retriever", "name": "Retriever · 知识库取证",
     "desc": "为每个子任务检索平台全部知识库向量语料（RAG 增强）"},
    {"id": "data_agent", "name": "DataAgent · 数据查询",
     "desc": "查询实体台账结构化数据：总量/分类聚合统计 + 最新明细（真实数据，杜绝编造)"},
    {"id": "graph_agent", "name": "GraphAgent · 图谱事实",
     "desc": "实体图谱关键词检索，产出结构化事实卡"},
    {"id": "tool_agent", "name": "ToolAgent · 工具调用",
     "desc": "Function Calling 自主取证：多轮调用知识库/图谱/台账内置工具与 MCP 外部工具，真实数据杜绝编造"},
    {"id": "critic", "name": "Critic · 评审质控",
     "desc": "素材交叉验证、冲突消解与质量裁定（可选组合）"},
]
OPTIONAL_IDS = [a["id"] for a in OPTIONAL_AGENTS]
DEFAULT_AGENTS = ["retriever", "data_agent", "graph_agent", "critic"]

# ── 示例任务（任务类型多样：研究 / 写作 / 总结，业务无关） ─────

EXAMPLES = [
    {
        "id": "task-overview",
        "title": "研究 · 主题盘点",
        "task": "检索平台知识库语料，盘点其中覆盖了哪些主题领域，归纳每个主题的核心结论，并标注出处。",
    },
    {
        "id": "task-graph",
        "title": "研究 · 图谱探查",
        "task": "在实体图谱中检索与任务描述最相关的实体及其关系，梳理它们的关联结构，评估图谱覆盖面。",
    },
    {
        "id": "task-writing",
        "title": "写作 · 简报撰写",
        "task": "基于知识库语料写一份平台知识覆盖情况简报，分三个小节，关键结论标注引用来源。",
    },
    {
        "id": "task-summary",
        "title": "总结 · 一页纸摘要",
        "task": "把知识库中与本体建模最相关的内容总结成一页纸摘要，突出三个要点。",
    },
]

PLAN_TIMEOUT = 15.0        # 规划 LLM 超时（秒）
SYNTH_TIMEOUT = 180.0      # 合成 LLM 超时（秒）：思考型模型可见输出前静默期长，需放宽
MAX_RETRIEVERS = 4         # 并行子任务上限
KB_TOP_K = 3               # 每个知识库取回分片数
MAX_KBS = 4                # 参与检索的知识库数上限
MAX_CHUNKS = 8             # 全部取回分片总量上限
MIN_SCORE = 0.15           # 向量相似度过滤（1 - distance）
MAX_ENTITIES = 5           # 图谱命中实体数上限
DATA_SAMPLE = 8            # 台账明细卡条数上限
DATA_TYPE_GROUPS = 6       # 台账统计卡按类型聚合展示的分组数上限
DATA_STRONG_MIN = 3        # 类型/名称级命中数达到该值才视为强口径（排除仅描述命中）


def _normalize_agents(agents: Optional[list[str]]) -> list[str]:
    """组合归一化：保留名册内能力智能体 id 与自定义智能体（custom:{id}）。

    内置按名册顺序、自定义按传入顺序排后；None=默认组合。
    """
    if agents is None:
        return list(DEFAULT_AGENTS)
    req = [str(a).strip() for a in (agents or []) if str(a).strip()]
    builtin = [aid for aid in OPTIONAL_IDS if aid in req]
    return builtin + [a for a in req if a.startswith("custom:")]


# ── 场景定义 ─────────────────────────────────────────────────

class UniversalScenario(MultiAgentScenario):
    scenario_id = "universal"
    name = "通用智能体团队"
    business = "任意任务"
    description = (
        "核心智能体内置（Planner / Synthesizer），能力智能体自由组队"
        "（Retriever / DataAgent / GraphAgent / Critic），任务类型不限——研判、写作、"
        "总结、问答皆可。Planner 用 LLM 动态分解，并行执行，流式交付。"
        "对标 AutoGen/CrewAI 的智能体组合范式。"
    )
    adhoc = True   # 自由任务输入（前端据此渲染任务工作台）

    def meta(self) -> dict:
        meta = super().meta()
        meta["agents"] = {                      # 组队选择器名册
            "core": CORE_AGENTS,
            "optional": OPTIONAL_AGENTS,
            "custom": [],                       # 自定义智能体（路由层查库填充）
            "default": DEFAULT_AGENTS,
        }
        return meta

    # ── 目标列表：示例任务预设 ────────────────────────────────

    async def list_targets(self) -> list[dict]:
        return [
            {
                "id": ex["id"],
                "title": ex["title"],
                "subtitle": "示例任务 · 点击填入",
                "headline": ex["task"],
                "details": [],
                "runnable": True,
                "run_label": "填入并运行",
                "task": ex["task"],
            }
            for ex in EXAMPLES
        ]

    # ── 构建引擎：按组合动态装配 ──────────────────────────────

    async def build_engine(self, target_id: str) -> MultiAgentEngine:
        """示例任务入口：按 id 找回任务文本，走统一 build（默认组合）。"""
        task = next((ex["task"] for ex in EXAMPLES if ex["id"] == target_id), None)
        if not task:
            raise KeyError(target_id)
        return await self.build_engine_from_task(task)

    async def build_engine_from_task(
        self, task: str, agents: Optional[list[str]] = None,
        route: Optional[dict] = None,
        on_step: Optional[Callable[[dict], None]] = None,
    ) -> MultiAgentEngine:
        """自由任务入口：两级路由（0.6B）→ 组合归一化 → LLM 动态规划 → 按组合装配 → 引擎。

        on_step：构建期进度回调（每步完成即时调用，事件由路由层转 SSE 推给
        前端，构建期间不再静默）。事件契约：
        - {"type": "step_done", "step": "planner", "elapsed_ms", "summary"}
          —— LLM 任务规划完成（子任务已分解）；
        - {"type": "step_done", "step": "nl2filter", "elapsed_ms", "hit"}
          —— NL2Filter 抽取完成（结果同步记入 route.nl2filter_ms / nl2filter_hit）。

        两级路由（services/multi_agent/router_service.py，可整体降级）：
        - chat（高置信）→ 大模型直答，编排不启动（收益最硬）；
        - data / graph / kb → 精简组合；data 放行第二级 NL2Filter，
          kb 触发 Retriever 检索改写门控；
        - 低置信 / 路由不可用 → 全组合 + 老规则兜底（行为与接入前一致）。
        显式传入非空 agents 时尊重用户组合，路由仅叠加 NL2Filter / 改写门控；
        agents=None 或 [] 时由路由接管组合。

        组合语义：
        - planner / synthesizer：核心内置，必在；
        - retriever：选中 → 每个子任务由 Retriever-i 检索知识库（RAG 增强）；
          未选 → 由 Worker-i 用模型知识直接执行子任务；
        - data_agent：追加台账数据查询节点（聚合统计 + 明细，真实数据）；
        - graph_agent：追加图谱事实节点；
        - critic：追加评审节点（未选时并行节点直通 synthesizer）。
        """
        task = (task or "").strip()
        if not task:
            raise ValueError("任务描述不能为空")

        # ── 第一级：意图路由（失败自动 fallback，永不中断） ─────────
        if route is None:
            _t0 = time.perf_counter()
            route = await routing_decision(task)
            route["elapsed_ms"] = int((time.perf_counter() - _t0) * 1000)
        explicit = bool(agents)                      # 非空列表 = 用户显式组队
        route["manual"] = explicit                   # 手动组队标记：组合由用户指定，路由仅辅助 NL2Filter/改写门控
        if not explicit and route.get("chat_direct"):
            return self._build_direct(task, route)   # chat → 直答，编排不启动
        if not explicit and route.get("agents"):
            agents = list(route["agents"])           # 路由精简组合

        caps = _normalize_agents(agents)
        if not explicit and _looks_like_chart_task(task):
            # 自动组队：任务确有图表/表格诉求时才追加启用中的图表智能体
            # （v6.2 修复：原先无条件追加，「整理本周告警」等无图表意图的任务
            # 也会拉上图表智能体空跑；是否真成图仍由其节点内判断兜底）
            chart_aid = await _find_chart_agent_id()
            if chart_aid and f"custom:{chart_aid}" not in caps:
                caps.append(f"custom:{chart_aid}")
        use_retrieval = "retriever" in caps
        _pt0 = time.perf_counter()
        goals = await self._plan_goals(task, use_retrieval)
        if on_step:   # 规划一完成即外抛（此刻 NL2Filter 尚未开始）
            on_step({"type": "step_done", "step": "planner",
                     "elapsed_ms": int((time.perf_counter() - _pt0) * 1000),
                     "summary": f"LLM 分解 {len(goals)} 个并行子任务"})

        # ── 第二级：NL2Filter（仅 data 类放行；抽不出 → DataAgent 词频老路兜底） ──
        nl_filter: Optional[dict] = None
        if route.get("nl2filter") and "data_agent" in caps:
            _t0 = time.perf_counter()
            nl_filter = await _nl2filter_extract(task)
            route["nl2filter_ms"] = int((time.perf_counter() - _t0) * 1000)
            route["nl2filter_hit"] = nl_filter is not None
            if on_step:   # 抽取一完成即外抛，不等后续装配
                on_step({"type": "step_done", "step": "nl2filter",
                         "elapsed_ms": route["nl2filter_ms"],
                         "hit": route["nl2filter_hit"]})
        rewrite_gate = bool(route.get("rewrite_gate")) and use_retrieval

        # 自定义智能体（智能体配置页，custom:{id}）：查库取启用配置，防脏 id
        custom_agents = await _load_custom_agents(caps)

        # 计划：子任务执行者（并行） → 图谱（可选） → 评审（可选） → 合成（必在）
        plan: list[dict] = [
            {
                "id": f"w{i}",
                "node": f"worker_{i}",
                "role": "retriever" if use_retrieval else "worker",
                "goal": g,
            }
            for i, g in enumerate(goals, start=1)
        ]
        if "data_agent" in caps:
            plan.append({"id": "d1", "node": "data_agent", "role": "data_agent",
                         "goal": "实体台账结构化查询与统计"})
        if "graph_agent" in caps:
            plan.append({"id": "g1", "node": "graph_agent", "role": "graph_agent",
                         "goal": "实体图谱关联事实"})
        if "tool_agent" in caps:
            plan.append({"id": "t1", "node": "tool_agent", "role": "tool_agent",
                         "goal": "Function Calling 工具调用取证"})
        for i, ca in enumerate(custom_agents, start=1):
            plan.append({"id": f"cu{i}", "node": f"custom_{i}", "role": "custom",
                         "goal": ca["goal"], "agent": ca})
        if "critic" in caps:
            plan.append({"id": "c1", "node": "critic", "role": "critic",
                         "goal": "素材交叉验证与质量裁定"})
        plan.append({"id": "s1", "node": "synthesizer", "role": "synthesizer",
                     "goal": "结果合成（流式）"})

        # 团队名册（前端时间线按此渲染成员名）
        worker_prefix = "Retriever" if use_retrieval else "Worker"
        members = (
            [{"node": "planner", "role": "planner", "name": "Planner · 任务规划"}]
            + [
                {"node": p["node"], "role": p["role"],
                 "name": f"{worker_prefix}-{i} · {p['goal'][:10]}…"}
                for i, p in enumerate([s for s in plan if s["role"] in ("retriever", "worker")], start=1)
            ]
            + [
                {"node": p["node"], "role": p["role"],
                 "name": (p["agent"]["name"] if p["role"] == "custom" else {
                          "data_agent": "DataAgent · 数据查询",
                          "graph_agent": "GraphAgent · 图谱事实",
                          "tool_agent": "ToolAgent · 工具调用",
                          "critic": "Critic · 评审质控",
                          "synthesizer": "Synthesizer · 结果合成"}[p["role"]])}
                for p in plan if p["role"] in ("data_agent", "graph_agent", "tool_agent",
                                               "custom", "critic", "synthesizer")
            ]
        )
        cap_names = [a["name"].split(" · ")[0] for a in OPTIONAL_AGENTS if a["id"] in caps]
        cap_names += [ca["name"] for ca in custom_agents]
        cap_label = " + ".join(cap_names) or "纯模型协作"
        team_info = {
            "team": f"通用智能体团队（{cap_label}）",
            "members": members,
        }

        team_info["route"] = route   # 两级路由决策透出（前端可渲染，不认识则忽略）

        return MultiAgentEngine(
            context={"task": task, "agents": caps, "route": route},
            team_info=team_info,
            plan=plan,
            node_factory=lambda eng: self._make_nodes(
                eng, plan, task, nl_filter=nl_filter, rewrite_gate=rewrite_gate),
            pacing=0.1,
        )

    # ── chat 直答团（第一级路由命中：编排不启动，收益最硬） ─────

    def _build_direct(self, task: str, route: dict) -> MultiAgentEngine:
        """跳过 LLM 规划与全部并行角色，单 Synthesizer 节点直答。

        复用引擎与 SSE 契约（token 流式事件不变）：时间线 Planner → Synthesizer，
        无素材/评审环节，合成官直接回答用户问题。
        """
        plan = [{"id": "s1", "node": "synthesizer", "role": "synthesizer",
                 "goal": "大模型直答（路由命中 chat，编排未启动）"}]
        team_info = {
            "team": f"直答 · 意图路由（chat · 置信 {route.get('confidence', 0):.2f}）",
            "members": [
                {"node": "planner", "role": "planner", "name": "Planner · 任务规划"},
                {"node": "synthesizer", "role": "synthesizer", "name": "Synthesizer · 直答"},
            ],
            "route": route,
        }
        return MultiAgentEngine(
            context={"task": task, "agents": [], "route": route},
            team_info=team_info,
            plan=plan,
            node_factory=lambda eng: {"synthesizer": self._make_direct_answer(eng, task)},
            pacing=0.05,
        )

    def _make_direct_answer(self, eng: MultiAgentEngine, task: str):
        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "synthesizer",
                      "role": "synthesizer", "goal": "大模型直答"})
            conclusion, reason = "", ""
            try:
                conclusion = await eng.llm_stream(
                    "你是平台助手，直接回答用户问题：要点式中文回答，条理清晰。"
                    "不要编造平台内数据（台账数值/图谱关系），涉及平台内数据时"
                    "建议用户改用多智能体研判。",
                    task,
                    on_token=lambda t: eng.emit({"type": "token", "content": t}),
                    timeout=SYNTH_TIMEOUT,
                )
            except RuntimeError as exc:   # llm_stream：LLM 未配置
                reason = str(exc) or "LLM 未配置"
            except Exception as exc:      # 超时 / 网络 / SDK 异常
                reason = f"LLM 调用失败（{type(exc).__name__}）"
            degraded = not conclusion
            if degraded:
                conclusion = (f"直答服务暂不可用（{reason or '未知原因'}），请稍后重试；"
                              "也可在多智能体页选择完整智能体组合发起研判。")
                eng.emit({"type": "error", "content": conclusion})
            eng.emit({"type": "node_done", "node": "synthesizer",
                      "summary": "直答完成（大模型直连）" if not degraded else "直答降级"})
            return {"conclusion": conclusion}

        return _fn

    # ── 动态规划（Plan-and-Solve，随组合切换分解策略） ─────────

    async def _plan_goals(self, task: str, use_retrieval: bool) -> list[str]:
        """LLM 分解任务为并行子任务；失败回落默认分解。

        选了 Retriever → 子任务是「可检索的资料问题」；
        未选 Retriever → 子任务是「成员用自身知识完成的关键环节」。
        """
        if use_retrieval:
            system = (
                "你是多智能体团队的规划师。把用户的任务拆成 1~4 个可并行执行的"
                "资料检索子任务（简单任务只拆 1 个即可，不要为凑数硬拆）。每个子任务"
                "是一句具体、可检索的中文问题（面向知识库语料或结构化数据）。"
                "只输出 JSON 数组，格式："
                '[{"goal": "子任务描述"}]，不要输出任何其他文字。'
            )
        else:
            system = (
                "你是多智能体团队的规划师。把用户的任务拆成 1~4 个可并行完成的"
                "关键子任务（如不同维度、章节或要点，由成员用自身知识直接完成，"
                "不依赖外部检索；简单任务只拆 1 个即可，不要为凑数硬拆）。"
                "只输出 JSON 数组，格式："
                '[{"goal": "子任务描述"}]，不要输出任何其他文字。'
            )
        goals: list[str] = []
        try:
            llm = create_llm()
            raw = await asyncio.wait_for(
                llm.ainvoke([("system", system), ("human", task)]),
                timeout=PLAN_TIMEOUT,
            )
            text = getattr(raw, "content", "") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            m = re.search(r"\[.*\]", text, re.S)
            if m:
                arr = json.loads(m.group(0))
                goals = [
                    str(it.get("goal", "")).strip()
                    for it in arr if isinstance(it, dict) and str(it.get("goal", "")).strip()
                ]
        except Exception:
            goals = []
        goals = goals[:MAX_RETRIEVERS]
        if not goals:
            goals = (
                [
                    f"检索与任务直接相关的资料与结论：{task[:80]}",
                    f"检索与「{task[:40]}」相关的背景定义、关联概念与事实",
                ]
                if use_retrieval
                else [
                    f"梳理任务要求，给出总体框架与关键要点：{task[:80]}",
                    "补充细节、示例与风险提示，完善任务成果",
                ]
            )
        return goals

    # ── 节点实现（闭包 engine：emit / llm_stream） ────────────

    def _make_nodes(self, eng: MultiAgentEngine, plan: list[dict], task: str,
                    nl_filter: Optional[dict] = None,
                    rewrite_gate: bool = False) -> dict:
        nodes = {}
        for step in plan:
            if step["role"] == "retriever":
                nodes[step["node"]] = self._make_kb_retriever(eng, step, rewrite_gate)
            elif step["role"] == "worker":
                nodes[step["node"]] = self._make_worker(eng, step, task)
            elif step["role"] == "custom":
                nodes[step["node"]] = self._make_custom_agent(eng, step, task)
            elif step["role"] == "data_agent":
                nodes[step["node"]] = self._make_data_agent(eng, task, nl_filter)
            elif step["role"] == "tool_agent":
                nodes[step["node"]] = self._make_tool_agent(eng, task)
            elif step["role"] == "graph_agent":
                nodes[step["node"]] = self._make_graph_agent(eng, task)
            elif step["role"] == "critic":
                nodes[step["node"]] = self._make_critic(eng, task)
            elif step["role"] == "synthesizer":
                nodes[step["node"]] = self._make_synthesizer(eng, task)
        return nodes

    def _make_kb_retriever(self, eng: MultiAgentEngine, step: dict,
                           rewrite_gate: bool = False):
        """Retriever-i：检索增强执行——检索知识库语料，命中即素材卡。

        rewrite_gate（kb 模式路由触发）：检索前对子任务做一次轻量改写，
        改写失败自动回落原查询——门控不拦人。
        """
        node, goal, domain = step["node"], step["goal"], step["id"]

        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": node, "role": "retriever", "goal": goal})
            query = await _rewrite_query(eng, goal) if rewrite_gate else goal
            chunks = await _search_kb_chunks(query)
            cards = [_chunk_card(f"ev-{node}-{i}", domain, c)
                     for i, c in enumerate(chunks)]
            eng.emit({"type": "evidence", "domain": domain, "cards": cards})
            summary = (f"全库检索命中 {len(cards)} 条语料"
                       if cards else "未命中知识库语料")
            if rewrite_gate and query != goal:
                summary += "（改写门控已启用）"
            eng.emit({"type": "node_done", "node": node, "summary": summary})
            return {"evidence": {domain: cards}}

        return _fn

    def _make_worker(self, eng: MultiAgentEngine, step: dict, task: str):
        """Worker-i：纯模型执行——用自身知识完成子任务，产出行家卡。"""
        node, goal, domain = step["node"], step["goal"], step["id"]

        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": node, "role": "worker", "goal": goal})
            text = ""
            try:
                text = await eng.llm_stream(
                    "你是多智能体团队的执行成员，用自身知识独立完成分配的子任务，"
                    "为团队最终成果提供素材。直接输出要点式中文结果（不超过 150 字），"
                    "不要寒暄或重复任务。",
                    f"总体任务：{task}\n你的子任务：{goal}",
                )
            except Exception:
                text = ""
            card = {
                "id": f"ev-{node}",
                "domain": domain,
                "grade": "model_output",
                "source": "Worker · 模型生成",
                "title": goal[:24],
                "summary": text[:160] if text else "（执行失败，无产出）",
                "quote": "",
                "stance": "neutral",
            }
            cards = [card] if text else []
            eng.emit({"type": "evidence", "domain": domain, "cards": cards})
            eng.emit({
                "type": "node_done", "node": node,
                "summary": "子任务完成（模型生成）" if text else "子任务执行失败（LLM 未配置）",
            })
            return {"evidence": {domain: cards}}

        return _fn

    def _make_custom_agent(self, eng: MultiAgentEngine, step: dict, task: str):
        """Custom-i：智能体配置页定义的自定义智能体入队执行（并行成员）。

        与 Worker 的差异：人设（system_prompt）来自智能体配置；绑定了知识库
        时先检索该库语料（仅限绑定库，区别于 Retriever 的全库检索），语料
        以素材卡落黑板后再按人设完成子任务——配置化智能体直接参与协作。

        use_tools=1（配置页「工具调用」开关）时升级为 Function Calling 工具
        循环（与 ToolAgent 同机制）：内置工具（kb_search/graph_search/
        data_query）+ MCP 外部工具（如开源图表 @antv/mcp-server-chart）由
        LLM 自主调用；工具产出以事实卡落黑板，图表工具返回的图片 URL 额外
        提取为图表卡（grade=chart_result，前端「图表产出」tab 内嵌渲染）。
        """
        node, domain = step["node"], step["id"]
        agent = step.get("agent") or {}
        name = agent.get("name") or "自定义智能体"
        goal = step["goal"]
        persona = (agent.get("system_prompt") or "").strip()
        kb_id = agent.get("kb_id") or ""
        use_tools = bool(agent.get("use_tools"))

        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": node, "role": "custom",
                      "goal": f"{name} · {goal}"})
            cards: list[dict] = []
            kb_ctx = ""
            if kb_id:   # 绑定知识库：先取证（仅绑定库），语料进 prompt 并落黑板
                chunks = await _search_kb_chunks(f"{task}\n{goal}", kb_id=kb_id)
                cards = [_chunk_card(f"ev-{node}-{i}", domain, c)
                         for i, c in enumerate(chunks)]
                if cards:
                    kb_ctx = "\n".join(f"- {c['summary']}" for c in cards)
                    eng.emit({"type": "evidence", "domain": domain, "cards": cards})
            system = persona or (
                "你是多智能体团队的执行成员，用自身知识独立完成分配的子任务。")
            user = f"总体任务：{task}\n你的子任务：{goal}"
            if kb_ctx:
                user += f"\n\n绑定知识库相关语料：\n{kb_ctx}"

            text, tool_facts, tool_note = "", [], ""
            if use_tools and eng.llm() is not None:
                # 工具循环路径：人设 + 平台工具（内置 + MCP），LLM 自主决定调用
                system += ("可以调用平台提供的工具完成任务；需要真实数据或图表/表格"
                           "产出时必须调用工具获取，禁止编造数据；工具调用完成后，"
                           "输出不超过 150 字的中文要点总结。")
                try:
                    from services.agent_loop import run_tool_loop
                    from services.tool_registry import PlatformTools

                    loop = None
                    mcp_note = ""
                    async with PlatformTools() as pt:
                        if pt.mcp_status:
                            ok_n = sum(1 for s in pt.mcp_status if s["ok"])
                            mcp_note = (f"，MCP 接入 {ok_n}/"
                                        f"{len(pt.mcp_status)} 个外部服务器")
                        if pt.registry.names():
                            loop = await run_tool_loop(
                                eng.llm(), pt.registry, system, user,
                                on_event=lambda e: eng.emit({"node": node, **e}))
                    if loop is not None:
                        chart_facts = _chart_facts(loop)
                        text = loop.final_text or ""
                        tool_facts = _tool_facts(loop) + chart_facts
                        used: dict[str, int] = {}
                        for c in loop.calls:
                            used[c.name] = used.get(c.name, 0) + 1
                        call_desc = ("、".join(f"{k}×{v}" for k, v in used.items())
                                     or "未调用工具")
                        tool_note = (f"，Function Calling：{call_desc}" + mcp_note
                                     + (f"，产出图表 {len(chart_facts)} 张"
                                        if chart_facts else ""))
                except Exception:
                    # 工具链路故障 → 回落纯人设（不因 MCP 抖动拖垮节点）
                    text, tool_facts, tool_note = "", [], ""
            if not text and not tool_facts:
                # 未启用工具 / 工具循环不可用 → 纯人设流式（原行为，token 实时可见）
                system += "直接输出要点式中文结果（不超过 150 字），不要寒暄或重复任务。"
                try:
                    text = await eng.llm_stream(system, user)
                except Exception:
                    text = ""
            if tool_facts:
                eng.emit({"type": "fact", "facts": tool_facts})
            card = {
                "id": f"ev-{node}",
                "domain": domain,
                "grade": "model_output",
                "source": f"{name} · 智能体配置",
                "title": goal[:24],
                "summary": text[:160] if text else "（执行失败，无产出）",
                "quote": "",
                "stance": "neutral",
            }
            out_cards = cards + ([card] if text else [])
            if text:
                eng.emit({"type": "evidence", "domain": domain, "cards": [card]})
            eng.emit({
                "type": "node_done", "node": node,
                "summary": (f"{name} 完成（模型生成{tool_note}）"
                            + (f"，引用绑定库语料 {len(cards)} 条" if cards else "")
                            if (text or tool_facts)
                            else f"{name} 执行失败（LLM 未配置）"),
            })
            return {"evidence": {domain: out_cards}, "facts": tool_facts}

        return _fn

    def _make_graph_agent(self, eng: MultiAgentEngine, task: str):
        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "graph_agent", "role": "graph_agent",
                      "goal": "实体图谱关联事实"})
            facts = await _graph_facts(task)
            eng.emit({"type": "fact", "facts": facts})
            eng.emit({
                "type": "node_done", "node": "graph_agent",
                "summary": (f"图谱命中 {len(facts)} 个关联实体"
                            if facts else "图谱未命中相关实体"),
            })
            return {"facts": facts}

        return _fn

    def _make_data_agent(self, eng: MultiAgentEngine, task: str,
                         nl_filter: Optional[dict] = None):
        goal = "NL2Filter 结构化取数与统计" if nl_filter else "实体台账结构化查询与统计"

        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "data_agent", "role": "data_agent",
                      "goal": goal})
            facts = await _data_facts(task, nl_filter)
            eng.emit({"type": "fact", "facts": facts})
            eng.emit({
                "type": "node_done", "node": "data_agent",
                "summary": (f"台账命中，产出 {len(facts)} 张数据卡"
                            f"（1 统计 + {len(facts) - 1} 明细）"
                            if facts else "台账未查询到结构化数据（NL2Filter 未命中，词频兜底亦空）"
                            if nl_filter else "台账未查询到结构化数据"),
            })
            return {"facts": facts}

        return _fn

    def _make_tool_agent(self, eng: MultiAgentEngine, task: str):
        """ToolAgent：Function Calling 自主取证。

        与 Retriever / DataAgent / GraphAgent 的本质差异：后三者的取数路径
        由代码写死（节点内直接查询），ToolAgent 把平台能力注册为工具，由 LLM
        在工具调用循环（services/agent_loop.py）中自主决定「调什么、调几次、
        带什么参数」——原生 Function Calling / Tool Use 机制；内置工具之外，
        还可通过 MCP（settings.MCP_SERVERS）无差别接入外部工具服务器。
        每次工具调用产出一张工具事实卡（grade=tool_result），与其它成员
        同一黑板协作，供合成官引用编号。
        """
        node = "tool_agent"

        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": node, "role": "tool_agent",
                      "goal": "Function Calling 工具调用取证"})
            facts: list[dict] = []
            try:
                from services.agent_loop import run_tool_loop
                from services.tool_registry import PlatformTools

                if eng.llm() is None:
                    raise RuntimeError("LLM 未配置")

                mcp_note = ""
                async with PlatformTools() as pt:
                    if not pt.registry.names():
                        raise RuntimeError("工具注册表为空")
                    if pt.mcp_status:
                        ok_n = sum(1 for s in pt.mcp_status if s["ok"])
                        mcp_note = f"，MCP 接入 {ok_n}/{len(pt.mcp_status)} 个外部服务器"
                    loop = await run_tool_loop(
                        eng.llm(), pt.registry,
                        system=(
                            "你是多智能体团队的「工具执行官」，通过调用工具获取"
                            "平台真实数据支撑团队任务。规则：1) 优先调用工具取"
                            "真实数据，禁止编造；2) 相同参数的工具不要重复调用；"
                            "3) 数据足够后输出不超过 120 字的中文要点总结，"
                            "说明拿到了哪些关键信息。"
                        ),
                        user=f"团队总体任务：{task}",
                        on_event=lambda e: eng.emit({"node": node, **e}),
                    )
                facts = _tool_facts(loop)
                used: dict[str, int] = {}
                for c in loop.calls:
                    used[c.name] = used.get(c.name, 0) + 1
                call_desc = "、".join(f"{k}×{v}" for k, v in used.items()) or "未调用工具"
                summary = (f"Function Calling {loop.iterations} 轮：{call_desc}，"
                           f"产出 {len(facts)} 张工具事实卡{mcp_note}")
            except RuntimeError as exc:   # LLM 未配置 / 工具表为空
                summary = f"工具循环未执行（{exc}）"
            except Exception as exc:      # 工具循环兜底：不中断流水线
                summary = f"工具循环失败（{type(exc).__name__}: {exc}）"

            eng.emit({"type": "fact", "facts": facts})
            eng.emit({"type": "node_done", "node": node, "summary": summary})
            return {"facts": facts}

        return _fn

    def _make_critic(self, eng: MultiAgentEngine, task: str):
        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "critic", "role": "critic",
                      "goal": "素材交叉验证与质量裁定"})
            evidence, facts = state["evidence"], state["facts"]

            # 1) 确定性裁定（素材充分性硬规则，永远先算，可复现）
            verdict = _universal_verdict(evidence, facts)

            # 2) LLM 解释增强（失败静默降级）
            if verdict["conflicts"]:
                try:
                    verdict["comment"] = await eng.llm_stream(
                        "你是多智能体团队的评审官。请用不超过 3 句中文解释本次评审依据"
                        "（素材充分性、保守原则）；不要输出 JSON、列表或标题。",
                        f"任务：{task}\n\n硬规则裁定：{verdict['suggest_label']}；\n"
                        f"素材卡 {sum(len(v) for v in evidence.values())} 条，"
                        f"图谱事实 {len(facts)} 条；存疑点："
                        f"{json.dumps(verdict['conflicts'], ensure_ascii=False)}",
                    )
                except Exception:
                    verdict["comment"] = ""

            eng.emit({
                "type": "conflict",
                "conflicts": verdict["conflicts"],
                "suggestion": verdict["suggestion"],
                "suggest_label": verdict["suggest_label"],
                "need_human": verdict["need_human"],
                "confidence": verdict["confidence"],
                "comment": verdict["comment"],
            })
            eng.emit({
                "type": "node_done", "node": "critic",
                "summary": (f"{len(verdict['conflicts'])} 处存疑 → {verdict['suggest_label']}"
                            f"（置信度 {verdict['confidence']}）"),
            })
            return {"verdict": verdict}

        return _fn

    def _make_synthesizer(self, eng: MultiAgentEngine, task: str):
        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "synthesizer", "role": "synthesizer",
                      "goal": "结果合成（流式）"})
            evidence, facts, verdict = state["evidence"], state["facts"], state["verdict"]

            conclusion, fail_reason = await _synth_llm(eng, task, verdict, evidence, facts)

            # 引用自检（与评估器 citation_valid 同口径）：事实卡非空时结论必须带
            # 合法 [事实n]（n ≤ 事实卡数），无事实卡时禁止出现事实引用。
            # 首轮不合规 → 带病因重新合成一次（reset 重放：先清空前端已渲染的草稿）。
            if conclusion and not _citation_ok(conclusion, len(facts)):
                eng.emit({"type": "token", "content": "", "reset": True})
                eng.emit({"type": "error", "content":
                          f"引用自检未通过（{_citation_issue(conclusion, len(facts))}），正在重新合成…"})
                fixed, _ = await _synth_llm(eng, task, verdict, evidence, facts,
                                            fix_citation=conclusion)
                if _citation_ok(fixed, len(facts)):
                    conclusion = fixed

            # 终极兜底：重试后引用仍不合规且事实卡非空 → 追加引用索引（确定性可溯源）。
            if conclusion and not _citation_ok(conclusion, len(facts)) and facts:
                conclusion = _append_citation_index(conclusion, facts)
                eng.emit({"type": "error", "content": "重新合成后引用仍不合规，已追加引用索引兜底"})

            degraded = not conclusion
            if degraded:
                conclusion = _template_conclusion(task, verdict, evidence, facts)
                eng.emit({"type": "error",
                          "content": f"{fail_reason or 'LLM 未配置'}，已降级为模板结论（评审仍为确定性规则）"})

            eng.emit({
                "type": "node_done", "node": "synthesizer",
                "summary": "成果已合成（LLM 流式）" if not degraded else "成果已合成（模板降级）",
            })
            return {"conclusion": conclusion}

        return _fn


async def _load_custom_agents(caps: list[str]) -> list[dict]:
    """组合中的自定义智能体（custom:{agent_id}）→ 启用中的配置（防脏 id）。

    id 无效或已停用的项静默跳过，不中断流水线；输出保持用户勾选顺序。
    """
    ids = [a.split(":", 1)[1] for a in caps if a.startswith("custom:")]
    if not ids:
        return []
    from database import async_session
    from models import Agent

    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(Agent).where(Agent.id.in_(ids), Agent.is_enabled == 1)
            )).scalars().all()
        by_id = {r.id: r for r in rows}
        out: list[dict] = []
        for aid in ids:
            r = by_id.get(aid)
            if not r:
                continue
            out.append({
                "id": aid,
                "name": r.name,
                "goal": (r.description or "").strip()[:40] or f"{r.name} 专项分析",
                "system_prompt": r.system_prompt or "",
                "kb_id": (r.kb_id or "").strip(),
                "use_tools": bool(int(getattr(r, "use_tools", 0) or 0)),
            })
        return out
    except Exception:
        return []


# ── 图表需求自动组队 + 图表卡提取（开源图表 MCP @antv/mcp-server-chart） ──

# 图表/表格意图词（自动组队门控：任务文本命中才追加图表智能体，成图与否仍由节点内判断）
_CHART_INTENT_RE = re.compile(
    r"图表|画图|绘图|成图|可视化|柱状|条形|折线|曲线图|饼图|环形图|散点|雷达|漏斗|"
    r"直方|词云|桑基|瀑布图|热力|趋势|占比|分布图|统计表|报表|表格|chart|spreadsheet",
    re.IGNORECASE)

# 图表工具产出中的图片 URL（AntV 图表 MCP 返回「成功生成…: <图片URL>」）
_CHART_URL_RE = re.compile(
    r"https?://[^\s<>\"'（）《》【】，。]+?\.(?:png|jpe?g|webp|gif|svg|html?)"
    r"(?:\?[^\s<>\"'（）《》【】，。]*)?", re.IGNORECASE)


def _looks_like_chart_task(task: str) -> bool:
    """任务文本是否含图表/表格需求（自动组队用）。"""
    return bool(_CHART_INTENT_RE.search(task or ""))


async def _find_chart_agent_id() -> str:
    """启用中的图表智能体 id：固定 id（agent_chart，种子脚本创建）优先，
    名称含「图表」的自定义智能体兜底；异常一律返回空串（不阻断组队）。"""
    from database import async_session
    from models import Agent
    from services.agent_service import CHART_AGENT_ID

    try:
        async with async_session() as db:
            row = await db.get(Agent, CHART_AGENT_ID)
            if row is None or not row.is_enabled:
                row = (await db.execute(
                    select(Agent).where(Agent.is_enabled == 1,
                                        Agent.name.ilike("%图表%"))
                )).scalars().first()
            return (row.id if row is not None and row.is_enabled else "")
    except Exception:
        return ""


def _chart_facts(loop) -> list[dict]:
    """工具循环中图表类 MCP 工具的图片产出 → 图表事实卡（grade=chart_result）。

    URL 提取自工具返回文本；图表卡经 fact 事件落黑板，前端「图表产出」tab
    内嵌渲染图片，合成官与协作回放亦可引用。
    """
    facts: list[dict] = []
    for c in loop.calls:
        rtext = c.result_text or ""
        if c.error or not rtext:
            continue
        m = _CHART_URL_RE.search(rtext)
        if not m and "generate_" in (c.name or ""):
            # 图表工具（mcp_<srv>_generate_*）URL 无扩展名时兜底（AntV 返回 /original 结尾）
            m = re.search(r"https?://[^\s<>\"'（）《》【】，。]+", rtext)
        if not m:
            continue
        url = m.group(0).rstrip("。，；,;.")
        args = c.arguments if isinstance(c.arguments, dict) else {}
        title = str(args.get("title") or "").strip() or c.name
        facts.append({
            "id": f"fact-chart-{len(facts) + 1}",
            "grade": "chart_result",
            "title": f"{title}（{c.name}）",
            "detail": f"图表工具 {c.name} 已生成可视化图片：{url}",
            "image": url,
        })
        if len(facts) >= 8:
            break
    return facts


# ── 平台通用取证（业务无关） ──────────────────────────────────

async def _search_kb_chunks(query: str, kb_id: str = "") -> list[dict]:
    """跨全部知识库（或指定绑定库）的向量检索（VectorDataService），失败逐库降级。"""
    from database import async_session
    from models import KnowledgeBase
    from services.vector_data_service import VectorDataService

    try:
        stmt = select(KnowledgeBase).limit(MAX_KBS)
        if kb_id:
            stmt = stmt.where(KnowledgeBase.id == kb_id)
        async with async_session() as db:
            kbs = (await db.execute(stmt)).scalars().all()
    except Exception:
        return []

    out: list[dict] = []
    for kb in kbs:
        try:
            data = await VectorDataService.similarity_test(kb.id, query, top_k=KB_TOP_K)
        except Exception:
            continue   # 向量库未配置 / 单库失败 → 跳过
        for it in data.get("items", []):
            text = (it.get("chunk_text") or "").strip()
            score = it.get("score") or 0
            if not text or score < MIN_SCORE:
                continue
            out.append({
                "kb_name": kb.name,
                "file_name": it.get("file_name") or "未命名文件",
                "page": it.get("page_number"),
                "score": score,
                "text": text,
            })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:MAX_CHUNKS]


def _chunk_card(cid: str, domain: str, c: dict) -> dict:
    src = f"知识库《{c['kb_name']}》 · {c['file_name']}"
    if c.get("page"):
        src += f" 第{c['page']}页"
    head = c["text"][:24] + ("…" if len(c["text"]) > 24 else "")
    return {
        "id": cid, "domain": domain, "grade": "sourced_doc",
        "source": src, "title": head,
        # 280 字：回归发现 160 字截断会掐掉部门归属/阈值数值等细节，
        # 合成官"看不到"即"取偏"——放宽截断让关键细节进 prompt（factuality P0）
        "summary": c["text"][:280] + ("…" if len(c["text"]) > 280 else ""),
        "quote": f"相似度 {c['score']:.2f}",
        "stance": "neutral",
    }


def _tool_facts(loop) -> list[dict]:
    """工具循环调用记录 → 工具事实卡（grade=tool_result，前端「工具产出」tab）。

    detail 优先展示工具返回的 note（结论性摘要），其余结构化字段序列化截断，
    保证合成官与前端都能拿到可引用的真实数据摘要。
    """
    facts: list[dict] = []
    for i, c in enumerate(loop.calls, start=1):
        if c.error:
            detail = f"调用失败：{c.error}"
        elif isinstance(c.raw, dict):
            note = str(c.raw.get("note") or "")
            payload = {k: v for k, v in c.raw.items() if k != "note"}
            blob = json.dumps(payload, ensure_ascii=False)
            detail = f"{note}｜{blob[:280]}" if note else blob[:300]
        else:
            detail = (c.result_text or "")[:300]
        args = json.dumps(c.arguments, ensure_ascii=False)[:80]
        facts.append({
            "id": f"fact-tool-{i}",
            "grade": "tool_result",
            "title": f"工具 {c.name}（{args}）",
            "detail": detail or "（空结果）",
        })
    return facts


async def _graph_facts(task: str) -> list[dict]:
    """实体图谱关键词检索：命中实体 + 其关系链 → 图谱事实卡。业务无关。"""
    from database import async_session
    from models import Entity, Relation

    kws = _keywords(task)
    if not kws:
        return []
    try:
        async with async_session() as db:
            cond = or_(*[
                or_(Entity.name.ilike(f"%{kw}%"), Entity.description.ilike(f"%{kw}%"))
                for kw in kws
            ])
            entities = (await db.execute(select(Entity).where(cond).limit(MAX_ENTITIES))).scalars().all()

            facts: list[dict] = []
            for e in entities[:MAX_ENTITIES]:
                rels = (await db.execute(
                    select(Relation).where(or_(Relation.source_entity_id == e.id,
                                               Relation.target_entity_id == e.id))
                )).scalars().all()
                peer_ids = sorted({r.source_entity_id if r.target_entity_id == e.id
                                   else r.target_entity_id for r in rels})[:6]
                peers: dict[str, str] = {}
                if peer_ids:
                    for p in (await db.execute(
                        select(Entity).where(Entity.id.in_(peer_ids))
                    )).scalars().all():
                        peers[p.id] = f"{p.entity_type}「{p.name}」"
                links = [
                    f"—{r.relation_type}→ {peers[r.target_entity_id]}"
                    if r.target_entity_id in peers and r.source_entity_id == e.id
                    else f"←{r.relation_type}— {peers[r.source_entity_id]}"
                    for r in rels
                    if (r.source_entity_id in peers or r.target_entity_id in peers)
                ][:4]
                detail = f"实体类型「{e.entity_type}」"
                if e.description:
                    detail += f"，{e.description[:80]}"
                if links:
                    detail += "；关系：" + "；".join(links)
                facts.append({
                    "id": f"fact-{e.id}",
                    "grade": "graph_fact",
                    "title": f"{e.entity_type} · {e.name}",
                    "detail": detail,
                })
            return facts
    except Exception:
        return []


def _type_scope_text(total: int, type_rows: list) -> str:
    """类型分组聚合 → 统计口径文案（业务无关）。

    主类型占绝对多数（≥60%）时以主类型为统计焦点，其余类型标注为关键词
    关联实体——避免「告警规则/概念」等名称沾边的知识型实体混入总量叙事
    （信息仍完整展示，仅口径明确：统计以主类型为准）。
    """
    if not type_rows:
        return f"共 {total} 条记录"
    if len(type_rows) == 1:
        t, c = type_rows[0]
        return f"共 {total} 条记录，类型「{t or '未分类'}」{c} 条"
    groups = "、".join(f"{t or '未分类'} {c} 条"
                       for t, c in type_rows[:DATA_TYPE_GROUPS])
    top_t, top_n = type_rows[0]
    if total > 0 and top_n * 10 >= total * 6:
        others = "、".join(f"{t or '未分类'} {c} 条"
                           for t, c in type_rows[1:][:4])
        return (f"共命中 {total} 条相关实体，主类型「{top_t or '未分类'}」{top_n} 条"
                f"（统计以此为准）；其余 {total - top_n} 条为关键词关联实体"
                f"（{others}）；全部类型：{groups}")
    return f"共 {total} 条记录；按实体类型：{groups}"


async def _data_facts(task: str, nl_filter: Optional[dict] = None) -> list[dict]:
    """实体台账结构化查询（业务无关）：关键词命中 → 聚合统计 + 明细事实卡。

    两级路由接线：nl_filter 非空时先走 NL2Filter 结构化取数
    （_data_facts_by_filter），未命中 / 抽错 / 异常 → 回落词频老路（本函数主体），
    即「抽不出/抽错 → 词频老路兜底」。

    与 GraphAgent 的差异：不只找「关联实体」，而是给出台账口径的真实数据——
    命中总量、按实体类型聚合计数、最新若干条记录的关键属性，供合成官引用
    真实数字（而非模型编造）。关键词同时匹配实体类型（如「告警」→ 运行告警），
    命中为 0 时回退全库统计并在卡片中注明口径。
    """
    from database import async_session
    from models import Entity

    if nl_filter:
        facts = await _data_facts_by_filter(nl_filter)
        if facts:
            return facts   # 结构化口径命中，无需词频兜底

    kws = _keywords(task)
    try:
        async with async_session() as db:
            def _match(*fields):
                return or_(*[
                    or_(*[f.ilike(f"%{kw}%") for f in fields])
                    for kw in kws
                ])

            cond, scope_note = None, ""
            if kws:
                # 强弱信号分级（业务无关）：实体类型/名称含关键词 = 强信号，
                # 仅描述全文含关键词 = 弱信号（易被「结果」等通用词跨库误命中）。
                strong = _match(Entity.entity_type, Entity.name)
                strong_n = (await db.execute(
                    select(func.count()).select_from(Entity).where(strong)
                )).scalar_one()
                if strong_n >= DATA_STRONG_MIN:
                    cond = strong
                    full_n = (await db.execute(
                        select(func.count()).select_from(Entity)
                        .where(_match(Entity.entity_type, Entity.name, Entity.description))
                    )).scalar_one()
                    dropped = max(full_n - strong_n, 0)
                    scope_note = f"，类型/名称口径；仅描述命中 {dropped} 条已排除" if dropped else "，类型/名称口径"
                else:
                    cond = _match(Entity.entity_type, Entity.name, Entity.description)

            cnt = select(func.count()).select_from(Entity)
            if cond is not None:
                cnt = cnt.where(cond)
            total = (await db.execute(cnt)).scalar_one()
            if total == 0:
                return []

            type_q = (select(Entity.entity_type, func.count())
                      .group_by(Entity.entity_type)
                      .order_by(func.count().desc()))
            if cond is not None:
                type_q = type_q.where(cond)
            type_rows = (await db.execute(type_q)).all()

            stmt = select(Entity)
            if cond is not None:
                stmt = stmt.where(cond)
            rows = (await db.execute(
                stmt.order_by(Entity.created_at.desc()).limit(DATA_SAMPLE)
            )).scalars().all()

            kw_note = f"关键词：{' / '.join(kws[:4])}" if kws else "全库口径"
            if scope_note:
                kw_note += scope_note
            facts = [{
                "id": "fact-data-agg",
                "grade": "data_fact",
                "title": f"台账统计 · 命中 {total} 条",
                "detail": (f"实体台账结构化查询（{kw_note}）："
                           f"{_type_scope_text(total, type_rows)}。"),
            }]
            for e in rows:
                props = _entity_props(e.properties)
                kv = "；".join(f"{k}={v}" for k, v in props.items())
                desc = str(e.description or "")
                if not kv and desc:
                    kv = desc[:80]
                created = str(e.created_at or "—")[:10]
                facts.append({
                    "id": f"fact-data-{e.id}",
                    "grade": "data_fact",
                    "title": f"{e.entity_type} · {e.name}",
                    "detail": (f"台账记录（{created}）"
                               + (f"｜{kv}" if kv else ""))[:200],
                })
            return facts
    except Exception:
        return []


async def _data_facts_by_filter(f: dict) -> list[dict]:
    """NL2Filter 结构化取数：filter 条件精确命中台账（事实卡口径与词频路径一致）。

    条件全部落库执行（entity_type / name 模糊匹配 + time_range 映射
    created_at 下界）；无可用条件或 0 命中返回 []，由调用方回落词频老路。
    """
    from database import async_session
    from models import Entity

    conds = []
    if f.get("entity_type"):
        conds.append(Entity.entity_type.ilike(f"%{f['entity_type']}%"))
    if f.get("name"):
        conds.append(Entity.name.ilike(f"%{f['name']}%"))
    floor, _label = _time_floor(f.get("time_range", ""))
    if floor:
        conds.append(Entity.created_at >= floor)   # created_at 为 ISO 字符串，字典序即时间序
    if not conds:
        return []
    try:
        async with async_session() as db:
            cond = and_(*conds)
            total = (await db.execute(
                select(func.count()).select_from(Entity).where(cond)
            )).scalar_one()
            if total == 0:
                return []
            rows = (await db.execute(
                select(Entity).where(cond)
                .order_by(Entity.created_at.desc()).limit(DATA_SAMPLE)
            )).scalars().all()

            type_rows = (await db.execute(
                select(Entity.entity_type, func.count()).where(cond)
                .group_by(Entity.entity_type).order_by(func.count().desc())
            )).all()

            note = "、".join(x for x in [
                f"类型≈{f['entity_type']}" if f.get("entity_type") else "",
                f"名称≈{f['name']}" if f.get("name") else "",
                f"时间≥{_time_floor(f.get('time_range', ''))[1]}" if floor else "",
                f"指标={f['metric']}" if f.get("metric") else "",
            ] if x)
            facts = [{
                "id": "fact-data-agg",
                "grade": "data_fact",
                "title": f"台账统计（NL2Filter 精准口径）· 命中 {total} 条",
                "detail": (f"实体台账结构化查询（filter：{note}）："
                           f"{_type_scope_text(total, type_rows)}。"),
            }]
            for e in rows:
                props = _entity_props(e.properties)
                kv = "；".join(f"{k}={v}" for k, v in props.items())
                desc = str(e.description or "")
                if not kv and desc:
                    kv = desc[:80]
                created = str(e.created_at or "—")[:10]
                facts.append({
                    "id": f"fact-data-{e.id}",
                    "grade": "data_fact",
                    "title": f"{e.entity_type} · {e.name}",
                    "detail": (f"台账记录（{created}）"
                               + (f"｜{kv}" if kv else ""))[:200],
                })
            return facts
    except Exception:
        return []


def _time_floor(time_range: str) -> tuple[Optional[str], str]:
    """time_range 词 → created_at 下界（ISO 字符串可直接比较）与口径标签。

    兼容两种输入：NL2Filter 英文契约（today/yesterday/this_week/this_month，
    0.6B 小模型抽取输出）与中文时间词。
    """
    today = date.today()
    week = (today - timedelta(days=today.weekday())).isoformat()
    month = today.replace(day=1).isoformat()
    mapping = {
        "今天": (today.isoformat(), "今天"),
        "today": (today.isoformat(), "今天"),
        "昨天": ((today - timedelta(days=1)).isoformat(), "昨天"),
        "yesterday": ((today - timedelta(days=1)).isoformat(), "昨天"),
        "本周": (week, "本周"),
        "this_week": (week, "本周"),
        "本月": (month, "本月"),
        "this_month": (month, "本月"),
    }
    return mapping.get((time_range or "").strip().lower(), (None, ""))


async def _rewrite_query(eng: MultiAgentEngine, goal: str) -> str:
    """kb 模式检索改写门控：单次轻量改写，失败回落原查询（门控不拦人）。"""
    try:
        return await eng.llm_stream(
            "把检索问题改写成更适合向量检索的独立查询：补全省略指代，保留关键实体"
            "与意图，去掉口语与格式词。只输出改写后的查询本身，不要任何解释。",
            goal, timeout=8.0,
        ) or goal
    except Exception:
        return goal


def _entity_props(raw: str) -> dict:
    """实体 properties JSON → 键值对（最多 6 个，过滤空值与长文本/嵌套结构）。"""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if not k or v is None or isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if not s or len(s) > 40:
            continue
        out[str(k)] = s
        if len(out) >= 6:
            break
    return out


def _keywords(text: str) -> list[str]:
    """任务关键词：引号词优先，jieba 抽取兜底，简单分词最后。"""
    kws: list[str] = []
    for m in re.findall(r"[「\"']([^「」\"']{2,20})[」\"']", text):
        kws.append(m.strip())
    rest = [k for k in kws]
    try:
        import jieba.analyse
        for k in jieba.analyse.extract_tags(text, topK=8):
            if len(k) >= 2 and k not in rest:
                rest.append(k)
    except Exception:
        for k in re.split(r"[\s，。；：、,.;:？！?!（）()]+", text):
            if len(k) >= 2 and k not in rest:
                rest.append(k)
    return rest[:6]


# ── 通用评审与合成（确定性兜底） ──────────────────────────────

# 合成系统提示词（回归迭代 P0：引用从"建议"升级为硬性要求 + 忠实性约束）。
# 引用只允许单卡写法 [事实n] / [素材n]——区间写法 [事实2-9]、组合写法 [事实2、5]
# 无法被 citation_valid 的 \[事实(\d+)\] 校验识别（上一轮 10 条"引用 无"失分里
# 部分即此类格式错位），统一单卡写法后产出与校验口径严格对齐。
SYNTH_SYSTEM = (
    "你是多智能体团队的合成官，基于共享黑板里的素材卡与图谱事实交付用户任务的"
    "最终成果。无论任务是研判、写作、总结还是问答，直接产出任务要求的成果形态。"
    "必须用 markdown 且只包含四个小节：### 最终结果 / ### 关键依据 / ### 风险与存疑 /"
    " ### 建议动作。\n"
    "【引用规则·硬性，违反即返工】事实卡非空时，「关键依据」每一条必须以 [事实n] "
    "开头标注来源编号（n 为事实卡列表给出的编号），最终结果的关键论断也尽量带上"
    "对应编号；素材卡用 [素材n]。只允许单卡写法 [事实3] / [素材2]，不要发明区间或"
    "组合写法（如 [事实2-9]、[事实2、5]），禁止编造不存在的编号。\n"
    "【忠实性规则】每个论断（尤其是职责/牵头/负责归属、部门名称、数值阈值、分级"
    "标准）必须能在素材卡或事实卡原文中找到依据；素材未呈现的细节如实写"
    "「素材未呈现」，禁止按常识推测补全；多张素材口径冲突时，采用与原文表述一致的"
    "一条，并在「风险与存疑」中披露分歧。\n"
    "风险与存疑逐条列出（无则写「无」）；建议动作 2~3 条。全文中文，400 字以内。"
)


async def _synth_llm(eng: MultiAgentEngine, task: str, verdict: dict,
                     evidence: dict, facts: list[dict],
                     fix_citation: str = "") -> tuple[str, str]:
    """合成一次结论（流式 token 外抛）。返回 (conclusion, fail_reason)。

    fix_citation 传入上一版引用不合规格的结论时，在用户提示后附加针对性重写要求
    （引用自检重试路径）——指出具体病因与编号范围，比整段重试命中率高得多。
    """
    user = _synth_user(task, verdict, evidence, facts)
    if fix_citation:
        user += (
            f"\n\n【重写要求】上一版结论引用标注不合规（{_citation_issue(fix_citation, len(facts))}）。"
            f"请重新输出完整结论：「关键依据」每条以 [事实n] 开头，编号只能取 1~{len(facts)}；"
            "内容忠实素材原文，不要改变事实口径。"
        )
    try:
        conclusion = await eng.llm_stream(
            SYNTH_SYSTEM, user,
            on_token=lambda t: eng.emit({"type": "token", "content": t}),
            timeout=SYNTH_TIMEOUT,
        )
        return conclusion, ""
    except RuntimeError as exc:  # llm_stream: LLM 未配置
        return "", str(exc) or "LLM 未配置"
    except Exception as exc:     # 超时 / 网络 / SDK 异常
        return "", f"LLM 调用失败（{type(exc).__name__}: {exc or '超时'}）"


def _citation_ok(conclusion: str, n_facts: int) -> bool:
    """引用合规自检（与评估器 citation_valid 同口径）。

    事实卡非空：至少一个 [事实n] 且编号不越界；无事实卡：不允许出现事实引用。
    """
    cited = [int(n) for n in re.findall(r"\[事实(\d+)\]", conclusion or "")]
    if n_facts <= 0:
        return not cited
    return bool(cited) and max(cited) <= n_facts


def _citation_issue(conclusion: str, n_facts: int) -> str:
    """引用不合规的具体病因（用于重试提示与前端提示）。"""
    cited = sorted({int(n) for n in re.findall(r"\[事实(\d+)\]", conclusion or "")})
    if n_facts <= 0:
        return f"无事实卡但出现了事实引用 {cited[:5]}" if cited else "无事实卡"
    if not cited:
        return "结论没有任何 [事实n] 引用"
    return f"引用编号越界（最大 {max(cited)} > 事实卡 {n_facts} 张）"


def _append_citation_index(conclusion: str, facts: list[dict]) -> str:
    """引用兜底：结论尾部追加「引用索引」行，逐张列出 [事实n] + 标题。

    仅在 LLM 两次合成后仍无合法引用时触发（概率极低）；列出的都是黑板上
    真实存在的事实卡，可溯源、不引入新事实。
    """
    idx = "；".join(f"[事实{i}] {f.get('title', '')}"
                    for i, f in enumerate(facts, 1))
    return f"{conclusion.rstrip()}\n\n> 引用索引：{idx}"


def _universal_verdict(evidence: dict, facts: list[dict]) -> dict:
    """素材充分性硬规则（素材 = 知识库语料 + 模型产出 + 图谱事实），任务类型无关。"""
    n_docs = sum(len(v) for v in evidence.values())
    n_total = n_docs + len(facts)
    if n_total == 0:
        conflicts = [{
            "topic": "素材覆盖",
            "verdict": "知识库、图谱与子任务执行均无产出，成果缺乏支撑",
            "basis": "规则：无素材不出成果（保守原则）",
            "review_hint": "开启 Retriever/DataAgent/GraphAgent 补充素材，或充实任务描述后重试",
        }]
        suggestion = "先补充素材或调整任务表述，再发起协作"
        label, conf, human = "需补充素材", "低", True
    elif n_total < 3:
        conflicts = [{
            "topic": "素材充分性",
            "verdict": f"仅 {n_total} 条素材，成果可能片面",
            "basis": "规则：素材 < 3 条建议人工确认关键内容",
            "review_hint": "建议人工确认关键内容后再采纳成果",
        }]
        suggestion = "谨慎采纳成果，并人工确认关键内容"
        label, conf, human = "建议补充论证", "中", True
    else:
        conflicts = []
        suggestion = "素材充分，可采纳团队成果（保留引用备查）"
        label, conf, human = "可采纳交付", "高", False
    return {
        "conflicts": conflicts,
        "suggestion": suggestion,
        "suggest_label": label,
        "confidence": conf,
        "need_human": human,
        "comment": "",
    }


def _synth_user(task: str, verdict: dict, evidence: dict, facts: list[dict]) -> str:
    cards = [
        {"n": i + 1, "source": c.get("source", ""), "text": c.get("summary", "")}
        for i, c in enumerate(x for cs in evidence.values() for x in cs)
    ]
    fcards = [{"n": i + 1, "title": f["title"], "detail": f["detail"]}
              for i, f in enumerate(facts)]
    return (
        f"用户任务：{task}\n\n"
        f"评审意见：{verdict.get('suggest_label', '未评审')}"
        f"（置信度 {verdict.get('confidence', '未知')}）。\n\n"
        f"素材卡（共 {len(cards)} 张，[素材n] 编号 1~{len(cards)}）：\n"
        f"{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
        f"图谱事实卡（共 {len(fcards)} 张，[事实n] 编号 1~{len(fcards)}）：\n"
        f"{json.dumps(fcards, ensure_ascii=False, indent=1)}\n\n"
        f"存疑点：\n{json.dumps(verdict.get('conflicts', []), ensure_ascii=False, indent=1)}"
    )


def _template_conclusion(task: str, verdict: dict, evidence: dict, facts: list[dict]) -> str:
    """无 LLM 时的模板成果（确定性拼装）。"""
    n_docs = sum(len(v) for v in evidence.values())
    lines = [
        "### 最终结果",
        f"任务「{task[:60]}」：{verdict['suggest_label']}"
        f"（置信度：{verdict['confidence']}）。",
        "",
        "### 关键依据",
    ]
    for i, f in enumerate(facts, 1):
        lines.append(f"- [事实{i}] {f['title']}：{f['detail']}")
    idx = 0
    for cards in evidence.values():
        for c in cards:
            idx += 1
            lines.append(f"- [素材{idx}] {c.get('title', '')}（{c.get('source', '')}）："
                         f"{c.get('summary', '')[:80]}")
    if not facts and not n_docs:
        lines.append("- 平台语料与图谱未命中，无可用依据。")
    lines += ["", "### 风险与存疑"]
    if verdict["conflicts"]:
        for c in verdict["conflicts"]:
            hint = f"→ {c['review_hint']}" if c.get("review_hint") else ""
            lines.append(f"- ⚠️ {c['topic']}：{c['verdict']}{hint}")
    else:
        lines.append("- 无：多源素材口径一致。")
    lines += [
        "",
        "### 建议动作",
        "1. 依据引用逐条核对原文，确认关键内容。",
        "2. 如素材不足，补充资料到知识库后重新协作。",
        "3. 成果由 AI 团队合成，重要决策请人工复核后采纳。",
    ]
    return "\n".join(lines)


register(UniversalScenario())
