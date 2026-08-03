import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path
from pydantic import BaseModel

from database import get_db
from services.file_service import FileService
from models import File as FileModel

router = APIRouter()

class BatchDeleteRequest(BaseModel):
    file_ids: list[str]


@router.post("/upload/chunk")
async def upload_chunk(
    file_id: str = Form(...),
    file_name: str = Form(...),
    file_size: int = Form(...),
    kb_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    chunk: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    return await FileService.upload_chunk(
        db, file_id, file_name, file_size,
        kb_id, chunk_index, total_chunks, chunk.file,
    )


@router.post("/files/{file_id}/process")
async def process_file(
    file_id: str,
    extract_graph: bool = True,
    db: AsyncSession = Depends(get_db),
):
    ok = await FileService.start_processing(file_id, db, extract_graph=extract_graph)
    if not ok:
        raise HTTPException(400, "File not ready for processing")
    return {"status": "processing"}


@router.post("/files/{file_id}/reprocess")
async def reprocess_file(
    file_id: str,
    extract_graph: bool = True,
    db: AsyncSession = Depends(get_db),
):
    ok = await FileService.restart_processing(file_id, db, extract_graph=extract_graph)
    if not ok:
        raise HTTPException(400, "File not ready for reprocessing")
    return {"status": "processing"}


@router.get("/files/{file_id}/status")
async def file_status(file_id: str, db: AsyncSession = Depends(get_db)):
    result = await FileService.get_status(file_id, db)
    if not result:
        raise HTTPException(404, "File not found")
    return result


@router.get("/files/{file_id}/events")
async def file_status_events(
    file_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    initial = await FileService.get_status(file_id, db)
    if not initial:
        raise HTTPException(404, "File not found")

    async def event_stream():
        queue = FileService.subscribe_status(file_id)
        try:
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            FileService.unsubscribe_status(file_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/files")
async def list_files(db: AsyncSession = Depends(get_db)):
    return await FileService.list_all(db)


@router.post("/files/{file_id}/cancel")
async def cancel_processing(file_id: str, db: AsyncSession = Depends(get_db)):
    cancelled = await FileService.cancel_processing(db, file_id)
    if not cancelled:
        raise HTTPException(400, "File not processing or not found")
    return {"status": "cancelled"}


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await FileService.delete(db, file_id)
    if not deleted:
        raise HTTPException(404, "File not found")
    return {"status": "deleted"}


@router.post("/files/batch-delete")
async def batch_delete_files(request: BatchDeleteRequest, db: AsyncSession = Depends(get_db)):
    if not request.file_ids:
        raise HTTPException(400, "file_ids is required")
    result = await FileService.batch_delete(db, request.file_ids)
    return result


@router.get("/files/{file_id}/preview")
async def preview_file(file_id: str, db: AsyncSession = Depends(get_db)):
    """返回文件内容或文件流，供前端预览。

    TXT/MD: 返回纯文本
    PDF: 返回二进制文件流
    DOCX: 返回 501 未实现
    """
    result = await db.execute(select(FileModel).where(FileModel.id == file_id))
    f = result.scalars().first()
    if not f or not f.path:
        raise HTTPException(404, "File not found")

    fp = Path(f.path)
    if not fp.exists():
        raise HTTPException(404, "File not found on disk")

    ext = fp.suffix.lower()
    if ext in (".txt", ".md"):
        text = fp.read_text(encoding="utf-8", errors="ignore")
        return PlainTextResponse(text)

    if ext == ".pdf":
        return FileResponse(fp, media_type="application/pdf")

    raise HTTPException(501, f"Preview not supported for {ext}")
