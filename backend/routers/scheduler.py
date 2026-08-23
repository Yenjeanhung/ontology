"""定时调度路由：计划 CRUD + 启停 + 立即执行 + 运行历史 + cron 预览/校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Workflow
from services import scheduler_service as svc
from services import scheduler_engine as engine
from services.scheduler_service import validate_trigger, trigger_summary

router = APIRouter()


@router.get("/schedules")
async def list_schedules(db: AsyncSession = Depends(get_db)):
    """调度计划列表（含关联工作流名 + 触发器摘要）。"""
    return await svc.list_schedules()


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    s = await svc.get_schedule(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="计划不存在")
    return s


@router.post("/schedules")
async def create_schedule(payload: dict, db: AsyncSession = Depends(get_db)):
    try:
        s = await svc.create_schedule(payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await engine.sync_job(s["id"])
    return s


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    try:
        s = await svc.update_schedule(schedule_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await engine.sync_job(schedule_id)
    return s


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    await svc.delete_schedule(schedule_id)
    await engine.sync_job(schedule_id)  # 内部会按 DB 是否存在自动移除 job
    return {"ok": True}


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str, body: dict = None, db: AsyncSession = Depends(get_db)):
    enabled = bool((body or {}).get("enabled", True))
    try:
        s = await svc.set_enabled(schedule_id, enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await engine.sync_job(schedule_id)
    return s


@router.post("/schedules/{schedule_id}/run-now")
async def run_now(schedule_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await engine.run_now(schedule_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/schedules/{schedule_id}/runs")
async def schedule_runs(schedule_id: str, db: AsyncSession = Depends(get_db)):
    return await svc.list_runs(schedule_id)


@router.post("/schedules/validate-cron")
async def validate_cron(payload: dict):
    """校验 cron 表达式语法（前端高级模式兜底）。"""
    fields = ["minute", "hour", "day", "month", "day_of_week"]
    cfg = {f: str(payload.get(f, "*")) for f in fields}
    expr = f"{cfg['minute']} {cfg['hour']} {cfg['day']} {cfg['month']} {cfg['day_of_week']}"
    err = validate_trigger("cron", cfg)
    if err:
        return {"valid": False, "error": err, "expression": expr}
    return {"valid": True, "expression": expr, "summary": trigger_summary("cron", cfg)}


@router.post("/schedules/preview-next-run")
async def preview_next_run(payload: dict):
    """根据触发器配置预览下次运行时间（ISO，含时区）。"""
    trigger = payload.get("trigger")
    cfg = payload.get("trigger_config", {})
    err = validate_trigger(trigger, cfg)
    if err:
        raise HTTPException(status_code=422, detail=err)
    nxt = svc.compute_next_run(trigger, cfg)
    return {"next_run_at": nxt, "summary": trigger_summary(trigger, cfg)}
