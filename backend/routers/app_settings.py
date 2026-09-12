"""系统偏好设置接口（页面开关，运行时可改）。"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.app_settings_service import AppSettingsService, DEFAULTS

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings/app")
async def get_app_settings(db: AsyncSession = Depends(get_db)):
    """读取全部系统偏好（含默认值，未知 key 忽略）。"""
    return {
        "values": await AppSettingsService.get_all(db),
        "defaults": dict(DEFAULTS),
    }


@router.put("/settings/app")
async def update_app_settings(req: Request, db: AsyncSession = Depends(get_db)):
    """更新系统偏好：仅接收已知 key，未知项静默忽略。"""
    body = await req.json()
    patch = body.get("values") if isinstance(body.get("values"), dict) else body
    if not isinstance(patch, dict):
        return {"values": await AppSettingsService.get_all(db)}
    return {"values": await AppSettingsService.upsert(db, patch)}
