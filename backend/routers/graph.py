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


@router.get("/graph/expand")
async def graph_expand(
    entity_id: str = Query(..., min_length=1),
    relation_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """双击展开实体的一跳邻居（懒加载），按关系 id 稳定分页。"""
    try:
        return await GraphDataService.expand_entity(
            db,
            entity_id=entity_id,
            relation_type=relation_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/graph/ontology-view")
async def graph_ontology_view(
    category_id: str | None = Query(default=None),
    ontology_id: str | None = Query(default=None),
    entity_query: str | None = Query(default=None),
    relation_type: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """按本体类别/域查看图谱（不依赖知识库，可查看非文件抽取实体）。"""
    if not category_id and not ontology_id:
        raise HTTPException(status_code=400, detail="category_id 或 ontology_id 至少提供一个")
    return await GraphDataService.get_ontology_view(
        db,
        category_id=category_id,
        ontology_id=ontology_id,
        entity_query=entity_query,
        relation_type=relation_type,
        limit=limit,
        offset=offset,
    )
