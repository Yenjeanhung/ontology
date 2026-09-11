"""密码哈希与 JWT 签发/校验。

密码：bcrypt（cost=12）。bcrypt 只取前 72 字节，超长密码需先截断。
JWT：HS256，payload 只放非敏感的标识信息（user_id / sid / token_version）。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import bcrypt
import jwt

from config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72

_SECRET_FILE = Path("./data/.auth_secret_key")
_cached_secret: str | None = None


def _load_or_create_secret() -> str:
    """返回 JWT 签名密钥：优先 .env，其次持久化到 data/.auth_secret_key。"""
    global _cached_secret
    if _cached_secret:
        return _cached_secret

    if getattr(settings, "AUTH_SECRET_KEY", ""):
        _cached_secret = settings.AUTH_SECRET_KEY
        return _cached_secret

    # 未配置时生成一次并落盘，避免每次重启导致历史 token 全部失效
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _SECRET_FILE.exists():
            existing = _SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                _cached_secret = existing
                return _cached_secret
        generated = secrets.token_urlsafe(48)
        _SECRET_FILE.write_text(generated, encoding="utf-8")
        try:
            _SECRET_FILE.chmod(0o600)
        except Exception:  # Windows 上 chmod 可能不支持，忽略
            pass
        logger.warning(
            "未配置 AUTH_SECRET_KEY，已自动生成并保存到 %s（请妥善保管，丢失后所有登录态失效）",
            _SECRET_FILE,
        )
        _cached_secret = generated
        return _cached_secret
    except Exception:
        logger.exception("生成 JWT 密钥失败，回退到进程内随机密钥（重启后登录态失效）")
        _cached_secret = secrets.token_urlsafe(32)
        return _cached_secret


def secret_key() -> str:
    return _load_or_create_secret()


def _clip(raw: str) -> bytes:
    return raw.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_clip(plain), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_clip(plain), hashed.encode("ascii"))
    except Exception:
        return False


async def hash_password_async(plain: str) -> str:
    # bcrypt 是 CPU 密集的同步实现，丢到线程池避免阻塞事件循环
    return await asyncio.to_thread(hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_password, plain, hashed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exp(minutes: int = 0, days: int = 0) -> datetime:
    return _now() + timedelta(minutes=minutes, days=days)


def create_access_token(user_id: str, username: str, sid: str, token_version: int) -> tuple[str, int]:
    """返回 (token, expires_in_seconds)。"""
    ttl_minutes = max(1, int(getattr(settings, "ACCESS_TOKEN_TTL_MINUTES", 120)))
    payload = {
        "sub": user_id,
        "username": username,
        "sid": sid,
        "ver": int(token_version or 1),
        "typ": "access",
        "iat": _now(),
        "exp": _exp(minutes=ttl_minutes),
    }
    token = jwt.encode(payload, secret_key(), algorithm=ALGORITHM)
    return token, ttl_minutes * 60


def create_refresh_token(user_id: str, sid: str, token_version: int) -> tuple[str, int]:
    ttl_days = max(1, int(getattr(settings, "REFRESH_TOKEN_TTL_DAYS", 7)))
    payload = {
        "sub": user_id,
        "sid": sid,
        "ver": int(token_version or 1),
        "typ": "refresh",
        "iat": _now(),
        "exp": _exp(days=ttl_days),
    }
    token = jwt.encode(payload, secret_key(), algorithm=ALGORITHM)
    return token, ttl_days * 86400


def decode_token(token: str) -> dict[str, Any] | None:
    """校验签名与过期时间；失败返回 None。"""
    try:
        return jwt.decode(token, secret_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def now_iso() -> str:
    return datetime.now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_expired(value: str | None) -> bool:
    dt = parse_iso(value)
    return dt is not None and dt <= _now()
