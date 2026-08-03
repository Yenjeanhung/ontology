from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from config import settings
from schemas import (
    AttachAssetsRequest,
    CreateCrawlJobRequest,
    CreateDirectoryRequest,
    UpdateAssetRequest,
    UpdateDirectoryRequest,
)
from services.crawl_service import CrawlService
from services.library_service import LibraryService

router = APIRouter()


@router.get("/config")
async def get_config():
    from providers.graph_store import get_graph_store_provider_name
    from providers.vector_store import get_vector_store_provider_name
    return {
        "crawl_max_pages": settings.CRAWL_MAX_PAGES,
        "vector_provider": get_vector_store_provider_name(),
        "graph_provider": get_graph_store_provider_name(),
    }


@router.get("/file-directories")
async def list_directories(db: AsyncSession = Depends(get_db)):
    return await LibraryService.list_directories(db)


@router.post("/file-directories")
async def create_directory(req: CreateDirectoryRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await LibraryService.create_directory(db, req.name, req.parent_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.put("/file-directories/{directory_id}")
async def update_directory(
    directory_id: str,
    req: UpdateDirectoryRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await LibraryService.update_directory(db, directory_id, req.name, req.parent_id)
    if not result:
        raise HTTPException(404, "Directory not found")
    return result


@router.delete("/file-directories/{directory_id}")
async def delete_directory(directory_id: str, db: AsyncSession = Depends(get_db)):
    try:
        deleted = await LibraryService.delete_directory(db, directory_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "Directory not found")
    return {"status": "deleted"}


@router.get("/assets")
async def list_assets(
    directory_id: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await LibraryService.list_assets(db, directory_id=directory_id, q=q)


@router.post("/assets/upload/chunk")
async def upload_asset_chunk(
    asset_id: str = Form(...),
    file_name: str = Form(...),
    file_size: int = Form(...),
    directory_id: str | None = Form(None),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    chunk: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await LibraryService.upload_asset_chunk(
            db,
            asset_id,
            file_name,
            file_size,
            directory_id,
            chunk_index,
            total_chunks,
            chunk.file,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/assets/{asset_id}/preview")
async def preview_asset(asset_id: str, db: AsyncSession = Depends(get_db)):
    asset = await LibraryService.get_asset(db, asset_id)
    if not asset or not asset.path:
        raise HTTPException(404, "Asset not found")

    path = Path(asset.path)
    if not path.exists():
        raise HTTPException(404, "Asset not found on disk")

    ext = path.suffix.lower()
    if ext in (".txt", ".md", ".csv", ".json", ".html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        return PlainTextResponse(text)
    if ext == ".pdf":
        return FileResponse(path, media_type="application/pdf")
    raise HTTPException(501, f"Preview not supported for {ext}")


@router.put("/assets/{asset_id}")
async def update_asset(asset_id: str, req: UpdateAssetRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await LibraryService.update_asset(
            db,
            asset_id,
            name=req.name,
            directory_id=req.directory_id,
            summary=req.summary,
            content=req.content,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not result:
        raise HTTPException(404, "Asset not found")
    return result


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str, db: AsyncSession = Depends(get_db)):
    try:
        deleted = await LibraryService.delete_asset(db, asset_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "Asset not found")
    return {"status": "deleted"}


@router.post("/kb/{kb_id}/assets")
async def attach_assets(kb_id: str, req: AttachAssetsRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await LibraryService.attach_assets_to_kb(
            db,
            kb_id,
            req.asset_ids,
            auto_process=req.auto_process,
            extract_graph=req.extract_graph,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/crawl-jobs")
async def create_crawl_job(req: CreateCrawlJobRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await CrawlService.create_job(
            db,
            req.keyword,
            directory_id=req.directory_id,
            max_pages=req.max_pages,
            auto_attach_kb_id=req.auto_attach_kb_id,
            auto_process=req.auto_process,
            extract_graph=req.extract_graph,
            analysis_depth=req.analysis_depth,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/crawl-jobs/latest")
async def get_latest_crawl_job(db: AsyncSession = Depends(get_db)):
    return await CrawlService.get_latest_job(db)


@router.get("/crawl-jobs/{job_id}")
async def get_crawl_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await CrawlService.get_job(db, job_id)
    if not result:
        raise HTTPException(404, "Crawl job not found")
    return result
