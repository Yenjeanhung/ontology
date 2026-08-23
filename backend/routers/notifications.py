from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import File, OntologySuggestion, Schedule

router = APIRouter()


@router.get("/notifications/summary")
async def notification_summary(db: AsyncSession = Depends(get_db)):
    """侧栏红点/顶栏消息总数聚合：待审核建议 + 处理中文件 + 失败文件 + 调度失败告警。"""
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
    # 定时调度：已达告警阈值且未静默的计划数（右上角消息中心提示）
    schedule_alerts = int(
        (await db.execute(
            select(func.count()).where(
                Schedule.alert_on_failure == 1,
                Schedule.muted == 0,
                Schedule.consecutive_failures >= Schedule.max_failures_alert,
            )
        )).scalar() or 0
    )

    items = []
    if suggestions:
        items.append({"key": "suggestions", "label": "待审核本体建议", "count": suggestions, "to": "/ontology/suggestions"})
    if processing:
        items.append({"key": "files_processing", "label": "文件处理中", "count": processing, "to": "/files"})
    if failed:
        items.append({"key": "files_failed", "label": "文件处理失败", "count": failed, "to": "/kb"})
    if schedule_alerts:
        items.append({"key": "schedule_alerts", "label": "定时任务连续失败", "count": schedule_alerts, "to": "/schedules"})

    return {
        "suggestions": suggestions,
        "files_processing": processing,
        "files_failed": failed,
        "schedule_alerts": schedule_alerts,
        "total": suggestions + processing + failed + schedule_alerts,
        "items": items,
    }
