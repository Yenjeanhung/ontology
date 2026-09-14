"""智能体（OAG）路由 + 技能（Skill）管理路由。"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.deps import get_current_user_id
from database import get_db
from schemas import (
    AgentCreate,
    AgentUpdate,
    AgentQueryRequest,
    AgentSkillCreate,
    AgentSkillGroupCreate,
    AgentSkillGroupUpdate,
    AgentSkillUpdate,
)
from services import skill_import_service
from services.agent_service import AgentService
from services.chat_service import ChatService
from services.kb_service import KBService
from services.memory_store import MemoryStore
from services.ontology_service import OntologyService
from services.oag_service import OAGService
from services.skill_group_service import SkillGroupService, group_paths
from services.skill_service import SkillService

logger = logging.getLogger(__name__)

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
    if req.group_id and not await _get_skill_group(db, req.group_id):
        raise HTTPException(404, "所属分组不存在")
    return await SkillService.create(db, {
        "name": req.name,
        "code": req.code,
        "description": req.description,
        "instructions": req.instructions,
        "sort_order": req.sort_order,
        "group_id": req.group_id,
        **({"files": req.files} if req.files is not None else {}),
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
    if data.get("group_id") and not await _get_skill_group(db, data["group_id"]):
        raise HTTPException(404, "所属分组不存在")
    updated = await SkillService.update(db, skill_id, data)
    if not updated:
        raise HTTPException(404, "技能不存在")
    return updated


@router.delete("/agent/skills/{skill_id}")
async def delete_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    ok = await SkillService.delete(db, skill_id)
    if not ok:
        raise HTTPException(404, "技能不存在")
    return {"ok": True}


# ─────────────────────── 技能分组 CRUD ───────────────────────


async def _get_skill_group(db: AsyncSession, group_id: str):
    from models import AgentSkillGroup

    return await db.get(AgentSkillGroup, group_id)


@router.get("/agent/skill-groups")
async def list_skill_groups(db: AsyncSession = Depends(get_db)):
    """分组扁平列表（含每组直属技能数），前端自行建树并累加子孙计数。"""
    return await SkillGroupService.list(db)


@router.post("/agent/skill-groups")
async def create_skill_group(req: AgentSkillGroupCreate, db: AsyncSession = Depends(get_db)):
    from models import AgentSkillGroup
    from sqlalchemy import select

    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "分组名称不能为空")
    if req.parent_id and not await _get_skill_group(db, req.parent_id):
        raise HTTPException(404, "父分组不存在")
    cond = (
        AgentSkillGroup.parent_id.is_(None) if not req.parent_id
        else AgentSkillGroup.parent_id == req.parent_id
    )
    dup = await db.execute(
        select(AgentSkillGroup).where(AgentSkillGroup.name == name, cond))
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(409, f"同级已存在同名分组「{name}」")
    return await SkillGroupService.create(db, name, req.parent_id, req.sort_order)


@router.put("/agent/skill-groups/{group_id}")
async def update_skill_group(group_id: str, req: AgentSkillGroupUpdate, db: AsyncSession = Depends(get_db)):
    from models import AgentSkillGroup
    from sqlalchemy import select

    if not await _get_skill_group(db, group_id):
        raise HTTPException(404, "分组不存在")
    data = req.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        name = str(data["name"]).strip()
        if not name:
            raise HTTPException(400, "分组名称不能为空")
        sibling_parent = data.get("parent_id")
        if sibling_parent is None and "parent_id" not in data:
            # 未改父分组 → 查重范围沿用当前父；显式传 parent_id（含 null）→ 按新父查重
            current = await _get_skill_group(db, group_id)
            sibling_parent = current.parent_id
        cond = (
            AgentSkillGroup.parent_id.is_(None) if not sibling_parent
            else AgentSkillGroup.parent_id == sibling_parent
        )
        dup = await db.execute(
            select(AgentSkillGroup).where(
                AgentSkillGroup.name == name, cond, AgentSkillGroup.id != group_id))
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(409, f"同级已存在同名分组「{name}」")
    if data.get("parent_id") and not await _get_skill_group(db, data["parent_id"]):
        raise HTTPException(404, "父分组不存在")
    try:
        updated = await SkillGroupService.update(db, group_id, data)
    except ValueError as e:  # 环 / 移到自己名下
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "分组不存在")
    return updated


@router.delete("/agent/skill-groups/{group_id}")
async def delete_skill_group(group_id: str, db: AsyncSession = Depends(get_db)):
    """删除分组：子分组上移一级、子树内技能移入未分组（绝不删技能）。"""
    result = await SkillGroupService.delete(db, group_id)
    if result is None:
        raise HTTPException(404, "分组不存在")
    return result


@router.post("/agent/skills/seed")
async def seed_skills(db: AsyncSession = Depends(get_db)):
    from services.skill_service import seed_presets
    count = await seed_presets(db)
    return {"seeded": count}


# ─────────────────────── 技能导入导出 ───────────────────────


@router.get("/agent/skills/export")
async def export_skills(
    skill_id: str | None = Query(None, description="仅导出指定技能"),
    db: AsyncSession = Depends(get_db),
):
    """导出技能为 JSON（不含 id / 时间戳；带分组路径；配套文件从磁盘回读文本内容）。

    skill_id 给定时仅导出该单个技能。
    """
    all_skills = await SkillService.list(db)
    paths = await group_paths(db)
    export = []
    for s in all_skills:
        if skill_id and s["id"] != skill_id:
            continue
        item = {
            "name": s["name"],
            "code": s["code"],
            "description": s["description"],
            "instructions": s["instructions"],
            "sort_order": s["sort_order"],
        }
        group_path = paths.get(s.get("group_id") or "")
        if group_path:
            item["group_path"] = group_path
        if s.get("files") or s.get("file_dir"):
            item["files"] = skill_import_service.read_files_from_disk(s)
        export.append(item)
    if skill_id and not export:
        raise HTTPException(404, "技能不存在")
    return {"skills": export}


@router.get("/agent/skills/export-zip")
async def export_skills_zip(
    skill_id: str | None = Query(None, description="仅导出指定技能"),
    db: AsyncSession = Depends(get_db),
):
    """导出完整 ZIP：每技能一个 <code>/ 目录（SKILL.md + 配套文件，二进制原样）。

    skill_id 给定时仅导出该单个技能。
    """
    from datetime import datetime
    from fastapi.responses import Response

    content = await skill_import_service.build_export_zip(db, skill_id=skill_id)
    if skill_id and not content:
        raise HTTPException(404, "技能不存在")
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="knowsource-skills-{stamp}.zip"',
        },
    )


@router.post("/agent/skills/import")
async def import_skills(body: dict, db: AsyncSession = Depends(get_db)):
    """从 JSON 数组批量导入技能。重复 code 跳过（overwrite=true 时覆盖更新）。"""
    raw_skills = body.get("skills") if isinstance(body.get("skills"), list) else []
    if not raw_skills:
        raise HTTPException(400, "请求体须包含 skills 数组")
    overwrite = bool(body.get("overwrite", False))
    group_id = await _validate_import_group(db, body.get("group_id"))

    # skill.json 松散字段兼容（title/skill_name 等 → 标准字段）
    normalized = []
    for item in raw_skills:
        if (isinstance(item, dict) and not item.get("name")
                and (item.get("title") or item.get("skill_name"))):
            parsed = skill_import_service.parse_skill_json(item)
            parsed["files"] = item.get("files")
            normalized.append(parsed)
        else:
            normalized.append(item)

    outcome = await skill_import_service.import_skills(
        db, normalized, overwrite=overwrite, group_id=group_id)
    return outcome.to_response()


@router.post("/agent/skills/import-url")
async def import_skills_from_url(body: dict, db: AsyncSession = Depends(get_db)):
    """从 URL 导入：JSON / SKILL.md / ZIP 文件直链 / GitHub 仓库 / SkillsMP 页面。"""
    import httpx

    url = (body.get("url") or "").strip()
    overwrite = bool(body.get("overwrite", False))
    group_id = await _validate_import_group(db, body.get("group_id"))
    if not url:
        raise HTTPException(400, "url 不能为空")

    # GitHub 仓库 → 整仓 zipball 优先
    if _is_github_repo_url(url):
        return await _import_from_github(url, db, overwrite, group_id)

    # SkillsMP 技能页面
    if "skillsmp.com" in url:
        return await _import_from_skillsmp(url, db, overwrite, group_id)

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"URL 请求失败：HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(400, f"URL 请求失败：{e}")

    # ZIP：魔数 / 后缀 / content-type 任一命中即走 ZIP 管线
    if (skill_import_service.is_zip_bytes(resp.content)
            or url.lower().endswith(".zip")
            or "zip" in content_type.lower()):
        return await _run_zip_import(resp.content, db, overwrite, group_id, source=url)

    if "html" in content_type.lower():
        raise HTTPException(
            400, "该 URL 返回 HTML 页面，请使用文件直链（Raw / ZIP 下载地址）或 GitHub 仓库地址")

    if content_type.startswith("text/") or "json" in content_type.lower() or "javascript" in content_type.lower():
        stripped = resp.text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            # JSON（{skills:[...]} / 单个技能对象 / 数组）
            try:
                data = resp.json()
            except Exception as e:
                raise HTTPException(400, f"JSON 解析失败：{e}")
            if isinstance(data, dict) and "skills" in data:
                return await import_skills({**data, "overwrite": overwrite, "group_id": group_id}, db)
            if isinstance(data, dict) and ("name" in data or "title" in data):
                return await import_skills({"skills": [data], "overwrite": overwrite, "group_id": group_id}, db)
            if isinstance(data, list):
                return await import_skills({"skills": data, "overwrite": overwrite, "group_id": group_id}, db)
            raise HTTPException(400, "JSON 格式不支持：须为 {skills:[...]}、单个技能对象或数组")
        # Markdown / SKILL.md
        parsed = skill_import_service.parse_skill_md(resp.text, url)
        outcome = await skill_import_service.import_skills(
            db, [parsed], overwrite=overwrite, group_id=group_id)
        return outcome.to_response()

    raise HTTPException(400, f"不支持的响应类型：{content_type or '未知'}")


async def _validate_import_group(db: AsyncSession, group_id) -> str | None:
    """导入目标分组参数校验：空 → None；非空但不存在 → 404。"""
    if not group_id:
        return None
    if not await _get_skill_group(db, group_id):
        raise HTTPException(404, "目标分组不存在")
    return group_id


async def _run_zip_import(
    content: bytes, db: AsyncSession, overwrite: bool,
    group_id: str | None = None, source: str = "",
):
    """ZIP bytes → 解析 → 共用导入执行器。"""
    try:
        parsed = skill_import_service.parse_zip_bytes(content, source=source)
    except skill_import_service.SkillImportError as e:
        raise HTTPException(e.status, str(e))
    if not parsed:
        raise HTTPException(400, "ZIP 中未找到可导入的技能")
    outcome = await skill_import_service.import_skills(
        db, parsed, overwrite=overwrite, group_id=group_id)
    return outcome.to_response()


@router.post("/agent/skills/import-zip")
async def import_skills_from_zip(
    file: UploadFile = File(...),
    overwrite: bool = Query(False, description="code 重复时是否覆盖更新"),
    group_id: str | None = Query(None, description="导入目标分组 id（空 = 未分组）"),
    db: AsyncSession = Depends(get_db),
):
    """从上传的 ZIP 技能包导入（SKILL.md + 配套文件落盘，清单入库）。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 文件")
    group_id = await _validate_import_group(db, group_id)
    content = await file.read()
    return await _run_zip_import(content, db, overwrite, group_id, source=file.filename)


