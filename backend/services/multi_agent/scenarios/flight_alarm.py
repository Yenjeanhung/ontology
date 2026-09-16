# -*- coding: utf-8 -*-
"""
场景适配器：航班运行监控 · 组合告警复核。

业务底稿：《G 航空公司航班运行风险管理研究》。
- 规则引擎管「发现」（Drools 组合规则告警），多智能体管「研判」：
  跨航班动态/规则阈值/机组保障多源取证 + 图谱历史事实 + 冲突消解 + 处置建议；
- 数据来源：**平台实体库真实数据**（flight_store：运行告警/航段/规则/机组/航空器
  实体与关系）；真实组合告警不足时按规则生成补充（真实入库），平台完全无数据
  才回落内置演示告警（目标卡标注「生成数据/演示数据」）；
- 克制边界：单条件低风险告警规则引擎已给结论，不进多智能体（runnable=False）。

本模块只包含业务逻辑（取证来源、证据分级硬规则、运控话术），编排与
SSE 设施全部来自通用引擎 MultiAgentEngine。
"""

import asyncio
import json

from ..engine import MultiAgentEngine
from .. import flight_store
from ..flight_data import get_alarm, hard_rule_verdict, list_alarms
from . import MultiAgentScenario, register

# ── 团队编制（六角色 × 本场景） ──────────────────────────────

_TEAM_INFO = {
    "team": "告警研判团队",
    "members": [
        {"node": "planner", "role": "planner", "name": "Planner · 任务分解"},
        {"node": "retriever_ops", "role": "retriever", "name": "Retriever · 航班动态"},
        {"node": "retriever_rule", "role": "retriever", "name": "Retriever · 规则阈值"},
        {"node": "retriever_resource", "role": "retriever", "name": "Retriever · 机组与机队"},
        {"node": "retriever_weather", "role": "retriever", "name": "Retriever · 气象补充"},
        {"node": "graph_agent", "role": "graph_agent", "name": "GraphAgent · 本体事实"},
        {"node": "critic", "role": "critic", "name": "Critic · 冲突消解"},
        {"node": "synthesizer", "role": "synthesizer", "name": "Synthesizer · 结论合成"},
    ],
}

# 固定复核模板（planner 规则分解，快、稳、可复现）
_PLAN = [
    {"id": "r_ops", "node": "retriever_ops", "role": "retriever", "goal": "并行取证：航段动态与前序衔接"},
    {"id": "r_rule", "node": "retriever_rule", "role": "retriever", "goal": "并行取证：告警规则阈值与触发记录"},
    {"id": "r_res", "node": "retriever_resource", "role": "retriever", "goal": "并行取证：机组名单与机队保障"},
    {"id": "r_wx", "node": "retriever_weather", "role": "retriever", "goal": "补充取证：气象趋势（生成数据）"},
    {"id": "g_graph", "node": "graph_agent", "role": "graph_agent", "goal": "历史同类事件与关联实体事实卡"},
    {"id": "c_critic", "node": "critic", "role": "critic", "goal": "证据交叉验证与冲突消解"},
    {"id": "s_syn", "node": "synthesizer", "role": "synthesizer", "goal": "处置建议合成（流式）"},
]

_RETRIEVER_LABELS = {
    "retriever_ops": ("ops", "航段动态与前序衔接"),
    "retriever_rule": ("rule", "告警规则阈值与触发记录"),
    "retriever_resource": ("resource", "机组名单与机队保障"),
    "retriever_weather": ("weather", "气象趋势（生成数据）"),
}

# 内置演示告警（fallback）的取证域 → 本场景域映射
_DEMO_DOMAIN_MAP = {"weather": "weather", "manual": "rule", "crew": "resource"}


def _demo_alarm(alarm_id: str) -> dict | None:
    """fallback 演示告警：标注 generated 并把取证域重映射到本场景四域。"""
    alarm = get_alarm(alarm_id)
    if not alarm:
        return None
    alarm = dict(alarm)
    alarm["source"] = "generated"
    alarm["evidence_pack"] = {
        _DEMO_DOMAIN_MAP.get(domain, domain): cards
        for domain, cards in (alarm.get("evidence_pack") or {}).items()
    }
    return alarm


