"""S6（P1-3）对象视图路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import SaveObjectViewRequest
from services.ontology_object_view_service import ObjectViewService

router = APIRouter()


def _nf(detail: str):
    return HTTPException(status_code=404, detail=detail)


def _bad_request(detail: str):
    return HTTPException(status_code=400, detail=detail)


@router.get("/ontology-categories/{category_id}/object-views")
async def list_object_views(category_id: str, db: AsyncSession = Depends(get_db)):
    return await ObjectViewService.list_for_category(db, category_id)


@router.post("/ontology-categories/{category_id}/object-views")
async def create_object_view(
    category_id: str, req: SaveObjectViewRequest, db: AsyncSession = Depends(get_db)
):
    if not req.name or not req.name.strip():
        raise _bad_request("视图名称不能为空")
    view, err = await ObjectViewService.create(db, category_id, req)
    if err:
        raise _bad_request(err)
    return view


@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/object-view")
async def resolve_object_view(
    category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)
):
    """实体详情页用：解析该本体当前应展示的视图（含类别缺省），无则返回 null。"""
    return await ObjectViewService.resolve(db, category_id, ontology_id)


@router.put("/object-views/{view_id}")
async def update_object_view(
    view_id: str, req: SaveObjectViewRequest, db: AsyncSession = Depends(get_db)
):
    view, err = await ObjectViewService.update(db, view_id, req)
    if err:
        raise _nf(err)
    return view


@router.post("/object-views/{view_id}/set-default")
async def set_default_object_view(view_id: str, db: AsyncSession = Depends(get_db)):
    view, err = await ObjectViewService.set_default(db, view_id)
    if err:
        raise _nf(err)
    return view


@router.delete("/object-views/{view_id}")
async def delete_object_view(view_id: str, db: AsyncSession = Depends(get_db)):
    if not await ObjectViewService.delete(db, view_id):
        raise _nf("对象视图不存在")
    return {"status": "deleted"}
