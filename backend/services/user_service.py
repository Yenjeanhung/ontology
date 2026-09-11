"""用户与用户组服务：CRUD、角色/组绑定、密码策略、启停与解锁。"""
from __future__ import annotations

import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import hash_password, hash_password_async, is_expired, now_iso
from models import Role, User, UserGroup, UserGroupMember, UserPermission, UserRole, UserSession
from services.role_service import RoleService
from services.security_settings_service import SecuritySettingsService
from services.session_service import SessionService

logger = logging.getLogger(__name__)

_TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"


def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def validate_password(password: str) -> None:
    """按当前策略校验密码强度，不满足时抛 ValueError。"""
    if not password:
        raise ValueError("密码不能为空")
    min_len = SecuritySettingsService.get_int("password_min_length", 8)
    if len(password) < min_len:
        raise ValueError(f"密码长度不能少于 {min_len} 位")
    level = SecuritySettingsService.get("password_complexity", "medium")
    if level == "none":
        return
    has_lower = bool(re.search(r"[a-z]", password))
    has_upper = bool(re.search(r"[A-Z]", password))
    has_digit = bool(re.search(r"\d", password))
    has_special = bool(re.search(r"[^A-Za-z0-9]", password))
    if level == "low" and not (has_lower or has_upper) or (level == "low" and not has_digit):
        raise ValueError("密码需同时包含字母和数字")
    if level in ("low", "medium"):
        if not ((has_lower or has_upper) and has_digit):
            raise ValueError("密码需同时包含字母和数字")
    if level == "medium" and not (has_lower and has_upper and has_digit):
        raise ValueError("密码需包含大写字母、小写字母和数字")
    if level == "high" and not (has_lower and has_upper and has_digit and has_special):
        raise ValueError("密码需包含大写字母、小写字母、数字和特殊字符")


def _user_row(user: User, roles: list[dict], groups: list[dict]) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname,
        "email": user.email,
        "phone": user.phone,
        "avatar": user.avatar,
        "status": user.status,
        "is_system": user.is_system,
        "must_change_password": user.must_change_password,
        "locked_until": user.locked_until,
        "failed_attempts": user.failed_attempts,
        "last_login_at": user.last_login_at,
        "last_login_ip": user.last_login_ip,
        "password_changed_at": user.password_changed_at,
        "remark": user.remark,
        "created_by": user.created_by,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "roles": roles,
        "groups": groups,
    }


