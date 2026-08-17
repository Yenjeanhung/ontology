"""技能分组（AgentSkillGroup）管理服务。

分组为全局组织结构（不按知识库隔离），支持任意层级嵌套：
- parent_id 指向父分组，NULL = 根级；树形结构由前端按扁平列表构建
- 移动分组时做环检测（不能移到自己或其子分组名下）
- 删除分组：子分组提升到被删组的 parent，整个子树内技能移入「未分组」，
  单事务完成，绝不删除技能本身
- group_paths / resolve_group_path 支撑导出回流（group_path "A/B" ↔ 分组树）
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentSkill, AgentSkillGroup

logger = logging.getLogger(__name__)


def _to_dict(g: AgentSkillGroup, skill_count: int = 0) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "parent_id": g.parent_id,
        "sort_order": g.sort_order,
        "skill_count": skill_count,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }


async def _load_all(db: AsyncSession) -> list[AgentSkillGroup]:
    result = await db.execute(
        select(AgentSkillGroup).order_by(AgentSkillGroup.sort_order, AgentSkillGroup.created_at)
    )
    return list(result.scalars().all())


async def resolve_group_path(db: AsyncSession, parts: list[str]) -> AgentSkillGroup | None:
    """按路径逐级「找或建」（导出回流用）；parts 为空返回 None。

    例 ["写作", "润色"] → 根级「写作」→ 其下「润色」，缺哪级建哪级。
    """
    if not parts:
        return None
    parent_id: str | None = None
    group: AgentSkillGroup | None = None
    for name in parts:
        cond = (
            AgentSkillGroup.parent_id.is_(None) if parent_id is None
            else AgentSkillGroup.parent_id == parent_id
        )
        result = await db.execute(
            select(AgentSkillGroup).where(AgentSkillGroup.name == name, cond)
        )
        group = result.scalar_one_or_none()
        if group is None:
            group = AgentSkillGroup(name=name, parent_id=parent_id)
            db.add(group)
            await db.flush()
        parent_id = group.id
    await db.commit()
    return group


async def group_paths(db: AsyncSession) -> dict[str, str]:
    """返回 {group_id: "A/B"} 全量路径表（导出用）。"""
    groups = await _load_all(db)
    by_id = {g.id: g for g in groups}
    out: dict[str, str] = {}

    def path_of(g: AgentSkillGroup) -> str:
        parent = by_id.get(g.parent_id) if g.parent_id else None
        return f"{path_of(parent)}/{g.name}" if parent else g.name

    for g in groups:
        out[g.id] = path_of(g)
    return out


class SkillGroupService:
    @staticmethod
    async def list(db: AsyncSession) -> list[dict]:
        """扁平列表（前端建树）；skill_count 为该组直属技能数，子孙计数由前端累加。"""
        groups = await _load_all(db)
        counts = dict((await db.execute(
            select(AgentSkill.group_id, func.count())
            .where(AgentSkill.group_id.is_not(None))
            .group_by(AgentSkill.group_id)
        )).all())
        return [_to_dict(g, counts.get(g.id, 0)) for g in groups]

    @staticmethod
    async def create(
        db: AsyncSession, name: str,
        parent_id: str | None = None, sort_order: int = 0,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("分组名称不能为空")
        group = AgentSkillGroup(name=name, parent_id=parent_id, sort_order=sort_order)
        db.add(group)
        await db.commit()
        await db.refresh(group)
        return _to_dict(group)

    @staticmethod
    async def update(db: AsyncSession, group_id: str, data: dict) -> dict | None:
        """name / sort_order 常规更新；parent_id 按「键存在即生效」处理（显式 None = 移到根级）。"""
        group = await db.get(AgentSkillGroup, group_id)
        if not group:
            return None
        if "parent_id" in data:
            new_parent = data["parent_id"]
            if new_parent == group_id:
                raise ValueError("不能把分组移动到自己名下")
            if new_parent:
                # 环检测：沿新父的祖先链向上走，遇到自己即拒绝
                by_id = {g.id: g for g in await _load_all(db)}
                cursor: str | None = new_parent
                while cursor:
                    if cursor == group_id:
                        raise ValueError("不能把分组移动到其子分组名下")
                    node = by_id.get(cursor)
                    cursor = node.parent_id if node else None
            group.parent_id = new_parent
        if data.get("name") is not None:
            name = str(data["name"]).strip()
            if name:
                group.name = name
        if data.get("sort_order") is not None:
            group.sort_order = int(data["sort_order"])
        group.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(group)
        return _to_dict(group)

    @staticmethod
    async def delete(db: AsyncSession, group_id: str) -> dict | None:
        """删除分组：子分组提升到其 parent、整个子树技能移入未分组。单事务，绝不删技能。"""
        group = await db.get(AgentSkillGroup, group_id)
        if not group:
            return None
        children_of: dict[str | None, list[str]] = defaultdict(list)
        for g in await _load_all(db):
            children_of[g.parent_id].append(g.id)
        # 收集子树（BFS；环由 update 阻断，这里防御性用 seen）
        subtree: list[str] = []
        stack, seen = [group_id], {group_id}
        while stack:
            gid = stack.pop()
            subtree.append(gid)
            for cid in children_of.get(gid, []):
                if cid not in seen:
                    seen.add(cid)
                    stack.append(cid)
        direct_children = children_of.get(group_id, [])
        skill_rows = (await db.execute(
            select(AgentSkill.id).where(AgentSkill.group_id.in_(subtree))
        )).all()
        # ① 直接子分组提升 ② 子树技能 → 未分组 ③ 删自身，最后一次性 commit
        await db.execute(
            update(AgentSkillGroup)
            .where(AgentSkillGroup.parent_id == group_id)
            .values(parent_id=group.parent_id)
        )
        await db.execute(
            update(AgentSkill)
            .where(AgentSkill.group_id.in_(subtree))
            .values(group_id=None)
        )
        await db.delete(group)
        await db.commit()
        return {
            "ok": True,
            "children_promoted": len(direct_children),
            "skills_ungrouped": len(skill_rows),
        }
