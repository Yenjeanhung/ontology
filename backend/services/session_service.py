"""会话服务：在线状态权威在 DB，进程内缓存保证鉴权热路径不打库。

"踢人"生效链路：
    管理端调用 kick* → DB 置 kicked + `invalidate(sid)` 清缓存
    → 被踢用户下一次请求 miss 缓存 → 回查 DB 得到 kicked → 401(KICKED)
    → 同进程内立即生效，多副本时最多延迟 AUTH_CACHE_TTL_SECONDS。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import User, UserSession

logger = logging.getLogger(__name__)

# sid -> {"status","user_id","username","ver","user_status","last_active","ts","db_synced"}
_cache: dict[str, dict] = {}
# 会话活跃时间回写 DB 的节流间隔（秒）
_DB_TOUCH_INTERVAL = 30.0

_BROWSERS = (
    ("Edg", "Edge"), ("Chrome", "Chrome"), ("Firefox", "Firefox"),
    ("Safari", "Safari"), ("PostmanRuntime", "Postman"), ("curl", "curl"),
)
_OS = (("Windows", "Windows"), ("Mac OS X", "macOS"), ("Android", "Android"),
       ("iPhone", "iOS"), ("iPad", "iOS"), ("Linux", "Linux"))


def parse_device(ua: str) -> str:
    if not ua:
        return "未知设备"
    browser = next((name for key, name in _BROWSERS if key in ua), "")
    system = next((name for key, name in _OS if key in ua), "")
    if browser and system:
        return f"{browser} / {system}"
    return browser or system or ua[:40]


def _cache_ttl() -> float:
    return max(1.0, float(getattr(settings, "AUTH_CACHE_TTL_SECONDS", 10)))


class SessionService:
    @staticmethod
    def invalidate(sid: str) -> None:
        _cache.pop(sid, None)

    @staticmethod
    def invalidate_user(user_id: str) -> None:
        for sid, entry in list(_cache.items()):
            if entry.get("user_id") == user_id:
                _cache.pop(sid, None)

    @staticmethod
    def cache_put(sid: str, entry: dict) -> None:
        entry["ts"] = time.monotonic()
        _cache[sid] = entry

    @staticmethod
    async def create(db: AsyncSession, user: User, *, ip: str = "", ua: str = "",
                     ttl_seconds: int) -> UserSession:
        now = datetime.now()
        session = UserSession(
            id=uuid.uuid4().hex,
            user_id=user.id,
            username=user.username,
            status="online",
            ip=ip,
            user_agent=(ua or "")[:500],
            device=parse_device(ua)[:100],
            login_at=now.isoformat(),
            last_active_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=max(60, ttl_seconds))).isoformat(),
            created_at=now.isoformat(),
        )
        db.add(session)
        await db.flush()
        SessionService.cache_put(session.id, {
            "status": "online",
            "user_id": user.id,
            "username": user.username,
            "ver": int(user.token_version or 1),
            "user_status": user.status,
            "last_active": now.isoformat(),
            "db_synced": time.monotonic(),
        })
        return session

    @staticmethod
    async def state(sid: str) -> dict | None:
        """读取会话状态：命中缓存直接用，否则回查 DB。"""
        now = time.monotonic()
        entry = _cache.get(sid)
        if entry and now - entry.get("ts", 0) < _cache_ttl():
            return entry

        from database import async_session

        async with async_session() as db:
            session = await db.get(UserSession, sid)
            if not session:
                _cache.pop(sid, None)
                return None
            user = await db.get(User, session.user_id)
            entry = {
                "status": session.status,
                "user_id": session.user_id,
                "username": session.username,
                "ver": int(user.token_version or 1) if user else 1,
                "user_status": user.status if user else "disabled",
                "last_active": session.last_active_at or session.login_at,
                "db_synced": time.monotonic(),
            }
            SessionService.cache_put(sid, entry)
            return entry

    @staticmethod
    async def touch(sid: str, *, idle_timeout_minutes: int = 0) -> str | None:
        """刷新活跃时间（DB 回写节流）；会话已过期时返回原因。

        返回 None 表示正常，否则返回 'kicked' / 'expired' / 'offline' 等状态。
        """
        entry = _cache.get(sid)
        if not entry:
            return None
        now = datetime.now()
        entry["last_active"] = now.isoformat()

        if idle_timeout_minutes > 0 and entry.get("status") == "online":
            last = entry.get("last_active") or ""
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                last_dt = now
            if now - last_dt > timedelta(minutes=idle_timeout_minutes):
                return "expired"
        return None

    @staticmethod
    async def persist_touch(sid: str) -> None:
        """把内存中的 last_active 节流回写 DB。"""
        entry = _cache.get(sid)
        if not entry:
            return
        if time.monotonic() - entry.get("db_synced", 0) < _DB_TOUCH_INTERVAL:
            return
        entry["db_synced"] = time.monotonic()
        try:
            from database import async_session

            async with async_session() as db:
                session = await db.get(UserSession, sid)
                if session and session.status == "online":
                    session.last_active_at = datetime.now().isoformat()
                    await db.commit()
        except Exception:
            logger.exception("回写会话活跃时间失败 sid=%s", sid)

    @staticmethod
    async def close(db: AsyncSession, sid: str, status: str = "offline",
                    kicked_by: str = "", kick_reason: str = "") -> bool:
        session = await db.get(UserSession, sid)
        if not session:
            SessionService.invalidate(sid)
            return False
        session.status = status
        session.logout_at = datetime.now().isoformat()
        if kicked_by:
            session.kicked_by = kicked_by
        if kick_reason:
            session.kick_reason = kick_reason
        await db.commit()
        SessionService.invalidate(sid)
        return True

    @staticmethod
    async def kick_user(db: AsyncSession, user_id: str, *, kicked_by: str = "",
                        reason: str = "", exclude_sid: str = "") -> int:
        rows = (await db.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.status == "online")
        )).scalars().all()
        now = datetime.now().isoformat()
        count = 0
        for s in rows:
            if exclude_sid and s.id == exclude_sid:
                continue
            s.status = "kicked"
            s.logout_at = now
            s.kicked_by = kicked_by
            s.kick_reason = reason[:200]
            SessionService.invalidate(s.id)
            count += 1
        if count:
            await db.commit()
        return count

    @staticmethod
    async def kick_all(db: AsyncSession, *, kicked_by: str = "", reason: str = "",
                       exclude_user_id: str = "") -> int:
        rows = (await db.execute(
            select(UserSession).where(UserSession.status == "online")
        )).scalars().all()
        now = datetime.now().isoformat()
        count = 0
        for s in rows:
            if exclude_user_id and s.user_id == exclude_user_id:
                continue
            s.status = "kicked"
            s.logout_at = now
            s.kicked_by = kicked_by
            s.kick_reason = reason[:200]
            SessionService.invalidate(s.id)
            count += 1
        if count:
            await db.commit()
        return count

    @staticmethod
    async def enforce_max_sessions(db: AsyncSession, user_id: str, max_sessions: int) -> int:
        """超出同账号会话上限时踢掉最旧的；返回被踢数量。"""
        if max_sessions <= 0:
            return 0
        rows = (await db.execute(
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.status == "online")
            .order_by(UserSession.login_at.asc())
        )).scalars().all()
        overflow = len(rows) - max_sessions
        if overflow <= 0:
            return 0
        now = datetime.now().isoformat()
        for s in rows[:overflow]:
            s.status = "kicked"
            s.logout_at = now
            s.kick_reason = "超出同账号会话上限"
            SessionService.invalidate(s.id)
        await db.commit()
        return overflow

    @staticmethod
    async def list_sessions(db: AsyncSession, *, keyword: str = "", status: str = "",
                            user_id: str = "", page: int = 1, page_size: int = 20) -> dict:
        conds = []
        if user_id:
            conds.append(UserSession.user_id == user_id)
        if status:
            conds.append(UserSession.status == status)
        if keyword:
            like = f"%{keyword}%"
            conds.append((UserSession.username.like(like)) | (UserSession.ip.like(like)))

        where = conds[0] if conds else True
        for c in conds[1:]:
            where = where & c

        total = (await db.execute(select(func.count()).select_from(UserSession).where(where))).scalar() or 0
        rows = (await db.execute(
            select(UserSession).where(where)
            .order_by(UserSession.status.asc(), UserSession.last_active_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()

        online_by_user = dict(
            (await db.execute(
                select(UserSession.user_id, func.count())
                .where(UserSession.status == "online").group_by(UserSession.user_id)
            )).all()
        )
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [{
                "id": s.id, "user_id": s.user_id, "username": s.username,
                "status": s.status, "ip": s.ip, "user_agent": s.user_agent,
                "device": s.device, "login_at": s.login_at, "last_active_at": s.last_active_at,
                "logout_at": s.logout_at, "kicked_by": s.kicked_by, "kick_reason": s.kick_reason,
                "online_count": online_by_user.get(s.user_id, 0),
            } for s in rows],
        }

    @staticmethod
    async def stats(db: AsyncSession) -> dict:
        online = (await db.execute(
            select(func.count()).select_from(UserSession).where(UserSession.status == "online")
        )).scalar() or 0
        online_users = (await db.execute(
            select(func.count(func.distinct(UserSession.user_id)))
            .where(UserSession.status == "online")
        )).scalar() or 0
        today = datetime.now().date().isoformat()
        today_logins = (await db.execute(
            select(func.count()).select_from(UserSession).where(UserSession.login_at >= today)
        )).scalar() or 0
        total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
        return {
            "online_sessions": online,
            "online_users": online_users,
            "today_logins": today_logins,
            "total_users": total_users,
        }
