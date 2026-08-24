"""本体服务（动作）路由：本体级 CRUD + 实体继承/自定义/调用 + AI 辅助编写。"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import Entity, Ontology
from providers.llm import build_llm, chunk_text
from schemas import (
    AiAssistServiceCodeRequest,
    InvokeEntityServiceRequest,
    SaveOntologyServiceRequest,
    TestOntologyServiceRequest,
)
from services.ontology_action_service import (
    OntologyServiceService,
    ServiceRuntimeService,
    serialize_service,
)
from services.service_runtime import IMPORT_WHITELIST, check_code

router = APIRouter()


def _nf(detail: str):
    return HTTPException(status_code=404, detail=detail)


def _bad_request(detail: str):
    return HTTPException(status_code=400, detail=detail)


async def _ensure_ontology(db: AsyncSession, ontology_id: str) -> Ontology:
    row = await db.execute(select(Ontology).where(Ontology.id == ontology_id))
    ont = row.scalar_one_or_none()
    if not ont:
        raise _nf("Ontology not found")
    return ont


async def _ensure_entity(db: AsyncSession, entity_id: str) -> Entity:
    row = await db.execute(select(Entity).where(Entity.id == entity_id))
    ent = row.scalar_one_or_none()
    if not ent:
        raise _nf("Entity not found")
    return ent


# ===== 本体服务（本体编辑器）=====

@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/services")
async def list_ontology_services(
    category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)
):
    await _ensure_ontology(db, ontology_id)
    return await OntologyServiceService.list_for_ontology(db, ontology_id)


@router.post("/ontology-categories/{category_id}/ontologies/{ontology_id}/services")
async def create_ontology_service(
    category_id: str,
    ontology_id: str,
    req: SaveOntologyServiceRequest,
    db: AsyncSession = Depends(get_db),
):
    await _ensure_ontology(db, ontology_id)
    svc, err = await OntologyServiceService.create(
        db, owner_type="ontology", ontology_id=ontology_id, entity_id=None, req=req
    )
    if err:
        raise _bad_request(err)
    return svc


@router.put("/ontology-services/{service_id}")
async def update_ontology_service(
    service_id: str, req: SaveOntologyServiceRequest, db: AsyncSession = Depends(get_db)
):
    svc, err = await OntologyServiceService.update(db, service_id, req)
    if err == "服务不存在":
        raise _nf(err)
    if err:
        raise _bad_request(err)
    return svc


@router.delete("/ontology-services/{service_id}")
async def delete_ontology_service(service_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyServiceService.delete(db, service_id):
        raise _nf("服务不存在")
    return {"status": "deleted"}


@router.get("/ontology-services/{service_id}")
async def get_ontology_service(service_id: str, db: AsyncSession = Depends(get_db)):
    """按 id 取服务详情（适用于本体服务与实体自定义服务，service.id 全局唯一）。"""
    svc = await OntologyServiceService.get(db, service_id)
    if not svc:
        raise _nf("服务不存在")
    return serialize_service(svc)


@router.post("/ontology-services/{service_id}/test")
async def test_ontology_service(
    service_id: str, req: TestOntologyServiceRequest, db: AsyncSession = Depends(get_db)
):
    result, err = await ServiceRuntimeService.test_run(db, service_id, req)
    if err:
        raise _bad_request(err)
    return result


# ===== AI 辅助编写 =====

AI_CODE_SYSTEM_PROMPT = f"""你是本体服务（动作）的代码生成助手。为沙箱环境编写 Python 动作代码。

【运行契约】
- 代码必须定义入口函数：def run(params, entity, context) -> dict
- 返回值必须是可 JSON 序列化的 dict（不含函数/类/生成器等）
- params: dict，动作入参，键为参数标识
- entity: dict，形如 {{"id", "name", "entity_type", "description", "properties": {{...}}}}
- context: dict，形如 {{"ontology_name", "entity_id", "service_code"}}

【安全限制（务必遵守，否则代码会被拒绝执行）】
- 允许 import 的模块仅限：{", ".join(sorted(IMPORT_WHITELIST))}
- 禁止 import os / sys / subprocess / socket / pathlib / shutil 等任何其他模块
- 禁止使用 open() / eval() / exec() / compile() / __import__() / globals()
- 网络请求（requests/httpx）必须带 timeout 参数
- 代码要自包含：只定义常量、辅助函数与 run 函数，不要有顶层副作用（如模块加载时发请求）