@router.get("/agent/skills/search-market")
async def search_skill_market(q: str = "", page: int = 1):
    """从 SkillsMP 市场搜索技能（不导入，仅预览）。"""
    import httpx

    params = {"q": q, "page": page, "limit": 20} if q else {"page": page, "limit": 20}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://skillsmp.com/api/skills", params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(502, f"市场搜索失败：{e}")


# ─────────────── URL 导入辅助函数 ───────────────


def _is_github_repo_url(url: str) -> bool:
    """判断是否为 GitHub 仓库 URL。"""
    import re
    return bool(re.match(r'https?://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+/?$', url.strip("/")))


async def _import_from_github(
    repo_url: str, db: AsyncSession, overwrite: bool = False,
    group_id: str | None = None,
):
    """从 GitHub 仓库导入完整技能。

    瀑布：① codeload 整仓 zipball（main → master，拿到 SKILL.md + 配套文件）
          ② 退回 raw 单文件逐试（skill.json → SKILL.md → CLAUDE.md → .claude/skills/SKILL.md）
    """
    import httpx

    repo = repo_url.strip("/").removesuffix(".git")
    owner, repo_name = repo.rsplit("/", 1)

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # ① 整仓 zipball（体积可达数 MB，慢速网络下放宽读取超时）
        for branch in ("main", "master"):
            try:
                resp = await client.get(
                    f"https://codeload.github.com/{owner}/{repo_name}/zip/refs/heads/{branch}",
                    timeout=90)
                if resp.status_code == 200 and skill_import_service.is_zip_bytes(resp.content):
                    try:
                        parsed = skill_import_service.parse_zip_bytes(resp.content, source=repo_name)
                    except skill_import_service.SkillImportError as e:
                        if e.status == 413:
                            raise HTTPException(e.status, f"仓库体积超出技能包上限：{e}")
                        parsed = []  # 仓库无 SKILL.md 结构 → 退回单文件
                    if parsed:
                        outcome = await skill_import_service.import_skills(
                            db, parsed, overwrite=overwrite, group_id=group_id)
                        return outcome.to_response()
            except HTTPException:
                raise
            except Exception:
                continue

        # ② 退回 raw 单文件逐试
        for branch in ("main", "master"):
            raw_base = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}"
            for path in ["skill.json", "SKILL.md", "CLAUDE.md", ".claude/skills/SKILL.md"]:
                try:
                    resp = await client.get(f"{raw_base}/{path}")
                    if resp.status_code != 200:
                        continue
                    if path.endswith(".json"):
                        item = skill_import_service.parse_skill_json(resp.json(), repo_name)
                    else:
                        item = skill_import_service.parse_skill_md(resp.text, repo_url)
                    outcome = await skill_import_service.import_skills(
                        db, [item], overwrite=overwrite, group_id=group_id)
                    return outcome.to_response()
                except HTTPException:
                    raise
                except Exception:
                    continue

    raise HTTPException(404, "未在仓库中找到 skill.json / SKILL.md / CLAUDE.md")


