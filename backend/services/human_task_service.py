"""人工节点任务服务：挂起时建单、处理时校验与落库、并产出注入下游的决策输出。

一次运行（run）+ 一个人工节点 = 一条任务。任务处理后由引擎续跑（见 workflow_engine.resume_run_stream）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import WorkflowHumanTask
from services.notification_hub import hub

logger = logging.getLogger(__name__)

# 表单字段类型 → 值强转与校验
FIELD_TYPES = {"text", "textarea", "number", "select", "date", "boolean"}

PENDING = "pending"
DECIDED_STATUSES = ("approved", "rejected", "submitted")


def _json(s: str | None, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except (ValueError, TypeError):
        return default if default is not None else {}


def _now() -> str:
    return datetime.now().isoformat()


def _coerce_field(ftype: str, value: Any, field: dict) -> tuple[Any, str | None]:
    """按字段类型强转值，返回 (值, 错误信息)。"""
    if ftype in ("text", "textarea", "date"):
        if not isinstance(value, str):
            value = str(value)
        return value, None
    if ftype == "number":
        if isinstance(value, bool):
            return None, "应为数字"
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None, "应为数字"
        return int(num) if num.is_integer() else num, None
    if ftype == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes", "on"):
                return True, None
            if value.lower() in ("false", "0", "no", "off"):
                return False, None
        return None, "应为布尔值"
    if ftype == "select":
        options = field.get("options") or []
        if value not in options:
            return None, f"必须是选项之一：{options}"
        return value, None
    return value, None


class HumanTaskService:
    """人工任务 CRUD + 决策校验。"""

    # ─────────────────────── 创建（引擎挂起时调用） ───────────────────────

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        run_id: str,
        workflow_id: str,
        workflow_name: str,
        node_id: str,
        node_title: str,
        config: dict,
        description: str,
        form_data: dict,
        trigger_source: str | None = None,
    ) -> dict:
        cfg = config or {}
        mode = cfg.get("mode") or "approve"
        comment_cfg = cfg.get("comment") or {}
        timeout = cfg.get("timeout") or {}
        hours = timeout.get("hours")

        due_at = None
        try:
            if hours:
                due_at = (datetime.now() + timedelta(hours=float(hours))).isoformat()
        except (TypeError, ValueError):
            due_at = None

        # 幂等：同一运行 + 同一节点只保留一条任务（异常重试挂起时复用并重置，避免唯一约束冲突）
        existing = (await db.execute(
            select(WorkflowHumanTask)
            .where(WorkflowHumanTask.run_id == run_id, WorkflowHumanTask.node_id == node_id)
        )).scalar_one_or_none()
        if existing is not None:
            existing.status = PENDING
            existing.decision = None
            existing.comment = ""
            existing.filled_data = "{}"
            existing.operator = ""
            existing.decided_at = None
            existing.updated_at = _now()
            await db.commit()
            await db.refresh(existing)
            logger.warning("[human] 任务已存在并重置 run=%s node=%s task=%s", run_id, node_id, existing.id)
            hub.notify()
            return HumanTaskService._to_dict(existing)

        task = WorkflowHumanTask(
            run_id=run_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name or "",
            node_id=node_id,
            node_title=node_title or "",
            status=PENDING,
            mode=mode,
            description=description or "",
            form_schema=json.dumps({
                "display_fields": cfg.get("display_fields") or [],
                "form_fields": cfg.get("form_fields") or [],
                "decisions": cfg.get("decisions") or [],
                "comment": comment_cfg,
                "submit_text": cfg.get("submit_text") or "提交",
            }, ensure_ascii=False),
            form_data=json.dumps(form_data or {}, ensure_ascii=False, default=str),
            filled_data="{}",
            comment_required=1 if comment_cfg.get("required") else 0,
            assignee=cfg.get("assignee") or "",
            due_at=due_at,
            timeout_action=timeout.get("action") or "keep_pending",
            trigger_source=trigger_source,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        logger.info("[human] 任务已创建 %s (run=%s node=%s mode=%s)", task.id, run_id, node_id, mode)
        hub.notify()   # 唤醒 SSE 推送：新待办生成
        return HumanTaskService._to_dict(task)

    # ─────────────────────── 查询 ───────────────────────

    @staticmethod
    def _to_dict(task: WorkflowHumanTask) -> dict:
        schema = _json(task.form_schema, {})
        return {
            "id": task.id,
            "run_id": task.run_id,
            "workflow_id": task.workflow_id,
            "workflow_name": task.workflow_name or "",
            "node_id": task.node_id,
            "node_title": task.node_title or "",
            "status": task.status,
            "mode": task.mode or "approve",
            "description": task.description or "",
            "form_schema": schema,
            "form_data": _json(task.form_data, {}),
            "filled_data": _json(task.filled_data, {}),
            "comment_required": bool(task.comment_required),
            "decision": task.decision,
            "comment": task.comment or "",
            "operator": task.operator or "",
            "assignee": task.assignee or "",
            "due_at": task.due_at,
            "timeout_action": task.timeout_action or "keep_pending",
            "trigger_source": task.trigger_source,
            "created_at": task.created_at,
            "decided_at": task.decided_at,
            "updated_at": task.updated_at,
            "overdue": HumanTaskService.is_overdue(task),
        }

    @staticmethod
    def is_overdue(task: WorkflowHumanTask) -> bool:
        """懒判定：任务是否超期（未配置 due_at 视为永不超期）。"""
        if task.status != PENDING or not task.due_at:
            return False
        try:
            return datetime.now() > datetime.fromisoformat(task.due_at)
        except (TypeError, ValueError):
            return False

    @staticmethod
    async def get(db: AsyncSession, task_id: str) -> dict | None:
        row = await db.get(WorkflowHumanTask, task_id)
        return HumanTaskService._to_dict(row) if row else None

    @staticmethod
    async def get_row(db: AsyncSession, task_id: str) -> WorkflowHumanTask | None:
        return await db.get(WorkflowHumanTask, task_id)

    @staticmethod
    async def list_tasks(
        db: AsyncSession,
        *,
        status: str | None = None,
        workflow_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        stmt = select(WorkflowHumanTask)
        if status:
            stmt = stmt.where(WorkflowHumanTask.status == status)
        if workflow_id:
            stmt = stmt.where(WorkflowHumanTask.workflow_id == workflow_id)
        rows = (await db.execute(
            stmt.order_by(WorkflowHumanTask.created_at.desc()).limit(max(1, min(limit, 200)))
        )).scalars().all()
        return [HumanTaskService._to_dict(r) for r in rows]

    @staticmethod
    async def count_pending(db: AsyncSession) -> int:
        rows = (await db.execute(
            select(WorkflowHumanTask).where(WorkflowHumanTask.status == PENDING)
        )).scalars().all()
        return len(rows)

    # ─────────────────────── 决策校验 ───────────────────────

    @staticmethod
    def validate(task: WorkflowHumanTask, decision: str, comment: str, data: dict | None) -> tuple[dict, str | None]:
        """校验决策入参，返回 (填写结果 data, 错误信息)。错误时 data 为 {}。

        信息量有限：字段级错误以 ValueError 形式抛出（含 field_errors）。
        """
        mode = task.mode or "approve"
        schema = _json(task.form_schema, {})
        decisions = schema.get("decisions") or []

        # 1. decision 合法性
        allowed = [d.get("key") for d in decisions] if decisions else ["approved", "rejected"]
        if mode == "form":
            allowed = ["submitted"]
        if decision not in allowed:
            return {}, f"非法的处理结果：{decision}（本任务仅支持 {allowed}）"

        # 2. 意见必填
        comment_cfg = schema.get("comment") or {}
        required = bool(comment_cfg.get("required")) or bool(task.comment_required)
        if not required:
            required = decision in (comment_cfg.get("required_on") or [])
        if required and not (comment or "").strip():
            label = comment_cfg.get("label") or "处理意见"
            return {}, f"请填写{label}"

        # 3. 表单字段校验
        filled: dict[str, Any] = {}
        if mode == "form":
            fields = schema.get("form_fields") or []
            field_errors: dict[str, str] = {}
            for f in fields:
                if not isinstance(f, dict):
                    continue
                key = f.get("key")
                if not key:
                    continue
                ftype = f.get("type") or "text"
                raw = (data or {}).get(key, f.get("default"))
                if f.get("required") and (raw is None or raw == ""):
                    field_errors[key] = "必填"
                    continue
                if raw is None:
                    continue
                value, err = _coerce_field(ftype, raw, f)
                if err:
                    field_errors[key] = err
                else:
                    filled[key] = value
            if field_errors:
                raise ValueError(json.dumps(field_errors, ensure_ascii=False))
        return filled, None

    # ─────────────────────── 决策落库 ───────────────────────

    @staticmethod
    async def decide(
        db: AsyncSession,
        task_id: str,
        *,
        decision: str,
        comment: str = "",
        data: dict | None = None,
        operator: str = "",
    ) -> dict | None:
        """乐观锁更新：仅 pending 任务可被处理。返回更新后的任务；None 表示已被处理/不存在。"""
        task = await db.get(WorkflowHumanTask, task_id)
        if not task:
            return None
        filled, err = HumanTaskService.validate(task, decision, comment, data)
        if err:
            raise ValueError(err)

        now = _now()
        result = await db.execute(
            update(WorkflowHumanTask)
            .where(WorkflowHumanTask.id == task_id, WorkflowHumanTask.status == PENDING)
            .values(
                status=decision,
                decision=decision,
                comment=comment or "",
                filled_data=json.dumps(filled or {}, ensure_ascii=False, default=str),
                operator=operator or "",
                decided_at=now,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            await db.rollback()
            return None
        await db.commit()
        await db.refresh(task)
        logger.info("[human] 任务已处理 %s → %s (operator=%s)", task_id, decision, operator)
        hub.notify()   # 唤醒 SSE 推送：待办数减少
        return HumanTaskService._to_dict(task)

    @staticmethod
    async def batch_decide(
        db: AsyncSession,
        task_ids: list[str],
        *,
        decision: str,
        comment: str = "",
        operator: str = "",
    ) -> dict:
        """批量处理：逐条独立，部分成功语义。仅 approve 模式的 pending 任务可批量。"""
        if len(task_ids) > settings.WORKFLOW_HUMAN_BATCH_LIMIT:
            raise ValueError(f"单次批量最多 {settings.WORKFLOW_HUMAN_BATCH_LIMIT} 条，当前 {len(task_ids)} 条")

        succeeded: list[str] = []
        failed: list[dict] = []
        for tid in task_ids:
            task = await db.get(WorkflowHumanTask, tid)
            if task is None:
                failed.append({"id": tid, "reason": "任务不存在"})
                continue
            if task.status != PENDING:
                failed.append({"id": tid, "reason": "该任务已处理"})
                continue
            if (task.mode or "approve") != "approve":
                failed.append({"id": tid, "reason": "表单任务需逐条填写，不支持批量"})
                continue
            try:
                updated = await HumanTaskService.decide(
                    db, tid, decision=decision, comment=comment, operator=operator,
                )
                if updated is None:
                    failed.append({"id": tid, "reason": "该任务已处理"})
                else:
                    succeeded.append(tid)
            except ValueError as e:
                failed.append({"id": tid, "reason": str(e)})
        if succeeded:
            hub.notify()
        return {"succeeded": succeeded, "failed": failed}

    # ─────────────────────── 其他状态流转 ───────────────────────

    @staticmethod
    async def cancel_pending_of_run(db: AsyncSession, run_id: str) -> int:
        """运行被取消/删除时，把其 pending 任务置 cancelled。返回影响条数。"""
        result = await db.execute(
            update(WorkflowHumanTask)
            .where(WorkflowHumanTask.run_id == run_id, WorkflowHumanTask.status == PENDING)
            .values(status="cancelled", updated_at=_now())
        )
        await db.commit()
        n = result.rowcount or 0
        if n:
            hub.notify()
        return n

    @staticmethod
    async def delete_of_run(db: AsyncSession, run_id: str) -> int:
        """删除运行记录时一并清理其任务。"""
        rows = (await db.execute(
            select(WorkflowHumanTask).where(WorkflowHumanTask.run_id == run_id)
        )).scalars().all()
        for r in rows:
            await db.delete(r)
        if rows:
            await db.commit()
            hub.notify()
        return len(rows)

    # ─────────────────────── 决策输出（注入引擎 context） ───────────────────────

    @staticmethod
    def decision_output(task: WorkflowHumanTask) -> dict:
        """人工节点的处理结果，作为该节点输出注入下游。"""
        decision = task.decision or ("submitted" if (task.mode or "approve") == "form" else "approved")
        return {
            "approved": decision != "rejected",
            "decision": decision,
            "data": _json(task.filled_data, {}),
            "comment": task.comment or "",
            "operator": task.operator or "",
            "decided_at": task.decided_at or "",
            "task_id": task.id,
            "timed_out": False,
        }
