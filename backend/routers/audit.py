"""操作日志 / 认证日志 / 安全策略路由。"""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import audit_ctx
from database import get_db
from services.audit_service import ACTION_LABELS, AuditService
from services.security_settings_service import DEFAULTS, SecuritySettingsService

router = APIRouter()


@router.get("/audit/actions")
async def action_options():
    return AuditService.action_options()


@router.get("/audit/logs/export")
async def export_logs(req: Request, user_id: str = "", module: str = "", action: str = "",
                      result: str = "", keyword: str = "", start: str = "", end: str = "",
                      db: AsyncSession = Depends(get_db)):
    data = await AuditService.list_logs(
        db, user_id=user_id, module=module, action=action, result=result,
        keyword=keyword, start=start, end=end, page=1, page_size=100000,
    )
    AuditService.record(
        **audit_ctx(req), module="system", action="export",
        action_label="导出操作日志", target_type="audit_log", target_id="",
        target_name=f"{data['total']} 条",
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["时间", "用户", "模块", "动作", "目标", "方法", "路径", "IP", "耗时(ms)", "结果", "错误信息"])
    for item in data["items"]:
        writer.writerow([
            item.get("created_at"), item.get("username"), item.get("module"),
            item.get("action_label") or ACTION_LABELS.get(item.get("action"), item.get("action")),
            item.get("target_name"), item.get("method"), item.get("path"),
            item.get("ip"), item.get("duration_ms"), item.get("result"), item.get("error_msg"),
        ])
    buffer.seek(0)
    filename = "audit-logs.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/audit/logs/{log_id}")
async def get_log(log_id: str, db: AsyncSession = Depends(get_db)):
    log = await AuditService.get_log(db, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    return log


@router.get("/audit/logs")
async def list_logs(user_id: str = "", module: str = "", action: str = "",
                    result: str = "", keyword: str = "", start: str = "", end: str = "",
                    page: int = 1, page_size: int = 20,
                    db: AsyncSession = Depends(get_db)):
    return await AuditService.list_logs(
        db, user_id=user_id, module=module, action=action, result=result,
        keyword=keyword, start=start, end=end, page=page, page_size=page_size,
    )


@router.get("/audit/auth-logs")
async def list_auth_logs(user_id: str = "", action: str = "", result: str = "",
                         keyword: str = "", start: str = "", end: str = "",
                         page: int = 1, page_size: int = 20,
                         db: AsyncSession = Depends(get_db)):
    return await AuditService.list_auth_logs(
        db, user_id=user_id, action=action, result=result,
        keyword=keyword, start=start, end=end, page=page, page_size=page_size,
    )


@router.post("/audit/archive")
async def archive_logs(req: Request, db: AsyncSession = Depends(get_db)):
    removed = await AuditService.cleanup(db)
    AuditService.record(
        **audit_ctx(req), module="system", action="delete", action_label="归档清理日志",
        target_type="audit_log", target_id="", target_name=json.dumps(removed, ensure_ascii=False),
    )
    return removed


@router.get("/security-settings")
async def get_security_settings(db: AsyncSession = Depends(get_db)):
    values = await SecuritySettingsService.get_all(db)
    return {
        "values": values,
        "schema": [
            {"key": k, "default": v, "type": "bool" if v in ("true", "false") else "int"
             if v.isdigit() else "text"}
            for k, v in DEFAULTS.items()
        ],
    }


@router.put("/security-settings")
async def update_security_settings(req: Request, db: AsyncSession = Depends(get_db)):
    body = await req.json()
    patch = body.get("values") if isinstance(body.get("values"), dict) else body
    before = await SecuritySettingsService.get_all(db)
    values = await SecuritySettingsService.upsert(db, patch)
    AuditService.record(
        **audit_ctx(req), module="system", action="update", action_label="修改安全策略",
        target_type="security_settings", target_id="", target_name="安全策略",
        before_value={k: before.get(k) for k in patch},
        after_value={k: values.get(k) for k in patch},
    )
    return {"values": values}