class FlightAlarmScenario(MultiAgentScenario):
    scenario_id = "flight_alarm"
    name = "组合告警复核"
    business = "航班运行监控"
    description = (
        "基于平台实体库真实运行告警（航段/规则/机组/机队实体与关系）做多智能体复核；"
        "真实组合告警不足时按规则生成补充。单条件低风险告警规则引擎已给结论，"
        "不进多智能体（克制边界）。"
    )

    # ── 目标列表（通用目标卡） ────────────────────────────────

    async def list_targets(self) -> list[dict]:
        alarms: list[dict] = []
        try:
            await flight_store.ensure_platform_alarms()   # 真实不足时生成补充（入库）
            alarms = await flight_store.list_platform_alarms()
        except Exception:
            alarms = []                                   # 库不可用 → 演示数据兜底
        if not alarms:
            alarms = [_demo_alarm(a["alarm_id"]) or a for a in list_alarms()]

        targets = []
        for a in alarms:
            runnable = a.get("trigger_kind") == "combined"
            source = a.get("source", "platform")
            targets.append({
                "id": a["alarm_id"],
                "title": a["flight_no"],
                "subtitle": f"{a['route']} · {a['aircraft']}",
                "headline": a["rule_name"],
                "level": a.get("level"),
                "level_label": a.get("level_label")
                or {"高": "橙色 · 高风险", "中": "黄色 · 中风险", "低": "蓝色 · 低风险"}
                .get(a.get("risk_level"), a.get("risk_level", "")),
                "triggered_at": a.get("triggered_at"),
                "details": [c["desc"] for c in a.get("conditions", [])],
                "runnable": runnable,
                "run_label": "AI 复核",
                "badge": {"platform": "平台数据", "generated": "生成数据"}.get(source),
                "note": None if runnable
                else f"单条件{a.get('risk_level', '')}风险告警：规则引擎已给结论，{a.get('handle_dept', '首响岗位')}持续跟踪即可，无需多智能体复核（克制边界）",
            })
        return targets

    # ── 构建引擎 ──────────────────────────────────────────────

    async def build_engine(self, target_id: str) -> MultiAgentEngine:
        alarm = None
        if target_id.startswith("alm-"):          # 内置演示告警（fallback）
            alarm = _demo_alarm(target_id)
        else:                                      # 平台真实告警实体
            try:
                alarm = await flight_store.get_platform_alarm(target_id)
            except Exception:
                alarm = None
        if not alarm:
            raise KeyError(target_id)
        return MultiAgentEngine(
            context={"alarm": alarm},
            team_info=_TEAM_INFO,
            plan=_PLAN,
            node_factory=self._make_nodes,
            pacing=0.1,
        )

    # ── 节点实现（闭包 engine：emit / llm_stream） ────────────

    def _make_nodes(self, eng: MultiAgentEngine) -> dict:
        def make_retriever(node: str):
            domain, label = _RETRIEVER_LABELS[node]

            async def _fn(state: dict) -> dict:
                eng.emit({"type": "node_start", "node": node, "role": "retriever", "goal": f"并行取证：{label}"})
                await asyncio.sleep(0.3)  # 演示节奏：多路并行可见
                cards = [dict(c) for c in state["context"]["alarm"].get("evidence_pack", {}).get(domain, [])]
                eng.emit({"type": "evidence", "domain": domain, "cards": cards})
                eng.emit({"type": "node_done", "node": node, "summary": f"{label}：取证 {len(cards)} 张证据卡"})
                return {"evidence": {domain: cards}}

            return _fn

        async def graph_agent(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "graph_agent", "role": "graph_agent", "goal": "历史同类事件与关联实体"})
            await asyncio.sleep(0.25)
            facts = [dict(f) for f in state["context"]["alarm"].get("fact_pack", [])]
            eng.emit({"type": "fact", "facts": facts})
            eng.emit({"type": "node_done", "node": "graph_agent", "summary": f"图谱事实卡 {len(facts)} 条（历史同类 + 关联实体）"})
            return {"facts": facts}

        async def critic(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "critic", "role": "critic", "goal": "证据交叉验证与冲突消解"})
            alarm = state["context"]["alarm"]
            evidence, facts = state["evidence"], state["facts"]

            # 1) 确定性兜底裁定（永远先算，保证可复现）
            verdict = hard_rule_verdict(alarm, evidence, facts)

            # 2) LLM 解释增强（失败静默降级，裁定不变）
            comment = ""
            if verdict["conflicts"]:
                try:
                    comment = await eng.llm_stream(
                        "你是航空公司运行控制中心的风险研判助理。请用不超过 3 句中文，解释本次证据分级裁定"
                        "的依据（分级优先级、保守原则、手册条款）；不要输出 JSON、列表或标题。",
                        f"{_alarm_brief(alarm)}\n\n"
                        f"证据卡：\n{json.dumps([c for cs in evidence.values() for c in cs], ensure_ascii=False, indent=1)}\n\n"
                        f"图谱事实卡：\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
                        f"硬规则裁定：{verdict['suggest_label']}；冲突裁定：{verdict['conflicts'][0]['verdict']}",
                    )
                except Exception:
                    comment = ""
            verdict["comment"] = comment

            eng.emit({
                "type": "conflict",
                "conflicts": verdict["conflicts"],
                "suggestion": verdict["suggestion"],
                "suggest_label": verdict["suggest_label"],
                "need_human": verdict["need_human"],
                "confidence": verdict["confidence"],
                "comment": comment,
            })
            summary = (
                f"{len(verdict['conflicts'])} 处冲突消解 → 建议{verdict['suggest_label']}（置信度 {verdict['confidence']}）"
                if verdict["conflicts"]
                else f"证据口径一致 → 建议{verdict['suggest_label']}（置信度 {verdict['confidence']}）"
            )
            eng.emit({"type": "node_done", "node": "critic", "summary": summary})
            return {"verdict": verdict}

        async def synthesizer(state: dict) -> dict:
            eng.emit({"type": "node_start", "node": "synthesizer", "role": "synthesizer", "goal": "合成处置建议（流式）"})
            alarm, verdict = state["context"]["alarm"], state["verdict"]
            evidence, facts = state["evidence"], state["facts"]

            conclusion = ""
            try:
                conclusion = await eng.llm_stream(
                    "你是航空公司运行控制中心的风险研判助理，按公司三级响应制度输出研判结论。"
                    "必须用 markdown 且只包含四个小节：### 研判结论 / ### 关键依据 / ### 冲突与存疑 / ### 建议动作。"
                    "要求：结论明确『建议 **xxx**』；依据逐条标注 [事实]/[来源] 引用；冲突与存疑逐条列出冲突证据与复核建议"
                    "（无冲突写『无：多源证据口径一致』）；建议动作给 2~3 条，含主责部门与响应时限，"
                    "并注明『最终由值班员人工确认』。全文中文，300 字以内。",
                    _synth_user(alarm, verdict, evidence, facts),
                    on_token=lambda t: eng.emit({"type": "token", "content": t}),
                )
            except Exception:
                conclusion = ""

            degraded = not conclusion
            if degraded:
                conclusion = _template_conclusion(verdict, evidence, facts)
                eng.emit({"type": "error", "content": "LLM 未配置或调用失败，已降级为模板结论（裁定仍为确定性规则）"})

            eng.emit({
                "type": "node_done",
                "node": "synthesizer",
                "summary": "处置建议已生成（LLM 流式）" if not degraded else "处置建议已生成（模板降级）",
            })
            return {"conclusion": conclusion}

        return {
            "retriever_ops": make_retriever("retriever_ops"),
            "retriever_rule": make_retriever("retriever_rule"),
            "retriever_resource": make_retriever("retriever_resource"),
            "retriever_weather": make_retriever("retriever_weather"),
            "graph_agent": graph_agent,
            "critic": critic,
            "synthesizer": synthesizer,
        }


