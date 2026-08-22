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
        "is_enabled": a.is_enabled,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
    }


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
    async def resolve(db: AsyncSession, agent_id: str) -> dict | None:
        """按 agent_id 展开出 OAG 入参 {id, name, kb_id, system_prompt, skill_ids}。

        不存在 / 已禁用返回 None；skill_ids 的无效 id 交由 SkillService.resolve 容错过滤。
        """
        agent = await AgentService.get(db, agent_id)
        if not agent or not agent.is_enabled:
            return None
        return {
            "id": agent.id,
            "name": agent.name,
            "kb_id": agent.kb_id,
            "system_prompt": agent.system_prompt or "",
            "skill_ids": _skill_ids_to_list(agent.skill_ids),
        }
