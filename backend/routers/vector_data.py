from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.vector_data_service import VectorDataService

router = APIRouter()


@router.get("/vector-records")
async def list_vector_records(
    kb_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    unsynced_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await VectorDataService.list_records(
        db,
        kb_id=kb_id,
        query=q,
        unsynced_only=unsynced_only,
        limit=limit,
        offset=offset,
    )


@router.get("/vector-search-test")
async def vector_search_test(
    kb_id: str = Query(...),
    query: str = Query(..., min_length=1),
    top_k: int = Query(default=8, ge=1, le=20),
):
    return await VectorDataService.similarity_test(kb_id, query, top_k=top_k)


@router.get("/vector-summary-export")
async def export_vector_summary(
    kb_id: str | None = Query(default=None),
    format: str = Query(default="json", pattern="^(json|md)$"),
    db: AsyncSession = Depends(get_db),
):
    result = await VectorDataService.export_summary(db, kb_id=kb_id, fmt=format)
    if format == "md":
        return PlainTextResponse(result, media_type="text/markdown")
    return result