【输出要求】按以下 Markdown 结构输出，除此之外不要输出任何其他文字：
（1）先写实现说明：简短中文，说明实现了什么、返回哪些字段、注意事项
（2）然后输出完整代码：
```python
（含 run 函数的完整 Python 代码）
```
（3）最后输出参数定义（动作无需入参则输出 []）：
```json
[
  {{"name": "参数标识(英文)", "label": "参数名称(中文)", "type": "string|number|boolean|date|datetime|text", "required": false, "default": null, "description": "说明"}}
]
```
若对话中提供了「当前代码」，通常在其可用部分的基础上按最新需求修改，而非完全重写。"""


def _parse_markdown_result(text: str) -> dict | None:
    """从 Markdown 回复中解析 实现说明/代码/参数。"""
    code_m = re.search(r"```(?:python)?\s*\n([\s\S]*?)```", text)
    if code_m:
        code = code_m.group(1)
    else:
        # 允许未闭合的代码块（直接取到结尾）
        m2 = re.search(r"```(?:python)?\s*\n([\s\S]+)$", text)
        if not m2:
            return None
        code = m2.group(1)
    explanation_m = re.search(r"^([\s\S]*?)```", text)
    explanation = (explanation_m.group(1) if explanation_m else "").strip()

    params: list = []
    pm = re.search(r"```json\s*\n([\s\S]*?)```", text)
    if pm:
        try:
            v = json.loads(pm.group(1))
            if isinstance(v, list):
                params = v
        except json.JSONDecodeError:
            pass
    return {
        "code_text": code.rstrip() + "\n",
        "params": params,
        "explanation": explanation,
    }


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/ontology-services/ai-assist")
async def ai_assist_service_code(req: AiAssistServiceCodeRequest):
    """用已配置的大模型按需求描述生成动作代码（SSE 流式输出，结束后做静态安全校验）。"""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise _bad_request("请先描述想要的动作功能")
    if not settings.OPENAI_API_KEY or not settings.LLM_MODEL:
        raise _bad_request("尚未配置大模型，请先在「系统配置」中配置并激活 LLM")

    ctx_lines = [f"需求：{prompt}"]
    if req.owner_name:
        ctx_lines.append(f"所属本体/实体：{req.owner_name}")
    if req.name:
        ctx_lines.append(f"服务名称：{req.name}")
    if req.code:
        ctx_lines.append(f"动作标识：{req.code}")
    if req.description:
        ctx_lines.append(f"服务描述：{req.description}")
    if (req.current_code or "").strip():
        ctx_lines.append(f"当前代码：\n{req.current_code.strip()[:8000]}")
    if (req.selected_code or "").strip():
        ctx_lines.append(f"选中的代码片段（需求重点针对它）：\n{req.selected_code.strip()[:4000]}")

    # 组装多轮消息：system + 历史 + 本轮
    messages = [SystemMessage(content=AI_CODE_SYSTEM_PROMPT)]
    for m in (req.history or [])[-10:]:
        content = (m.content or "").strip()
        if not content:
            continue
        content = content[:8000]
        messages.append(AIMessage(content=content) if m.role == "assistant" else HumanMessage(content=content))
    messages.append(HumanMessage(content="\n".join(ctx_lines)))

    llm = build_llm(
        provider=settings.LLM_PROVIDER,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.LLM_MODEL,
        max_tokens=max(settings.LLM_MAX_TOKENS, 2048),
        temperature=0.2,  # 代码生成用低温度
    )

    async def event_stream():
        full = ""
        try:
            async for chunk in llm.astream(messages):
                delta = chunk_text(chunk)
                if not delta:
                    continue
                full += delta
                yield _sse({"type": "delta", "content": delta})
        except Exception as e:
            yield _sse({"type": "error", "detail": f"调用大模型失败：{e}"})
            return

        parsed = _parse_markdown_result(full)
        if not parsed or not (parsed.get("code_text") or "").strip():
            yield _sse({"type": "error", "detail": "大模型返回内容无法解析为代码结果，请调整描述后重试"})
            return
        err = check_code(parsed["code_text"])
        if err:
            yield _sse({"type": "error", "detail": f"生成的代码未通过安全校验（{err}），请调整描述后重试"})
            return
        yield _sse({"type": "done", "data": parsed})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===== 实体服务（实体详情页：继承 + 自定义 + 调用）=====

@router.get("/entities/{entity_id}/services")
async def list_entity_services(entity_id: str, db: AsyncSession = Depends(get_db)):
    ent = await _ensure_entity(db, entity_id)
    return await OntologyServiceService.get_effective_services(db, ent)


@router.post("/entities/{entity_id}/services")
async def create_entity_service(
    entity_id: str, req: SaveOntologyServiceRequest, db: AsyncSession = Depends(get_db)
):
    ent = await _ensure_entity(db, entity_id)
    svc, err = await OntologyServiceService.create(
        db, owner_type="entity", ontology_id=ent.ontology_id, entity_id=ent.id, req=req
    )
    if err:
        raise _bad_request(err)
    return svc


@router.post("/entities/{entity_id}/services/{service_id}/invoke")
async def invoke_entity_service(
    entity_id: str,
    service_id: str,
    req: InvokeEntityServiceRequest,
    db: AsyncSession = Depends(get_db),
):
    result, err = await ServiceRuntimeService.invoke(db, entity_id, service_id, req.params)
    if err:
        raise _bad_request(err)
    return result


@router.post("/entities/{entity_id}/services/{service_id}/copy")
async def copy_service_to_entity(
    entity_id: str, service_id: str, db: AsyncSession = Depends(get_db)
):
    """把本体服务复制为该实体的自定义服务（覆盖起点）。"""
    ent = await _ensure_entity(db, entity_id)
    svc, err = await OntologyServiceService.copy_to_entity(db, ent, service_id)
    if err:
        raise _bad_request(err)
    return svc
