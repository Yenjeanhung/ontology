"""智能体（OAG）路由。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import AgentQueryRequest
from services.kb_service import KBService
from services.ontology_service import OntologyService
from services.oag_service import OAGService

router = APIRouter()


@router.post("/agent/query")
async def agent_query(req: AgentQueryRequest, db: AsyncSession = Depends(get_db)):
    kb = await KBService.get(db, req.kb_id)
    if not kb:
        raise HTTPException(404, "Knowledge base not found")

    # 预加载本体 schema：db 会话在响应返回后释放，SSE 生成器不再持有 db
    try:
        ontology_schema = await OntologyService.get_kb_extraction_constraints(db, req.kb_id)
    except Exception:
        ontology_schema = None

    return StreamingResponse(
        OAGService.query_stream(req.kb_id, req.query, kb["name"], ontology_schema),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
