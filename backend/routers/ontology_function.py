"""S3（P0-3）函数与派生属性路由。

对应设计文档《本体能力对标Palantir_补强设计》§4.3：

- 函数：只读计算，可被动作/视图/派生属性/智能体复用，确定性函数支持 TTL 缓存；
- 派生属性：来源为函数（实时算）或图计算指标（读图分析结果），可物化写入实体属性。
"""
import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session, get_db
from providers.llm import build_llm, chunk_reasoning, chunk_text
from schemas import (
    AiAssistServiceCodeRequest,
    InvokeFunctionRequest,
    ResolveFunctionsRequest,
    SaveDerivedPropertyRequest,
    SaveFunctionRequest,
    TestDerivedPropertyRequest,
    TestFunctionRequest,
)
from services.ontology_function_service import (
    DerivedPropertyService,
    FunctionService,
    serialize_function,
)
from services.service_runtime import IMPORT_WHITELIST, check_code

router = APIRouter()


def _nf(msg: str) -> HTTPException:
    return HTTPException(status_code=404, detail=msg)


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


# ===== 函数 =====


@router.get("/ontology-categories/{category_id}/functions")
async def list_functions(
    category_id: str, ontology_id: str = "", db: AsyncSession = Depends(get_db)
):
    return await FunctionService.list_functions(db, category_id, ontology_id)


@router.post("/ontology-categories/{category_id}/functions")
async def create_function(
    category_id: str, req: SaveFunctionRequest, db: AsyncSession = Depends(get_db)
):
    fn, err = await FunctionService.create(db, category_id, req)
    if err:
        raise _bad(err)
    return fn


@router.get("/functions/{function_id}")
async def get_function(function_id: str, db: AsyncSession = Depends(get_db)):
    fn = await FunctionService.get(db, function_id)
    if not fn:
        raise _nf("函数不存在")
    return serialize_function(fn)


@router.put("/functions/{function_id}")
async def update_function(
    function_id: str, req: SaveFunctionRequest, db: AsyncSession = Depends(get_db)
):
    fn, err = await FunctionService.update(db, function_id, req)
    if err:
        raise _nf(err)
    return fn


@router.delete("/functions/{function_id}")
async def delete_function(function_id: str, db: AsyncSession = Depends(get_db)):
    if not await FunctionService.delete(db, function_id):
        raise _nf("函数不存在")
    return {"status": "deleted"}


@router.post("/functions/{function_id}/test")
async def test_function(
    function_id: str, req: TestFunctionRequest, db: AsyncSession = Depends(get_db)
):
    res, err = await FunctionService.test_run(db, function_id, req)
    if err:
        raise _bad(err)
    return res


@router.post("/entities/{entity_id}/functions/{function_id}/invoke")
async def invoke_function(
    entity_id: str,
    function_id: str,
    req: InvokeFunctionRequest,
    db: AsyncSession = Depends(get_db),
):
    res, err = await FunctionService.invoke(db, entity_id, function_id, req.params or {})
    if err:
        raise _bad(err)
    return res


@router.post("/functions/resolve")
async def resolve_functions(req: ResolveFunctionsRequest, db: AsyncSession = Depends(get_db)):
    """批量解析：对象集/视图一次取多个实体的函数值。"""
    res, err = await FunctionService.resolve_batch(
        db, req.function_id, req.entity_ids or [], req.params or {}
    )
    if err:
        raise _bad(err)
    return res


# ===== 派生属性 =====


@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/derived-properties")
async def list_derived_properties(
    category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)
):
    return await DerivedPropertyService.list_for_ontology(db, ontology_id)


@router.get("/derived-properties")
async def list_all_derived_properties(
    ontology_id: str = "", db: AsyncSession = Depends(get_db)
):
    """全局派生属性列表（可选按本体过滤），附带本体名称。"""
    return await DerivedPropertyService.list_all(db, ontology_id)


@router.post("/ontology-categories/{category_id}/ontologies/{ontology_id}/derived-properties")
async def create_derived_property(
    category_id: str,
    ontology_id: str,
    req: SaveDerivedPropertyRequest,
    db: AsyncSession = Depends(get_db),
):
    dp, err = await DerivedPropertyService.create(db, ontology_id, req)
    if err:
        raise _bad(err)
    return dp


