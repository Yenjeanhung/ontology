"""角色与权限管理路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import audit_ctx
from database import get_db
from middleware.permission import invalidate_user
from models import User, UserRole
from services.audit_service import AuditService
from services.role_service import RoleService

router = APIRouter()


@router.get("/roles")
async def list_roles(db: AsyncSession = Depends(get_db)):
    return await RoleService.list_roles(db)


@router.get("/roles/{role_id}")
async def get_role(role_id: str, db: AsyncSession = Depends(get_db)):
    role = await RoleService.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@router.post("/roles")
async def create_role(req: Request, db: AsyncSession = Depends(get_db)):
    body = await req.json()
    try:
        role = await RoleService.create_role(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AuditService.record(
        **audit_ctx(req), module="system", action="create", action_label="新增角色",
        target_type="role", target_id=role["id"], target_name=role["name"],
        after_value={"code": role["code"], "permissions": role["permissions"]},
    )
    return role


@router.put("/roles/{role_id}")
async def update_role(role_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    before = await RoleService.get_role(db, role_id)
    if not before:
        raise HTTPException(status_code=404, detail="角色不存在")
    body = await req.json()
    try:
        after = await RoleService.update_role(db, role_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 角色权限变更：失效所有持有者缓存，最迟下次请求即生效
    holders = [r[0] for r in (await db.execute(
        select(UserRole.user_id).where(UserRole.role_id == role_id)
    )).all()]
    for uid in holders:
        invalidate_user(uid)
    AuditService.record(
        **audit_ctx(req), module="system", action="grant", action_label="修改角色权限",
        target_type="role", target_id=role_id, target_name=after["name"],
        before_value={"permissions": before["permissions"]},
        after_value={"permissions": after["permissions"]},
    )
    return after


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    before = await RoleService.get_role(db, role_id)
    if not before:
        raise HTTPException(status_code=404, detail="角色不存在")
    try:
        await RoleService.delete_role(db, role_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AuditService.record(
        **audit_ctx(req), module="system", action="delete", action_label="删除角色",
        target_type="role", target_id=role_id, target_name=before["name"],
        before_value={"code": before["code"]},
    )
    return {"status": "deleted"}


@router.get("/permissions")
async def permission_tree(db: AsyncSession = Depends(get_db)):
    return await RoleService.permission_tree(db)


@router.post("/permissions/sync")
async def sync_permissions(req: Request, db: AsyncSession = Depends(get_db)):
    added = await RoleService.sync_permissions(db)
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="同步权限点",
        target_type="permission", target_id="", target_name=f"新增 {added} 项",
    )
    return {"added": added}


@router.get("/roles/{role_id}/users")
async def role_users(role_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(User.id, User.username, User.nickname)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role_id == role_id)
    )).all()
    return [{"id": r[0], "username": r[1], "nickname": r[2]} for r in rows]
