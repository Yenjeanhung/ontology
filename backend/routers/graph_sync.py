"""图迁入 API（图分析工作台 Tab1 迁入管理）。

设计来源：《图迁入与图计算推理_功能设计》4.4。
迁入是长任务（10 万级 1~2 分钟）：POST run 创建任务后立即返回 run_id，
后台 BackgroundTasks 执行，前端轮询 GET runs/{run_id} 展示进度。
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.graph_sync_service import GraphSyncService

router = APIRouter()


class SyncRunRequest(BaseModel):
    mode: str = "full"          # full / incremental（P2）
    dry_run: bool = False       # 只预检统计，不执行


@router.get("/graph-sync/categories")
async def list_sync_categories(db: AsyncSession = Depends(get_db)):
    """可迁入类别列表（PG 统计 + 最近迁入 + 图/投影状态）。"""
    return await GraphSyncService.list_sync_categories(db)


@router.post("/graph-sync/{category_id}/run")
async def start_sync_run(
    category_id: str,
    body: SyncRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """启动迁入（dry_run 只预检）；返回 run 摘要，后台执行，轮询进度。"""
    try:
        result = await GraphSyncService.start_run(
            db, category_id, mode=body.mode, dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not result.get("dry_run"):
        background_tasks.add_task(
            GraphSyncService.execute_run, result["id"], category_id,
        )
    return result


@router.get("/graph-sync/runs/{run_id}")
async def get_sync_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """迁入进度轮询。"""
    run = await GraphSyncService.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="迁入任务不存在")
    return run


@router.get("/graph-sync/{category_id}/runs")
async def list_sync_runs(
    category_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """某类别的迁入历史。"""
    return await GraphSyncService.list_runs(db, category_id, limit=limit)
