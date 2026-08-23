"""工作流运行记录服务：裁剪等维护逻辑。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import WorkflowRun


class WorkflowRunService:
    @staticmethod
    async def trim(db: AsyncSession, workflow_id: str, keep: int | None = None):
        """保留每个工作流最近 keep 条运行记录，超出删除（按 started_at 降序）。

        在落库新 run 后调用，避免运行记录无限增长。
        """
        if keep is None:
            keep = settings.WORKFLOW_KEEP_RUNS
        if keep <= 0:
            return
        rows = (
            await db.execute(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id == workflow_id)
                .order_by(WorkflowRun.started_at.desc())
            )
        ).scalars().all()
        to_delete = rows[keep:]
        if not to_delete:
            return
        for old in to_delete:
            await db.delete(old)
        await db.commit()
