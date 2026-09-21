"""RAG 评测路由：评测集管理（CRUD/导入导出）、评测任务（发起/取消/结果）、Badcase 标记与回流。

设计文档：doc/知识库/RAG评测/RAG评测页面设计.md
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import get_current_username
from database import get_db
from services import rag_eval_service as svc

router = APIRouter()


def _err(e: Exception, code: int = 422):
    raise HTTPException(status_code=code, detail=str(e))


# ────────────────────────── 评估模型选项 ──────────────────────────


@router.get("/eval/llm-options")
async def llm_options(db: AsyncSession = Depends(get_db)):
    """评估模型下拉选项：模型配置页全部配置（精简视图，不暴露密钥）。"""
    return await svc.list_llm_options(db)


# ────────────────────────── 评测集 ──────────────────────────


@router.get("/eval/testsets")
async def list_testsets(db: AsyncSession = Depends(get_db)):
    return await svc.list_testsets(db)


@router.post("/eval/testsets")
async def create_testset(payload: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.create_testset(db, payload)
    except ValueError as e:
        _err(e)


@router.put("/eval/testsets/{testset_id}")
async def update_testset(testset_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.update_testset(db, testset_id, payload)
    except ValueError as e:
        _err(e)


@router.delete("/eval/testsets/{testset_id}")
async def delete_testset(testset_id: str, db: AsyncSession = Depends(get_db)):
    try:
        await svc.delete_testset(db, testset_id)
    except ValueError as e:
        _err(e)
    return {"ok": True}


@router.get("/eval/testsets/{testset_id}/items")
async def list_items(
    testset_id: str,
    origin: str = "",
    keyword: str = "",
    enabled: int | None = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await svc.list_items(
            db, testset_id, origin=origin, keyword=keyword, enabled=enabled,
            page=max(1, page), page_size=min(200, max(1, page_size)),
        )
    except ValueError as e:
        _err(e)


@router.post("/eval/testsets/{testset_id}/items")
async def add_item(testset_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.add_item(db, testset_id, payload)
    except ValueError as e:
        _err(e)


@router.put("/eval/testsets/{testset_id}/items/{item_id}")
async def update_item(testset_id: str, item_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.update_item(db, testset_id, item_id, payload)
    except ValueError as e:
        _err(e)


@router.delete("/eval/testsets/{testset_id}/items/{item_id}")
async def delete_item(testset_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        await svc.delete_item(db, testset_id, item_id)
    except ValueError as e:
        _err(e)
    return {"ok": True}


@router.post("/eval/testsets/{testset_id}/import")
async def import_items(testset_id: str, file: UploadFile, db: AsyncSession = Depends(get_db)):
    """上传导入：JSONL / CSV / XLSX，列名兼容 ragas 合成集（user_input/ground_truth）。"""
    try:
        content = await file.read()
        return await svc.import_items(db, testset_id, file.filename or "", content)
    except ValueError as e:
        _err(e)


@router.get("/eval/testsets/{testset_id}/export")
async def export_items(testset_id: str, format: str = "jsonl", db: AsyncSession = Depends(get_db)):
    try:
        filename, mime, content = await svc.export_items(db, testset_id, fmt=format)
    except ValueError as e:
        _err(e)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ────────────────────────── 评测任务 ──────────────────────────


@router.get("/eval/runs")
async def list_runs(
    testset_id: str = "",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_runs(db, testset_id=testset_id, page=max(1, page),
                               page_size=min(100, max(1, page_size)))


@router.post("/eval/runs")
async def create_run(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(get_current_username),
):
    try:
        return await svc.create_run(db, payload, username)
    except ValueError as e:
        _err(e)


@router.get("/eval/runs/stream")
async def runs_stream():
    """SSE：评测任务进度/状态事件推送（事件驱动刷新，空闲零流量）。

    须注册在 /eval/runs/{run_id} 之前，否则 "stream" 会被路径参数吞掉。
    """
    async def gen():
        q = svc.subscribe_events()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            svc.unsubscribe_events(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/eval/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.get_run(db, run_id)
    except ValueError as e:
        _err(e, 404)


@router.post("/eval/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.cancel_run(db, run_id)
    except ValueError as e:
        _err(e)


@router.delete("/eval/runs/{run_id}")
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """删除评测任务及其全部结果条目（执行中的任务须先取消）。"""
    try:
        await svc.delete_run(db, run_id)
    except ValueError as e:
        _err(e)
    return {"ok": True}


@router.get("/eval/runs/{run_id}/items")
async def list_run_items(
    run_id: str,
    filter: str = "all",
    low_score_metric: str = "faithfulness",
    low_score_threshold: float = 0.6,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await svc.list_run_items(
            db, run_id, filter=filter, low_score_metric=low_score_metric,
            low_score_threshold=low_score_threshold,
            page=max(1, page), page_size=min(100, max(1, page_size)),
        )
    except ValueError as e:
        _err(e)


@router.get("/eval/runs/{run_id}/items/{item_id}")
async def get_run_item(run_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await svc.get_run_item(db, run_id, item_id)
    except ValueError as e:
        _err(e, 404)


@router.get("/eval/runs/{run_id}/report")
async def export_report(run_id: str, db: AsyncSession = Depends(get_db)):
    try:
        filename, _mime, content = await svc.export_report(db, run_id)
    except ValueError as e:
        _err(e, 404)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ────────────────────────── Badcase 标记与回流 ──────────────────────────


@router.put("/eval/runs/{run_id}/items/{item_id}/badcase")
async def mark_badcase(
    run_id: str,
    item_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(get_current_username),
):
    try:
        return await svc.mark_badcase(db, run_id, item_id, payload, username)
    except ValueError as e:
        _err(e)


@router.put("/eval/runs/{run_id}/items/batch-badcase")
async def mark_badcase_batch(
    run_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(get_current_username),
):
    try:
        return await svc.mark_badcase_batch(db, run_id, payload, username)
    except ValueError as e:
        _err(e)


@router.post("/eval/runs/{run_id}/backflow")
async def backflow(run_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    """把已标记的 Badcase 回流进评测集（默认目标=本次评测所用评测集）。"""
    try:
        return await svc.backflow(db, run_id, payload)
    except ValueError as e:
        _err(e)
