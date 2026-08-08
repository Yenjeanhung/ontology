"""大模型（LLM）配置接口（数据库持久化）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from schemas import LLMConfigTest, LLMPlanRequest
from services import config_service

router = APIRouter()

_VALID_PROVIDERS = ("openai", "anthropic")


@router.get("/config/llm")
async def get_llm_config(db: AsyncSession = Depends(get_db)):
    """读取当前生效的 LLM 配置（密钥掩码）。无生效配置时返回默认空结构。"""
    active = await config_service.get_active(db)
    if active:
        return active
    return {
        "provider": settings.LLM_PROVIDER,
        "api_key_masked": "",
        "has_key": False,
        "base_url": "",
        "model": "",
        "max_tokens": settings.LLM_MAX_TOKENS,
        "temperature": settings.LLM_TEMPERATURE,
        "is_active": False,
    }


@router.post("/config/llm/test")
async def test_llm_config(req: LLMConfigTest):
    """用给定参数临时构造 LLM 并发送一次 ping，验证连通性。"""
    if req.provider not in _VALID_PROVIDERS:
        raise HTTPException(400, "provider 仅支持 openai 或 anthropic")
    from providers.llm import build_llm
    from services.config_service import KEY_UNCHANGED
    # 密钥为空或掩码占位时，沿用已生效的密钥（支持直接测试当前配置）
    api_key = req.api_key
    if not api_key or api_key.strip() == "" or api_key.strip() == KEY_UNCHANGED:
        api_key = settings.OPENAI_API_KEY
    try:
        llm = build_llm(
            provider=req.provider,
            api_key=api_key,
            base_url=req.base_url,
            model=req.model,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        resp = llm.invoke("ping")
        content = getattr(resp, "content", "") or ""
        preview = content.strip().replace("\n", " ")[:80]
        return {"ok": True, "message": "连接成功", "preview": preview}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "message": f"连接失败：{e}"}


# ───────────────────────── 配置方案（plans） ─────────────────────────

@router.get("/config/llm/plans")
async def list_llm_plans(db: AsyncSession = Depends(get_db)):
    return await config_service.list_plans(db)


@router.post("/config/llm/plans")
async def create_llm_plan(req: LLMPlanRequest, db: AsyncSession = Depends(get_db)):
    if req.provider not in _VALID_PROVIDERS:
        raise HTTPException(400, "provider 仅支持 openai 或 anthropic")
    return await config_service.create_plan(db, req)


@router.put("/config/llm/plans/{plan_id}")
async def update_llm_plan(plan_id: str, req: LLMPlanRequest, db: AsyncSession = Depends(get_db)):
    if req.provider not in _VALID_PROVIDERS:
        raise HTTPException(400, "provider 仅支持 openai 或 anthropic")
    result = await config_service.update_plan(db, plan_id, req)
    if result is None:
        raise HTTPException(404, "配置不存在")
    return result


@router.delete("/config/llm/plans/{plan_id}")
async def delete_llm_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    result = await config_service.delete_plan(db, plan_id)
    if result is None:
        raise HTTPException(404, "配置不存在")
    return result


@router.post("/config/llm/plans/{plan_id}/apply")
async def apply_llm_plan(plan_id: str, db: AsyncSession = Depends(get_db)):
    """把指定方案设为生效。"""
    result = await config_service.apply_plan(db, plan_id)
    if result is None:
        raise HTTPException(404, "配置不存在")
    return result
