"""系统偏好设置（运行时可改的页面开关，进程内缓存读取，避免频繁打库）。

与安全策略（security_settings）分开存放：这里放业务与运行时偏好。
"""
from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AppSetting

DEFAULTS: dict[str, str] = {
    # 上传 / 从文件管理挂载后是否自动分析文档并推荐分片策略。
    # 默认关闭：分析需解析全文并统计文档特征，大文档耗时明显；
    # 需要推荐时可在处理确认弹窗点「开始分析」手动触发。
    "chunk_auto_analyze": "false",
}

_CACHE_TTL_SECONDS = 5.0

_cache: dict[str, str] = {}
_loaded_at = 0.0


class AppSettingsService:
    """偏好读写；上传等热路径用 `get_bool` 读缓存。"""

    @staticmethod
    async def refresh(db: AsyncSession) -> dict[str, str]:
        global _cache, _loaded_at
        rows = (await db.execute(select(AppSetting))).scalars().all()
        merged = dict(DEFAULTS)
        for row in rows:
            if row.key in DEFAULTS:
                merged[row.key] = row.value or DEFAULTS[row.key]
        _cache = merged
        _loaded_at = time.monotonic()
        return merged

    @staticmethod
    async def get_all(db: AsyncSession) -> dict[str, str]:
        if time.monotonic() - _loaded_at > _CACHE_TTL_SECONDS:
            await AppSettingsService.refresh(db)
        return dict(_cache or DEFAULTS)

    @staticmethod
    async def get_bool(db: AsyncSession, key: str, default: bool = False) -> bool:
        values = await AppSettingsService.get_all(db)
        raw = values.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    async def upsert(db: AsyncSession, patch: dict, updated_by: str = "") -> dict[str, str]:
        now = datetime.now().isoformat()
        for key, value in patch.items():
            if key not in DEFAULTS:
                continue
            row = await db.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key)
                db.add(row)
            row.value = str(value)
            row.updated_by = updated_by
            row.updated_at = now
        await db.commit()
        return await AppSettingsService.refresh(db)

    @staticmethod
    async def ensure_defaults(db: AsyncSession) -> None:
        existed = {r[0] for r in (await db.execute(select(AppSetting.key))).all()}
        missing = [k for k in DEFAULTS if k not in existed]
        if missing:
            now = datetime.now().isoformat()
            for key in missing:
                db.add(AppSetting(key=key, value=DEFAULTS[key], updated_by="system", updated_at=now))
            await db.commit()
        await AppSettingsService.refresh(db)
