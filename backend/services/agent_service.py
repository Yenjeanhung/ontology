"""智能体（Agent）配置服务。

智能体 = 知识库(KB) + 技能(Skills) + 人设(System Prompt) 的可复用组合。
v1：仅暴露 KB / 技能 / 人设 三个核心维度；model / temperature 字段预留（未在 UI 暴露）。
执行时由 resolve() 展开出 OAG 入参，检索逻辑零改动。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Agent, KnowledgeBase


def _skill_ids_to_list(raw: str | None) -> list[str]:
    """skill_ids 列（JSON 字符串）→ list；损坏数据静默降级为空。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _serialize(
    a: Agent,
    *,
    kb_name: str | None = None,
    skill_count: int | None = None,
) -> dict:
    skill_ids = _skill_ids_to_list(a.skill_ids)
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description or "",
        "kb_id": a.kb_id,
        "kb_name": kb_name,
        "system_prompt": a.system_prompt or "",
        "skill_ids": skill_ids,
        "skill_count": len(skill_ids) if skill_count is None else skill_count,
        "is_preset": a.is_preset,
        "is_enabled": a.is_enabled,
        "use_tools": int(getattr(a, "use_tools", 0) or 0),
        "created_at": a.created_at,
        "updated_at": a.updated_at,
    }


# 内置「系统默认」智能体固定 id：seed 生成，不可删除（可改名称/描述/KB/技能/人设）
DEFAULT_AGENT_ID = "agent_default"
DEFAULT_AGENT_DESCRIPTION = (
    "内置默认智能体：未绑定知识库/技能时，问答页自动跟随页面选择的知识库与技能"
    "（技能默认全选启用项）。可修改其配置作为全局默认，不可删除。"
)

# 图表智能体固定 id 与配置：多智能体协作的图表/表格产出成员。
# 由 backend/scripts/seed_chart_agent.py 创建（is_preset=0，可在配置页编辑/删除，
# 删除即取消自动组队）；任务含图表需求时按该 id 定位（名称含「图表」兜底），
# use_tools=1 → 协作节点走 Function Calling 工具循环调用开源图表 MCP（AntV）成图。
CHART_AGENT_ID = "agent_chart"

CHART_AGENT_DESCRIPTION = (
    "有图表需求且数据适合成图时，调用开源图表 MCP（AntV）生成柱状/折线/饼图等"
    "常用图表与数据表格；无图表需求或数据不足时如实说明。"
)

CHART_AGENT_SYSTEM_PROMPT = (
    "你是多智能体团队中的「图表智能体」，专职把任务中的数据转化为常用的图表与表格。"
    "工作流程：\n"
    "1. 取数：调用工具获取真实数据——台账结构化数据用 data_query，知识库语料用"
    " kb_search，图谱事实用 graph_search；严禁编造数据；取数最多 2~3 次，相同参数"
    "不要重复调用，一旦拿到含分类/日期/数值的可聚合数据就立即进入成图，不要反复"
    "换关键词查询。\n"
    "2. 成图判定：只要取到的真实数据存在可聚合维度（分类计数、时间趋势、占比构成、"
    "排名分布），就必须调用图表 MCP 工具生成至少 1 张图表；只有完全取不到结构化"
    "数据时才允许不调用图表工具，并用文字说明原因与建议。\n"
    "3. 成图：调用系统注册的图表 MCP 工具（generate_* 系列，工具清单见注册中心）"
    "生成最合适的常用图形，一次任务最多 4 张：分类对比用柱状/条形类，趋势变化用"
    "折线/面积类，占比构成用饼图/环形类，分布关系用散点/直方/雷达类，明细数据用"
    "表格类工具；data 参数传上一步取到的真实数据数组（每项含分类与数值字段），"
    "title 用简短中文。\n"
    "4. 总结：图表生成后用不超过 150 字中文总结数据要点（最高/最低、趋势方向、"
    "占比头部），并逐条列出已生成图表的名称。"
)


