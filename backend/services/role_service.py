"""角色与权限服务：权限点同步、角色 CRUD、用户有效权限计算。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import GroupRole, Permission, Role, RolePermission, UserGroupMember, UserPermission, UserRole
from services.permission_registry import (
    MODULES,
    MODULE_NAMES,
    SUPER_ADMIN_ROLE_CODE,
    default_role_permissions,
    iter_default_roles,
    iter_permissions,
)

logger = logging.getLogger(__name__)


class RoleService:
    # ── 引导 ──

    @staticmethod
    async def sync_permissions(db: AsyncSession) -> int:
        """把注册中心的权限点同步到库（新增缺失的，不删除多余的）。"""
        existing = {p.code: p for p in (await db.execute(select(Permission))).scalars().all()}
        added = 0
        for item in iter_permissions():
            if item["code"] in existing:
                row = existing[item["code"]]
                row.name = item["name"]
                row.module = item["module"]
                row.type = item["type"]
                row.resource = item["resource"]
                row.sort_order = item["sort_order"]
                continue
            db.add(Permission(
                id=uuid.uuid4().hex[:12],
                code=item["code"], name=item["name"], module=item["module"],
                type=item["type"], resource=item["resource"],
                is_system=item["is_system"], sort_order=item["sort_order"],
                created_at=datetime.now().isoformat(),
            ))
            added += 1
        if added:
            await db.commit()
        return added

    @staticmethod
    async def seed_default_roles(db: AsyncSession) -> int:
        """创建内置角色并绑定默认权限（幂等，只补不覆盖已有配置）。"""
        perm_rows = (await db.execute(select(Permission))).scalars().all()
        code_to_id = {p.code: p.id for p in perm_rows}
        mapping = default_role_permissions(code_to_id)
        now = datetime.now().isoformat()

        existing = {r.code: r for r in (await db.execute(select(Role))).scalars().all()}
        created = 0
        for spec in iter_default_roles():
            role = existing.get(spec["code"])
            if role is None:
                role = Role(
                    id=uuid.uuid4().hex[:12],
                    code=spec["code"], name=spec["name"],
                    description=spec["description"], is_system=spec["is_system"],
                    sort_order=spec["sort_order"], created_at=now, updated_at=now,
                )
                db.add(role)
                await db.flush()
                created += 1
            else:
                role.name = spec["name"]
                role.description = spec["description"]
                role.is_system = spec["is_system"]
                role.sort_order = spec["sort_order"]
                role.updated_at = now

            # 超级管理员绑定全部权限点（含 system:*），便于页面展示与排查；
            # 鉴权时仍走"直接放行"分支，不依赖这里的绑定结果。
            wanted = list(code_to_id.values()) if spec["code"] == SUPER_ADMIN_ROLE_CODE \
                else mapping.get(spec["code"], [])
            bound = {r.permission_id for r in (await db.execute(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )).scalars().all()}
            for pid in wanted:
                if pid and pid not in bound:
                    db.add(RolePermission(
                        id=uuid.uuid4().hex[:12], role_id=role.id,
                        permission_id=pid, created_at=now,
                    ))
        await db.commit()
        return created

    # ── 查询 ──

    @staticmethod
    async def list_roles(db: AsyncSession) -> list[dict]:
        roles = (await db.execute(select(Role).order_by(Role.sort_order, Role.id))).scalars().all()
        result = []
        for r in roles:
            perm_count = (await db.execute(
                select(RolePermission).where(RolePermission.role_id == r.id)
            )).scalars().all()
            user_count = (await db.execute(
                select(UserRole).where(UserRole.role_id == r.id)
            )).scalars().all()
            result.append({
                "id": r.id, "code": r.code, "name": r.name, "description": r.description,
                "is_system": r.is_system, "sort_order": r.sort_order,
                "permission_count": len(perm_count), "user_count": len(user_count),
                "created_at": r.created_at, "updated_at": r.updated_at,
            })
        return result

    @staticmethod
    def _role_row(r: Role, perm_codes: list[str]) -> dict:
        return {
            "id": r.id, "code": r.code, "name": r.name, "description": r.description,
            "is_system": r.is_system, "sort_order": r.sort_order,
            "permissions": perm_codes, "created_at": r.created_at, "updated_at": r.updated_at,
        }

    @staticmethod
    async def get_role(db: AsyncSession, role_id: str) -> dict | None:
        role = await db.get(Role, role_id)
        if not role:
            return None
        return RoleService._role_row(role, await RoleService.role_permission_codes(db, role_id))

    @staticmethod
    async def role_permission_codes(db: AsyncSession, role_id: str) -> list[str]:
        rows = (await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .order_by(Permission.sort_order)
        )).all()
        return [r[0] for r in rows]

    @staticmethod
    async def permission_tree(db: AsyncSession) -> list[dict]:
        """按模块分组返回权限点，供角色授权页面渲染权限树。"""
        perms = (await db.execute(
            select(Permission).order_by(Permission.module, Permission.sort_order)
        )).scalars().all()
        groups: dict[str, dict] = {}
        for p in perms:
            g = groups.setdefault(p.module, {
                "module": p.module,
                "module_name": MODULE_NAMES.get(p.module, p.module),
                "permissions": [],
            })
            g["permissions"].append({
                "id": p.id, "code": p.code, "name": p.name,
                "type": p.type, "resource": p.resource,
            })
        order = [code for code, _name, _prefixes in MODULES]
        return sorted(
            groups.values(),
            key=lambda g: order.index(g["module"]) if g["module"] in order else 99,
        )

    # ── 变更 ──

    @staticmethod
    async def create_role(db: AsyncSession, payload: dict, operator: str = "") -> dict:
        code = (payload.get("code") or "").strip()
        if not code:
            raise ValueError("角色标识不能为空")
        exists = (await db.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
        if exists:
            raise ValueError(f"角色标识已存在：{code}")
        now = datetime.now().isoformat()
        role = Role(
            id=uuid.uuid4().hex[:12],
            code=code,
            name=(payload.get("name") or code).strip(),
            description=payload.get("description") or "",
            is_system=0,
            sort_order=int(payload.get("sort_order") or 100),
            created_at=now, updated_at=now,
        )
        db.add(role)
        await db.flush()
        await RoleService.set_role_permissions(db, role.id, payload.get("permissions") or [], operator)
        await db.commit()
        return RoleService._role_row(role, await RoleService.role_permission_codes(db, role.id))

    @staticmethod
    async def update_role(db: AsyncSession, role_id: str, payload: dict, operator: str = "") -> dict:
        role = await db.get(Role, role_id)
        if not role:
            raise ValueError("角色不存在")
        if "name" in payload and payload["name"]:
            role.name = str(payload["name"]).strip()
        if "description" in payload:
            role.description = payload["description"] or ""
        if "sort_order" in payload:
            role.sort_order = int(payload["sort_order"] or 100)
        role.updated_at = datetime.now().isoformat()
        if role.code == SUPER_ADMIN_ROLE_CODE:
            # 超级管理员固定全权限，忽略外部权限变更
            await db.commit()
            return RoleService._role_row(role, await RoleService.role_permission_codes(db, role.id))
        if "permissions" in payload and payload["permissions"] is not None:
            await RoleService.set_role_permissions(db, role.id, payload["permissions"], operator)
        await db.commit()
        return RoleService._role_row(role, await RoleService.role_permission_codes(db, role.id))

    @staticmethod
    async def delete_role(db: AsyncSession, role_id: str) -> None:
        role = await db.get(Role, role_id)
        if not role:
            raise ValueError("角色不存在")
        if role.is_system:
            raise ValueError(f"内置角色不可删除：{role.name}")
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        await db.execute(delete(UserRole).where(UserRole.role_id == role_id))
        await db.execute(delete(GroupRole).where(GroupRole.role_id == role_id))
        await db.delete(role)
        await db.commit()

    @staticmethod
    async def set_role_permissions(db: AsyncSession, role_id: str, codes: list[str],
                                   operator: str = "") -> list[str]:
        role = await db.get(Role, role_id)
        if not role:
            raise ValueError("角色不存在")
        if role.code == SUPER_ADMIN_ROLE_CODE:
            return await RoleService.role_permission_codes(db, role_id)
        ids = [r[0] for r in (await db.execute(
            select(Permission.id).where(Permission.code.in_(codes or []))
        )).all()]
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        now = datetime.now().isoformat()
        for pid in ids:
            db.add(RolePermission(id=uuid.uuid4().hex[:12], role_id=role_id,
                                  permission_id=pid, created_at=now))
        await db.flush()
        return await RoleService.role_permission_codes(db, role_id)

    # ── 用户有效权限计算 ──

    @staticmethod
    async def user_role_ids(db: AsyncSession, user_id: str) -> list[str]:
        direct = {r[0] for r in (await db.execute(
            select(UserRole.role_id).where(UserRole.user_id == user_id)
        )).all()}
        group_ids = [r[0] for r in (await db.execute(
            select(UserGroupMember.group_id).where(UserGroupMember.user_id == user_id)
        )).all()]
        if group_ids:
            rows = (await db.execute(
                select(GroupRole.role_id).where(GroupRole.group_id.in_(group_ids))
            )).all()
            direct |= {r[0] for r in rows}
        return list(direct)

    @staticmethod
    async def user_role_codes(db: AsyncSession, user_id: str) -> list[str]:
        role_ids = await RoleService.user_role_ids(db, user_id)
        if not role_ids:
            return []
        rows = (await db.execute(
            select(Role.code).where(Role.id.in_(role_ids))
        )).all()
        return [r[0] for r in rows]

    @staticmethod
    async def user_permission_codes(db: AsyncSession, user_id: str) -> list[str]:
        """有效权限 = 直接角色 ∪ 组角色 的权限 ∪ 例外授权。"""
        role_ids = await RoleService.user_role_ids(db, user_id)
        codes: set[str] = set()
        if role_ids:
            rows = (await db.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id.in_(role_ids))
            )).all()
            codes |= {r[0] for r in rows}

        extra = (await db.execute(
            select(Permission.code, UserPermission.effect)
            .join(Permission, Permission.id == UserPermission.permission_id)
            .where(UserPermission.user_id == user_id)
        )).all()
        for code, effect in extra:
            if effect == "deny":
                codes.discard(code)
            else:
                codes.add(code)
        return sorted(codes)
