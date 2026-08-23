"""
定时调度计划服务层（Scheduler Service）

职责：
  - Schedule 计划的 CRUD（写 DB）
  - 触发器校验（cron / interval / once）
  - input_params 强校验：匹配关联工作流 start 节点声明的入参
  - 计算 next_run_at（用于列表展示）
  - 提供触发器摘要（用于前端展示「每天 08:00」之类）

调度触发本身在 scheduler_engine.py 中完成，本模块只负责「数据 + 规则」。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from croniter import croniter
from sqlalchemy import select, func

from database import async_session
from models import Schedule, Workflow, WorkflowRun
from config import settings

logger = logging.getLogger("scheduler_service")

VALID_TRIGGERS = ("cron", "interval", "once")


# ───────────────────────────── 校验 ─────────────────────────────

def _parse_json(text: str, default=None):
    try:
        return json.loads(text) if text else (default if default is not None else {})
    except json.JSONDecodeError:
        return default if default is not None else {}


def validate_trigger(trigger: str, trigger_config: dict) -> Optional[str]:
    """校验触发器类型与配置，返回错误信息字符串（None 表示通过）。"""
    if trigger not in VALID_TRIGGERS:
        return f"不支持的触发器类型: {trigger!r}（可选: cron / interval / once）"

    if trigger == "cron":
        # cron 字段：minute hour day month day_of_week（允许部分字段）
        fields = ["minute", "hour", "day", "month", "day_of_week"]
        cfg = {f: str(trigger_config.get(f, "*")) for f in fields}
        if any(v.strip() == "" for v in cfg.values()):
            return "cron 字段不能为空"
        expr = f"{cfg['minute']} {cfg['hour']} {cfg['day']} {cfg['month']} {cfg['day_of_week']}"
        if not croniter.is_valid(expr):
            return f"非法的 cron 表达式: {expr}"
        return None

    if trigger == "interval":
        every = trigger_config.get("every")
        unit = trigger_config.get("unit")
        try:
            every = float(every)
        except (TypeError, ValueError):
            return "interval.every 必须为正数"
        if every <= 0:
            return "interval.every 必须为正数"
        if unit not in ("minutes", "hours", "days"):
            return f"interval.unit 必须为 minutes / hours / days（收到: {unit!r}）"
        return None

    if trigger == "once":
        run_at = trigger_config.get("run_at")
        if not run_at:
            return "once.run_at 不能为空"
        try:
            dt = datetime.fromisoformat(run_at)
        except ValueError:
            return "once.run_at 必须是合法的 ISO 时间（如 2026-09-01T09:00:00）"
        # 允许带时区或不带；这里仅做格式校验，未来时间校验在创建时结合 now 做
        return None

    return "未知触发器"


def get_start_inputs(definition: dict) -> list:
    """从工作流 definition 中提取 start 节点声明的输入列表。"""
    nodes = definition.get("nodes") or []
    for n in nodes:
        if n.get("type") == "start":
            cfg = n.get("data", {}).get("config", {})
            return cfg.get("inputs", []) or []
    return []


def validate_inputs(workflow_definition: dict, input_params: dict) -> Optional[str]:
    """强校验 input_params 匹配工作流 start 节点声明。返回错误信息（None 通过）。"""
    declared = get_start_inputs(workflow_definition)
    declared_names = {i.get("name") for i in declared}

    # 必填字段必须出现
    for item in declared:
        name = item.get("name")
        required = bool(item.get("required", False))
        if required and (name not in input_params or input_params.get(name) in (None, "")):
            return f"缺少必填入参: {name!r}"

    # 不允许出现未声明的字段
    for key in input_params:
        if key not in declared_names:
            return f"未知入参: {key!r}（工作流未声明该输入）"

    # 类型弱校验：声明了 type 时，检查基本类型一致
    type_map = {"text": str, "string": str, "number": (int, float), "integer": int,
                "boolean": bool, "bool": bool, "object": dict, "array": list, "json": (dict, list)}
    for item in declared:
        name = item.get("name")
        tcfg = (item.get("type") or "text").lower()
        expected = type_map.get(tcfg, None)
        if name in input_params and expected is not None:
            val = input_params[name]
            if val is None:
                continue
            if not isinstance(val, expected):
                return f"入参 {name!r} 类型不匹配：期望 {tcfg}，实际 {type(val).__name__}"
    return None


# ───────────────────────────── next_run 计算 ─────────────────────────────

def compute_next_run(trigger: str, trigger_config: dict,
                     base: Optional[datetime] = None) -> Optional[str]:
    """计算下一次运行时间（ISO 字符串，含时区），用于列表展示。"""
    if base is None:
        base = datetime.now(timezone.utc)
    try:
        if trigger == "cron":
            fields = ["minute", "hour", "day", "month", "day_of_week"]
            cfg = {f: str(trigger_config.get(f, "*")) for f in fields}
            expr = f"{cfg['minute']} {cfg['hour']} {cfg['day']} {cfg['month']} {cfg['day_of_week']}"
            itr = croniter(expr, base)
            nxt = itr.get_next(datetime)
            return nxt.isoformat()
        if trigger == "interval":
            every = float(trigger_config.get("every", 1))
            unit = trigger_config.get("unit", "hours")
            secs = {"minutes": 60, "hours": 3600, "days": 86400}[unit] * every
            nxt = base.timestamp() + secs
            return datetime.fromtimestamp(nxt, tz=timezone.utc).isoformat()
        if trigger == "once":
            run_at = trigger_config.get("run_at")
            if run_at:
                return datetime.fromisoformat(run_at).isoformat()
    except Exception as e:
        logger.warning("compute_next_run 失败 trigger=%s err=%s", trigger, e)
    return None


def trigger_summary(trigger: str, trigger_config: dict) -> str:
    """人类可读的触发器摘要，如「每天 08:00」「每 30 分钟」「一次性 2026-09-01 09:00」。"""
    if trigger == "cron":
        cfg = {f: str(trigger_config.get(f, "*")) for f in
               ["minute", "hour", "day", "month", "day_of_week"]}
        if cfg["minute"] == "0" and cfg["hour"] != "*" and cfg["day"] == "*" \
                and cfg["month"] == "*" and cfg["day_of_week"] == "*":
            return f"每天 {cfg['hour']}:00"
        if cfg["minute"] != "*" and cfg["hour"] == "*":
            return f"每小时第 {cfg['minute']} 分"
        return f"cron {cfg['minute']} {cfg['hour']} {cfg['day']} {cfg['month']} {cfg['day_of_week']}"
    if trigger == "interval":
        return f"每 {trigger_config.get('every')} {trigger_config.get('unit', 'hours')}"
    if trigger == "once":
        return f"一次性 {trigger_config.get('run_at', '')}"
    return trigger


# ───────────────────────────── CRUD ─────────────────────────────

async def list_schedules() -> list:
    async with async_session() as db:
        rows = (await db.execute(select(Schedule).order_by(Schedule.created_at.desc()))).scalars().all()
        # 补充关联工作流名 + 触发器摘要
        result = []
        for s in rows:
            wf = (await db.execute(select(Workflow).where(Workflow.id == s.workflow_id))).scalar_one_or_none()
            d = _schedule_to_dict(s)
            d["workflow_name"] = wf.name if wf else "(工作流已删除)"
            d["trigger_summary"] = trigger_summary(s.trigger, _parse_json(s.trigger_config))
            result.append(d)
        return result


async def get_schedule(schedule_id: str) -> Optional[dict]:
    async with async_session() as db:
        s = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
        if not s:
            return None
        wf = (await db.execute(select(Workflow).where(Workflow.id == s.workflow_id))).scalar_one_or_none()
        d = _schedule_to_dict(s)
        d["workflow_name"] = wf.name if wf else "(工作流已删除)"
        d["trigger_summary"] = trigger_summary(s.trigger, _parse_json(s.trigger_config))
        return d


async def create_schedule(data: dict) -> dict:
    """创建计划。data 含 name/description/workflow_id/trigger/trigger_config/input_params 等。"""
    trigger = data["trigger"]
    trigger_config = data.get("trigger_config", {})
    input_params = data.get("input_params", {})

    err = validate_trigger(trigger, trigger_config)
    if err:
        raise ValueError(err)

    async with async_session() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.id == data["workflow_id"]))).scalar_one_or_none()
        if not wf:
            raise ValueError(f"关联工作流不存在: {data['workflow_id']}")

        err = validate_inputs(_parse_json(wf.definition, {"nodes": []}), input_params)
        if err:
            raise ValueError(err)

        # once 类型要求未来时间
        if trigger == "once":
            try:
                run_at = datetime.fromisoformat(trigger_config["run_at"])
                if run_at <= datetime.now():
                    raise ValueError("once.run_at 必须晚于当前时间")
            except ValueError as e:
                if "必须晚于" in str(e):
                    raise
                raise ValueError(f"once.run_at 非法: {e}")

        s = Schedule(
            name=data["name"],
            description=data.get("description", ""),
            workflow_id=data["workflow_id"],
            trigger=trigger,
            trigger_config=json.dumps(trigger_config, ensure_ascii=False),
            input_params=json.dumps(input_params, ensure_ascii=False),
            enabled=int(data.get("enabled", 1)),
            muted=int(data.get("muted", 0)),
            max_failures_alert=int(data.get("max_failures_alert", 3)),
            alert_on_failure=int(data.get("alert_on_failure", 1)),
        )
        s.next_run_at = compute_next_run(trigger, trigger_config)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return _schedule_to_dict(s)


async def update_schedule(schedule_id: str, data: dict) -> dict:
    async with async_session() as db:
        s = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
        if not s:
            raise ValueError("计划不存在")

        # 可更新字段
        for f in ("name", "description", "muted", "max_failures_alert", "alert_on_failure", "enabled"):
            if f in data:
                setattr(s, f, data[f])

        # 触发器变更需重新校验
        if "trigger" in data or "trigger_config" in data:
            trigger = data.get("trigger", s.trigger)
            trigger_config = data.get("trigger_config", _parse_json(s.trigger_config))
            err = validate_trigger(trigger, trigger_config)
            if err:
                raise ValueError(err)
            s.trigger = trigger
            s.trigger_config = json.dumps(trigger_config, ensure_ascii=False)
            if trigger == "once":
                try:
                    run_at = datetime.fromisoformat(trigger_config["run_at"])
                    if run_at <= datetime.now():
                        raise ValueError("once.run_at 必须晚于当前时间")
                except ValueError as e:
                    if "必须晚于" in str(e):
                        raise
                    raise ValueError(f"once.run_at 非法: {e}")

        # workflow_id 变更需校验工作流存在
        if "workflow_id" in data and data["workflow_id"] != s.workflow_id:
            wf = (await db.execute(select(Workflow).where(Workflow.id == data["workflow_id"]))).scalar_one_or_none()
            if not wf:
                raise ValueError(f"关联工作流不存在: {data['workflow_id']}")
            s.workflow_id = data["workflow_id"]

        # input_params 变更需强校验
        if "input_params" in data:
            wf = (await db.execute(select(Workflow).where(Workflow.id == s.workflow_id))).scalar_one_or_none()
            err = validate_inputs(_parse_json(wf.definition, {"nodes": []}), data["input_params"])
            if err:
                raise ValueError(err)
            s.input_params = json.dumps(data["input_params"], ensure_ascii=False)

        s.next_run_at = compute_next_run(s.trigger, _parse_json(s.trigger_config))
        s.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(s)
        return _schedule_to_dict(s)


async def delete_schedule(schedule_id: str):
    async with async_session() as db:
        s = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
        if s:
            await db.delete(s)
            await db.commit()


async def set_enabled(schedule_id: str, enabled: bool) -> dict:
    async with async_session() as db:
        s = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
        if not s:
            raise ValueError("计划不存在")
        s.enabled = int(enabled)
        s.updated_at = datetime.now().isoformat()
        if enabled:
            s.next_run_at = compute_next_run(s.trigger, _parse_json(s.trigger_config))
        else:
            s.next_run_at = None
        await db.commit()
        await db.refresh(s)
        return _schedule_to_dict(s)


async def list_runs(schedule_id: str) -> list:
    async with async_session() as db:
        rows = (await db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.schedule_id == schedule_id)
            .order_by(WorkflowRun.started_at.desc())
            .limit(100)
        )).scalars().all()
        return [{
            "id": r.id,
            "workflow_id": r.workflow_id,
            "status": r.status,
            "error": r.error,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "duration_ms": r.duration_ms,
        } for r in rows]


def _schedule_to_dict(s: Schedule) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "workflow_id": s.workflow_id,
        "trigger": s.trigger,
        "trigger_config": _parse_json(s.trigger_config),
        "input_params": _parse_json(s.input_params),
        "enabled": bool(s.enabled),
        "muted": bool(s.muted),
        "next_run_at": s.next_run_at,
        "last_run_at": s.last_run_at,
        "last_status": s.last_status,
        "consecutive_failures": s.consecutive_failures,
        "max_failures_alert": s.max_failures_alert,
        "alert_on_failure": bool(s.alert_on_failure),
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }
