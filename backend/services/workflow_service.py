"""工作流（Workflow）定义层：CRUD + 定义静态校验。"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Workflow

NODE_TYPES = {"start", "end", "agent", "service", "llm", "condition", "code"}


def parse_definition(raw: str | None) -> dict:
    if not raw:
        return {"nodes": [], "edges": []}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"nodes": [], "edges": []}
    except (ValueError, TypeError):
        return {"nodes": [], "edges": []}


def validate_definition(definition: dict) -> str | None:
    """静态校验工作流定义，返回错误信息；None 表示通过。

    允许空定义（空 draft，运行前会再校验），结构非法才报错。
    """
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return "定义格式错误：nodes/edges 须为数组"

    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(set(ids)):
        return "节点 id 重复"

    start_count = 0
    end_count = 0
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            return "存在缺少 id 的节点"
        if n.get("type") not in NODE_TYPES:
            return f"未知节点类型：{n.get('type')}"
        if n.get("type") == "start":
            start_count += 1
        elif n.get("type") == "end":
            end_count += 1

    if start_count != 1:
        return "必须恰好一个「开始」节点"
    if end_count < 1:
        return "至少一个「结束」节点"

    id_set = set(ids)
    out_degree: dict[str, int] = {}
    for e in edges:
        if not isinstance(e, dict):
            return "边格式错误"
        if e.get("source") not in id_set:
            return f"边 source 指向不存在的节点：{e.get('source')}"
        if e.get("target") not in id_set:
            return f"边 target 指向不存在的节点：{e.get('target')}"
        if e["source"] == e["target"]:
            return "节点不能连接自己"
        out_degree[e["source"]] = out_degree.get(e["source"], 0) + 1

    # 开始节点不能有入边；结束节点不能有出边
    node_by_id = {n["id"]: n for n in nodes}
    for e in edges:
        src_type = (node_by_id.get(e["source"]) or {}).get("type")
        tgt_type = (node_by_id.get(e["target"]) or {}).get("type")
        if tgt_type == "start":
            return "「开始」节点之前不能再连接其他节点"
        if src_type == "end":
            return "「结束」节点之后不能再连接其他节点"

    if _has_cycle(nodes, edges):
        return "工作流存在循环，不能包含环"
    return None


def _has_cycle(nodes: list, edges: list) -> bool:
    id_set = {n["id"] for n in nodes}
    indegree = {nid: 0 for nid in id_set}
    adj: dict[str, list[str]] = {nid: [] for nid in id_set}
    for e in edges:
        indegree[e["target"]] += 1
        adj[e["source"]].append(e["target"])
    queue = [nid for nid in id_set if indegree[nid] == 0]
    seen = 0
    while queue:
        nid = queue.pop()
        seen += 1
        for t in adj[nid]:
            indegree[t] -= 1
            if indegree[t] == 0:
                queue.append(t)
    return seen != len(id_set)


class WorkflowService:
    @staticmethod
    async def list(db: AsyncSession) -> list[dict]:
        result = await db.execute(select(Workflow).order_by(Workflow.created_at))
        rows = result.scalars().all()
        out = []
        for w in rows:
            d = parse_definition(w.definition)
            out.append({
                "id": w.id,
                "name": w.name,
                "description": w.description or "",
                "node_count": len(d.get("nodes", [])),
                "edge_count": len(d.get("edges", [])),
                "is_published": w.is_published,
                "created_at": w.created_at,
                "updated_at": w.updated_at,
            })
        return out

    @staticmethod
    async def get(db: AsyncSession, workflow_id: str) -> dict | None:
        row = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        w = row.scalar_one_or_none()
        if not w:
            return None
        return {
            "id": w.id,
            "name": w.name,
            "description": w.description or "",
            "definition": parse_definition(w.definition),
            "is_published": w.is_published,
            "created_at": w.created_at,
            "updated_at": w.updated_at,
        }

    @staticmethod
    async def create(db: AsyncSession, data: dict) -> dict:
        data = dict(data)
        if isinstance(data.get("definition"), dict):
            data["definition"] = json.dumps(data["definition"], ensure_ascii=False)
        wf = Workflow(**data)
        db.add(wf)
        await db.commit()
        await db.refresh(wf)
        return {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description or "",
            "definition": parse_definition(wf.definition),
            "created_at": wf.created_at,
        }

    @staticmethod
    async def update(db: AsyncSession, workflow_id: str, data: dict) -> dict | None:
        row = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = row.scalar_one_or_none()
        if not wf:
            return None
        data = dict(data)
        if isinstance(data.get("definition"), dict):
            data["definition"] = json.dumps(data["definition"], ensure_ascii=False)
        for key, value in data.items():
            if value is not None:
                setattr(wf, key, value)
        wf.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(wf)
        return {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description or "",
            "definition": parse_definition(wf.definition),
            "created_at": wf.created_at,
            "updated_at": wf.updated_at,
        }

    @staticmethod
    async def delete(db: AsyncSession, workflow_id: str) -> bool:
        row = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = row.scalar_one_or_none()
        if not wf:
            return False
        await db.delete(wf)
        await db.commit()
        return True
