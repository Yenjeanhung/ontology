"""安全与登录策略（运行时可改，进程内缓存读取，避免每个请求打库）。"""
from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import SecuritySetting

DEFAULTS: dict[str, str] = {
    "auth_enabled": "true",
    "password_min_length": "8",
    "password_complexity": "medium",       # none / low / medium / high
    "password_expire_days": "90",          # 0 = 不过期
    "max_failed_attempts": "5",            # 0 = 不锁定
    "lock_minutes": "30",                  # 0 = 需管理员解锁
    "max_sessions_per_user": "5",          # 0 = 不限
    "idle_timeout_minutes": "120",         # 0 = 不限
    "access_token_ttl_minutes": "120",
    "refresh_token_ttl_days": "7",
    "single_device_login": "false",
    "captcha_enabled": "false",            # P2 预留
    "audit_log_retention_days": "180",
    "auth_log_retention_days": "365",
    "log_get_requests": "false",
}

_CACHE_TTL_SECONDS = 10.0

_cache: dict[str, str] = {}
_loaded_at = 0.0


class SecuritySettingsService:
    """策略读写。中间件热路径用 `snapshot()` 读缓存，不走数据库。"""

    @staticmethod
    async def refresh(db: AsyncSession) -> dict[str, str]:
        global _cache, _loaded_at
        rows = (await db.execute(select(SecuritySetting))).scalars().all()
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
            await SecuritySettingsService.refresh(db)
        return dict(_cache or DEFAULTS)

    @staticmethod
    def snapshot() -> dict[str, str]:
        """同步读取缓存（未加载时返回默认值）。

        中间件在启动时已通过 bootstrap 预热；极早期请求会落到默认值。
        """
        return dict(_cache or DEFAULTS)

    @staticmethod
    def get(key: str, default: str = "") -> str:
        return (_cache or DEFAULTS).get(key, default)

    @staticmethod
    def get_int(key: str, default: int = 0) -> int:
        raw = (_cache or DEFAULTS).get(key)
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def get_bool(key: str, default: bool = False) -> bool:
        raw = (_cache or DEFAULTS).get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    async def upsert(db: AsyncSession, patch: dict, updated_by: str = "") -> dict[str, str]:
        from datetime import datetime

        now = datetime.now().isoformat()
        for key, value in patch.items():
            if key not in DEFAULTS:
                continue
            row = await db.get(SecuritySetting, key)
            if row is None:
                row = SecuritySetting(key=key)
                db.add(row)
            row.value = str(value)
            row.updated_by = updated_by
            row.updated_at = now
        await db.commit()
        return await SecuritySettingsService.refresh(db)

    @staticmethod
    async def ensure_defaults(db: AsyncSession) -> None:
        from datetime import datetime

        existed = {r[0] for r in (await db.execute(select(SecuritySetting.key))).all()}
        missing = [k for k in DEFAULTS if k not in existed]
        if missing:
            now = datetime.now().isoformat()
            for key in missing:
                db.add(SecuritySetting(key=key, value=DEFAULTS[key], updated_by="system", updated_at=now))
            await db.commit()
        await SecuritySettingsService.refresh(db)
