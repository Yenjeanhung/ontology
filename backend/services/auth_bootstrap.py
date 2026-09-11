"""启动引导：安全策略默认值、权限点、内置角色、初始管理员账号（全部幂等）。"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import hash_password_async, now_iso
from models import Role, User, UserRole
from services.role_service import RoleService
from services.security_settings_service import SecuritySettingsService
from services.user_service import generate_temp_password

logger = logging.getLogger(__name__)

_INITIAL_PASSWORD_FILE = Path("./data/initial_admin_password.txt")


async def bootstrap(db: AsyncSession) -> None:
    await SecuritySettingsService.ensure_defaults(db)
    added = await RoleService.sync_permissions(db)
    if added:
        logger.info("同步权限点 %d 项", added)
    created = await RoleService.seed_default_roles(db)
    if created:
        logger.info("创建内置角色 %d 个", created)
    await _ensure_admin(db)


async def _ensure_admin(db: AsyncSession) -> None:
    """无任何用户时创建内置超级管理员 admin（随机密码，落盘 + 打印日志）。"""
    total = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    if total > 0:
        return

    role = (await db.execute(select(Role).where(Role.code == "super_admin"))).scalar_one_or_none()
    password = generate_temp_password(12)
    now = now_iso()
    user = User(
        id="sysadmin0001",
        username="admin",
        password_hash=await hash_password_async(password),
        nickname="系统管理员",
        status="active",
        is_system=1,
        must_change_password=1,
        password_changed_at=now,
        created_by="system",
        remark="内置超级管理员，首次登录请立即修改密码",
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    await db.flush()
    if role:
        db.add(UserRole(id="sysur0000001", user_id=user.id, role_id=role.id,
                        created_by="system", created_at=now))
    await db.commit()

    try:
        _INITIAL_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _INITIAL_PASSWORD_FILE.write_text(
            f"username: admin\npassword: {password}\n"
            f"（首次登录后请立即修改密码；确认后请删除本文件）\n",
            encoding="utf-8",
        )
    except Exception:
        logger.exception("初始管理员密码落盘失败")

    logger.warning("=" * 60)
    logger.warning("已创建初始超级管理员账号：admin / %s", password)
    logger.warning("密码同时写入 %s，首次登录后会强制修改密码", _INITIAL_PASSWORD_FILE)
    logger.warning("=" * 60)