async def ensure_default_agent(db: AsyncSession) -> bool:
    """启动 seed：确保内置「系统默认」智能体存在（幂等；已存在则不覆盖用户修改）。"""
    if await db.get(Agent, DEFAULT_AGENT_ID) is not None:
        return False
    db.add(Agent(
        id=DEFAULT_AGENT_ID,
        name="系统默认",
        description=DEFAULT_AGENT_DESCRIPTION,
        is_preset=1,
        is_enabled=1,
    ))
    await db.commit()
    return True


class AgentService:
    @staticmethod
    async def list(db: AsyncSession) -> list[dict]:
        """全部智能体（含禁用），附 KB 名与技能数。"""
        result = await db.execute(select(Agent).order_by(Agent.created_at))
        agents = result.scalars().all()

        kb_ids = list({a.kb_id for a in agents})
        kb_name_by_id: dict[str, str] = {}
        if kb_ids:
            kb_rows = await db.execute(
                select(KnowledgeBase.id, KnowledgeBase.name).where(
                    KnowledgeBase.id.in_(kb_ids)
                )
            )
            kb_name_by_id = {row[0]: row[1] for row in kb_rows.all()}

        return [
            _serialize(a, kb_name=kb_name_by_id.get(a.kb_id))
            for a in agents
        ]

    @staticmethod
    async def get(db: AsyncSession, agent_id: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_detail(db: AsyncSession, agent_id: str) -> dict | None:
        agent = await AgentService.get(db, agent_id)
        if not agent:
            return None
        kb = await db.get(KnowledgeBase, agent.kb_id)
        return _serialize(agent, kb_name=kb.name if kb else None)

    @staticmethod
    async def create(db: AsyncSession, data: dict) -> dict:
        if isinstance(data.get("skill_ids"), list):
            data["skill_ids"] = json.dumps(data["skill_ids"], ensure_ascii=False)
        data = dict(data)
        data.setdefault("is_preset", 0)  # 页面创建的都是自定义智能体
        agent = Agent(**data)
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return _serialize(agent)

    @staticmethod
    async def update(db: AsyncSession, agent_id: str, data: dict) -> dict | None:
        agent = await AgentService.get(db, agent_id)
        if not agent:
            return None
        if isinstance(data.get("skill_ids"), list):
            data["skill_ids"] = json.dumps(data["skill_ids"], ensure_ascii=False)
        # 内置智能体：允许改名称/描述/KB/技能/人设；禁止禁用、禁止篡改内置标识
        if agent.is_preset:
            data = {k: v for k, v in data.items() if k not in ("is_enabled", "is_preset")}
        for key, value in data.items():
            if value is not None:
                setattr(agent, key, value)
        agent.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(agent)
        return _serialize(agent)

    @staticmethod
    async def delete(db: AsyncSession, agent_id: str) -> bool:
        agent = await AgentService.get(db, agent_id)
        if not agent:
            return False
        await db.delete(agent)
        await db.commit()
        return True

    @staticmethod
    async def resolve(db: AsyncSession, agent_id: str, *, fallback_kb_id: str | None = None) -> dict | None:
        """按 agent_id 展开出 OAG 入参 {id, name, kb_id, system_prompt, skill_ids}。

        不存在 / 已禁用返回 None；skill_ids 的无效 id 交由 SkillService.resolve 容错过滤。
        内置「默认智能体」（is_preset）kb 为空 → 回退 fallback_kb_id（页面选的 KB）；
        技能一律以智能体自身绑定为准（未绑定 = 不启用技能），配置页/问答页共用同一份数据；
        自定义智能体的 KB / 技能以自身配置为准：未绑 KB 即纯 LLM 对话，不再回退页面选择。
        """
        agent = await AgentService.get(db, agent_id)
        if not agent or not agent.is_enabled:
            return None
        is_preset = bool(getattr(agent, "is_preset", 0))
        kb_id = agent.kb_id or (fallback_kb_id or "" if is_preset else "")
        return {
            "id": agent.id,
            "name": agent.name,
            "kb_id": kb_id,
            "system_prompt": agent.system_prompt or "",
            "skill_ids": _skill_ids_to_list(agent.skill_ids),
        }
