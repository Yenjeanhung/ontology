"""认证中间件（纯 ASGI）：解析 JWT + 校验服务端会话状态，支撑"即时踢人"。

为什么用纯 ASGI 而不是 BaseHTTPMiddleware：
    后者会缓冲整个响应体，工作流 /run、监控 stream、文件解析 events 等 SSE
    接口会退化成"转圈半天后一次性吐出"。这里不触碰 send，对 SSE 完全透明。

把当前用户写入 scope["auth_user"]，路由层用 `core.deps.CurrentUser` 读取。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

from core.security import decode_token, parse_iso
from services.security_settings_service import SecuritySettingsService
from services.session_service import SessionService

logger = logging.getLogger("auth")

# 无需登录即可访问的路径（只对 /api/ 前缀做鉴权，前端静态资源一律放行）
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/status",
}
PUBLIC_PREFIXES = (
    "/api/auth/",
    "/docs",
    "/redoc",
    "/openapi.json",
)
# 这些 /api/auth/* 子路径仍需登录
PROTECTED_AUTH_PATHS = {
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/permissions",
    "/api/auth/change-password",
}


def _is_public(path: str) -> bool:
    if path in PROTECTED_AUTH_PATHS:
        return False
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


async def _json_response(send, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _extract_token(scope) -> str:
    headers = scope.get("headers") or []
    for key, value in headers:
        if key == b"authorization":
            raw = value.decode("latin-1").strip()
            if raw.lower().startswith("bearer "):
                return raw[7:].strip()
        elif key == b"x-access-token":
            return value.decode("latin-1").strip()
    # SSE（EventSource）无法自定义请求头，允许通过 ?token= 传递
    query = scope.get("query_string") or b""
    if query:
        params = parse_qs(query.decode("latin-1"))
        token = params.get("token") or params.get("access_token")
        if token:
            return token[0]
    return ""


class AuthMiddleware:
    """校验顺序：白名单 → 取 token → 验签 → 会话状态 → 用户状态 → 版本 → 空闲超时。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()

        if method == "OPTIONS" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        if not SecuritySettingsService.get_bool("auth_enabled", True):
            scope.setdefault("auth_user", None)
            await self.app(scope, receive, send)
            return

        if _is_public(path):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else ""
        ua = ""
        for key, value in scope.get("headers") or []:
            if key == b"user-agent":
                ua = value.decode("latin-1")
                break

        token = _extract_token(scope)
        if not token:
            await _json_response(send, 401, {"detail": "未登录或登录已失效", "code": "NOT_LOGIN"})
            return

        payload = decode_token(token)
        if not payload or payload.get("typ") != "access":
            await _json_response(send, 401, {"detail": "登录凭证无效，请重新登录", "code": "TOKEN_INVALID"})
            return

        sid = payload.get("sid") or ""
        state = await SessionService.state(sid)
        if state is None:
            await _json_response(send, 401, {"detail": "会话不存在，请重新登录", "code": "TOKEN_INVALID"})
            return

        if state.get("status") == "kicked":
            await _json_response(send, 401, {
                "detail": "您已被管理员强制下线", "code": "KICKED",
                "reason": "管理员强制下线",
            })
            return
        if state.get("status") in ("offline", "expired"):
            await _json_response(send, 401, {"detail": "会话已结束，请重新登录", "code": "TOKEN_INVALID"})
            return
        if state.get("user_status") != "active":
            await _json_response(send, 401, {
                "detail": "账号已停用或锁定，请联系管理员", "code": "DISABLED",
            })
            return
        if int(payload.get("ver") or 1) != int(state.get("ver") or 1):
            await _json_response(send, 401, {"detail": "凭证已失效，请重新登录", "code": "TOKEN_INVALID"})
            return

        # 空闲超时
        idle_minutes = SecuritySettingsService.get_int("idle_timeout_minutes", 120)
        if idle_minutes > 0:
            last = parse_iso(state.get("last_active"))
            if last is not None:
                now = datetime.now(timezone.utc)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() > idle_minutes * 60:
                    from database import async_session
                    from services.audit_service import AuditService

                    async with async_session() as db:
                        await SessionService.close(db, sid, "expired")
                    AuditService.record_auth(
                        user_id=state.get("user_id", ""), username=state.get("username", ""),
                        action="expired", result="success", session_id=sid, reason="空闲超时",
                    )
                    await _json_response(send, 401, {
                        "detail": "长时间无操作已自动退出，请重新登录", "code": "EXPIRED",
                    })
                    return

        scope["auth_user"] = {
            "user_id": state.get("user_id"),
            "username": state.get("username"),
            "session_id": sid,
            "ip": ip,
            "user_agent": ua[:500],
        }

        try:
            await self.app(scope, receive, send)
        finally:
            # 刷新活跃时间（内存即时，DB 节流回写）
            SessionService.cache_put(sid, {
                **state,
                "last_active": datetime.now().isoformat(),
                "ts": time.monotonic(),
            })
            try:
                await SessionService.persist_touch(sid)
            except Exception:
                pass
