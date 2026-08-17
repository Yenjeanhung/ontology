"""智能体（OAG）路由 + 技能（Skill）管理路由。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import AgentQueryRequest, AgentSkillCreate, AgentSkillUpdate
from services.kb_service import KBService
from services.ontology_service import OntologyService
from services.oag_service import OAGService
from services.skill_service import SkillService

router = APIRouter()


# ─────────────────────── 技能 CRUD ───────────────────────


@router.get("/agent/skills")
async def list_skills(db: AsyncSession = Depends(get_db)):
    return await SkillService.list(db)


@router.post("/agent/skills")
async def create_skill(req: AgentSkillCreate, db: AsyncSession = Depends(get_db)):
    # 校验 code 唯一
    from models import AgentSkill
    from sqlalchemy import select
    result = await db.execute(select(AgentSkill).where(AgentSkill.code == req.code))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(409, f"技能编码 '{req.code}' 已存在")
    return await SkillService.create(db, {
        "name": req.name,
        "code": req.code,
        "description": req.description,
        "instructions": req.instructions,
        "sort_order": req.sort_order,
    })


@router.put("/agent/skills/{skill_id}")
async def update_skill(skill_id: str, req: AgentSkillUpdate, db: AsyncSession = Depends(get_db)):
    from models import AgentSkill
    from sqlalchemy import select
    result = await db.execute(select(AgentSkill).where(AgentSkill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "技能不存在")
    # 预设技能禁止改 code
    if skill.is_preset and req.code is not None and req.code != skill.code:
        raise HTTPException(400, "预设技能不能修改编码")
    data = req.model_dump(exclude_unset=True)
    updated = await SkillService.update(db, skill_id, data)
    if not updated:
        raise HTTPException(404, "技能不存在")
    return updated


@router.delete("/agent/skills/{skill_id}")
async def delete_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    try:
        ok = await SkillService.delete(db, skill_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "技能不存在")
    return {"ok": True}


@router.post("/agent/skills/seed")
async def seed_skills(db: AsyncSession = Depends(get_db)):
    from services.skill_service import seed_presets
    count = await seed_presets(db)
    return {"seeded": count}


# ─────────────────────── 智能体查询 ───────────────────────


@router.post("/agent/query")
async def agent_query(req: AgentQueryRequest, db: AsyncSession = Depends(get_db)):
    kb = await KBService.get(db, req.kb_id)
    if not kb:
        raise HTTPException(404, "Knowledge base not found")

    # 预加载本体 schema：db 会话在响应返回后释放，SSE 生成器不再持有 db
    try:
        ontology_schema = await OntologyService.get_kb_extraction_constraints(db, req.kb_id)
    except Exception:
        ontology_schema = None

    # 预加载技能：db 会话在响应返回后释放
    skills = await SkillService.resolve(db, req.skill_ids)

    return StreamingResponse(
        OAGService.query_stream(req.kb_id, req.query, kb["name"], ontology_schema, skills),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
