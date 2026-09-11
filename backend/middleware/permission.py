"""接口鉴权中间件（纯 ASGI）：method + path → 权限码 → 校验。

- 权限码由 `services.permission_registry.resolve()` 推导；
- 超级管理员（角色含 super_admin）直接放行；
- 用户有效权限在进程内缓存 30s，角色变更后最迟 30s 生效。
"""
from __future__ import annotations

import json
import logging
import time

from database import async_session
from services.permission_registry import SUPER_ADMIN_ROLE_CODE, resolve
from services.role_service import RoleService
from services.security_settings_service import SecuritySettingsService

logger = logging.getLogger("perm")

_PERM_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, set[str], bool]] = {}


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


async def _user_grants(user_id: str) -> tuple[set[str], bool]:
    """返回 (权限码集合, 是否超级管理员)。"""
    now = time.monotonic()
    cached = _cache.get(user_id)
    if cached and now - cached[0] < _PERM_CACHE_TTL:
        return cached[1], cached[2]
    async with async_session() as db:
        codes = set(await RoleService.user_permission_codes(db, user_id))
        role_codes = await RoleService.user_role_codes(db, user_id)
    is_super = SUPER_ADMIN_ROLE_CODE in role_codes
    _cache[user_id] = (now, codes, is_super)
    return codes, is_super


def invalidate_user(user_id: str) -> None:
    """角色/权限变更后调用，立即失效缓存。"""
    _cache.pop(user_id, None)


class PermissionMiddleware:
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
            await self.app(scope, receive, send)
            return

        user = scope.get("auth_user")
        if not user:
            # 认证中间件已返回 401；兜底放行避免重复响应
            await self.app(scope, receive, send)
            return

        required = resolve(method, path)
        if not required:
            await self.app(scope, receive, send)
            return

        codes, is_super = await _user_grants(user["user_id"])
        if is_super or required in codes:
            await self.app(scope, receive, send)
            return

        logger.info("拒绝访问：%s %s %s 缺少权限 %s", user.get("username"), method, path, required)
        await _json_response(send, 403, {
            "detail": "没有操作权限，请联系管理员",
            "code": "FORBIDDEN",
            "required_permission": required,
        })
