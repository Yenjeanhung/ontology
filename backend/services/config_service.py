"""大模型（LLM）运行时配置服务（数据库持久化）。

- 配置方案存于 llm_configs 表；同一时间至多一条 is_active=1（生效中）。
- 生效配置同时写入内存 settings 并重建 LLM 单例（不再写 .env）。
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import LLMConfig

# 掩码占位符：前端回传该值或空串时表示"保持密钥不变"
KEY_UNCHANGED = "__unchanged__"


def mask_key(key: str | None) -> str:
    """对 API Key 做掩码处理，仅保留首尾少量字符。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return key[:3] + "••••" + key[-4:]


def _row_to_dict(row: LLMConfig) -> dict:
    return {
        "id": row.id,
        "name": row.name or "",
        "provider": row.provider or "openai",
        "api_key_masked": mask_key(row.api_key),
        "has_key": bool(row.api_key),
        "base_url": row.base_url or "",
        "model": row.model or "",
        "max_tokens": row.max_tokens if row.max_tokens is not None else 4096,
        "temperature": row.temperature if row.temperature is not None else 0.7,
        "is_active": bool(row.is_active),
    }


def _resolve_key(api_key) -> str:
    """密钥为空/掩码占位时，沿用当前生效密钥。"""
    if api_key is None or str(api_key).strip() in ("", KEY_UNCHANGED):
        return settings.OPENAI_API_KEY
    return api_key


def _apply_to_settings(provider, key, base_url, model, max_tokens, temperature) -> None:
    """把一组配置写入内存 settings 并重建 LLM 单例。"""
    settings.LLM_PROVIDER = provider
    settings.OPENAI_API_KEY = key
    settings.OPENAI_BASE_URL = base_url
    settings.LLM_MODEL = model
    settings.LLM_MAX_TOKENS = int(max_tokens)
    settings.LLM_TEMPERATURE = float(temperature)
    from providers.llm import reset_llm, create_llm
    reset_llm()
    try:
        create_llm()
    except Exception:
        # 重建失败时不阻塞操作（密钥/网络问题留给"测试连接"去诊断），重置已生效
        pass


async def load_active_into_settings(db: AsyncSession) -> None:
    """启动时把生效配置载入内存 settings（供 create_llm 使用）。"""
    row = (await db.execute(select(LLMConfig).where(LLMConfig.is_active == 1))).scalars().first()
    if not row:
        return
    settings.LLM_PROVIDER = row.provider or "openai"
    settings.OPENAI_API_KEY = row.api_key or ""
    settings.OPENAI_BASE_URL = row.base_url or ""
    settings.LLM_MODEL = row.model or ""
    settings.LLM_MAX_TOKENS = row.max_tokens or 4096
    settings.LLM_TEMPERATURE = row.temperature if row.temperature is not None else 0.7


async def list_plans(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(LLMConfig).order_by(LLMConfig.created_at))).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def get_active(db: AsyncSession) -> dict | None:
    row = (await db.execute(select(LLMConfig).where(LLMConfig.is_active == 1))).scalars().first()
    return _row_to_dict(row) if row else None


async def create_plan(db: AsyncSession, req) -> list[dict]:
    has_active = (await db.execute(select(LLMConfig).where(LLMConfig.is_active == 1))).scalars().first()
    row = LLMConfig(
        name=(req.name or "").strip() or "未命名配置",
        provider=(req.provider or "openai").lower(),
        api_key=_resolve_key(req.api_key),
        base_url=req.base_url or "",
        model=req.model or "",
        max_tokens=int(req.max_tokens),
        temperature=float(req.temperature),
        is_active=0 if has_active else 1,   # 首个配置自动生效
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    if row.is_active:
        _apply_to_settings(row.provider, row.api_key, row.base_url, row.model, row.max_tokens, row.temperature)
    return await list_plans(db)


async def update_plan(db: AsyncSession, plan_id: str, req) -> list[dict] | None:
    row = (await db.execute(select(LLMConfig).where(LLMConfig.id == plan_id))).scalars().first()
    if not row:
        return None
    if req.name is not None:
        row.name = req.name.strip() or row.name
    if req.provider is not None:
        row.provider = req.provider.lower()
    if req.api_key is not None and str(req.api_key).strip() not in ("", KEY_UNCHANGED):
        row.api_key = req.api_key
    if req.base_url is not None:
        row.base_url = req.base_url
    if req.model is not None:
        row.model = req.model
    if req.max_tokens is not None:
        row.max_tokens = int(req.max_tokens)
    if req.temperature is not None:
        row.temperature = float(req.temperature)
    row.updated_at = datetime.now().isoformat()
    was_active = bool(row.is_active)
    await db.commit()
    # 编辑的是当前生效配置 → 重新应用，使改动即时生效
    if was_active:
        _apply_to_settings(row.provider, row.api_key, row.base_url, row.model, row.max_tokens, row.temperature)
    return await list_plans(db)


async def delete_plan(db: AsyncSession, plan_id: str) -> list[dict] | None:
    row = (await db.execute(select(LLMConfig).where(LLMConfig.id == plan_id))).scalars().first()
    if not row:
        return None
    was_active = bool(row.is_active)
    await db.delete(row)
    await db.commit()
    if was_active:
        # 删除的是生效配置：自动激活剩下的一条（若有），否则 LLM 不可用
        other = (await db.execute(select(LLMConfig).order_by(LLMConfig.created_at))).scalars().first()
        if other:
            other.is_active = 1
            other.updated_at = datetime.now().isoformat()
            await db.commit()
            _apply_to_settings(other.provider, other.api_key, other.base_url, other.model, other.max_tokens, other.temperature)
        else:
            settings.OPENAI_API_KEY = ""
            settings.LLM_MODEL = ""
            from providers.llm import reset_llm
            reset_llm()
    return await list_plans(db)


async def apply_plan(db: AsyncSession, plan_id: str) -> list[dict] | None:
    row = (await db.execute(select(LLMConfig).where(LLMConfig.id == plan_id))).scalars().first()
    if not row:
        return None
    await db.execute(update(LLMConfig).values(is_active=0))
    row.is_active = 1
    row.updated_at = datetime.now().isoformat()
    await db.commit()
    _apply_to_settings(row.provider, row.api_key, row.base_url, row.model, row.max_tokens, row.temperature)
    return await list_plans(db)