class UserService:
    # ── 查询 ──

    @staticmethod
    async def list_users(db: AsyncSession, *, keyword="", status="", group_id="",
                         page=1, page_size=20) -> dict:
        conds = []
        if keyword:
            like = f"%{keyword}%"
            conds.append(or_(User.username.like(like), User.nickname.like(like), User.email.like(like)))
        if status:
            conds.append(User.status == status)
        if group_id:
            member_ids = [r[0] for r in (await db.execute(
                select(UserGroupMember.user_id).where(UserGroupMember.group_id == group_id)
            )).all()]
            if not member_ids:
                return {"total": 0, "page": page, "page_size": page_size, "items": []}
            conds.append(User.id.in_(member_ids))

        where = conds[0] if conds else True
        for c in conds[1:]:
            where = where & c

        total = (await db.execute(select(func.count()).select_from(User).where(where))).scalar() or 0
        users = (await db.execute(
            select(User).where(where).order_by(User.created_at.asc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()

        role_rows = (await db.execute(select(UserRole))).scalars().all()
        member_rows = (await db.execute(select(UserGroupMember))).scalars().all()
        roles = (await db.execute(select(Role))).scalars().all()
        groups = (await db.execute(select(UserGroup))).scalars().all()
        role_map = {r.id: {"id": r.id, "code": r.code, "name": r.name} for r in roles}
        group_map = {g.id: {"id": g.id, "name": g.name} for g in groups}

        items = []
        for u in users:
            u_roles = [role_map[ur.role_id] for ur in role_rows if ur.user_id == u.id and ur.role_id in role_map]
            u_groups = [group_map[m.group_id] for m in member_rows if m.user_id == u.id and m.group_id in group_map]
            items.append(_user_row(u, u_roles, u_groups))
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    @staticmethod
    async def get_user(db: AsyncSession, user_id: str) -> dict | None:
        user = await db.get(User, user_id)
        if not user:
            return None
        return _user_row(
            user,
            await UserService.user_roles(db, user_id),
            await UserService.user_groups(db, user_id),
        )

    @staticmethod
    async def user_roles(db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(
            select(Role.id, Role.code, Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )).all()
        return [{"id": r[0], "code": r[1], "name": r[2]} for r in rows]

    @staticmethod
    async def user_groups(db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(
            select(UserGroup.id, UserGroup.name)
            .join(UserGroupMember, UserGroupMember.group_id == UserGroup.id)
            .where(UserGroupMember.user_id == user_id)
        )).all()
        return [{"id": r[0], "name": r[1]} for r in rows]

    # ── 变更 ──

    @staticmethod
    async def create_user(db: AsyncSession, payload: dict, operator: str = "") -> dict:
        username = (payload.get("username") or "").strip()
        if not username:
            raise ValueError("登录名不能为空")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
            raise ValueError("登录名需为 3-64 位字母、数字、下划线、点或中划线")
        exists = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if exists:
            raise ValueError(f"登录名已存在：{username}")

        password = payload.get("password") or generate_temp_password()
        validate_password(password)
        now = now_iso()
        user = User(
            id=uuid.uuid4().hex[:12],
            username=username,
            password_hash=await hash_password_async(password),
            nickname=(payload.get("nickname") or username).strip(),
            email=payload.get("email") or "",
            phone=payload.get("phone") or "",
            avatar=payload.get("avatar") or "",
            status=payload.get("status") or "active",
            is_system=0,
            must_change_password=1 if not payload.get("password") else 0,
            remark=payload.get("remark") or "",
            created_by=operator,
            password_changed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()
        if payload.get("role_ids"):
            await UserService.set_roles(db, user.id, payload["role_ids"], operator)
        if payload.get("group_ids"):
            await UserService.set_groups(db, user.id, payload["group_ids"])
        await db.commit()
        row = await UserService.get_user(db, user.id)
        row["temp_password"] = password
        return row

    @staticmethod
    async def update_user(db: AsyncSession, user_id: str, payload: dict, operator: str = "") -> dict:
        user = await db.get(User, user_id)
        if not user:
            raise ValueError("用户不存在")
        for field in ("nickname", "email", "phone", "avatar", "remark"):
            if field in payload and payload[field] is not None:
                setattr(user, field, str(payload[field]))
        user.updated_at = now_iso()
        if "role_ids" in payload and payload["role_ids"] is not None:
            await UserService.set_roles(db, user_id, payload["role_ids"], operator)
        if "group_ids" in payload and payload["group_ids"] is not None:
            await UserService.set_groups(db, user_id, payload["group_ids"])
        await db.commit()
        return await UserService.get_user(db, user_id)

    @staticmethod
    async def delete_user(db: AsyncSession, user_id: str, operator: str = "") -> None:
        user = await db.get(User, user_id)
        if not user:
            raise ValueError("用户不存在")
        if user.is_system:
            raise ValueError("内置账号不可删除")
        await SessionService.kick_user(db, user_id, kicked_by=operator, reason="账号已删除")
        await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        await db.execute(delete(UserGroupMember).where(UserGroupMember.user_id == user_id))
        await db.execute(delete(UserPermission).where(UserPermission.user_id == user_id))
        await db.delete(user)
        await db.commit()

    @staticmethod
    async def set_roles(db: AsyncSession, user_id: str, role_ids: list[str], operator: str = "") -> None:
        valid = {r[0] for r in (await db.execute(
            select(Role.id).where(Role.id.in_(role_ids or []))
        )).all()}
        await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        now = now_iso()
        for rid in valid:
            db.add(UserRole(id=uuid.uuid4().hex[:12], user_id=user_id,
                            role_id=rid, created_by=operator, created_at=now))
        await db.flush()

    @staticmethod
    async def set_groups(db: AsyncSession, user_id: str, group_ids: list[str]) -> None:
        valid = {r[0] for r in (await db.execute(
            select(UserGroup.id).where(UserGroup.id.in_(group_ids or []))
        )).all()}
        await db.execute(delete(UserGroupMember).where(UserGroupMember.user_id == user_id))
        now = now_iso()
        for gid in valid:
            db.add(UserGroupMember(id=uuid.uuid4().hex[:12], group_id=gid,
                                   user_id=user_id, created_at=now))
        await db.flush()

    @staticmethod
    async def reset_password(db: AsyncSession, user_id: str, operator: str = "") -> dict:
        user = await db.get(User, user_id)
        if not user:
            raise ValueError("用户不存在")
        temp = generate_temp_password()
        user.password_hash = await hash_password_async(temp)
        user.must_change_password = 1
        user.failed_attempts = 0
        user.locked_until = None
        user.password_changed_at = now_iso()
        user.updated_at = now_iso()
        # 改密后其他设备立即失效
        user.token_version = int(user.token_version or 1) + 1
        await db.commit()
        await SessionService.kick_user(db, user_id, kicked_by=operator, reason="密码已重置")
        return {"username": user.username, "temp_password": temp}

    @staticmethod
    async def set_status(db: AsyncSession, user_id: str, status: str, operator: str = "") -> dict:
        user = await db.get(User, user_id)
        if not user:
            raise ValueError("用户不存在")
        if status not in ("active", "disabled", "locked"):
            raise ValueError("状态取值非法")
        if user.is_system and status != "active":
            raise ValueError("内置账号不可停用或锁定")
        user.status = status
        user.updated_at = now_iso()
        if status == "active":
            user.failed_attempts = 0
            user.locked_until = None
            await db.commit()
            return await UserService.get_user(db, user_id)

        # 停用 / 锁定：提升 token_version 让已签发的 token 立即失效，并踢掉全部会话
        user.token_version = int(user.token_version or 1) + 1
        user.locked_until = None
        await db.commit()
        await SessionService.kick_user(
            db, user_id, kicked_by=operator,
            reason="账号已停用" if status == "disabled" else "账号已锁定",
        )
        return await UserService.get_user(db, user_id)

    # ── 用户组 ──

    @staticmethod
    async def list_groups(db: AsyncSession) -> list[dict]:
        groups = (await db.execute(
            select(UserGroup).order_by(UserGroup.sort_order, UserGroup.created_at)
        )).scalars().all()
        members = (await db.execute(select(UserGroupMember))).scalars().all()
        result = []
        for g in groups:
            result.append({
                "id": g.id, "name": g.name, "code": g.code, "parent_id": g.parent_id,
                "sort_order": g.sort_order, "is_system": g.is_system, "remark": g.remark,
                "member_count": sum(1 for m in members if m.group_id == g.id),
                "created_at": g.created_at, "updated_at": g.updated_at,
            })
        return result

    @staticmethod
    async def create_group(db: AsyncSession, payload: dict) -> dict:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("用户组名称不能为空")
        now = now_iso()
        group = UserGroup(
            id=uuid.uuid4().hex[:12], name=name,
            code=(payload.get("code") or "").strip(),
            parent_id=payload.get("parent_id") or None,
            sort_order=int(payload.get("sort_order") or 100),
            is_system=0, remark=payload.get("remark") or "",
            created_at=now, updated_at=now,
        )
        db.add(group)
        await db.flush()
        if payload.get("role_ids"):
            await UserService.set_group_roles(db, group.id, payload["role_ids"])
        await db.commit()
        groups = await UserService.list_groups(db)
        return next(g for g in groups if g["id"] == group.id)

    @staticmethod
    async def update_group(db: AsyncSession, group_id: str, payload: dict) -> dict:
        group = await db.get(UserGroup, group_id)
        if not group:
            raise ValueError("用户组不存在")
        for field in ("name", "code", "remark", "parent_id", "sort_order"):
            if field in payload and payload[field] is not None:
                setattr(group, field, payload[field])
        group.updated_at = now_iso()
        if "role_ids" in payload and payload["role_ids"] is not None:
            await UserService.set_group_roles(db, group_id, payload["role_ids"])
        if "member_ids" in payload and payload["member_ids"] is not None:
            await UserService.set_group_members(db, group_id, payload["member_ids"])
        await db.commit()
        groups = await UserService.list_groups(db)
        return next(g for g in groups if g["id"] == group_id)

    @staticmethod
    async def delete_group(db: AsyncSession, group_id: str) -> None:
        group = await db.get(UserGroup, group_id)
        if not group:
            raise ValueError("用户组不存在")
        if group.is_system:
            raise ValueError("内置用户组不可删除")
        from models import GroupRole

        await db.execute(delete(UserGroupMember).where(UserGroupMember.group_id == group_id))
        await db.execute(delete(GroupRole).where(GroupRole.group_id == group_id))
        await db.delete(group)
        await db.commit()

    @staticmethod
    async def set_group_members(db: AsyncSession, group_id: str, user_ids: list[str]) -> None:
        await db.execute(delete(UserGroupMember).where(UserGroupMember.group_id == group_id))
        now = now_iso()
        for uid in user_ids or []:
            db.add(UserGroupMember(id=uuid.uuid4().hex[:12], group_id=group_id,
                                   user_id=uid, created_at=now))
        await db.flush()

    @staticmethod
    async def set_group_roles(db: AsyncSession, group_id: str, role_ids: list[str]) -> None:
        from models import GroupRole, Role

        valid = {r[0] for r in (await db.execute(
            select(Role.id).where(Role.id.in_(role_ids or []))
        )).all()}
        await db.execute(delete(GroupRole).where(GroupRole.group_id == group_id))
        now = now_iso()
        for rid in valid:
            db.add(GroupRole(id=uuid.uuid4().hex[:12], group_id=group_id,
                             role_id=rid, created_at=now))
        await db.flush()

    @staticmethod
    async def group_role_ids(db: AsyncSession, group_id: str) -> list[str]:
        from models import GroupRole

        rows = (await db.execute(
            select(GroupRole.role_id).where(GroupRole.group_id == group_id)
        )).all()
        return [r[0] for r in rows]
