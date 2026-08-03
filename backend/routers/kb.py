from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import CreateKBRequest, UpdateKBRequest
from services.kb_service import KBService

router = APIRouter()


@router.post("/kb")
async def create_kb(req: CreateKBRequest, db: AsyncSession = Depends(get_db)):
    return await KBService.create(db, req.name)


@router.get("/kb")
async def list_kbs(db: AsyncSession = Depends(get_db)):
    return await KBService.list_all(db)


@router.get("/kb/{kb_id}")
async def get_kb(kb_id: str, db: AsyncSession = Depends(get_db)):
    kb = await KBService.get(db, kb_id)
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    return kb


@router.put("/kb/{kb_id}")
async def update_kb(kb_id: str, req: UpdateKBRequest, db: AsyncSession = Depends(get_db)):
    kb = await KBService.update(db, kb_id, req.name, req.description)
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    return kb


@router.delete("/kb/{kb_id}")
async def delete_kb(kb_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await KBService.delete(db, kb_id)
    if not deleted:
        raise HTTPException(404, "Knowledge base not found")
    return {"status": "deleted"}