async def _import_from_skillsmp(
    url: str, db: AsyncSession, overwrite: bool = False,
    group_id: str | None = None,
):
    """从 SkillsMP 页面提取仓库信息并导入。"""
    import httpx, re

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()

    # 从页面提取 GitHub 仓库链接
    gh_match = re.search(r'github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)', resp.text)
    if gh_match:
        gh_url = f"https://github.com/{gh_match.group(1)}"
        return await _import_from_github(gh_url, db, overwrite, group_id)

    # 尝试 SkillsMP API
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://skillsmp.com/api/skills/{slug}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("repository"):
                    return await _import_from_github(data["repository"], db, overwrite, group_id)
    except Exception:
        pass

    raise HTTPException(400, "无法从 SkillsMP 页面获取技能仓库信息")



# ─────────────────────── 智能体配置 CRUD ───────────────────────


@router.get("/agents")
async def list_agents(db: AsyncSession = Depends(get_db)):
    return await AgentService.list(db)


@router.get("/agents/default-persona")
async def get_default_persona():
    """系统默认人设：智能体人设（System Prompt）留空时实际生效的内容。"""
    from services.oag_service import OAG_SYSTEM_PROMPT
    return {"persona": OAG_SYSTEM_PROMPT}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    agent = await AgentService.get_detail(db, agent_id)
    if not agent:
        raise HTTPException(404, "智能体不存在")
    return agent


