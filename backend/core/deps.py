"""FastAPI 依赖：从 ASGI scope 中取出认证中间件写入的当前用户。"""
from __future__ import annotations

from fastapi import HTTPException, Request


def get_current_user(request: Request) -> dict:
    """当前登录用户 {user_id, username, session_id, ip, user_agent}。"""
    user = request.scope.get("auth_user")
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    return user


def get_current_user_id(request: Request) -> str:
    return get_current_user(request)["user_id"]


def get_current_username(request: Request) -> str:
    return get_current_user(request).get("username") or ""


def audit_ctx(request: Request) -> dict:
    """业务埋点用的操作人上下文字段。"""
    user = request.scope.get("auth_user") or {}
    return {
        "user_id": user.get("user_id") or "",
        "username": user.get("username") or "",
        "session_id": user.get("session_id") or "",
        "ip": user.get("ip") or "",
        "user_agent": user.get("user_agent") or "",
    }
