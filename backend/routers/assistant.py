# -*- coding: utf-8 -*-
"""智能助手（全局浮标）单智能体端点。

POST /api/agent/assistant/run：SSE 流式对话（LLM + Function Calling 工具循环），
智能体身份 = 内置 preset「智能助手」（agent_assistant），技能/工具/人设经
智能体配置页（/agent/configs）维护。与多智能体协作端点（/agent/multi/*）无耦合。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import get_current_user_id
from database import get_db
from services.assistant_service import AssistantUnavailable, prepare_turn, stream_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])


class AssistantRunRequest(BaseModel):
    query: str
    session_id: str | None = None   # 续聊锚点（首次为空，服务端新建会话）


@router.post("/agent/assistant/run")
async def assistant_run(req: AssistantRunRequest, db: AsyncSession = Depends(get_db),
                        user_id: str = Depends(get_current_user_id)):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "提问内容不能为空")
    try:
        ctx = await prepare_turn(db, query, req.session_id, user_id)
    except AssistantUnavailable as exc:
        raise HTTPException(404, str(exc))

    async def _stream():
        async for evt in stream_turn(ctx):
            yield evt

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
