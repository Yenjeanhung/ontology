from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import File, OntologySuggestion

router = APIRouter()


@router.get("/notifications/summary")
async def notification_summary(db: AsyncSession = Depends(get_db)):
    """侧栏红点/顶栏消息总数聚合：待审核建议 + 处理中文件 + 失败文件。"""
    suggestions = int(
        (await db.execute(
            select(func.count()).where(OntologySuggestion.status == "ready")
        )).scalar() or 0
    )
    processing = int(
        (await db.execute(
            select(func.count()).where(File.status.in_(["processing", "uploading"]))
        )).scalar() or 0
    )
    failed = int(
        (await db.execute(
            select(func.count()).where(File.status == "failed")
        )).scalar() or 0
    )

    items = []
    if suggestions:
        items.append({"key": "suggestions", "label": "待审核本体建议", "count": suggestions, "to": "/ontology/suggestions"})
    if processing:
        items.append({"key": "files_processing", "label": "文件处理中", "count": processing, "to": "/files"})
    if failed:
        items.append({"key": "files_failed", "label": "文件处理失败", "count": failed, "to": "/kb"})

    return {
        "suggestions": suggestions,
        "files_processing": processing,
        "files_failed": failed,
        "total": suggestions + processing + failed,
        "items": items,
    }
