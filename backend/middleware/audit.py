"""操作审计中间件（纯 ASGI）：自动记录写操作的请求元数据，异步入队落库。

不读取请求体（避免影响分片上传与 SSE），params / before / after 由业务埋点补充。
"""
from __future__ import annotations

import logging
import time

from services.audit_service import AuditService
from services.permission_registry import VERB_TO_ACTION, module_of_path, resolve
from services.security_settings_service import SecuritySettingsService

logger = logging.getLogger("audit_mw")

# 高频轮询 / 登录类接口不记操作日志（登录走 auth_logs）
SKIP_PATHS = {
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/me",
    "/api/auth/status",
    "/api/auth/permissions",
    "/api/notifications/summary",
    "/api/notifications/stream",
    "/api/monitor/stream",
    "/api/audit/logs",
    "/api/audit/auth-logs",
}


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()
        if method in ("OPTIONS", "HEAD") or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if path in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        user = scope.get("auth_user")
        if not user:
            await self.app(scope, receive, send)
            return
        if method == "GET" and not SecuritySettingsService.get_bool("log_get_requests", False):
            await self.app(scope, receive, send)
            return

        ip = user.get("ip") or ""
        ua = user.get("user_agent") or ""
        status = 0
        start = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            error_msg = ""
        except Exception as exc:  # 记录失败后原样抛出
            status = status or 500
            error_msg = str(exc)[:500]
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            perm = resolve(method, path)
            module = perm.split(":")[0] if perm else (module_of_path(path) or "")
            action = perm.split(":")[-1] if perm else VERB_TO_ACTION.get(method, method.lower())
            try:
                AuditService.record(
                    user_id=user.get("user_id") or "",
                    username=user.get("username") or "",
                    session_id=user.get("session_id") or "",
                    module=module,
                    action=action,
                    method=method,
                    path=path,
                    result="success" if 200 <= (status or 0) < 400 else "fail",
                    status_code=status or 0,
                    error_msg=error_msg,
                    ip=ip,
                    user_agent=ua,
                    duration_ms=duration_ms,
                    source="http",
                )
            except Exception:
                logger.exception("写入操作审计失败 %s %s", method, path)
