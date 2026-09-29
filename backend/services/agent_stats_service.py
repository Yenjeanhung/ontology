"""Agent 每日调研量统计：明细采集 + 多维度聚合 + 保留期清理。

数据模型（models.AgentResearchLog，migration_046）：
    一次「智能体执行」落一行明细，按 stat_date（本地日期 YYYY-MM-DD）聚合
    就是每日调研量，再按 agent / scene / user / model / success 任一维度下钻。

采集点（各执行链路收尾处，见 AGENT_STATS_ENABLED）：
    single    单智能体问答（/api/agent/query，含 KB 问答与无 KB 聊天）
    assistant 全局智能助手浮标（/api/agent/assistant/run）
    multi     多智能体协作（/api/agent/multi/scenarios/{id}/run，含断点恢复）
    deep      深度模式（DeepAgents，自由任务或目标研判 deep=true）
    target    目标研判（/api/agent/multi/scenarios/{id}/targets/{tid}/run）

保留期：默认 30 天（RETENTION_DAYS）。写入时按「天」节流触发一次清理，
页面也可手动调用 cleanup()。统计只回看保留窗口内的数据，过量明细不入库。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import case, func, select

from models import AgentResearchLog

logger = logging.getLogger(__name__)

# 明细保留天数（最多保留一个月）
RETENTION_DAYS = 30
# 查询允许的最大回看天数（与保留期对齐，超出无数据）
MAX_QUERY_DAYS = 30

# 维度 → (聚合列, 维度展示名来源列)
_GROUP_FIELDS = {
    "date": ("stat_date", None),
    "agent": ("agent_id", "agent_name"),
    "scene": ("scene", None),
    "user": ("user_id", "username"),
    "model": ("model", None),
    "success": ("success", None),
}

SCENE_LABELS = {
    "single": "单智能体问答",
    "assistant": "智能助手",
    "multi": "多智能体协作",
    "deep": "深度模式",
    "target": "目标研判",
}

# 清理节流：同一天内只在首次写入时触发一次
_last_cleanup_date = ""


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _start_date(days: int) -> str:
    """回看窗口起始日期（含）：days=30 表示最近 30 天（含今天）。"""
    days = max(1, min(int(days or 1), MAX_QUERY_DAYS))
    return (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def _enabled() -> bool:
    try:
        from config import settings

        return bool(getattr(settings, "AGENT_STATS_ENABLED", True))
    except Exception:
        return True


def _model_name() -> str:
    try:
        from config import settings

        return getattr(settings, "LLM_MODEL", "") or ""
    except Exception:
        return ""


class AgentStatsService:
    """调研量统计：写入尽力而为（不影响主流程），查询只读。"""

    # ───────────────────────── 采集 ─────────────────────────

    @staticmethod
    async def record(*, user_id: str = "", username: str = "", agent_id: str = "",
                     agent_name: str = "", scene: str = "", model: str = "",
                     success: bool = True, duration_ms: int = 0,
                     error_msg: str = "") -> None:
        """记一次智能体执行。主流程收尾处调用，失败只记日志。"""
        if not _enabled():
            return
        try:
            from database import async_session

            async with async_session() as db:
                db.add(AgentResearchLog(
                    id=uuid.uuid4().hex[:12],
                    stat_date=_today(),
                    user_id=user_id or "",
                    username=username or "",
                    agent_id=agent_id or "",
                    agent_name=agent_name or "",
                    scene=scene or "",
                    model=model or _model_name(),
                    success=1 if success else 0,
                    duration_ms=max(0, int(duration_ms or 0)),
                    error_msg=(error_msg or "")[:500],
                    created_at=datetime.now().isoformat(),
                ))
                await db.commit()
        except Exception:
            logger.exception("调研量统计落库失败 scene=%s agent=%s", scene, agent_id)
            return

        # 保留期清理：每天首次写入触发一次
        await AgentStatsService._maybe_cleanup()

    @staticmethod
    async def _maybe_cleanup() -> None:
        global _last_cleanup_date
        today = _today()
        if _last_cleanup_date == today:
            return
        _last_cleanup_date = today
        try:
            from database import async_session

            async with async_session() as db:
                removed = await AgentStatsService.cleanup(db, RETENTION_DAYS)
            if removed:
                logger.info("调研量明细清理：删除 %s 条 %s 天前数据", removed, RETENTION_DAYS)
        except Exception:
            logger.warning("调研量明细清理失败", exc_info=True)

    # ───────────────────────── 查询 ─────────────────────────

    @staticmethod
    def _filters(*, days: int, agent_id: str = "", scene: str = "",
                 user_id: str = "", success: bool | None = None,
                 model: str = ""):
        conds = [AgentResearchLog.stat_date >= _start_date(days)]
        if agent_id:
            conds.append(AgentResearchLog.agent_id == agent_id)
        if scene:
            conds.append(AgentResearchLog.scene == scene)
        if user_id:
            conds.append(AgentResearchLog.user_id == user_id)
        if model:
            conds.append(AgentResearchLog.model == model)
        if success is not None:
            conds.append(AgentResearchLog.success == (1 if success else 0))
        return conds

    @staticmethod
    async def stats(db, *, days: int = 7, group_by: str = "date", agent_id: str = "",
                    scene: str = "", user_id: str = "", model: str = "",
                    success: bool | None = None) -> dict:
        """按维度聚合：总数 / 成功 / 失败 / 成功率 / 平均耗时。"""
        days = max(1, min(int(days or 7), MAX_QUERY_DAYS))
        if group_by not in _GROUP_FIELDS:
            group_by = "date"
        col_name, label_col = _GROUP_FIELDS[group_by]
        col = getattr(AgentResearchLog, col_name)

        cols = [
            col.label("key"),
            func.count().label("total"),
            func.sum(case((AgentResearchLog.success == 1, 1), else_=0)).label("success_count"),
            func.avg(AgentResearchLog.duration_ms).label("avg_duration_ms"),
            func.max(AgentResearchLog.duration_ms).label("max_duration_ms"),
            func.min(AgentResearchLog.created_at).label("first_at"),
            func.max(AgentResearchLog.created_at).label("last_at"),
        ]
        if label_col:
            cols.append(func.max(getattr(AgentResearchLog, label_col)).label("label"))

        stmt = (select(*cols)
                .where(*AgentStatsService._filters(
                    days=days, agent_id=agent_id, scene=scene,
                    user_id=user_id, success=success, model=model))
                .group_by(col))

        # 日期维度按时间正序（画趋势），其余按调用量倒序（看排行）
        stmt = stmt.order_by(col.asc() if group_by == "date" else func.count().desc())

        rows = (await db.execute(stmt)).all()
        items = []
        for r in rows:
            total = int(r.total or 0)
            ok = int(r.success_count or 0)
            raw_key = r.key
            key = str(raw_key) if raw_key is not None else ""
            label = getattr(r, "label", None) or ""
            if group_by == "success":
                key = "success" if key in ("1", "True") else "failed"
                label = "成功" if key == "success" else "失败"
            elif group_by == "scene":
                label = SCENE_LABELS.get(key, key or "未知")
            elif group_by == "date":
                label = key
            elif not label:
                label = key or "（空）"
            items.append({
                "key": key,
                "label": label,
                "total": total,
                "success": ok,
                "failed": total - ok,
                "success_rate": round(ok / total * 100, 1) if total else 0.0,
                "avg_duration_ms": int(r.avg_duration_ms or 0),
                "max_duration_ms": int(r.max_duration_ms or 0),
                "first_at": r.first_at or "",
                "last_at": r.last_at or "",
            })

        return {
            "group_by": group_by,
            "days": days,
            "start_date": _start_date(days),
            "end_date": _today(),
            "items": items,
        }

    @staticmethod
    async def overview(db, days: int = 7) -> dict:
        """概览卡：窗口总量 / 今日量 / 成功率 / 平均耗时 / 活跃智能体数。"""
        days = max(1, min(int(days or 7), MAX_QUERY_DAYS))
        start = _start_date(days)
        base = [AgentResearchLog.stat_date >= start]
        row = (await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((AgentResearchLog.success == 1, 1), else_=0)).label("success"),
                func.avg(AgentResearchLog.duration_ms).label("avg_ms"),
                func.count(func.distinct(AgentResearchLog.agent_id)).label("agents"),
                func.count(func.distinct(AgentResearchLog.user_id)).label("users"),
            ).where(*base)
        )).first()
        today_row = (await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((AgentResearchLog.success == 1, 1), else_=0)).label("success"),
            ).where(AgentResearchLog.stat_date == _today())
        )).first()

        total = int(row.total or 0)
        ok = int(row.success or 0)
        today_total = int(today_row.total or 0)
        return {
            "days": days,
            "total": total,
            "success": ok,
            "failed": total - ok,
            "success_rate": round(ok / total * 100, 1) if total else 0.0,
            "avg_duration_ms": int(row.avg_ms or 0),
            "active_agents": int(row.agents or 0),
            "active_users": int(row.users or 0),
            "today_total": today_total,
            "today_success": int(today_row.success or 0),
        }

    @staticmethod
    async def logs(db, *, days: int = 7, agent_id: str = "", scene: str = "",
                   user_id: str = "", model: str = "", success: bool | None = None,
                   page: int = 1, page_size: int = 20) -> dict:
        """明细分页（倒序）。"""
        days = max(1, min(int(days or 7), MAX_QUERY_DAYS))
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 20), 200))
        conds = AgentStatsService._filters(
            days=days, agent_id=agent_id, scene=scene,
            user_id=user_id, success=success, model=model)

        total = (await db.execute(
            select(func.count()).select_from(AgentResearchLog).where(*conds)
        )).scalar() or 0
        rows = (await db.execute(
            select(AgentResearchLog).where(*conds)
            .order_by(AgentResearchLog.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [{
                "id": r.id, "stat_date": r.stat_date,
                "user_id": r.user_id, "username": r.username,
                "agent_id": r.agent_id, "agent_name": r.agent_name,
                "scene": r.scene, "scene_label": SCENE_LABELS.get(r.scene or "", r.scene or ""),
                "model": r.model, "success": bool(r.success),
                "duration_ms": r.duration_ms or 0,
                "error_msg": r.error_msg or "",
                "created_at": r.created_at,
            } for r in rows],
        }

    @staticmethod
    async def agents(db, days: int = MAX_QUERY_DAYS) -> list[dict]:
        """筛选下拉用：窗口内出现过的智能体（id + 名称 + 次数）。"""
        rows = (await db.execute(
            select(
                AgentResearchLog.agent_id,
                func.max(AgentResearchLog.agent_name).label("name"),
                func.count().label("total"),
            ).where(AgentResearchLog.stat_date >= _start_date(days))
            .group_by(AgentResearchLog.agent_id)
            .order_by(func.count().desc())
        )).all()
        return [{"id": r[0] or "", "name": r[1] or (r[0] or "（未指定）"),
                 "total": int(r[2] or 0)} for r in rows]

    # ───────────────────────── 清理 ─────────────────────────

    @staticmethod
    async def cleanup(db, days: int = RETENTION_DAYS) -> int:
        """删除 days 天之前的明细；返回删除行数。"""
        from sqlalchemy import delete

        days = max(1, int(days or RETENTION_DAYS))
        edge = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        result = await db.execute(
            delete(AgentResearchLog).where(AgentResearchLog.stat_date < edge))
        await db.commit()
        return int(result.rowcount or 0)