@router.put("/derived-properties/{prop_id}")
async def update_derived_property(
    prop_id: str, req: SaveDerivedPropertyRequest, db: AsyncSession = Depends(get_db)
):
    dp, err = await DerivedPropertyService.update(db, prop_id, req)
    if err:
        raise _nf(err)
    return dp


@router.delete("/derived-properties/{prop_id}")
async def delete_derived_property(prop_id: str, db: AsyncSession = Depends(get_db)):
    if not await DerivedPropertyService.delete(db, prop_id):
        raise _nf("派生属性不存在")
    return {"status": "deleted"}


@router.get("/entities/{entity_id}/derived-properties")
async def resolve_entity_derived_properties(
    entity_id: str, refresh: bool = False, db: AsyncSession = Depends(get_db)
):
    """实体详情页用：该实体所属本体的派生属性及当前值（默认读存储值，refresh=true 实时计算）。"""
    return await DerivedPropertyService.resolve_for_entity(db, entity_id, refresh)


@router.post("/derived-properties/{prop_id}/materialize")
async def materialize_derived_property(
    prop_id: str, limit: int = 1000, db: AsyncSession = Depends(get_db)
):
    res, err = await DerivedPropertyService.materialize(db, prop_id, limit)
    if err:
        raise _nf(err)
    return res


