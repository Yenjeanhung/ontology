"""认证服务：登录 / 登出 / 刷新 / 改密 / 当前用户信息。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    is_expired,
    now_iso,
    verify_password_async,
)
from models import User
from services.audit_service import AuditService
from services.role_service import RoleService
from services.security_settings_service import SecuritySettingsService
from services.session_service import SessionService
from services.user_service import validate_password

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """认证失败：message 可直接返回前端。"""

    def __init__(self, message: str, code: str = "LOGIN_FAILED"):
        super().__init__(message)
        self.code = code


class AuthService:
    @staticmethod
    async def login(db: AsyncSession, username: str, password: str, *,
                    ip: str = "", ua: str = "") -> dict:
        username = (username or "").strip()
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()

        if user is None:
            AuditService.record_auth(username=username, action="login_fail",
                                     result="fail", reason="用户不存在", ip=ip, user_agent=ua)
            raise AuthError("用户名或密码错误")

        if user.status == "disabled":
            AuditService.record_auth(user_id=user.id, username=user.username, action="login_fail",
                                     result="fail", reason="账号已停用", ip=ip, user_agent=ua)
            raise AuthError("账号已停用，请联系管理员", "DISABLED")

        if user.status == "locked":
            if user.locked_until and not is_expired(user.locked_until):
                AuditService.record_auth(user_id=user.id, username=user.username, action="login_fail",
                                         result="fail", reason="账号锁定中", ip=ip, user_agent=ua)
                raise AuthError("账号已锁定，请稍后再试或联系管理员", "LOCKED")
            if not user.locked_until:
                AuditService.record_auth(user_id=user.id, username=user.username, action="login_fail",
                                         result="fail", reason="账号锁定（需管理员解锁）", ip=ip, user_agent=ua)
                raise AuthError("账号已锁定，请联系管理员解锁", "LOCKED")
            # 锁定已到期，自动解锁
            user.status = "active"
            user.failed_attempts = 0
            user.locked_until = None

        if not await verify_password_async(password or "", user.password_hash):
            max_failed = SecuritySettingsService.get_int("max_failed_attempts", 5)
            lock_minutes = SecuritySettingsService.get_int("lock_minutes", 30)
            user.failed_attempts = int(user.failed_attempts or 0) + 1
            if max_failed > 0 and user.failed_attempts >= max_failed:
                user.status = "locked"
                user.locked_until = (
                    (datetime.now() + timedelta(minutes=lock_minutes)).isoformat()
                    if lock_minutes > 0 else None
                )
                AuditService.record_auth(user_id=user.id, username=user.username, action="lock",
                                         result="success",
                                         reason=f"连续 {user.failed_attempts} 次登录失败", ip=ip, user_agent=ua)
            await db.commit()
            AuditService.record_auth(user_id=user.id, username=user.username, action="login_fail",
                                     result="fail", reason="密码错误", ip=ip, user_agent=ua)
            left = max(0, max_failed - user.failed_attempts) if max_failed > 0 else -1
            tip = f"，还可尝试 {left} 次" if left > 0 else ""
            raise AuthError(f"用户名或密码错误{tip}")

        # 密码有效期
        expire_days = SecuritySettingsService.get_int("password_expire_days", 90)
        if expire_days > 0 and user.password_changed_at:
            try:
                changed = datetime.fromisoformat(user.password_changed_at)
                if datetime.now() - changed > timedelta(days=expire_days):
                    user.must_change_password = 1
            except ValueError:
                pass

        # 同账号会话上限
        single = SecuritySettingsService.get_bool("single_device_login", False)
        max_sessions = 1 if single else SecuritySettingsService.get_int("max_sessions_per_user", 5)
        await SessionService.enforce_max_sessions(db, user.id, max_sessions)

        access_ttl = SecuritySettingsService.get_int("access_token_ttl_minutes", 120)
        session = await SessionService.create(db, user, ip=ip, ua=ua, ttl_seconds=access_ttl * 60)

        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now_iso()
        user.last_login_ip = ip
        await db.commit()

        access_token, access_expires = create_access_token(
            user.id, user.username, session.id, user.token_version)
        refresh_token, refresh_expires = create_refresh_token(user.id, session.id, user.token_version)

        AuditService.record_auth(user_id=user.id, username=user.username, action="login",
                                 result="success", session_id=session.id, ip=ip, user_agent=ua)

        profile = await AuthService.profile(db, user.id)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": access_expires,
            "refresh_expires_in": refresh_expires,
            "session_id": session.id,
            "user": profile["user"],
            "roles": profile["roles"],
            "permissions": profile["permissions"],
            "is_super_admin": profile["is_super_admin"],
            "must_change_password": bool(user.must_change_password),
        }

    @staticmethod
    async def logout(db: AsyncSession, sid: str, user_id: str = "") -> None:
        from models import UserSession

        session = await db.get(UserSession, sid)
        if session and session.status == "online":
            AuditService.record_auth(user_id=session.user_id, username=session.username,
                                     action="logout", result="success", session_id=sid)
        await SessionService.close(db, sid, "offline")

    @staticmethod
    async def refresh(db: AsyncSession, refresh_token: str, *, ip: str = "", ua: str = "") -> dict:
        payload = decode_token(refresh_token or "")
        if not payload or payload.get("typ") != "refresh":
            raise AuthError("刷新令牌无效，请重新登录", "TOKEN_INVALID")
        sid = payload.get("sid") or ""
        state = await SessionService.state(sid)
        if not state or state.get("status") != "online":
            raise AuthError("会话已失效，请重新登录", "TOKEN_INVALID")
        user = await db.get(User, payload.get("sub"))
        if not user or user.status != "active":
            raise AuthError("账号状态异常，请重新登录", "DISABLED")
        if int(payload.get("ver") or 1) != int(user.token_version or 1):
            raise AuthError("凭证已失效，请重新登录", "TOKEN_INVALID")

        access_token, expires = create_access_token(
            user.id, user.username, sid, user.token_version)
        return {"access_token": access_token, "expires_in": expires, "session_id": sid}

    @staticmethod
    async def profile(db: AsyncSession, user_id: str) -> dict:
        user = await db.get(User, user_id)
        if not user:
            raise AuthError("用户不存在", "TOKEN_INVALID")
        roles = await RoleService.user_role_codes(db, user_id)
        permissions = await RoleService.user_permission_codes(db, user_id)
        return {
            "user": {
                "id": user.id,
                "username": user.username,
                "nickname": user.nickname,
                "email": user.email,
                "phone": user.phone,
                "avatar": user.avatar,
                "status": user.status,
                "is_system": user.is_system,
                "must_change_password": bool(user.must_change_password),
                "last_login_at": user.last_login_at,
                "last_login_ip": user.last_login_ip,
                "created_at": user.created_at,
            },
            "roles": roles,
            "permissions": permissions,
            "is_super_admin": "super_admin" in roles,
        }

    @staticmethod
    async def change_password(db: AsyncSession, user_id: str, old_password: str,
                              new_password: str, *, ip: str = "", ua: str = "") -> dict:
        from core.security import hash_password_async, verify_password_async
        from services.user_service import validate_password

        user = await db.get(User, user_id)
        if not user:
            raise AuthError("用户不存在", "TOKEN_INVALID")
        if not await verify_password_async(old_password or "", user.password_hash):
            AuditService.record_auth(user_id=user.id, username=user.username, action="change_password",
                                     result="fail", reason="原密码错误", ip=ip, user_agent=ua)
            raise ValueError("原密码错误")
        validate_password(new_password)
        if new_password == old_password:
            raise ValueError("新密码不能与原密码相同")

        user.password_hash = await hash_password_async(new_password)
        user.must_change_password = 0
        user.password_changed_at = now_iso()
        user.updated_at = now_iso()
        user.token_version = int(user.token_version or 1) + 1
        await db.commit()
        await SessionService.kick_user(db, user_id, kicked_by=user.username,
                                       reason="修改密码", exclude_sid="")
        AuditService.record_auth(user_id=user.id, username=user.username, action="change_password",
                                 result="success", ip=ip, user_agent=ua)
        return {"status": "ok", "must_change_password": False}
