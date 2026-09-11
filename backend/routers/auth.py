"""认证路由：登录 / 登出 / 刷新 / 当前用户 / 改密 / 系统认证状态。"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from core.deps import get_current_user
from database import get_db
from services.auth_service import AuthError, AuthService
from services.security_settings_service import SecuritySettingsService

logger = logging.getLogger(__name__)

router = APIRouter()

# 登录限流：同一 IP + 用户名 每分钟最多 10 次
_RATE_WINDOW = 60.0
_RATE_LIMIT = 10
_attempts: dict[str, deque] = defaultdict(deque)


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > _RATE_WINDOW:
        bucket.popleft()
    bucket.append(now)
    return len(bucket) > _RATE_LIMIT


def _client_ip(request: Request) -> str:
    if request.client:
        return request.client.host or ""
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip()


@router.get("/auth/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    """公开接口：前端据此决定是否启用路由守卫。"""
    settings = await SecuritySettingsService.get_all(db)
    return {
        "auth_enabled": settings.get("auth_enabled", "true").lower() == "true",
        "captcha_enabled": settings.get("captcha_enabled", "false").lower() == "true",
    }


@router.post("/auth/login")
async def login(req: Request, db: AsyncSession = Depends(get_db)):
    body = await req.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    ip = _client_ip(req)
    ua = req.headers.get("user-agent", "")

    if _rate_limited(f"{ip}:{username}"):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")

    try:
        return await AuthService.login(db, username, password, ip=ip, ua=ua)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail={"message": str(exc), "code": exc.code})


@router.post("/auth/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    await AuthService.logout(db, user["session_id"], user["user_id"])
    return {"status": "ok"}


@router.post("/auth/refresh")
async def refresh(req: Request, db: AsyncSession = Depends(get_db)):
    body = await req.json()
    try:
        return await AuthService.refresh(db, body.get("refresh_token") or "")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail={"message": str(exc), "code": exc.code})


@router.get("/auth/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    try:
        return await AuthService.profile(db, user["user_id"])
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.get("/auth/permissions")
async def my_permissions(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    profile = await AuthService.profile(db, user["user_id"])
    return {
        "roles": profile["roles"],
        "permissions": profile["permissions"],
        "is_super_admin": profile["is_super_admin"],
    }


@router.post("/auth/change-password")
async def change_password(req: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(req)
    body = await req.json()
    ip = _client_ip(req)
    ua = req.headers.get("user-agent", "")
    try:
        return await AuthService.change_password(
            db, user["user_id"], body.get("old_password") or "",
            body.get("new_password") or "", ip=ip, ua=ua,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