@router.post("/derived-properties/{prop_id}/materialize/stream")
async def materialize_derived_property_stream(prop_id: str, limit: int = 1000):
    """流式物化（SSE）：逐批下发物化进度，供工作流 HTTP 节点实时透传到运行控制台。

    事件契约：progress{done,total,ok,fail,message} → done{property_id,updated,failed,total,...}
    → error{error}（失败时）→ [DONE]。
    """
    def _evt(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    async def gen():
        async with async_session() as db:
            queue: asyncio.Queue = asyncio.Queue()

            async def on_progress(info: dict):
                await queue.put(info)

            task = asyncio.create_task(
                DerivedPropertyService.materialize(db, prop_id, limit, on_progress=on_progress))
            while not (task.done() and queue.empty()):
                try:
                    info = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue  # 等待下一批进度
                yield _evt({"type": "progress", **info})
            try:
                res, err = task.result()
            except Exception as e:  # 物化过程抛异常：以 error 事件收尾
                yield _evt({"type": "error", "error": str(e)})
                yield "data: [DONE]\n\n"
                return
            if err:
                yield _evt({"type": "error", "error": err})
            else:
                yield _evt({"type": "done", **res})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/derived-properties/{prop_id}/test")
async def test_derived_property(
    prop_id: str, req: TestDerivedPropertyRequest, db: AsyncSession = Depends(get_db)
):
    """测试派生属性：对单个实体试算并（可选）写入实体属性。"""
    res, err = await DerivedPropertyService.test_run(db, prop_id, req)
    if err:
        raise _nf(err)
    return res


# ===== AI 辅助编写函数代码 =====

FUNCTION_CODE_SYSTEM_PROMPT = f"""你是本体平台"函数"的代码生成助手。函数是只读计算单元，为沙箱环境编写 Python 代码。

【运行契约】
- 代码必须定义入口函数：def run(params, entity, context)
- 返回值必须是可 JSON 序列化的（dict / list / 字符串 / 数字 / 布尔，不含函数/类/生成器等）
- params: dict，函数入参，键为参数标识
- entity: dict，当前实体快照，形如 {{"id", "name", "entity_type", "description", "properties": {{...}}}}，只读
- context: dict，运行上下文，形如 {{"ontology_name", "entity_id", "now"}}，只读

【与"动作/服务"的区别】函数是纯只读计算：严禁修改实体、写库、发送通知、调用 webhook 等任何副作用；如需求本质需要副作用，请提示用户改用"本体服务（动作）"。

【安全限制（务必遵守，否则代码会被拒绝执行）】
- 允许 import 的模块仅限：{", ".join(sorted(IMPORT_WHITELIST))}
- 禁止 import os / sys / subprocess / socket / pathlib / shutil 等任何其他模块
- 禁止使用 open() / eval() / exec() / compile() / __import__() / globals()
- 网络请求（requests/httpx）必须带 timeout 参数
- 代码要自包含：只定义常量、辅助函数与 run 函数，不要有顶层副作用
- 逻辑要容错：参数缺失/空值时返回合理默认值，不要抛异常

【输出要求】按以下 Markdown 结构输出，除此之外不要输出任何其他文字：
（1）先写实现说明：简短中文，说明实现了什么、返回哪些字段、注意事项
（2）然后输出完整代码：
```python
（含 run 函数的完整 Python 代码）
```
（3）最后输出参数定义（函数无需入参则输出 []）：
```json
[
  {{"name": "参数标识(英文)", "type": "string|number|boolean|object", "required": false, "description": "说明"}}
]
```
若对话中提供了「当前代码」，通常在其可用部分的基础上按最新需求修改，而非完全重写。"""


def _fn_parse_markdown_result(text: str) -> dict | None:
    """从 Markdown 回复中解析 实现说明/代码/参数定义。"""
    code_m = re.search(r"```(?:python)?\s*\n([\s\S]*?)```", text)
    if code_m:
        code = code_m.group(1)
    else:
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


def _fn_sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/ontology-functions/ai-assist")
async def ai_assist_function_code(req: AiAssistServiceCodeRequest):
    """用已配置的大模型按需求描述生成函数代码（SSE 流式输出，结束后做静态安全校验）。"""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise _bad("请先描述想要的函数功能")
    if not settings.OPENAI_API_KEY or not settings.LLM_MODEL:
        raise _bad("尚未配置大模型，请先在「系统配置」中配置并激活 LLM")

    ctx_lines = [f"需求：{prompt}"]
    if req.owner_name:
        ctx_lines.append(f"所属本体/实体：{req.owner_name}")
    if req.name:
        ctx_lines.append(f"函数名称：{req.name}")
    if req.code:
        ctx_lines.append(f"函数编码：{req.code}")
    if req.description:
        ctx_lines.append(f"函数描述：{req.description}")
    if (req.current_code or "").strip():
        ctx_lines.append(f"当前代码：\n{req.current_code.strip()[:8000]}")
    if (req.selected_code or "").strip():
        ctx_lines.append(f"选中的代码片段（需求重点针对它）：\n{req.selected_code.strip()[:4000]}")

    messages = [SystemMessage(content=FUNCTION_CODE_SYSTEM_PROMPT)]
    for m in (req.history or [])[-10:]:
        content = (m.content or "").strip()
        if not content:
            continue
        messages.append(
            AIMessage(content=content[:8000]) if m.role == "assistant" else HumanMessage(content=content[:8000])
        )
    messages.append(HumanMessage(content="\n".join(ctx_lines)))

    llm = build_llm(
        provider=settings.LLM_PROVIDER,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.LLM_MODEL,
        max_tokens=max(settings.LLM_MAX_TOKENS, 2048),
        temperature=0.2,
    )

    async def event_stream():
        full = ""
        try:
            async for chunk in llm.astream(messages):
                think = chunk_reasoning(chunk)
                if think:
                    yield _fn_sse({"type": "thinking", "content": think})
                delta = chunk_text(chunk)
                if not delta:
                    continue
                full += delta
                yield _fn_sse({"type": "delta", "content": delta})
        except Exception as e:
            yield _fn_sse({"type": "error", "detail": f"调用大模型失败：{e}"})
            return

        parsed = _fn_parse_markdown_result(full)
        if not parsed or not (parsed.get("code_text") or "").strip():
            yield _fn_sse({"type": "error", "detail": "大模型返回内容无法解析为代码结果，请调整描述后重试"})
            return
        err = check_code(parsed["code_text"])
        if err:
            yield _fn_sse({"type": "error", "detail": f"生成的代码未通过安全校验（{err}），请调整描述后重试"})
            return
        yield _fn_sse({"type": "done", "data": parsed})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
