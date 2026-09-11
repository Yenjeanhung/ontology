"""用户与用户组管理路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import audit_ctx, get_current_user
from database import get_db
from models import User, UserGroupMember
from services.audit_service import AuditService
from services.user_service import UserService

router = APIRouter()


@router.get("/users")
async def list_users(keyword: str = "", status: str = "", group_id: str = "",
                     page: int = 1, page_size: int = 20,
                     db: AsyncSession = Depends(get_db)):
    return await UserService.list_users(db, keyword=keyword, status=status,
                                        group_id=group_id, page=page, page_size=page_size)


@router.get("/users/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await UserService.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.post("/users")
async def create_user(req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    body = await req.json()
    try:
        user = await UserService.create_user(db, body, operator=current.get("username") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AuditService.record(
        **audit_ctx(req), module="system", action="create", action_label="新增用户",
        target_type="user", target_id=user["id"], target_name=user["username"],
        after_value={k: user.get(k) for k in ("username", "nickname", "email", "status", "roles")},
    )
    return user


@router.patch("/users/{user_id}")
async def update_user(user_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    body = await req.json()
    before = await UserService.get_user(db, user_id)
    if not before:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        after = await UserService.update_user(db, user_id, body, operator=current.get("username") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from middleware.permission import invalidate_user

    invalidate_user(user_id)
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="修改用户",
        target_type="user", target_id=user_id, target_name=after["username"],
        before_value={k: before.get(k) for k in ("nickname", "email", "phone", "status", "roles", "groups")},
        after_value={k: after.get(k) for k in ("nickname", "email", "phone", "status", "roles", "groups")},
    )
    return after


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    if current.get("user_id") == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    before = await UserService.get_user(db, user_id)
    if not before:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        await UserService.delete_user(db, user_id, operator=current.get("username") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AuditService.record(
        **audit_ctx(req), module="system", action="delete", action_label="删除用户",
        target_type="user", target_id=user_id, target_name=before["username"],
        before_value={"username": before["username"], "roles": before.get("roles")},
    )
    return {"status": "deleted"}


@router.post("/users/batch-delete")
async def batch_delete_users(req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    body = await req.json()
    ids = body.get("ids") or []
    deleted, skipped = [], []
    for uid in ids:
        if uid == current.get("user_id"):
            skipped.append(uid)
            continue
        try:
            await UserService.delete_user(db, uid, operator=current.get("username") or "")
            deleted.append(uid)
        except ValueError:
            skipped.append(uid)
    AuditService.record(
        **audit_ctx(req), module="system", action="delete", action_label="批量删除用户",
        target_type="user", target_id=",".join(deleted), target_name=f"{len(deleted)} 个用户",
    )
    return {"deleted": deleted, "skipped": skipped}


@router.patch("/users/{user_id}/status")
async def set_user_status(user_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    body = await req.json()
    if current.get("user_id") == user_id and body.get("status") != "active":
        raise HTTPException(status_code=400, detail="不能停用自己的账号")
    before = await UserService.get_user(db, user_id)
    if not before:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        after = await UserService.set_status(
            db, user_id, body.get("status") or "active", operator=current.get("username") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from middleware.permission import invalidate_user

    invalidate_user(user_id)
    AuditService.record(
        **audit_ctx(req), module="system",
        action={"active": "enable", "disabled": "disable", "locked": "lock"}.get(body.get("status"), "update"),
        action_label=f"账号{ {'active': '启用', 'disabled': '停用', 'locked': '锁定'}.get(body.get('status'), '变更') }",
        target_type="user", target_id=user_id, target_name=after["username"],
        before_value={"status": before["status"]}, after_value={"status": after["status"]},
    )
    return after


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    try:
        body = await req.json()
    except Exception:
        body = {}
    new_password = (body.get("new_password") or "").strip()
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        result = await UserService.reset_password(
            db, user_id, operator=current.get("username") or "", new_password=new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from middleware.permission import invalidate_user

    invalidate_user(user_id)
    AuditService.record(
        **audit_ctx(req), module="system", action="update",
        action_label="修改密码" if new_password else "重置密码",
        target_type="user", target_id=user_id, target_name=target.username,
    )
    return result


@router.post("/users/{user_id}/roles")
async def set_user_roles(user_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    body = await req.json()
    before = await UserService.user_roles(db, user_id)
    await UserService.set_roles(db, user_id, body.get("role_ids") or [],
                                operator=current.get("username") or "")
    after = await UserService.user_roles(db, user_id)
    await db.commit()
    from middleware.permission import invalidate_user

    invalidate_user(user_id)
    AuditService.record(
        **audit_ctx(req), module="system", action="grant", action_label="设置用户角色",
        target_type="user", target_id=user_id, target_name=(await db.get(User, user_id)).username,
        before_value={"roles": before}, after_value={"roles": after},
    )
    return {"status": "ok", "roles": after}


# ── 用户组 ──


@router.get("/user-groups")
async def list_groups(db: AsyncSession = Depends(get_db)):
    return await UserService.list_groups(db)


@router.get("/user-groups/{group_id}")
async def get_group(group_id: str, db: AsyncSession = Depends(get_db)):
    groups = await UserService.list_groups(db)
    group = next((g for g in groups if g["id"] == group_id), None)
    if not group:
        raise HTTPException(status_code=404, detail="用户组不存在")
    group = dict(group)
    group["role_ids"] = await UserService.group_role_ids(db, group_id)
    members = (await db.execute(
        select(UserGroupMember.user_id).where(UserGroupMember.group_id == group_id)
    )).all()
    group["member_ids"] = [m[0] for m in members]
    return group


@router.post("/user-groups")
async def create_group(req: Request, db: AsyncSession = Depends(get_db)):
    body = await req.json()
    try:
        group = await UserService.create_group(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AuditService.record(
        **audit_ctx(req), module="system", action="create", action_label="新增用户组",
        target_type="user_group", target_id=group["id"], target_name=group["name"],
    )
    return group


@router.put("/user-groups/{group_id}")
async def update_group(group_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    body = await req.json()
    try:
        group = await UserService.update_group(db, group_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if body.get("member_ids") is not None or body.get("role_ids") is not None:
        from middleware.permission import invalidate_user
        from models import UserGroupMember

        member_ids = [r[0] for r in (await db.execute(
            select(UserGroupMember.user_id).where(UserGroupMember.group_id == group_id)
        )).all()]
        for uid in member_ids:
            invalidate_user(uid)
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="修改用户组",
        target_type="user_group", target_id=group_id, target_name=group["name"],
    )
    return group


@router.delete("/user-groups/{group_id}")
async def delete_group(group_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    try:
        await UserService.delete_group(db, group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    AuditService.record(
        **audit_ctx(req), module="system", action="delete", action_label="删除用户组",
        target_type="user_group", target_id=group_id, target_name=group_id,
    )
    return {"status": "deleted"}
