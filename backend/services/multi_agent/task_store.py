# -*- coding: utf-8 -*-
"""多智能体任务库：可配置、可编辑的任务提示词模板（CRUD + 预置播种）。

任务 = {name, prompt, agents}：
- prompt 是任务提示词模板，支持 ``{question}`` 占位符——运行时由前端用用户
  输入的具体问题替换；模板未含占位符时，问题拼接在模板之后（见前端 composeTask）；
- agents 是选中该任务时应用的默认团队编制（可选能力智能体 id 数组），用户
  仍可在页面上临时改勾选。

预置任务仅在表完全为空（首次使用）时播种一次；用户删除后不复活，
避免覆盖用户自定义的任务库。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import MultiAgentTask

# 预置任务（is_preset=1）：覆盖研判 / 探查 / 写作 / 总结四类典型用法
PRESET_TASKS: list[dict] = [
    {
        "name": "研判 · 主题盘点",
        "prompt": "请围绕主题「{question}」检索平台知识库，归纳主题的核心脉络、关键结论与分歧点，"
                  "按要点逐条输出，并标注引用来源。",
        "agents": ["retriever", "data_agent", "graph_agent", "critic"],
    },
    {
        "name": "研判 · 图谱探查",
        "prompt": "请在实体图谱中检索与「{question}」最相关的实体及其关系，梳理它们的关键结构，"
                  "评估图谱覆盖面，指出缺失的关键节点或关系。",
        "agents": ["graph_agent", "critic"],
    },
    {
        "name": "数据 · 台账统计",
        "prompt": "请查询实体台账中与「{question}」相关的记录：给出总量与分类统计、"
                  "最新明细（编号/级别/状态等关键字段），并做简要研判与处置建议。",
        "agents": ["data_agent", "critic"],
    },
    {
        "name": "写作 · 简报撰写",
        "prompt": "请基于平台知识库检索结果，撰写一份关于「{question}」的简报："
                  "分「背景 / 要点 / 建议」三个小节，每节标注引用来源。",
        "agents": ["retriever", "critic"],
    },
    {
        "name": "总结 · 内容摘要",
        "prompt": "请对以下内容进行总结：提炼最显著的结构与关键选择，突出三个要点，"
                  "最后给出一段一句话摘要。\n\n{question}",
        "agents": ["critic"],
    },
]

_FIELDS = ("id", "name", "prompt", "is_preset", "sort_order", "created_at", "updated_at")


def _dump(row: MultiAgentTask) -> dict:
    data = {k: getattr(row, k) for k in _FIELDS}
    try:
        data["agents"] = json.loads(row.agents or "[]")
    except (TypeError, ValueError):
        data["agents"] = []
    return data


async def ensure_seed(db: AsyncSession) -> None:
    """表为空时播种预置任务（仅首次使用；删除后不复活）。"""
    if (await db.execute(select(MultiAgentTask.id).limit(1))).first() is not None:
        return
    for i, t in enumerate(PRESET_TASKS):
        db.add(MultiAgentTask(
            name=t["name"],
            prompt=t["prompt"],
            agents=json.dumps(t["agents"], ensure_ascii=False),
            is_preset=1,
            sort_order=i,
        ))
    await db.commit()


async def list_tasks(db: AsyncSession) -> list[dict]:
    await ensure_seed(db)
    rows = (await db.execute(
        select(MultiAgentTask).order_by(MultiAgentTask.sort_order, MultiAgentTask.created_at)
    )).scalars().all()
    return [_dump(r) for r in rows]


async def create_task(
    db: AsyncSession, name: str, prompt: str, agents: Optional[list[str]] = None,
    sort_order: int = 0, is_preset: int = 0,
) -> dict:
    row = MultiAgentTask(
        name=name,
        prompt=prompt,
        agents=json.dumps(agents or [], ensure_ascii=False),
        is_preset=is_preset,
        sort_order=sort_order,
    )
    db.add(row)
    await db.commit()
    return _dump(row)


async def update_task(db: AsyncSession, task_id: str, data: dict) -> Optional[dict]:
    row = await db.get(MultiAgentTask, task_id)
    if not row:
        return None
    if "name" in data and data["name"] is not None:
        row.name = data["name"]
    if "prompt" in data and data["prompt"] is not None:
        row.prompt = data["prompt"]
    if "agents" in data and data["agents"] is not None:
        row.agents = json.dumps(data["agents"], ensure_ascii=False)
    if "sort_order" in data and data["sort_order"] is not None:
        row.sort_order = data["sort_order"]
    row.updated_at = datetime.now().isoformat()
    await db.commit()
    return _dump(row)


async def delete_task(db: AsyncSession, task_id: str) -> bool:
    row = await db.get(MultiAgentTask, task_id)
    if not row:
        return False
    await db.delete(row)
    await db.commit()
    return True
