import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.notification_hub import HEARTBEAT_SECONDS, compute_summary, hub

router = APIRouter()


@router.get("/notifications/summary")
async def notification_summary(db: AsyncSession = Depends(get_db)):
    """侧栏红点/顶栏消息总数聚合：待审核建议 + 处理中文件 + 失败文件 + 调度失败告警 + 人工待办。"""
    return await compute_summary(db)


@router.get("/notifications/stream")
async def notification_stream(request: Request):
    """通知计数推送（SSE）：仅在计数变化时下发，替代前端定时轮询。

    事件体为 summary 对象；心跳为注释行 `: ping`（EventSource 忽略，仅用于保活）。
    """
    queue = await hub.subscribe()

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if payload is None:
                    break
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        finally:
            await hub.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