@router.post("/agents")
async def create_agent(req: AgentCreate, db: AsyncSession = Depends(get_db)):
    from models import KnowledgeBase

    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "智能体名称不能为空")
    # KB 选填：填了才校验存在
    if req.kb_id and not await db.get(KnowledgeBase, req.kb_id):
        raise HTTPException(404, "知识库不存在")
    data = req.model_dump()
    data["name"] = name
    return await AgentService.create(db, data)


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentUpdate, db: AsyncSession = Depends(get_db)):
    from models import KnowledgeBase

    data = req.model_dump(exclude_unset=True)
    if data.get("name") is not None and not str(data["name"]).strip():
        raise HTTPException(400, "智能体名称不能为空")
    if data.get("kb_id") and not await db.get(KnowledgeBase, data["kb_id"]):
        raise HTTPException(404, "知识库不存在")
    updated = await AgentService.update(db, agent_id, data)
    if not updated:
        raise HTTPException(404, "智能体不存在")
    return updated


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    agent = await AgentService.get(db, agent_id)
    if not agent:
        raise HTTPException(404, "智能体不存在")
    if agent.is_preset:
        raise HTTPException(400, "内置智能体不能删除")
    if not await AgentService.delete(db, agent_id):
        raise HTTPException(404, "智能体不存在")
    return {"status": "deleted"}


