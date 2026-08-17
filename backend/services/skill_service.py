"""智能体技能（AgentSkill）管理服务。

技能 = 一段有名字、有描述、可启停的 system prompt 指令增量。
v1：纯 prompt 技能，CRUD + 预设 seed + 查询时按 id 批量加载。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentSkill

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 预设技能定义
# ────────────────────────────────────────────────────────────────

_PRESET_SKILLS: list[dict] = [
    {
        "code": "deep_analysis",
        "name": "深度分析",
        "description": "先给结论，再分点论证；每个论点标注引用；指出资料矛盾与缺口",
        "instructions": (
            "回答时先直接给出结论（一句话或一小段），然后再分点展开论证。\n"
            "每个论点必须至少标注一个引用（[来源N] 或 [事实]）。\n"
            "如果参考资料或图谱事实之间存在矛盾，请显式指出并分析可能原因。\n"
            "如果回答存在信息缺口（资料不足以完全回答），请诚实说明哪些部分是推测。"
        ),
        "sort_order": 10,
    },
    {
        "code": "table_output",
        "name": "表格输出",
        "description": "对比类信息优先用 Markdown 表格整理，表后附口头总结",
        "instructions": (
            "涉及对比、属性、规格、多实体信息时，优先使用 Markdown 表格组织信息。\n"
            "表格应包含清晰的表头，每行对应一个实体/条目。\n"
            "表格之后附一段口头总结，提炼关键发现。"
        ),
        "sort_order": 20,
    },
    {
        "code": "concise",
        "name": "简洁模式",
        "description": "200 字以内直给答案，不铺陈过程",
        "instructions": (
            "回答必须简洁，控制在 200 字以内。\n"
            "直接给出答案核心内容，不展开分析过程、不重复参考资料原文。\n"
            "如果信息不足，用一句话说明即可。"
        ),
        "sort_order": 30,
    },
    {
        "code": "cite_strict",
        "name": "严格引用",
        "description": "每个结论必须至少一个引用标注；无法引用的推断须声明'推测'",
        "instructions": (
            "每个事实性结论必须至少标注一个引用（[来源N] 或 [事实]）。\n"
            "如果某个结论无法从参考资料或图谱事实中直接得出，必须显式标注"
            "「（推测）」并说明推断依据。\n"
            "纯推测且无任何依据的内容不得作为结论呈现。"
        ),
        "sort_order": 40,
    },
]


async def seed_presets(db: AsyncSession) -> int:
    """写入预设技能（幂等：按 code 唯一判断，已存在则跳过）。返回实际新建数。"""
    created = 0
    for preset in _PRESET_SKILLS:
        result = await db.execute(
            select(AgentSkill).where(AgentSkill.code == preset["code"])
        )
        if result.scalar_one_or_none() is not None:
            continue
        skill = AgentSkill(
            name=preset["name"],
            code=preset["code"],
            description=preset["description"],
            instructions=preset["instructions"],
            is_enabled=1,
            is_preset=1,
            sort_order=preset["sort_order"],
        )
        db.add(skill)
        created += 1
    if created:
        await db.commit()
        logger.info("Seeded %d preset agent skills", created)
    return created


class SkillService:
    @staticmethod
    async def list(db: AsyncSession) -> list[dict]:
        """获取全部技能（含禁用），按 sort_order / created_at 排序。"""
        result = await db.execute(
            select(AgentSkill).order_by(
                AgentSkill.sort_order, AgentSkill.created_at
            )
        )
        skills = result.scalars().all()
        return [
            {
                "id": s.id,
                "name": s.name,
                "code": s.code,
                "description": s.description or "",
                "instructions": s.instructions or "",
                "is_enabled": s.is_enabled,
                "is_preset": s.is_preset,
                "sort_order": s.sort_order,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in skills
        ]

    @staticmethod
    async def create(db: AsyncSession, data: dict) -> dict:
        skill = AgentSkill(**data)
        db.add(skill)
        await db.commit()
        await db.refresh(skill)
        return {
            "id": skill.id,
            "name": skill.name,
            "code": skill.code,
            "description": skill.description or "",
            "instructions": skill.instructions or "",
            "is_enabled": skill.is_enabled,
            "is_preset": skill.is_preset,
            "sort_order": skill.sort_order,
        }

    @staticmethod
    async def update(db: AsyncSession, skill_id: str, data: dict) -> dict | None:
        result = await db.execute(
            select(AgentSkill).where(AgentSkill.id == skill_id)
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return None
        now = datetime.now().isoformat()
        for key, value in data.items():
            if value is not None:
                setattr(skill, key, value)
        skill.updated_at = now
        await db.commit()
        await db.refresh(skill)
        return {
            "id": skill.id,
            "name": skill.name,
            "code": skill.code,
            "description": skill.description or "",
            "instructions": skill.instructions or "",
            "is_enabled": skill.is_enabled,
            "is_preset": skill.is_preset,
            "sort_order": skill.sort_order,
        }

    @staticmethod
    async def delete(db: AsyncSession, skill_id: str) -> bool:
        result = await db.execute(
            select(AgentSkill).where(AgentSkill.id == skill_id)
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return False
        if skill.is_preset:
            raise ValueError("预设技能不能删除，只能禁用")
        await db.delete(skill)
        await db.commit()
        return True

    @staticmethod
    async def resolve(db: AsyncSession, skill_ids: list[str]) -> list[dict]:
        """按 id 批量取启用中的技能；id 不存在/已禁用的静默跳过。"""
        if not skill_ids:
            return []
        result = await db.execute(
            select(AgentSkill).where(
                AgentSkill.id.in_(skill_ids),
                AgentSkill.is_enabled == 1,
            )
        )
        skills = result.scalars().all()
        return [
            {
                "id": s.id,
                "name": s.name,
                "code": s.code,
                "description": s.description or "",
                "instructions": (s.instructions or "").strip(),
            }
            for s in skills
            if (s.instructions or "").strip()  # 过滤空指令
        ]
