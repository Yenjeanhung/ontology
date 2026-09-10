"""
定时调度引擎（Scheduler Engine）

基于 APScheduler 的进程内调度器（单机部署）：
  - 服务启动（FastAPI lifespan）时从 DB 加载所有启用计划并注册为 job；
  - 到点触发 on_trigger：调用 workflow_engine.run_stream 执行关联工作流；
  - 更新计划的 last_run_at / last_status / consecutive_failures；
  - 连续失败达阈值且未静默 → 通过 notifications 聚合计数在右上角消息中心提示；
  - once 类型执行完成后自动停用。

本模块只依赖 scheduler_service 的校验/计算函数与 workflow_engine 的执行能力。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from config import settings
from database import async_session
from models import Schedule, Workflow
from services import scheduler_service as svc
from services.workflow_engine import run_stream

logger = logging.getLogger("scheduler_engine")

_scheduler: AsyncIOScheduler = None
_tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
_semaphore = None  # 限制并发触发执行数


# ───────────────────────────── 生命周期 ─────────────────────────────

async def start():
    """启动调度引擎：从 DB 恢复所有启用计划。"""
    global _scheduler, _semaphore
    if not settings.SCHEDULER_ENABLED:
        logger.info("SCHEDULER_ENABLED=false，跳过调度引擎启动")
        return

    _semaphore = asyncio.Semaphore(settings.SCHEDULER_MAX_CONCURRENT_RUNS)
    _scheduler = AsyncIOScheduler(timezone=_tz)

    async with async_session() as db:
        rows = (await db.execute(
            select(Schedule).where(Schedule.enabled == 1)
        )).scalars().all()
        for s in rows:
            _add_job(s)

    _scheduler.start()
    logger.info("调度引擎已启动，已加载 %d 个启用计划", len(_scheduler.get_jobs()))
    # 将调度器真实排期回写 DB：服务重启后网格可能变化，避免列表展示过期时间
    for job in _scheduler.get_jobs():
        await _persist_next_run(job.id)


async def shutdown():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("调度引擎已停止")


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


# ───────────────────────────── job 管理 ─────────────────────────────

def _parse_dt(value) -> Optional[datetime]:
    """解析 ISO 时间字符串，失败返回 None（不抛异常）。"""
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.replace(tzinfo=_tz) if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


def _build_trigger(schedule: Schedule):
    """根据计划类型构造 APScheduler 触发器。"""
    cfg = json.loads(schedule.trigger_config or "{}")
    if schedule.trigger == "cron":
        fields = ["minute", "hour", "day", "month", "day_of_week"]
        kwargs = {f: str(cfg.get(f, "*")) for f in fields}
        return CronTrigger(timezone=_tz, **kwargs)
    if schedule.trigger == "interval":
        unit = cfg.get("unit", "hours")
        every = float(cfg.get("every", 1))
        kwargs = {"minutes": every} if unit == "minutes" else \
                 {"hours": every} if unit == "hours" else {"days": every}
        # 进程重启重建 job 时，以 DB 记录的 next_run_at 作为网格锚点延续排期。
        # 否则每次重启都按「启动时刻 + interval」重新起算，网格漂移，
        # 列表展示的「下次运行」永远等不到（例如显示 21:48:14 实际排在 21:57:54）。
        start_date = _parse_dt(schedule.next_run_at)
        if start_date is not None:
            return IntervalTrigger(timezone=_tz, start_date=start_date, **kwargs)
        return IntervalTrigger(timezone=_tz, **kwargs)
    if schedule.trigger == "once":
        run_at = _parse_dt(cfg["run_at"])
        if run_at is None:
            raise ValueError(f"once.run_at 非法: {cfg.get('run_at')!r}")
        return DateTrigger(run_time=run_at, timezone=_tz)
    raise ValueError(f"未知触发器: {schedule.trigger}")


def _add_job(schedule: Schedule):
    if _scheduler is None:
        return
    try:
        trigger = _build_trigger(schedule)
        _scheduler.add_job(
            _on_trigger,
            trigger=trigger,
            id=schedule.id,
            args=(schedule.id,),
            replace_existing=True,
            misfire_grace_time=settings.SCHEDULER_MISFIRE_GRACE_SECONDS,
            coalesce=settings.SCHEDULER_COALESCE,
            max_instances=1,
        )
    except Exception as e:
        logger.error("注册计划 job 失败 id=%s err=%s", schedule.id, e)


def _remove_job(schedule_id: str):
    if _scheduler and _scheduler.get_job(schedule_id):
        _scheduler.remove_job(schedule_id)


async def _persist_next_run(schedule_id: str):
    """把 APScheduler 中 job 的真实下次触发时间回写 DB。

    列表展示的 next_run_at 此前只在创建/更新/执行完成时按「理论值」刷新，
    与调度器内存中的实际排期脱节（重启后尤其明显）→ 出现「到点不执行」的假象。
    """
    if not is_running():
        return
    job = _scheduler.get_job(schedule_id)
    nxt = job.next_run_time.isoformat() if (job and job.next_run_time) else None
    async with async_session() as db:
        s = (await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )).scalar_one_or_none()
        if not s:
            return
        s.next_run_at = nxt
        await db.commit()


async def sync_job(schedule_id: str):
    """计划创建/更新/启停后，同步调度器中的 job。"""
    if not is_running():
        return
    async with async_session() as db:
        s = (await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )).scalar_one_or_none()
    if not s:
        _remove_job(schedule_id)
        return
    _remove_job(schedule_id)
    if s.enabled:
        _add_job(s)
        await _persist_next_run(schedule_id)


# ───────────────────────────── 触发回调 ─────────────────────────────

async def _on_trigger(schedule_id: str):
    """到点触发：执行关联工作流并更新计划状态。"""
    if _semaphore:
        await _semaphore.acquire()
    try:
        async with async_session() as db:
            s = (await db.execute(
                select(Schedule).where(Schedule.id == schedule_id)
            )).scalar_one_or_none()
            if not s or not s.enabled:
                return
            wf = (await db.execute(
                select(Workflow).where(Workflow.id == s.workflow_id)
            )).scalar_one_or_none()

        if not wf:
            logger.warning("计划 %s 关联工作流 %s 不存在，跳过", schedule_id, s.workflow_id)
            await _record_result(schedule_id, "failed", "关联工作流不存在")
            return

        inputs = json.loads(s.input_params or "{}")
        definition = json.loads(wf.definition or '{"nodes":[],"edges":[]}')

        status, error = await _run_workflow_sync(s.workflow_id, definition, inputs, schedule_id)
        await _record_result(schedule_id, status, error if status == "failed" else None)

        # once 类型执行完自动停用
        if s.trigger == "once" and s.enabled:
            async with async_session() as db:
                row = (await db.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )).scalar_one_or_none()
                if row:
                    row.enabled = 0
                    row.next_run_at = None
                    row.updated_at = datetime.now().isoformat()
                    await db.commit()
            _remove_job(schedule_id)
    finally:
        if _semaphore:
            _semaphore.release()


async def _run_workflow_sync(workflow_id, definition, inputs, schedule_id) -> tuple:
    """消费 run_stream 直到 [DONE]，返回 (status, error)。"""
    try:
        async for chunk in run_stream(workflow_id, definition, inputs,
                                      trigger_source="schedule", schedule_id=schedule_id):
            if chunk.startswith("data: ") and "[DONE]" in chunk:
                break
            # 解析 workflow_finished 取最终状态
            try:
                payload = json.loads(chunk[len("data: "):])
                if payload.get("type") == "workflow_finished":
                    return payload.get("status", "failed"), payload.get("error")
            except (json.JSONDecodeError, ValueError):
                continue
        return "failed", "未收到 workflow_finished 事件"
    except Exception as e:
        logger.exception("调度执行异常 schedule=%s workflow=%s", schedule_id, workflow_id)
        return "failed", str(e)


async def _record_result(schedule_id: str, status: str, error: str = None):
    """更新计划运行统计；失败时连续失败计数 + 达阈值告警（经 notifications 聚合）。"""
    async with async_session() as db:
        s = (await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )).scalar_one_or_none()
        if not s:
            return
        s.last_run_at = datetime.now(timezone.utc).isoformat()
        s.last_status = status
        if status == "succeeded":
            s.consecutive_failures = 0
        elif status == "waiting":
            # 挂在人工节点等待处理：属正常挂起，不计入连续失败（否则会误报告警）
            pass
        else:
            s.consecutive_failures += 1
        s.updated_at = datetime.now().isoformat()
        # 刷新下次运行时间：优先取调度器 job 的真实排期（与实际触发一致），
        # job 不存在时（如 once 已完成被移除）退回理论计算值
        if s.enabled:
            job = _scheduler.get_job(schedule_id) if is_running() else None
            nxt = job.next_run_time if job else None
            s.next_run_at = nxt.isoformat() if nxt else \
                svc.compute_next_run(s.trigger, json.loads(s.trigger_config or "{}"))
        await db.commit()

        # 告警：连续失败达阈值且未静默
        if (status == "failed" and s.alert_on_failure == 1 and s.muted == 0
                and s.consecutive_failures >= s.max_failures_alert):
            logger.warning("⚠ 计划 %s「%s」连续失败 %d 次（将在右上角消息中心提示）",
                           s.id, s.name, s.consecutive_failures)
            # 告警经 notifications/summary 聚合计数呈现，无需单独落库；
            # 若后续需要明细消息，可在此写入独立通知表。错误信息记入下条运行记录即可。


async def run_now(schedule_id: str):
    """立即执行一次（手动触发，不走调度器时间）。"""
    async with async_session() as db:
        s = (await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )).scalar_one_or_none()
        if not s:
            raise ValueError("计划不存在")
        wf = (await db.execute(
            select(Workflow).where(Workflow.id == s.workflow_id)
        )).scalar_one_or_none()
    if not wf:
        raise ValueError("关联工作流不存在")
    inputs = json.loads(s.input_params or "{}")
    definition = json.loads(wf.definition or '{"nodes":[],"edges":[]}')
    status, error = await _run_workflow_sync(s.workflow_id, definition, inputs, schedule_id)
    await _record_result(schedule_id, status, error if status == "failed" else None)
    return {"status": status, "error": error}