@router.post("/agents/{agent_id}/test")
async def test_agent(agent_id: str, req: AgentQueryRequest, db: AsyncSession = Depends(get_db)):
    """用已配置智能体测试对话（SSE 流式，预览该智能体效果）。"""
    agent = await AgentService.resolve(db, agent_id)
    if not agent:
        raise HTTPException(404, "智能体不存在或已禁用")
    kb = await KBService.get(db, agent["kb_id"])
    if not kb:
        raise HTTPException(404, "智能体绑定的知识库不存在")

    try:
        ontology_schema = await OntologyService.get_kb_extraction_constraints(db, agent["kb_id"])
    except Exception:
        ontology_schema = None
    skills = await SkillService.resolve(db, agent["skill_ids"])

    return StreamingResponse(
        OAGService.query_stream(
            agent["kb_id"], req.query, kb["name"], ontology_schema, skills,
            persona=agent["system_prompt"],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────── 智能体查询 ───────────────────────


async def _chat_no_kb(query: str, persona: str | None, skills=None,
                      history=None, summary: str = "", memories=None):
    """未绑 KB 的智能体：无检索直接 LLM 回答（人设 + 技能）。

    不下发 entities/subgraph/chunks 事件（无检索过程，避免前端渲染
    空的「推理过程」卡片与缺失的「来源」引用框），仅 skills + reasoning + token。
    """
    from services.oag_service import (
        CHAT_SYSTEM_PROMPT, _augment_system_prompt, _history_messages, build_system_prompt,
    )

    skills = skills or []
    yield _sse_evt({"type": "skills", "skills": [{"id": s["id"], "name": s["name"], "code": s["code"]} for s in skills]})

    from providers.llm import chunk_text, create_llm, extract_reasoning
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = create_llm()
    if llm is None:
        yield _sse_evt({"type": "token", "content": "尚未配置大模型，请先在「系统配置」中激活 LLM。"})
        yield "data: [DONE]\n\n"
        return
    # 自定义人设优先；未配置时用纯聊天人设（不含 [来源N]/[事实] 引用标注要求）
    system_prompt = _augment_system_prompt(
        build_system_prompt(skills, base_prompt=persona or CHAT_SYSTEM_PROMPT) or "", summary, memories,
    ) or None
    messages = [*_history_messages(history), HumanMessage(content=query)]
    if system_prompt:
        messages.insert(0, SystemMessage(content=system_prompt))
    try:
        async for chunk in llm.astream(messages):
            reasoning = extract_reasoning(chunk)
            if reasoning:
                yield _sse_evt({"type": "reasoning", "content": reasoning})
            text = chunk_text(chunk)
            if text:
                yield _sse_evt({"type": "token", "content": text})
    except Exception:
        yield _sse_evt({"type": "token", "content": "\n\n[生成回答时出错]"})
    yield "data: [DONE]\n\n"


def _sse_evt(payload: dict) -> str:
    import json as _json
    return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"


# 会话级摘要互斥锁；只增不清（单锁几十字节，会话量级下可忽略）
_summary_locks: dict[str, asyncio.Lock] = {}


def _on_summary_done(task: asyncio.Task) -> None:
    """后台摘要任务收尾：吃掉异常并记日志，避免 unhandled task exception。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("后台摘要任务失败: %r", exc)


def _summarize_background(session_id: str) -> None:
    """滚动摘要后台化：非流式 LLM 压缩耗时不阻塞 SSE 响应。

    - maybe_summarize 幂等（summary_until_id 游标），同会话已有压缩在跑时
      直接跳过，本轮欠下的增量由下一轮收尾自动补压；
    - 后台任务自开 db 会话（请求级会话此时已释放）。
    """
    lock = _summary_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        logger.info("Summary in progress, skip: session=%s", session_id)
        return

    async def _run() -> None:
        async with lock:
            async for db in get_db():
                await ChatService.maybe_summarize(db, session_id)
                break

    asyncio.create_task(_run()).add_done_callback(_on_summary_done)


async def _persist_turn(session_id: str, query: str, answer: str,
                        agent_scope: str, user_id: str = "") -> None:
    """一轮问答收尾：助手消息落库（同步）+ 滚动摘要/长期记忆（后台）。

    SSE 生成器执行时路由的 db 会话已释放，这里自开新会话；全部尽力而为，
    失败只记日志，不影响已下发的回答。
    """
    try:
        async for db in get_db():
            await ChatService.append_message(db, session_id, "assistant", answer)
            break
        _summarize_background(session_id)
    except Exception:
        logger.exception("会话消息落库失败: session=%s", session_id)
    try:
        MemoryStore.add_background(query, answer, agent_id=agent_scope,
                                   user_id=user_id, session_id=session_id)
    except Exception:
        logger.exception("长期记忆写入失败: session=%s", session_id)


@router.post("/agent/query")
async def agent_query(req: AgentQueryRequest, db: AsyncSession = Depends(get_db),
                      user_id: str = Depends(get_current_user_id)):
    # 引用智能体（可选）：传 agent_id 时以其 KB / 技能 / 人设为准；
    # 内置「默认智能体」未绑 KB → 回退页面传的 kb_id；技能以智能体绑定为准（配置页/问答页同一份数据）
    agent = None
    if req.agent_id:
        agent = await AgentService.resolve(db, req.agent_id, fallback_kb_id=req.kb_id)
        if not agent:
            raise HTTPException(404, "智能体不存在或已禁用")
    agent_scope = req.agent_id or ""

    # 预加载技能/人设：db 会话在响应返回后释放，SSE 生成器不再持有 db
    if agent:
        skills = await SkillService.resolve(db, agent["skill_ids"])
        persona = agent["system_prompt"] or None
    else:
        skills = await SkillService.resolve(db, req.skill_ids)
        persona = None

    kb_id = agent["kb_id"] if agent else req.kb_id

    # ── 会话（短期记忆，doc/智能体/智能体会话_功能设计.md）──
    # 传 session_id 续聊（校验存在 + 属主，无效/非本人 404）；归属智能体变化时自动开新会话。
    # 不传则新建会话（标题默认取首问截断）。
    session = None
    if req.session_id:
        session = await ChatService.get_owned(db, req.session_id, user_id)
        if not session:
            raise HTTPException(404, "会话不存在或已被删除")
        if (session.agent_id or "") != agent_scope:
            session = None  # 切换智能体：自动开新会话，前端以 SSE session 事件为准
    if session is None:
        session = await ChatService.create_session(
            db, agent_id=agent_scope, kb_id=kb_id or "",
            user_id=user_id, title=req.query[:50])
    await ChatService.append_message(db, session.id, "user", req.query)

    # 预加载历史（近 N 轮 + 字符预算装填，含滚动摘要）与长期记忆事实
    hist = await ChatService.load_history(db, session.id)
    memories: list[str] = []
    if MemoryStore.available():
        memories = await MemoryStore.search(req.query, agent_id=agent_scope, user_id=user_id)

    if not kb_id:
        # 未绑 KB 的智能体：不使用知识库，直接 LLM 按人设+技能回答
        if not agent:
            raise HTTPException(400, "缺少 kb_id 或 agent_id")
        inner = _chat_no_kb(
            req.query, persona, skills=skills,
            history=hist["history"], summary=hist["summary"], memories=memories,
        )
    else:
        kb = await KBService.get(db, kb_id)
        if not kb:
            # 智能体绑定的 KB 已被删除等场景：回退到页面选择的 KB，避免悬空引用直接 404
            if agent and req.kb_id and req.kb_id != kb_id:
                kb = await KBService.get(db, req.kb_id)
                if kb:
                    kb_id = req.kb_id
        if not kb:
            raise HTTPException(
                404, f"知识库不存在（kb_id={kb_id}）：智能体绑定的知识库可能已被删除，请重新选择知识库或编辑智能体")

        # 预加载本体 schema：db 会话在响应返回后释放
        try:
            ontology_schema = await OntologyService.get_kb_extraction_constraints(db, kb_id)
        except Exception:
            ontology_schema = None

        inner = OAGService.query_stream(
            kb_id, req.query, kb["name"], ontology_schema, skills, persona=persona,
            history=hist["history"], summary=hist["summary"], memories=memories,
        )

    async def _stream():
        # 首事件：会话锚点（新建会话时前端据此记录 session_id 并刷新会话列表）
        yield _sse_evt({"type": "session", "session_id": session.id, "title": session.title})
        answer_parts: list[str] = []
        async for event in inner:
            # 累积回答正文，流结束后落库 + 写长期记忆
            if event.startswith("data: "):
                payload = event[6:].strip()
                if payload and payload != "[DONE]":
                    try:
                        evt = json.loads(payload)
                        if evt.get("type") == "token":
                            answer_parts.append(evt.get("content") or "")
                    except ValueError:
                        pass
            yield event
        await _persist_turn(session.id, req.query, "".join(answer_parts),
                            agent_scope, user_id)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
