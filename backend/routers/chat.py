"""会话管理路由（doc/智能体/智能体会话_功能设计.md P1）。

- GET    /chat/sessions?agent_id=&kb_id=   会话列表（按最近更新倒序，仅本人会话）
- GET    /chat/sessions/{sid}/messages     会话消息（历史回放，仅本人会话）
- POST   /chat/sessions/{sid}/rename       重命名（仅本人会话）
- DELETE /chat/sessions/{sid}              删除（连同消息，仅本人会话）

会话的创建不走这里：/agent/query 不带 session_id 时自动新建，
避免「空会话」堆积。

安全：全部接口按当前登录用户隔离（user_id 不匹配一律 404，不暴露会话存在性）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import get_current_user_id
from database import get_db
from schemas import ChatSessionRename
from services.chat_service import ChatService

router = APIRouter()


def _session_dict(s) -> dict:
    return {
        "id": s.id,
        "session_id": s.id,
        "agent_id": s.agent_id,
        "kb_id": s.kb_id,
        "user_id": s.user_id,
        "title": s.title,
        "has_summary": bool(s.summary),
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


@router.get("/chat/sessions")
async def list_sessions(
    agent_id: str | None = Query(default=None, description="按智能体过滤；不传=全部"),
    kb_id: str | None = Query(default=None, description="按知识库过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    rows = await ChatService.list_sessions(db, agent_id=agent_id, kb_id=kb_id,
                                           limit=limit, user_id=user_id)
    return [_session_dict(s) for s in rows]


@router.get("/chat/sessions/{session_id}/messages")
async def list_messages(session_id: str, db: AsyncSession = Depends(get_db),
                        user_id: str = Depends(get_current_user_id)):
    session = await ChatService.get_owned(db, session_id, user_id)
    if not session:
        raise HTTPException(404, "会话不存在或已被删除")
    messages = await ChatService.get_messages(db, session_id)
    import json as _json

    out: list[dict] = []
    for m in messages:
        meta = None
        if m.meta:
            try:
                meta = _json.loads(m.meta)
            except ValueError:
                meta = None
        out.append({
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "meta": meta,
            "created_at": m.created_at,
        })
    return {"session_id": session_id, "title": session.title, "messages": out}


@router.post("/chat/sessions/{session_id}/rename")
async def rename_session(session_id: str, req: ChatSessionRename, db: AsyncSession = Depends(get_db),
                         user_id: str = Depends(get_current_user_id)):
    if not await ChatService.get_owned(db, session_id, user_id):
        raise HTTPException(404, "会话不存在或已被删除")
    session = await ChatService.rename(db, session_id, req.title)
    if not session:
        raise HTTPException(404, "会话不存在或已被删除")
    return _session_dict(session)


@router.delete("/chat/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db),
                         user_id: str = Depends(get_current_user_id)):
    if not await ChatService.get_owned(db, session_id, user_id):
        raise HTTPException(404, "会话不存在或已被删除")
    if not await ChatService.delete(db, session_id):
        raise HTTPException(404, "会话不存在或已被删除")
    return {"status": "deleted"}