# ── 提示词与模板（运控话术，本场景私有） ──────────────────────

def _alarm_brief(alarm: dict) -> str:
    conds = "；".join(c["desc"] for c in alarm.get("conditions", []))
    return (
        f"航班 {alarm['flight_no']}（{alarm['route']}，航段 {alarm['aircraft']}），"
        f"规则「{alarm['rule_name']}」于 {alarm['triggered_at']} 触发。触发条件：{conds}。"
    )


def _synth_user(alarm: dict, verdict: dict, evidence: dict, facts: list[dict]) -> str:
    cards = [c for cs in evidence.values() for c in cs]
    return (
        f"{_alarm_brief(alarm)}\n\n"
        f"裁定结果：建议{verdict['suggest_label']}（置信度 {verdict['confidence']}），"
        f"{verdict['dept']}需{verdict['deadline']}。\n\n"
        f"证据卡：\n{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
        f"图谱事实卡：\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
        f"冲突消解：\n{json.dumps(verdict['conflicts'], ensure_ascii=False, indent=1)}"
    )


def _template_conclusion(verdict: dict, evidence: dict, facts: list[dict]) -> str:
    """无 LLM 时的模板结论（确定性拼装，保证无 LLM 环境全流程可跑）。"""
    lines = [
        "### 研判结论",
        f"建议 **{verdict['suggest_label']}**（置信度：{verdict['confidence']}）；{verdict['dept']}需{verdict['deadline']}。",
        "",
        "### 关键依据",
    ]
    for f in facts:
        lines.append(f"- [事实] {f['title']}：{f['detail']}")
    for cards in evidence.values():
        for c in cards:
            if c.get("stance") == "worsen":
                lines.append(f"- [来源] {c['title']}（{c['source']}）：{c['summary']}")
    lines += ["", "### 冲突与存疑"]
    if verdict["conflicts"]:
        for c in verdict["conflicts"]:
            hint = f"→ {c['review_hint']}" if c.get("review_hint") else ""
            lines.append(f"- ⚠️ {c['topic']}：{c['verdict']}{hint}")
    else:
        lines.append("- 无：多源证据口径一致。")
    lines += [
        "",
        "### 建议动作",
        f"1. 按手册条款升级告警等级，通知{verdict['dept']}介入。",
        "2. 持续跟踪告警解除状态，出现新证据立即重新研判。",
        "3. 研判结论记入运行日志（AI 建议，最终由值班员人工确认）。",
    ]
    return "\n".join(lines)


register(FlightAlarmScenario())
