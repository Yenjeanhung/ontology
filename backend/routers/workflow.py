"""工作流路由：定义 CRUD + 运行（SSE）+ 运行记录 + 节点面板数据源。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio
from datetime import datetime

from database import get_db
from config import settings
from models import WorkflowRun
from schemas import (HumanBatchDecisionRequest, HumanDecisionRequest,
                     ResumeRunRequest, RunWorkflowRequest, WorkflowSaveRequest)
from services.agent_service import AgentService
from services.human_task_service import HumanTaskService
from services.kb_service import KBService
from services.notification_channel import NotificationChannel
from services.skill_service import SkillService
from services.workflow_engine import resume_run_background, resume_run_stream, run_stream
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
    {"type": "human", "name": "人工", "icon": "👤", "desc": "人工处理 · 审批/填表"},
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
        run_stream(workflow_id, definition, req.inputs or {},
                   trigger_source="manual"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _preview(obj, max_keys=6, max_len=80):
    """压缩预览：仅取前若干 key，value 截断，避免大对象撑爆列表接口。"""
    if not isinstance(obj, dict):
        return obj
    out = {}
    for i, (k, v) in enumerate(obj.items()):
        if i >= max_keys:
            break
        s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
        out[k] = s[:max_len] + ("…" if len(s) > max_len else "")
    return out


@router.get("/workflows/{workflow_id}/runs")
async def list_workflow_runs(workflow_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow_id)
            .order_by(WorkflowRun.started_at.desc())
            .limit(settings.WORKFLOW_KEEP_RUNS)
        )
    ).scalars().all()

    out = []
    for r in rows:
        node_states = _json(r.node_states) or {}
        ns = [s for s in node_states.values() if isinstance(s, dict) and s.get("status")]
        out.append({
            "id": r.id, "workflow_id": r.workflow_id, "status": r.status,
            "started_at": r.started_at, "finished_at": r.finished_at,
            "duration_ms": r.duration_ms, "error": r.error or "",
            "node_count": len(ns),
            "node_summary": {
                "succeeded": sum(1 for s in ns if s.get("status") == "succeeded"),
                "failed": sum(1 for s in ns if s.get("status") == "failed"),
                "skipped": sum(1 for s in ns if s.get("status") == "skipped"),
            },
            "input_preview": _preview(_json(r.inputs)),
            "output_preview": _preview(_json(r.outputs)),
            "trigger_source": r.trigger_source, "schedule_id": r.schedule_id,
        })
    return out


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


@router.delete("/workflows/{workflow_id}/runs/{run_id}")
async def delete_workflow_run(workflow_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(WorkflowRun, run_id)
    if not row or row.workflow_id != workflow_id:
        raise HTTPException(404, "运行记录不存在")
    # 连带清理该运行的人工任务（含待办）
    await HumanTaskService.delete_of_run(db, run_id)
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.post("/workflows/{workflow_id}/runs/{run_id}/cancel")
async def cancel_workflow_run(workflow_id: str, run_id: str, db: AsyncSession = Depends(get_db)):
    """取消运行：正在执行/等待人工处理的运行都可取消，等待中的任务一并作废。"""
    row = await db.get(WorkflowRun, run_id)
    if not row or row.workflow_id != workflow_id:
        raise HTTPException(404, "运行记录不存在")
    if row.status in ("succeeded", "failed", "cancelled"):
        return {"ok": True, "status": row.status, "cancelled_tasks": 0}
    row.status = "cancelled"
    row.finished_at = datetime.now().isoformat()
    cancelled = await HumanTaskService.cancel_pending_of_run(db, run_id)
    await db.commit()
    return {"ok": True, "status": "cancelled", "cancelled_tasks": cancelled}


@router.post("/workflows/{workflow_id}/runs/{run_id}/resume")
async def resume_workflow_run(workflow_id: str, run_id: str, req: ResumeRunRequest,
                              db: AsyncSession = Depends(get_db)):
    """人工任务处理完成后续跑（SSE）。仅在 run 处于 waiting 时可用。"""
    row = await db.get(WorkflowRun, run_id)
    if not row or row.workflow_id != workflow_id:
        raise HTTPException(404, "运行记录不存在")
    return StreamingResponse(
        resume_run_stream(run_id, req.task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────── 人工节点任务 ───────────────────────


@router.get("/workflow/human-tasks")
async def list_human_tasks(status: str | None = None, workflow_id: str | None = None,
                           limit: int = 50, db: AsyncSession = Depends(get_db)):
    """待办列表：默认全部，status=pending 只看待处理。"""
    return await HumanTaskService.list_tasks(
        db, status=status, workflow_id=workflow_id, limit=limit)


@router.get("/workflow/human-tasks/{task_id}")
async def get_human_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await HumanTaskService.get(db, task_id)
    if not task:
        raise HTTPException(404, "人工任务不存在")
    return task


@router.post("/workflow/human-tasks/{task_id}/decision")
async def decide_human_task(task_id: str, req: HumanDecisionRequest,
                            db: AsyncSession = Depends(get_db)):
    """处理单条人工任务。

    auto_resume=false 时前端需自行调用 resume SSE 续播（编辑器场景，已确认的主路径）。
    """
    try:
        updated = await HumanTaskService.decide(
            db, task_id, decision=req.decision, comment=req.comment,
            data=req.data or {}, operator=req.operator)
    except ValueError as e:
        # 表单字段级错误以 JSON 串抛出，转为结构化响应
        raw = str(e)
        if raw.startswith("{"):
            try:
                raise HTTPException(400, {"message": "表单校验未通过", "field_errors": json.loads(raw)})
            except ValueError:
                pass
        raise HTTPException(400, raw)
    if updated is None:
        raise HTTPException(409, "该任务已处理或不存在")

    await NotificationChannel.dispatch(NotificationChannel.TASK_DECIDED, {
        "task_id": task_id, "run_id": updated["run_id"],
        "workflow_name": updated["workflow_name"], "decision": updated["decision"],
        "operator": updated["operator"], "comment": updated["comment"],
        "decided_at": updated["decided_at"],
    })

    if req.auto_resume:
        # 后台续跑：消费完事件流并落库，不占用当前请求
        asyncio.create_task(resume_run_background(updated["run_id"], task_id))
    return {"ok": True, "task": updated, "resumed": req.auto_resume}


@router.post("/workflow/human-tasks/batch-decision")
async def batch_decide_human_tasks(req: HumanBatchDecisionRequest,
                                   db: AsyncSession = Depends(get_db)):
    """批量处理（仅审批模式任务；表单任务需逐条填写）。"""
    if not req.task_ids:
        raise HTTPException(400, "请选择要处理的任务")
    try:
        result = await HumanTaskService.batch_decide(
            db, req.task_ids, decision=req.decision,
            comment=req.comment, operator=req.operator)
    except ValueError as e:
        raise HTTPException(400, str(e))

    resumed = []
    if req.auto_resume:
        # 逐条查询 run_id 后后台续跑（部分成功语义：失败条目不触发）
        for tid in result["succeeded"]:
            task = await HumanTaskService.get(db, tid)
            if task:
                asyncio.create_task(resume_run_background(task["run_id"], tid))
                resumed.append(tid)
    return {**result, "resumed": resumed}


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
