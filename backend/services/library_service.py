from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from models import File, FileAsset, FileDirectory, KnowledgeBase

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
ASSET_DIR = UPLOAD_DIR / "_assets"
CHUNK_DIR = Path(settings.CHUNK_DIR)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset_to_dict(asset: FileAsset) -> dict:
    seen: set[str] = set()
    kb_names: list[str] = []
    for f in asset.kb_files:
        if f.kb and f.kb.name not in seen:
            seen.add(f.kb.name)
            kb_names.append(f.kb.name)
    sources: list[dict] = []
    if asset.sources:
        try:
            sources = json.loads(asset.sources)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": asset.id,
        "directory_id": asset.directory_id,
        "name": asset.name,
        "size": asset.size,
        "ext": asset.ext,
        "mime_type": asset.mime_type,
        "sha256": asset.sha256,
        "source_type": asset.source_type,
        "source_url": asset.source_url,
        "source_keyword": asset.source_keyword,
        "sources": sources,
        "summary": asset.summary,
        "status": asset.status,
        "message": asset.message,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
        "kb_file_count": len(asset.kb_files),
        "kb_names": kb_names,
    }


def _is_text_editable(asset: FileAsset) -> bool:
    return asset.ext.lower() in {"txt", "md", "csv", "json", "html"}


def _directory_to_dict(directory: FileDirectory, file_count: int = 0) -> dict:
    return {
        "id": directory.id,
        "name": directory.name,
        "parent_id": directory.parent_id,
        "created_at": directory.created_at,
        "file_count": file_count,
    }


