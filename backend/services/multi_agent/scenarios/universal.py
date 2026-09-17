# -*- coding: utf-8 -*-
"""
通用智能体团队场景（adhoc · 默认场景，业务无关、任务类型无关）。

对标开源多智能体项目的「团队编制」协作范式（AutoGen / CrewAI / MetaGPT /
Magentic-One）：
- 核心智能体内置：Planner（任务规划）与 Synthesizer（结果合成）是任何团队
  都必需的角色，固定在编；
- 能力智能体自由勾选：Retriever（知识库取证）/ DataAgent（台账数据查询）/
  GraphAgent（图谱事实）/ ToolAgent（Function Calling 工具调用，含 MCP 外部
  工具接入）/ Critic（评审质控）由用户按需组队，流水线按编制
  动态装配（不选 Critic 时并行节点直通 Synthesizer，引擎零特判）；
- 任务类型不限：研判、写作、总结、问答皆可——Planner 分解提示词随编制
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
from typing import Optional

from sqlalchemy import func, or_, select

from providers.llm import create_llm
from ..engine import MultiAgentEngine
from . import MultiAgentScenario, register

# ── 智能体名册（前端组队选择器据此渲染） ──────────────────────

# 核心智能体：任何团队必需，内置不可取消
CORE_AGENTS = [
    {"id": "planner", "name": "Planner · 任务规划",
     "desc": "用 LLM 把任务分解为可并行子任务，产出执行计划"},
    {"id": "synthesizer", "name": "Synthesizer · 结果合成",
     "desc": "汇总共享黑板素材，流式交付最终成果"},
]

# 能力智能体：自由勾选组合，流水线按编制动态装配
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
     "desc": "素材交叉验证、冲突消解与质量裁定（可选编制）"},
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
    """编制归一化：只保留名册内的能力智能体 id，按名册顺序输出；None=默认编制。"""
    if agents is None:
        return list(DEFAULT_AGENTS)
    req = {str(a).strip() for a in (agents or [])}
    return [aid for aid in OPTIONAL_IDS if aid in req]


# ── 场景定义 ─────────────────────────────────────────────────

class UniversalScenario(MultiAgentScenario):
    scenario_id = "universal"
    name = "通用智能体团队"
    business = "任意任务"
    description = (
        "核心智能体内置（Planner / Synthesizer），能力智能体自由组队"
        "（Retriever / DataAgent / GraphAgent / Critic），任务类型不限——研判、写作、"
        "总结、问答皆可。Planner 用 LLM 动态分解，并行执行，流式交付。"
        "对标 AutoGen/CrewAI 的团队编制范式。"
    )
    adhoc = True   # 自由任务输入（前端据此渲染任务工作台）

    def meta(self) -> dict:
        meta = super().meta()
        meta["agents"] = {                      # 组队选择器名册
            "core": CORE_AGENTS,
            "optional": OPTIONAL_AGENTS,
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

    # ── 构建引擎：按编制动态装配 ──────────────────────────────

    async def build_engine(self, target_id: str) -> MultiAgentEngine:
        """示例任务入口：按 id 找回任务文本，走统一 build（默认编制）。"""
        task = next((ex["task"] for ex in EXAMPLES if ex["id"] == target_id), None)
        if not task:
            raise KeyError(target_id)
        return await self.build_engine_from_task(task)

    async def build_engine_from_task(
        self, task: str, agents: Optional[list[str]] = None
    ) -> MultiAgentEngine:
        """自由任务入口：编制归一化 → LLM 动态规划 → 按编制装配节点 → 引擎。

        编制语义：
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

        caps = _normalize_agents(agents)
        use_retrieval = "retriever" in caps
        goals = await self._plan_goals(task, use_retrieval)

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
                 "name": {"data_agent": "DataAgent · 数据查询",
                          "graph_agent": "GraphAgent · 图谱事实",
                          "tool_agent": "ToolAgent · 工具调用",
                          "critic": "Critic · 评审质控",
                          "synthesizer": "Synthesizer · 结果合成"}[p["role"]]}
                for p in plan if p["role"] in ("data_agent", "graph_agent", "tool_agent",
                                               "critic", "synthesizer")
            ]
        )
        cap_label = " + ".join(
            a["name"].split(" · ")[0] for a in OPTIONAL_AGENTS if a["id"] in caps
        ) or "纯模型协作"
        team_info = {
            "team": f"通用智能体团队（{cap_label}）",
            "members": members,
        }

        return MultiAgentEngine(
            context={"task": task, "agents": caps},
            team_info=team_info,
            plan=plan,
            node_factory=lambda eng: self._make_nodes(eng, plan, task),
            pacing=0.1,
        )

    # ── 动态规划（Plan-and-Solve，随编制切换分解策略） ─────────

    async def _plan_goals(self, task: str, use_retrieval: bool) -> list[str]:
        """LLM 分解任务为并行子任务；失败回落默认分解。

        选了 Retriever → 子任务是「可检索的资料问题」；
        未选 Retriever → 子任务是「成员用自身知识完成的关键环节」。
        """
        if use_retrieval:
            system = (
                "你是多智能体团队的规划师。把用户的任务拆成 2~4 个可并行执行的"
                "资料检索子任务。每个子任务是一句具体、可检索的中文问题（面向"
                "知识库语料或结构化数据）。只输出 JSON 数组，格式："
                '[{"goal": "子任务描述"}]，不要输出任何其他文字。'
            )
        else:
            system = (
                "你是多智能体团队的规划师。把用户的任务拆成 2~4 个可并行完成的"
                "关键子任务（如不同维度、章节或要点，由成员用自身知识直接完成，"
                "不依赖外部检索）。只输出 JSON 数组，格式："
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

    def _make_nodes(self, eng: MultiAgentEngine, plan: list[dict], task: str) -> dict:
        nodes = {}
        for step in plan:
            if step["role"] == "retriever":
                nodes[step["node"]] = self._make_kb_retriever(eng, step)
            elif step["role"] == "worker":
                nodes[step["node"]] = self._make_worker(eng, step, task)
            elif step["role"] == "data_agent":
                nodes[step["node"]] = self._make_data_agent(eng, task)
            elif step["role"] == "tool_agent":
                nodes[step["node"]] = self._make_tool_agent(eng, task)
            elif step["role"] == "graph_agent":
                nodes[step["node"]] = self._make_graph_agent(eng, task)
            elif step["role"] == "critic":
                nodes[step["node"]] = self._make_critic(eng, task)
            elif step["role"] == "synthesizer":
                nodes[step["node"]] = self._make_synthesizer(eng, task)
        return nodes

    def _make_kb_retriever(self, eng: MultiAgentEngine, step: dict):
        """Retriever-i：检索增强执行——检索知识库语料，命中即素材卡。"""
        node, goal, domain = step["node"], step["goal"], step["id"]

        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": node, "role": "retriever", "goal": goal})
            chunks = await _search_kb_chunks(goal)
            cards = [_chunk_card(f"ev-{node}-{i}", domain, c)
                     for i, c in enumerate(chunks)]
            eng.emit({"type": "evidence", "domain": domain, "cards": cards})
            eng.emit({
                "type": "node_done", "node": node,
                "summary": (f"全库检索命中 {len(cards)} 条语料"
                            if cards else "未命中知识库语料"),
            })
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

    def _make_data_agent(self, eng: MultiAgentEngine, task: str):
        async def _fn(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "data_agent", "role": "data_agent",
                      "goal": "实体台账结构化查询与统计"})
            facts = await _data_facts(task)
            eng.emit({"type": "fact", "facts": facts})
            eng.emit({
                "type": "node_done", "node": "data_agent",
                "summary": (f"台账命中，产出 {len(facts)} 张数据卡"
                            f"（1 统计 + {len(facts) - 1} 明细）"
                            if facts else "台账未查询到结构化数据"),
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


# ── 平台通用取证（业务无关） ──────────────────────────────────

async def _search_kb_chunks(query: str) -> list[dict]:
    """跨全部知识库的向量检索（VectorDataService），失败逐库降级。"""
    from database import async_session
    from models import KnowledgeBase
    from services.vector_data_service import VectorDataService

    try:
        async with async_session() as db:
            kbs = (await db.execute(
                select(KnowledgeBase).limit(MAX_KBS)
            )).scalars().all()
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


async def _data_facts(task: str) -> list[dict]:
    """实体台账结构化查询（业务无关）：关键词命中 → 聚合统计 + 明细事实卡。

    与 GraphAgent 的差异：不只找「关联实体」，而是给出台账口径的真实数据——
    命中总量、按实体类型聚合计数、最新若干条记录的关键属性，供合成官引用
    真实数字（而非模型编造）。关键词同时匹配实体类型（如「告警」→ 运行告警），
    命中为 0 时回退全库统计并在卡片中注明口径。
    """
    from database import async_session
    from models import Entity

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

            type_q = select(Entity.entity_type, func.count()).group_by(Entity.entity_type)
            if cond is not None:
                type_q = type_q.where(cond)
            type_counts = [
                f"{t or '未分类'} {c} 条"
                for t, c in (await db.execute(type_q)).all()
            ][:DATA_TYPE_GROUPS]

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
                "detail": f"实体台账结构化查询（{kw_note}）：共 {total} 条记录；"
                          f"按实体类型：{'、'.join(type_counts)}。",
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
