"""操作审计 + 认证日志：异步队列批量落库，避免阻塞业务请求。

两类日志分开存：
- `auth_logs`：登录/登出/失败/被踢/过期/改密/锁定（安全基线，保留更久）
- `audit_logs`：业务操作（增删改、权限变更、配置变更、导出等）

中间件负责自动采集请求元数据（method/path/状态码/耗时/IP/UA），
关键业务动作通过 `AuditService.record()` 补记 params / before / after。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from models import AuditLog, AuthLog
from services.security_settings_service import SecuritySettingsService

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "password", "new_password", "old_password", "confirm_password", "current_password",
    "token", "access_token", "refresh_token", "api_key", "secret", "authorization",
    "cookie", "password_hash", "set-cookie",
}
_MASK = "***"
_MAX_VALUE_LEN = 2000

ACTION_LABELS = {
    "login": "登录", "logout": "退出", "login_fail": "登录失败",
    "kicked": "强制下线", "expired": "会话过期",
    "change_password": "修改密码", "reset_password": "重置密码",
    "lock": "账号锁定", "unlock": "账号解锁",
    "create": "新增", "update": "修改", "delete": "删除",
    "enable": "启用", "disable": "停用", "export": "导出",
    "grant": "授权", "revoke": "收回权限", "import": "导入", "run": "执行",
}


def mask_value(value: Any, _depth: int = 0) -> Any:
    """递归脱敏：敏感 key 置为 ***，长字符串截断。"""
    if _depth > 6:
        return _MASK
    if isinstance(value, dict):
        return {
            k: (_MASK if str(k).lower() in SENSITIVE_KEYS else mask_value(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_value(v, _depth + 1) for v in value[:50]]
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE_LEN else value[:_MAX_VALUE_LEN] + "…(已截断)"
    return value


def dumps(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(mask_value(value), ensure_ascii=False, default=str)
    except Exception:
        return None


class AuditService:
    """审计日志服务：入队 → 后台 worker 批量落库。"""

    _queue: asyncio.Queue | None = None
    _worker_task: asyncio.Task | None = None
    _dropped = 0

    # ── 队列与 worker ──

    @staticmethod
    def start() -> None:
        if AuditService._worker_task and not AuditService._worker_task.done():
            return
        size = max(100, int(getattr(settings, "AUDIT_QUEUE_MAXSIZE", 5000)))
        AuditService._queue = asyncio.Queue(maxsize=size)
        AuditService._worker_task = asyncio.create_task(AuditService._worker())

    @staticmethod
    async def stop() -> None:
        task = AuditService._worker_task
        AuditService._worker_task = None
        if not task:
            return
        try:
            # 给在途日志一个收尾窗口
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        except Exception:
            task.cancel()

    @staticmethod
    def enqueue(table: str, entry: dict) -> None:
        """非阻塞入队；队列满时丢弃并计数（绝不阻塞业务）。"""
        queue = AuditService._queue
        if queue is None:
            return
        try:
            queue.put_nowait((table, entry))
        except asyncio.QueueFull:
            AuditService._dropped += 1
            if AuditService._dropped % 100 == 1:
                logger.warning("审计日志队列已满，累计丢弃 %d 条", AuditService._dropped)

    @staticmethod
    async def _worker() -> None:
        queue = AuditService._queue
        if queue is None:
            return
        batch_size = max(1, int(getattr(settings, "AUDIT_BATCH_SIZE", 100)))
        interval = float(getattr(settings, "AUDIT_FLUSH_INTERVAL_SECONDS", 1.0))
        loop = asyncio.get_event_loop()
        while True:
            batch: list[tuple[str, dict]] = []
            try:
                batch.append(await queue.get())
                deadline = loop.time() + interval
                while len(batch) < batch_size:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(queue.get(), remaining))
                    except (TimeoutError, asyncio.TimeoutError):
                        # 攒批窗口到点：立即刷写已收集的部分，不能丢弃
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("审计日志队列读取异常")
                await asyncio.sleep(interval)
                continue

            if not batch:
                continue
            try:
                await AuditService._flush(batch)
            except Exception:
                logger.exception("审计日志批量落库失败（%d 条已丢弃）", len(batch))

    @staticmethod
    async def _flush(batch: list[tuple[str, dict]]) -> None:
        async with async_session() as db:
            for table, entry in batch:
                try:
                    if table == "auth":
                        db.add(AuthLog(**entry))
                    else:
                        db.add(AuditLog(**entry))
                except Exception:
                    logger.exception("审计日志构造失败：%s", entry)
            await db.commit()

    # ── 写入入口 ──

    @staticmethod
    def record_auth(
        *,
        user_id: str = "", username: str = "", action: str, result: str = "success",
        reason: str = "", session_id: str = "", ip: str = "", user_agent: str = "",
    ) -> None:
        AuditService.enqueue("auth", {
            "id": uuid.uuid4().hex[:12],
            "user_id": user_id or "",
            "username": username or "",
            "action": action,
            "result": result,
            "reason": (reason or "")[:200],
            "session_id": session_id or "",
            "ip": ip or "",
            "user_agent": (user_agent or "")[:500],
            "created_at": datetime.now().isoformat(),
        })

    @staticmethod
    def record(
        *,
        user_id: str = "", username: str = "", nickname: str = "", session_id: str = "",
        module: str = "", action: str = "", action_label: str = "",
        target_type: str = "", target_id: str = "", target_name: str = "",
        method: str = "", path: str = "", params: Any = None,
        before_value: Any = None, after_value: Any = None,
        result: str = "success", status_code: int = 0, error_msg: str = "",
        ip: str = "", user_agent: str = "", duration_ms: int = 0,
        source: str = "system",
    ) -> None:
        """业务埋点：记录一次操作（含变更前后快照）。"""
        AuditService.enqueue("audit", {
            "id": uuid.uuid4().hex[:12],
            "user_id": user_id or "",
            "username": username or "",
            "nickname": nickname or "",
            "session_id": session_id or "",
            "module": module,
            "action": action,
            "action_label": action_label or ACTION_LABELS.get(action, action),
            "target_type": target_type,
            "target_id": str(target_id or ""),
            "target_name": (target_name or "")[:200],
            "method": method,
            "path": (path or "")[:300],
            "params": dumps(params),
            "before_value": dumps(before_value),
            "after_value": dumps(after_value),
            "result": result,
            "status_code": int(status_code or 0),
            "error_msg": (error_msg or "")[:1000],
            "ip": ip or "",
            "user_agent": (user_agent or "")[:500],
            "request_id": "",
            "duration_ms": int(duration_ms or 0),
            "source": source,
            "created_at": datetime.now().isoformat(),
        })

    # ── 查询 ──

    @staticmethod
    async def list_logs(db: AsyncSession, *, user_id="", module="", action="", result="",
                        keyword="", start="", end="", page=1, page_size=20) -> dict:
        conds = []
        if user_id:
            conds.append(AuditLog.user_id == user_id)
        if module:
            conds.append(AuditLog.module == module)
        if action:
            conds.append(AuditLog.action == action)
        if result:
            conds.append(AuditLog.result == result)
        if keyword:
            like = f"%{keyword}%"
            conds.append(
                (AuditLog.username.like(like)) | (AuditLog.target_name.like(like))
                | (AuditLog.path.like(like)) | (AuditLog.action_label.like(like))
            )
        if start:
            conds.append(AuditLog.created_at >= start)
        if end:
            conds.append(AuditLog.created_at <= end + "￿")

        where = conds[0] if conds else True
        for c in conds[1:]:
            where = where & c

        total = (await db.execute(select(func.count()).select_from(AuditLog).where(where))).scalar() or 0
        rows = (await db.execute(
            select(AuditLog).where(where)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [AuditService._audit_row(r) for r in rows],
        }

    @staticmethod
    async def get_log(db: AsyncSession, log_id: str) -> dict | None:
        row = await db.get(AuditLog, log_id)
        return AuditService._audit_row(row) if row else None

    @staticmethod
    def _audit_row(r: AuditLog) -> dict:
        return {
            "id": r.id, "user_id": r.user_id, "username": r.username, "nickname": r.nickname,
            "module": r.module, "action": r.action, "action_label": r.action_label,
            "target_type": r.target_type, "target_id": r.target_id, "target_name": r.target_name,
            "method": r.method, "path": r.path,
            "params": r.params, "before_value": r.before_value, "after_value": r.after_value,
            "result": r.result, "status_code": r.status_code, "error_msg": r.error_msg,
            "ip": r.ip, "user_agent": r.user_agent, "duration_ms": r.duration_ms,
            "source": r.source, "created_at": r.created_at,
        }

    @staticmethod
    async def list_auth_logs(db: AsyncSession, *, user_id="", action="", result="",
                             keyword="", start="", end="", page=1, page_size=20) -> dict:
        conds = []
        if user_id:
            conds.append(AuthLog.user_id == user_id)
        if action:
            conds.append(AuthLog.action == action)
        if result:
            conds.append(AuthLog.result == result)
        if keyword:
            like = f"%{keyword}%"
            conds.append((AuthLog.username.like(like)) | (AuthLog.ip.like(like)))
        if start:
            conds.append(AuthLog.created_at >= start)
        if end:
            conds.append(AuthLog.created_at <= end + "￿")

        where = conds[0] if conds else True
        for c in conds[1:]:
            where = where & c

        total = (await db.execute(select(func.count()).select_from(AuthLog).where(where))).scalar() or 0
        rows = (await db.execute(
            select(AuthLog).where(where)
            .order_by(AuthLog.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [{
                "id": r.id, "user_id": r.user_id, "username": r.username,
                "action": r.action, "action_label": ACTION_LABELS.get(r.action, r.action),
                "result": r.result, "reason": r.reason, "ip": r.ip,
                "user_agent": r.user_agent, "created_at": r.created_at,
            } for r in rows],
        }

    @staticmethod
    def action_options() -> list[dict]:
        return [{"value": k, "label": v} for k, v in ACTION_LABELS.items()]

    # ── 归档清理 ──

    @staticmethod
    async def cleanup(db: AsyncSession) -> dict:
        audit_days = SecuritySettingsService.get_int("audit_log_retention_days", 180)
        auth_days = SecuritySettingsService.get_int("auth_log_retention_days", 365)
        removed = {"audit_logs": 0, "auth_logs": 0}
        if audit_days > 0:
            edge = (datetime.now() - timedelta(days=audit_days)).isoformat()
            res = await db.execute(delete(AuditLog).where(AuditLog.created_at < edge))
            removed["audit_logs"] = res.rowcount or 0
        if auth_days > 0:
            edge = (datetime.now() - timedelta(days=auth_days)).isoformat()
            res = await db.execute(delete(AuthLog).where(AuthLog.created_at < edge))
            removed["auth_logs"] = res.rowcount or 0
        await db.commit()
        return removed