class LibraryService:
    @staticmethod
    async def ensure_directory_path(db: AsyncSession, parts: list[str]) -> FileDirectory:
        parent_id: str | None = None
        directory: FileDirectory | None = None
        for raw_name in parts:
            name = raw_name.strip()
            if not name:
                continue
            result = await db.execute(
                select(FileDirectory).where(
                    FileDirectory.name == name,
                    FileDirectory.parent_id.is_(None) if parent_id is None else FileDirectory.parent_id == parent_id,
                )
            )
            directory = result.scalar_one_or_none()
            if directory is None:
                directory = FileDirectory(name=name, parent_id=parent_id)
                db.add(directory)
                await db.flush()
            parent_id = directory.id
        if directory is None:
            directory = FileDirectory(name="未归档")
            db.add(directory)
            await db.flush()
        await db.commit()
        await db.refresh(directory)
        return directory

    @staticmethod
    async def default_kb_directory(db: AsyncSession, kb_id: str) -> FileDirectory:
        kb = await db.get(KnowledgeBase, kb_id)
        kb_name = kb.name if kb else kb_id
        return await LibraryService.ensure_directory_path(db, [settings.DEFAULT_KB_UPLOAD_DIR, kb_name])

    @staticmethod
    async def default_crawl_directory(db: AsyncSession, keyword: str) -> FileDirectory:
        return await LibraryService.ensure_directory_path(db, ["采集"])

    @staticmethod
    async def _calculate_directory_file_count(db: AsyncSession, directory_id: str | None, all_directories: list[FileDirectory], all_assets: list[FileAsset]) -> int:
        """递归计算目录及其所有子目录下的文件总数"""
        count = 0
        
        # 计算当前目录下的文件数
        for asset in all_assets:
            if asset.directory_id == directory_id:
                count += 1
        
        # 递归计算子目录的文件数
        for child_dir in all_directories:
            if child_dir.parent_id == directory_id:
                count += await LibraryService._calculate_directory_file_count(db, child_dir.id, all_directories, all_assets)
        
        return count

    @staticmethod
    async def list_directories(db: AsyncSession) -> list[dict]:
        # 先查询所有目录和所有文件
        dir_result = await db.execute(select(FileDirectory).order_by(FileDirectory.created_at.asc()))
        all_directories = list(dir_result.scalars().all())
        
        asset_result = await db.execute(select(FileAsset))
        all_assets = list(asset_result.scalars().all())
        
        # 为每个目录计算文件数量
        result = []
        for directory in all_directories:
            file_count = await LibraryService._calculate_directory_file_count(db, directory.id, all_directories, all_assets)
            result.append(_directory_to_dict(directory, file_count))
        
        return result

    @staticmethod
    async def create_directory(db: AsyncSession, name: str, parent_id: str | None = None) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("Directory name is required")
        if parent_id:
            parent = await db.get(FileDirectory, parent_id)
            if not parent:
                raise ValueError("Parent directory not found")
        directory = FileDirectory(name=name, parent_id=parent_id)
        db.add(directory)
        await db.commit()
        await db.refresh(directory)
        return _directory_to_dict(directory)

    @staticmethod
    async def update_directory(
        db: AsyncSession,
        directory_id: str,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> dict | None:
        directory = await db.get(FileDirectory, directory_id)
        if not directory:
            return None
        if name is not None and name.strip():
            directory.name = name.strip()
        if parent_id != directory_id:
            directory.parent_id = parent_id
        await db.commit()
        await db.refresh(directory)
        return _directory_to_dict(directory)

    @staticmethod
    async def delete_directory(db: AsyncSession, directory_id: str) -> bool:
        directory = await db.get(FileDirectory, directory_id)
        if not directory:
            return False

        child_count = await db.scalar(
            select(func.count()).select_from(FileDirectory).where(FileDirectory.parent_id == directory_id)
        )
        asset_count = await db.scalar(
            select(func.count()).select_from(FileAsset).where(FileAsset.directory_id == directory_id)
        )
        if child_count or asset_count:
            raise ValueError("Directory is not empty")

        await db.delete(directory)
        await db.commit()
        return True

    @staticmethod
    async def create_asset_from_path(
        db: AsyncSession,
        source_path: Path,
        *,
        name: str,
        directory_id: str | None,
        source_type: str = "upload",
        source_url: str | None = None,
        source_keyword: str | None = None,
        sources: list[dict] | None = None,
        summary: str | None = None,
        status: str = "ready",
        move: bool = False,
    ) -> FileAsset:
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(name).suffix.lower()
        asset_id = uuid.uuid4().hex[:12]
        target_path = ASSET_DIR / f"{asset_id}{ext}"
        if move:
            shutil.move(str(source_path), target_path)
        else:
            shutil.copyfile(source_path, target_path)

        size = target_path.stat().st_size
        asset = FileAsset(
            id=asset_id,
            directory_id=directory_id,
            name=name,
            size=size,
            ext=ext.lstrip("."),
            sha256=_hash_file(target_path),
            path=str(target_path),
            source_type=source_type,
            source_url=source_url,
            source_keyword=source_keyword,
            sources=json.dumps(sources, ensure_ascii=False) if sources else None,
            summary=summary,
            status=status,
            updated_at=_now_iso(),
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def upload_asset_chunk(
        db: AsyncSession,
        asset_id: str,
        file_name: str,
        file_size: int,
        directory_id: str | None,
        chunk_index: int,
        total_chunks: int,
        chunk_data,
    ) -> dict:
        if directory_id:
            directory = await db.get(FileDirectory, directory_id)
            if not directory:
                raise ValueError("Directory not found")

        asset = await db.get(FileAsset, asset_id)
        if asset is None:
            asset = FileAsset(
                id=asset_id,
                directory_id=directory_id,
                name=file_name,
                size=file_size,
                ext=Path(file_name).suffix.lower().lstrip("."),
                source_type="upload",
                status="uploading",
                message="上传中",
                updated_at=_now_iso(),
            )
            db.add(asset)
            await db.commit()

        chunk_path = CHUNK_DIR / f"asset_{asset_id}_{chunk_index:06d}"
        with open(chunk_path, "wb") as f:
            shutil.copyfileobj(chunk_data, f)

        received = len(list(CHUNK_DIR.glob(f"asset_{asset_id}_*")))
        if received == total_chunks:
            ASSET_DIR.mkdir(parents=True, exist_ok=True)
            ext = Path(file_name).suffix.lower()
            target_path = ASSET_DIR / f"{asset_id}{ext}"
            chunk_files = sorted(CHUNK_DIR.glob(f"asset_{asset_id}_*"))
            with open(target_path, "wb") as out:
                for part in chunk_files:
                    with open(part, "rb") as chunk_in:
                        out.write(chunk_in.read())
                    part.unlink()

            asset.path = str(target_path)
            asset.size = target_path.stat().st_size
            asset.sha256 = _hash_file(target_path)
            asset.status = "ready"
            asset.message = "上传完成"
            asset.updated_at = _now_iso()
            await db.commit()

        return {"status": "ok", "chunk_index": chunk_index, "received": received}

    @staticmethod
    async def list_assets(
        db: AsyncSession,
        directory_id: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        stmt = (
            select(FileAsset)
            .options(selectinload(FileAsset.kb_files).selectinload(File.kb))
            .order_by(FileAsset.created_at.desc())
        )
        if directory_id:
            stmt = stmt.where(FileAsset.directory_id == directory_id)
        if q:
            pattern = f"%{q.strip()}%"
            stmt = stmt.where(
                (FileAsset.name.ilike(pattern))
                | (FileAsset.summary.ilike(pattern))
                | (FileAsset.source_keyword.ilike(pattern))
            )
        result = await db.execute(stmt)
        return [_asset_to_dict(asset) for asset in result.scalars().all()]

    @staticmethod
    async def get_asset(db: AsyncSession, asset_id: str) -> FileAsset | None:
        return await db.get(FileAsset, asset_id)

    @staticmethod
    async def update_asset(
        db: AsyncSession,
        asset_id: str,
        *,
        name: str | None = None,
        directory_id: str | None = None,
        summary: str | None = None,
        content: str | None = None,
    ) -> dict | None:
        asset = await db.get(FileAsset, asset_id)
        if not asset:
            return None
        if name is not None and name.strip():
            asset.name = name.strip()
            asset.ext = Path(asset.name).suffix.lower().lstrip(".")
        if directory_id is not None:
            asset.directory_id = directory_id or None
        if summary is not None:
            asset.summary = summary.strip()
        if content is not None:
            if not asset.path:
                raise ValueError("Asset file path is missing")
            if not _is_text_editable(asset):
                raise ValueError("Only text-like assets can be edited")
            path = Path(asset.path)
            if not path.exists():
                raise ValueError("Asset file not found on disk")
            path.write_text(content, encoding="utf-8")
            asset.size = path.stat().st_size
            asset.sha256 = _hash_file(path)
        asset.updated_at = _now_iso()
        await db.commit()
        await db.refresh(asset)
        return _asset_to_dict(asset)

    @staticmethod
    async def delete_asset(db: AsyncSession, asset_id: str) -> bool:
        asset = await db.get(FileAsset, asset_id, options=[selectinload(FileAsset.kb_files)])
        if not asset:
            return False
        if asset.kb_files:
            raise ValueError("Asset is used by one or more knowledge bases")
        if asset.path:
            path = Path(asset.path)
            if path.exists():
                path.unlink()
        await db.delete(asset)
        await db.commit()
        return True

    @staticmethod
    async def attach_assets_to_kb(
        db: AsyncSession,
        kb_id: str,
        asset_ids: list[str],
        *,
        auto_process: bool = False,
        extract_graph: bool = True,
    ) -> list[dict]:
        from services.file_service import FileService

        kb = await db.get(KnowledgeBase, kb_id)
        if not kb:
            raise ValueError("Knowledge base not found")

        attached: list[File] = []
        for asset_id in asset_ids:
            asset = await db.get(FileAsset, asset_id)
            if not asset or not asset.path:
                continue
            existing = (
                await db.execute(select(File).where(File.kb_id == kb_id, File.asset_id == asset_id))
            ).scalar_one_or_none()
            if existing:
                attached.append(existing)
                continue
            kb_file = File(
                id=uuid.uuid4().hex[:12],
                asset_id=asset.id,
                kb_id=kb_id,
                name=asset.name,
                size=asset.size,
                total_chunks=0,
                status="uploaded",
                progress=0,
                message="已从文件管理加入，等待处理",
                path=asset.path,
            )
            FileService._write_detail(kb_file, FileService._empty_detail())
            FileService._write_logs(kb_file, [])
            db.add(kb_file)
            attached.append(kb_file)

        await db.commit()

        if auto_process:
            for kb_file in attached:
                await FileService.start_processing(kb_file.id, db, extract_graph=extract_graph)

        return [
            {
                "id": item.id,
                "asset_id": item.asset_id,
                "name": item.name,
                "size": item.size,
                "status": item.status,
                "progress": item.progress,
                "message": item.message,
            }
            for item in attached
        ]
