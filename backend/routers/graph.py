from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.graph_data_service import GraphDataService

router = APIRouter()


@router.get("/graph/relation-types")
async def graph_relation_types(
    kb_id: str = Query(..., min_length=1),
    file_id: str | None = Query(default=None),
):
    return await GraphDataService.list_relation_types(kb_id, file_id=file_id)


@router.get("/graph/view")
async def graph_view(
    kb_id: str = Query(..., min_length=1),
    file_id: str | None = Query(default=None),
    entity_query: str | None = Query(default=None),
    relation_type: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await GraphDataService.get_view(
            db,
            kb_id=kb_id,
            file_id=file_id,
            entity_query=entity_query,
            relation_type=relation_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
