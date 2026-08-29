"""通知计数中心：SSE 推送，替代前端轮询。

设计目标
--------
浏览器每 30s 拉一次 summary 的问题：页面常开时请求永不停歇，N 个浏览器就是
N 倍压力，且 99% 的请求结果和上次完全一样。

这里改为「事件驱动 + 单条 SSE 长连接」：

- 业务侧变更时只需调用 `hub.notify()`（O(1)，只置一个事件标志，不查库）；
- 后台刷新协程被唤醒后**查一次库**，结果有变化才广播给所有连接；
- 无变更时：零查询、零推送；连接仅靠心跳保活；
- 没有任何订阅者时刷新协程自动退出，彻底空转零成本。

兜底：每 `IDLE_REFRESH_SECONDS` 秒做一次检查，覆盖未埋点的变更来源
（文件处理状态、本体建议、调度告警等由各自的 service 推进，暂未全部埋点）。
由于只在有订阅者时运行，且总是「一次查询广播给所有人」，成本与浏览器数量无关。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

# 无事件时的兜底检查间隔（秒）；事件触发的刷新不受此限制
IDLE_REFRESH_SECONDS = 60
# SSE 心跳间隔（秒）：防止代理/浏览器因长时间无数据断开
HEARTBEAT_SECONDS = 25


class NotificationHub:
    """计数中心：维护订阅者队列 + 最新计数，变更时广播。"""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._dirty = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._current: dict[str, Any] = {}

    # ── 订阅 ──

    async def subscribe(self) -> asyncio.Queue:
        """订阅推送；返回队列，并立即放入当前计数（新连接秒得数据）。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(q)
        self._ensure_worker()
        if self._current:
            try:
                q.put_nowait(self._current)
            except asyncio.QueueFull:
                pass
        else:
            # 尚未缓存任何计数：立即触发一次刷新，避免新连接空等兜底周期
            self._dirty.set()
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
        if not self._subscribers:
            # 最后一个订阅者离开：唤醒协程，让它立即发现无人订阅并退出，
            # 否则要空等到下一个兜底周期
            self._dirty.set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def current(self) -> dict[str, Any]:
        return self._current

    # ── 变更通知 ──

    def notify(self) -> None:
        """业务侧调用：标记计数可能已变化，唤醒刷新协程。

        同步方法、不查库、不 await，可在任意执行路径（含同步函数）中安全调用。
        无人订阅时本操作几乎是零成本：新订阅者会在 subscribe 时触发首轮刷新。
        """
        self._dirty.set()

    # ── 刷新协程 ──

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        logger.info("[notify-hub] 刷新协程启动")
        try:
            while True:
                try:
                    await asyncio.wait_for(self._dirty.wait(), timeout=IDLE_REFRESH_SECONDS)
                except asyncio.TimeoutError:
                    pass
                self._dirty.clear()

                # 无人订阅 → 退出，避免无意义地查库
                if not self._subscribers:
                    self._worker = None
                    logger.info("[notify-hub] 无订阅者，刷新协程退出")
                    return

                try:
                    summary = await self._compute()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("[notify-hub] 计算通知计数失败")
                    await asyncio.sleep(2)
                    continue

                if summary != self._current:
                    self._current = summary
                    self._broadcast(summary)
        except asyncio.CancelledError:
            logger.info("[notify-hub] 刷新协程已取消")
            raise

    def _broadcast(self, summary: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(summary)
            except asyncio.QueueFull:
                # 消费极慢的连接：丢弃旧值，只保留最新（下次刷新会补上）
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(summary)
                except asyncio.QueueFull:
                    pass

    async def _compute(self) -> dict[str, Any]:
        from database import async_session

        async with async_session() as db:
            return await compute_summary(db)


async def compute_summary(db) -> dict[str, Any]:
    """计算通知计数聚合。

    放在 service 层而非 routers，使 hub（推送）与接口（HTTP 拉取）共用同一实现，
    依赖方向保持单向：routers → services，避免循环导入。
    """
    # 延迟导入 models：本模块可能在 models 加载完成前被引用
    from models import File, OntologySuggestion, Schedule, WorkflowHumanTask

    suggestions = int(
        (await db.execute(
            select(func.count()).where(OntologySuggestion.status == "ready")
        )).scalar() or 0
    )
    processing = int(
        (await db.execute(
            select(func.count()).where(File.status.in_(["processing", "uploading"]))
        )).scalar() or 0
    )
    failed = int(
        (await db.execute(
            select(func.count()).where(File.status == "failed")
        )).scalar() or 0
    )
    # 定时调度：已达告警阈值且未静默的计划数
    schedule_alerts = int(
        (await db.execute(
            select(func.count()).where(
                Schedule.alert_on_failure == 1,
                Schedule.muted == 0,
                Schedule.consecutive_failures >= Schedule.max_failures_alert,
            )
        )).scalar() or 0
    )
    # 人工节点待办：等待人工处理的任务数
    human_tasks = int(
        (await db.execute(
            select(func.count()).where(WorkflowHumanTask.status == "pending")
        )).scalar() or 0
    )

    items = []
    if human_tasks:
        items.append({"key": "human_tasks", "label": "待处理人工任务", "count": human_tasks, "to": "/human-tasks"})
    if suggestions:
        items.append({"key": "suggestions", "label": "待审核本体建议", "count": suggestions, "to": "/ontology/suggestions"})
    if processing:
        items.append({"key": "files_processing", "label": "文件处理中", "count": processing, "to": "/files"})
    if failed:
        items.append({"key": "files_failed", "label": "文件处理失败", "count": failed, "to": "/kb"})
    if schedule_alerts:
        items.append({"key": "schedule_alerts", "label": "定时任务连续失败", "count": schedule_alerts, "to": "/schedules"})

    return {
        "suggestions": suggestions,
        "files_processing": processing,
        "files_failed": failed,
        "schedule_alerts": schedule_alerts,
        "human_tasks": human_tasks,
        "total": suggestions + processing + failed + schedule_alerts + human_tasks,
        "items": items,
    }


hub = NotificationHub()
