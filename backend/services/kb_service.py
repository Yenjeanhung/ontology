from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from models import KnowledgeBase
from providers.graph_store import delete_kb_graph
from providers.vector_store import delete_kb_collection
from services.file_service import FileService


class KBService:
    @staticmethod
    async def create(db: AsyncSession, name: str) -> dict:
        kb = KnowledgeBase(name=name.strip())
        db.add(kb)
        await db.commit()
        await db.refresh(kb)
        return {"id": kb.id, "name": kb.name, "created_at": kb.created_at, "files": {}}

    @staticmethod
    async def list_all(db: AsyncSession) -> list[dict]:
        result = await db.execute(select(KnowledgeBase).options(selectinload(KnowledgeBase.files)))
        kbs = result.scalars().all()
        return [
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "created_at": kb.created_at,
                "file_count": len(kb.files),
                "file_types": sorted(
                    set(Path(file.name).suffix.lower().lstrip(".") for file in kb.files if file.name)
                ),
                # 添加处理状态：如果有文件正在处理，返回处理中的文件数和总进度
                "processing_files": sum(1 for file in kb.files if file.status == "processing"),
                "pending_files": sum(1 for file in kb.files if file.status in ("uploaded", "uploading")),
                # 添加失败文件数量
                "failed_files": sum(1 for file in kb.files if file.status == "failed"),
                "overall_progress": max((file.progress for file in kb.files), default=0) if kb.files else 0,
                "chunk_count": sum(file.total_chunks for file in kb.files),
                "updated_at": max((file.created_at for file in kb.files), default=kb.created_at),
            }
            for kb in kbs
        ]

    @staticmethod
    async def get(db: AsyncSession, kb_id: str) -> dict | None:
        result = await db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .options(selectinload(KnowledgeBase.files))
        )
        kb = result.scalar_one_or_none()
        if not kb:
            return None

        files = [
            {
                "id": file.id,
                "asset_id": file.asset_id,
                "name": file.name,
                "size": file.size,
                "status": file.status,
                "progress": file.progress,
                "message": file.message,
                "detail": FileService._read_detail(file),
                "logs": FileService._read_logs(file),
            }
            for file in kb.files
        ]
        return {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "created_at": kb.created_at,
            "file_count": len(kb.files),
            "files": files,
        }

    @staticmethod
    async def update(
        db: AsyncSession,
        kb_id: str,
        name: str | None,
        description: str | None,
    ) -> dict | None:
        result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalar_one_or_none()
        if not kb:
            return None

        if name is not None:
            kb.name = name.strip()
        if description is not None:
            kb.description = description.strip()
        await db.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}

    @staticmethod
    async def delete(db: AsyncSession, kb_id: str) -> bool:
        result = await db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .options(selectinload(KnowledgeBase.files))
        )
        kb = result.scalar_one_or_none()
        if not kb:
            return False

        kb_dir = Path(settings.UPLOAD_DIR) / kb_id
        if kb_dir.exists():
            shutil.rmtree(kb_dir)

        delete_kb_collection(kb_id)
        delete_kb_graph(kb_id)

        await db.delete(kb)
        await db.commit()
        return True
