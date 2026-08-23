"""工作流路由：定义 CRUD + 运行（SSE）+ 运行记录 + 节点面板数据源。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import WorkflowRun
from schemas import RunWorkflowRequest, WorkflowSaveRequest
from services.agent_service import AgentService
from services.kb_service import KBService
from services.skill_service import SkillService
from services.workflow_engine import run_stream
from services.workflow_service import WorkflowService, validate_definition

router = APIRouter()

NODE_TYPES = [
    {"type": "start", "name": "开始", "icon": "▶", "desc": "运行入口 · 声明入参"},
    {"type": "end", "name": "结束", "icon": "■", "desc": "运行出口 · 汇总输出"},
    {"type": "agent", "name": "智能体", "icon": "🤖", "desc": "OAG 问答 · KB+技能"},
    {"type": "service", "name": "实体服务", "icon": "⚙️", "desc": "沙箱动作 · 实体"},
    {"type": "llm", "name": "大模型", "icon": "✨", "desc": "通用 LLM 补全"},
    {"type": "condition", "name": "条件分支", "icon": "⇄", "desc": "true / false 路由"},
    {"type": "code", "name": "代码", "icon": "</>", "desc": "沙箱 Python"},
]


def _json(s):
    try:
        return json.loads(s) if s else {}
    except (ValueError, TypeError):
        return {}


# ─────────────────────── 工作流 CRUD ───────────────────────


@router.get("/workflows")
async def list_workflows(db: AsyncSession = Depends(get_db)):
    return await WorkflowService.list(db)


@router.post("/workflows")
async def create_workflow(req: WorkflowSaveRequest, db: AsyncSession = Depends(get_db)):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "工作流名称不能为空")
    # 新建允许空 draft（画布里再搭）；结构非法仍拒绝
    definition = req.definition or {"nodes": [], "edges": []}
    err = validate_definition(definition)
    if err and definition.get("nodes"):
        raise HTTPException(400, err)
    return await WorkflowService.create(
        db, {"name": name, "description": req.description or "", "definition": definition},
    )


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    wf = await WorkflowService.get(db, workflow_id)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    return wf


@router.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, req: WorkflowSaveRequest, db: AsyncSession = Depends(get_db)):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "工作流名称不能为空")
    data = {"name": name, "description": req.description or ""}
    if req.definition is not None:
        err = validate_definition(req.definition)
        if err:
            raise HTTPException(400, err)
        data["definition"] = req.definition
    updated = await WorkflowService.update(db, workflow_id, data)
    if not updated:
        raise HTTPException(404, "工作流不存在")
    return updated


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    if not await WorkflowService.delete(db, workflow_id):
        raise HTTPException(404, "工作流不存在")
    return {"status": "deleted"}


# ─────────────────────── 运行 ───────────────────────


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: str, req: RunWorkflowRequest, db: AsyncSession = Depends(get_db)):
    wf = await WorkflowService.get(db, workflow_id)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    definition = wf["definition"]
    err = validate_definition(definition)
    if err:
        raise HTTPException(400, err)
    return StreamingResponse(
        run_stream(workflow_id, definition, req.inputs or {}),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/workflows/{workflow_id}/runs")
async def list_workflow_runs(workflow_id: str, db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.started_at.desc())
        .limit(50)
    )
    return [
        {
            "id": r.id, "workflow_id": r.workflow_id, "status": r.status,
            "started_at": r.started_at, "finished_at": r.finished_at,
            "duration_ms": r.duration_ms, "error": r.error or "",
        }
        for r in rows.scalars().all()
    ]


@router.get("/workflows/{workflow_id}/runs/{run_id}")
async def get_workflow_run(workflow_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(WorkflowRun, run_id)
    if not row or row.workflow_id != workflow_id:
        raise HTTPException(404, "运行记录不存在")
    return {
        "id": row.id, "workflow_id": row.workflow_id, "status": row.status,
        "inputs": _json(row.inputs), "outputs": _json(row.outputs),
        "node_states": _json(row.node_states), "error": row.error or "",
        "started_at": row.started_at, "finished_at": row.finished_at,
        "duration_ms": row.duration_ms,
    }


# ─────────────────────── 节点面板数据源 ───────────────────────


@router.get("/workflow/palette")
async def workflow_palette(db: AsyncSession = Depends(get_db)):
    kbs = await KBService.list_all(db)
    skills = [s for s in await SkillService.list(db) if s.get("is_enabled")]
    # 工作流可引用的智能体：启用即可（未绑 KB 的以「无知识库纯对话」模式执行）
    agents = [a for a in await AgentService.list(db) if a.get("is_enabled")]
    return {
        "node_types": NODE_TYPES,
        "kbs": [{"id": kb["id"], "name": kb["name"]} for kb in kbs],
        "skills": [{"id": s["id"], "name": s["name"], "code": s["code"]} for s in skills],
        "agents": [
            {"id": a["id"], "name": a["name"], "kb_id": a["kb_id"], "kb_name": a.get("kb_name")}
            for a in agents
        ],
    }
