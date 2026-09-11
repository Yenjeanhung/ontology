"""在线会话（上下线管理）路由：查看在线用户、强制下线。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import audit_ctx, get_current_user
from database import get_db
from models import User
from services.audit_service import AuditService
from services.session_service import SessionService

router = APIRouter()


@router.get("/sessions")
async def list_sessions(keyword: str = "", status: str = "", user_id: str = "",
                        page: int = 1, page_size: int = 20,
                        db: AsyncSession = Depends(get_db)):
    return await SessionService.list_sessions(db, keyword=keyword, status=status,
                                              user_id=user_id, page=page, page_size=page_size)


@router.get("/sessions/stats")
async def session_stats(db: AsyncSession = Depends(get_db)):
    return await SessionService.stats(db)


@router.delete("/sessions/{sid}")
async def kick_session(sid: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    if current.get("session_id") == sid:
        raise HTTPException(status_code=400, detail="不能踢出当前会话，请使用退出登录")
    count = await SessionService.close(
        db, sid, "kicked",
        kicked_by=current.get("username") or "",
        kick_reason="管理员强制下线",
    )
    if not count:
        raise HTTPException(status_code=404, detail="会话不存在或已结束")
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="强制下线会话",
        target_type="session", target_id=sid, target_name=sid,
    )
    return {"status": "kicked"}


@router.post("/sessions/user/{user_id}/kick-all")
async def kick_user_sessions(user_id: str, req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    count = await SessionService.kick_user(
        db, user_id, kicked_by=current.get("username") or "", reason="管理员强制下线")
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="强制下线（全部会话）",
        target_type="user", target_id=user_id, target_name=target.username,
        after_value={"kicked_sessions": count},
    )
    return {"kicked": count}


@router.post("/sessions/kick-all")
async def kick_all_sessions(req: Request, db: AsyncSession = Depends(get_db)):
    current = get_current_user(req)
    count = await SessionService.kick_all(
        db, kicked_by=current.get("username") or "", reason="全局强制下线",
        exclude_user_id=current.get("user_id") or "")
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="全局强制下线",
        target_type="session", target_id="", target_name=f"{count} 个会话",
    )
    return {"kicked": count}
