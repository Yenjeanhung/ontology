"""工作流执行引擎：基于 LangGraph StateGraph 动态建图 + SSE 进度。

调度模型（LangGraph）：
- 运行时把 JSON 定义（nodes/edges）翻译成 StateGraph：逐节点 add_node，
  condition 节点用 add_conditional_edges 按 true/false 分支路由；
- 同一 superstep 内无依赖的节点由 LangGraph 自动并行执行（替代旧引擎串行调度）；
- 每次运行前重建+编译图（编译为纯内存操作，毫秒级）；configurable.thread_id
  已按 run 隔离，为后续接入 checkpointer 断点恢复预留；
- 节点执行器（agent/service/llm/code）、变量渲染、输出投影、摘要沿用原实现；
- 逐事件 yield SSE 字符串，事件契约与旧引擎完全一致：
  workflow_started / node_started / node_progress / node_finished /
  node_skipped / node_failed / workflow_finished / [DONE]。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Annotated, TypedDict, Optional

logger = logging.getLogger(__name__)

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from database import async_session
from models import WorkflowRun
from providers.llm import chunk_text, create_llm, extract_reasoning
from schemas import TestOntologyServiceRequest
from services.agent_service import AgentService
from services.kb_service import KBService
from services.oag_service import OAGService
from services.ontology_action_service import ServiceRuntimeService
from services.ontology_service import OntologyService
from services.service_runtime import execute_service
from services.skill_service import SkillService

_VAR_RE = re.compile(r"\{\{\s*([\w.\[\]-]+)\s*\}\}")
_MISSING = object()


# ══════════════════════ 变量渲染（沿用原实现） ══════════════════════

def _lookup(path: str, context: dict):
    cur = context
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _strip_citations(text: str) -> str:
    """去掉 OAG 问答返回的 [来源N] 引用标记。"""
    return re.sub(r"\[\s*来源\s*\d+\s*\]", "", text or "")


def render(value, context: dict):
    """解析变量引用 {{node.field}}。整串单引用返回原值（保留类型），嵌入则 str 化。"""
    if isinstance(value, str):
        m = re.fullmatch(r"\{\{\s*([\w.\[\]-]+)\s*\}\}", value)
        if m:
            v = _lookup(m.group(1), context)
            return value if v is _MISSING else v

        def _sub(mm):
            v = _lookup(mm.group(1), context)
            return mm.group(0) if v is _MISSING else str(v)
        return _VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


# ══════════════════════ 条件求值（沿用原实现） ══════════════════════

def _eval_condition(op: str, left, right) -> bool:
    if op == "empty":
        return not left
    if op == "not_empty":
        return bool(left)
    if op == "contains":
        return str(right) in str(left)
    if op == "not_contains":
        return str(right) not in str(left)
    if op == "gt":
        try:
            return float(left) > float(right)
        except (TypeError, ValueError):
            return False
    if op == "lt":
        try:
            return float(left) < float(right)
        except (TypeError, ValueError):
            return False
    if op == "!=":
        return str(left) != str(right)
    return str(left) == str(right)  # 默认 ==


# ══════════════════════ 节点执行器（沿用原实现） ══════════════════════

async def _exec_agent(cfg: dict, context: dict, db) -> dict:
    query = str(render(cfg.get("query_template", ""), context))
    # 结构化输出字段声明：[{name, type, description}]，声明后要求 LLM 末尾输出 JSON 块并解析
    struct_fields = [f for f in (cfg.get("structured_outputs") or []) if isinstance(f, dict) and f.get("name")]
    if struct_fields:
        query += _build_structured_suffix(struct_fields)
    if cfg.get("agent_id"):
        agent = await AgentService.resolve(db, cfg["agent_id"])
        if not agent:
            raise RuntimeError("智能体不存在或已禁用")
        kb_id, skill_ids, persona = agent["kb_id"], agent["skill_ids"], agent["system_prompt"]
    else:
        kb_id = cfg.get("kb_id") or ""
        skill_ids = cfg.get("skill_ids") or []
        persona = None

    # 未绑 KB：无知识库纯对话（人设 + 技能直接 LLM 回答），与问答页行为一致
    if not kb_id:
        skills = await SkillService.resolve(db, skill_ids)
        out = await _agent_chat_no_kb(query, persona, skills)
        if struct_fields:
            out.update(_parse_structured(out.get("answer") or "", struct_fields))
        return out

    # 工作流智能体节点：不复用 OAG 知识库问答（带了 [来源N] 引用、检索/图谱等副作用），
    # 而是走独立的 agent 问答逻辑——按人设+技能用 LLM 直接回答，无来源标注。
    if cfg.get("agent_id"):
        agent = await AgentService.resolve(db, cfg["agent_id"])
        if not agent:
            raise RuntimeError("智能体不存在或已禁用")
        kb_id, skill_ids, persona = agent["kb_id"], agent["skill_ids"], agent["system_prompt"]
    else:
        kb_id = cfg.get("kb_id") or ""
        skill_ids = cfg.get("skill_ids") or []
        persona = None

    # 未绑 KB：纯对话（人设 + 技能直接 LLM 回答）
    if not kb_id:
        skills = await SkillService.resolve(db, skill_ids)
        out = await _agent_chat_no_kb(query, persona, skills)
        if struct_fields:
            out.update(_parse_structured(out.get("answer") or "", struct_fields))
        return out

    # 绑定 KB：仍然走 OAG 检索问答（保留知识库能力），但去掉来源标注
    kb = await KBService.get(db, kb_id)
    if not kb:
        raise RuntimeError("知识库不存在")
    try:
        ontology_schema = await OntologyService.get_kb_extraction_constraints(db, kb_id)
    except Exception:
        ontology_schema = None
    skills = await SkillService.resolve(db, skill_ids)
    result = await OAGService.run(kb_id, query, kb["name"], ontology_schema, skills, persona)
    out = {
        "answer": _strip_citations(result["answer"]),
        "chunks": result["chunks"],
        "entities": result["entities"],
        "subgraph": result.get("subgraph"),
    }
    # 结构化字段解析后并入输出（如 count、names）
    if struct_fields:
        out.update(_parse_structured(out["answer"], struct_fields))
    # 手动追加的自定义输出：extra_outputs = {自定义名: 表达式}
    # 表达式可引用本节点固定输出（{{answer}} 等）与上游变量（{{n1.x}}），经 render 求值
    extra = cfg.get("extra_outputs") or {}
    if isinstance(extra, dict):
        local_ctx = dict(context)
        local_ctx["_self"] = out  # 支持子字段提取，如 {{_self.chunks.0.file_name}}
        for name, expr in extra.items():
            if name and isinstance(expr, str):
                out[name] = render(expr, local_ctx)
    return out


async def _exec_agent_stream(cfg: dict, context: dict, db, on_token=None, on_step=None, on_reasoning=None) -> dict:
    """智能体节点流式执行：LLM 生成过程中持续调用 on_token(token)、on_reasoning(token)。

    通过 on_step(label) 把固定前置步骤逐一下发，让前端知道当前进度。
    最终返回结果与非流式 _exec_agent 完全一致。
    """
    def _step(label: str):
        if on_step:
            on_step(label)

    query = str(render(cfg.get("query_template", ""), context))
    struct_fields = [f for f in (cfg.get("structured_outputs") or []) if isinstance(f, dict) and f.get("name")]
    if struct_fields:
        query += _build_structured_suffix(struct_fields)
    _step("准备查询")

    if cfg.get("agent_id"):
        _step("解析智能体配置")
        agent = await AgentService.resolve(db, cfg["agent_id"])
        if not agent:
            raise RuntimeError("智能体不存在或已禁用")
        kb_id, skill_ids, persona = agent["kb_id"], agent["skill_ids"], agent["system_prompt"]
    else:
        kb_id = cfg.get("kb_id") or ""
        skill_ids = cfg.get("skill_ids") or []
        persona = None

    if not kb_id:
        skills = await SkillService.resolve(db, skill_ids)
        out = await _agent_chat_no_kb_stream(query, persona, skills, on_token, on_step, on_reasoning)
    else:
        _step("加载知识库")
        kb = await KBService.get(db, kb_id)
        if not kb:
            raise RuntimeError("知识库不存在")
        try:
            ontology_schema = await OntologyService.get_kb_extraction_constraints(db, kb_id)
        except Exception:
            ontology_schema = None
        skills = await SkillService.resolve(db, skill_ids)
        _step("检索相关知识")
        out = await _oag_stream_collect(kb_id, query, kb["name"], ontology_schema, skills, persona, on_token, on_step, on_reasoning)

    if struct_fields:
        _step("解析结构化输出")
        out.update(_parse_structured(out.get("answer") or "", struct_fields))
    _step("整理输出")
    extra = cfg.get("extra_outputs") or {}
    if isinstance(extra, dict):
        local_ctx = dict(context)
        local_ctx["_self"] = out
        for name, expr in extra.items():
            if name and isinstance(expr, str):
                out[name] = render(expr, local_ctx)
    return out


async def _agent_chat_no_kb(query: str, persona: str | None, skills: list[dict]) -> dict:
    """未绑 KB 智能体的纯对话执行：LLM 按人设+技能回答，返回与 OAG 一致的结构。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from oag_service import build_system_prompt

    llm = create_llm()
    if llm is None:
        raise RuntimeError("尚未配置大模型，请先在「系统配置」中激活 LLM")
    system_prompt = build_system_prompt(skills, base_prompt=persona or "") or None
    messages = [HumanMessage(content=query)]
    if system_prompt:
        messages.insert(0, SystemMessage(content=system_prompt))
    try:
        resp = await llm.ainvoke(messages)
        text = chunk_text(resp)
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败：{e}")
    return {
        "answer": text,
        "chunks": [],
        "entities": [],
        "subgraph": None,
    }


async def _agent_chat_no_kb_stream(
    query: str,
    persona: str | None,
    skills: list[dict],
    on_token=None,
    on_step=None,
    on_reasoning=None,
) -> dict:
    """未绑 KB 智能体的纯对话执行（流式），每收到一个 token 调用 on_token、on_reasoning。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.oag_service import build_system_prompt

    if on_step:
        on_step("调用大模型")
    llm = create_llm()
    if llm is None:
        raise RuntimeError("尚未配置大模型，请先在「系统配置」中激活 LLM")
    system_prompt = build_system_prompt(skills, base_prompt=persona or "") or None
    messages = [HumanMessage(content=query)]
    if system_prompt:
        messages.insert(0, SystemMessage(content=system_prompt))
    try:
        parts = []
        reasoning_parts = []
        token_count = 0
        reasoning_seen = False
        async for chunk in llm.astream(messages):
            content = chunk_text(chunk)
            reasoning = extract_reasoning(chunk)
            if reasoning:
                if not reasoning_seen:
                    reasoning_seen = True
                    logger.debug("[agent] 首次收到反思内容（len=%d）", len(reasoning))
                reasoning_parts.append(reasoning)
                if on_reasoning:
                    on_reasoning(reasoning)
            if content:
                token_count += 1
                parts.append(content)
                if on_token:
                    on_token(content)
        text = "".join(parts)
        if token_count == 0:
            logger.warning("_agent_chat_no_kb_stream 未收到任何 token，可能当前模型不支持流式输出")
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败：{e}")
    return {
    "answer": text,
    "reasoning": "".join(reasoning_parts),
    "chunks": [],
    "entities": [],
    "subgraph": None,
    }


async def _oag_stream_collect(
    kb_id: str,
    query: str,
    kb_name: str,
    ontology_schema,
    skills,
    persona,
    on_token=None,
    on_step=None,
    on_reasoning=None,
) -> dict:
    """复用 OAGService.query_stream 收集完整结果，同时把 token/reasoning 透传。"""
    if on_step:
        on_step("调用大模型")
    answer_parts: list[str] = []
    reasoning_parts: list[str] = []
    chunks: list[dict] = []
    entities: list[dict] = []
    subgraph: dict | None = None
    async for s in OAGService.query_stream(kb_id, query, kb_name, ontology_schema, skills, persona):
        # SSE 格式兼容：data: 后可能有不同数量空格，也可能有 ID 前缀
        m = re.match(r"^(?:id:[^\n]*\n)?data:\s*", s, re.MULTILINE)
        if not m:
            continue
        payload = s[m.end():].strip()
        if payload == "[DONE]":
            break
        # 某些 SSE 实现会把 JSON 分在多个 data: 行，这里只处理单行完整 JSON
        try:
            evt = json.loads(payload)
        except ValueError:
            continue
        t = evt.get("type")
        if t == "token":
            content = evt.get("content") or ""
            answer_parts.append(content)
            if on_token:
                on_token(content)
        elif t == "reasoning":
            r = evt.get("content") or ""
            reasoning_parts.append(r)
            if on_reasoning:
                on_reasoning(r)
        elif t == "chunks":
            chunks = evt.get("chunks") or []
        elif t == "entities":
            entities = evt.get("entities") or []
        elif t == "subgraph":
            subgraph = evt
    if len(answer_parts) == 0:
        logger.warning("_oag_stream_collect 未收到任何 token，可能当前模型/OAG 不支持流式输出")
    return {
        "answer": _strip_citations("".join(answer_parts)),
        "reasoning": "".join(reasoning_parts),
        "chunks": chunks,
        "entities": entities,
        "subgraph": subgraph or {"facts": "", "entities": [], "relations": [], "retrieval_path": {}},
    }





def _build_structured_suffix(fields: list[dict]) -> str:
    """根据声明的结构化字段（支持任意多个）生成追加指令，要求 LLM 只输出 JSON。"""
    type_hint = {"string": "字符串", "number": "数字", "boolean": "true/false", "array": "JSON数组", "object": "JSON对象"}
    lines = []
    for i, f in enumerate(fields):
        if not (isinstance(f, dict) and f.get("name")):
            continue
        desc = f"，{f['description']}" if f.get("description") else ""
        hint = type_hint.get(f.get("type", "string"), "字符串")
        comma = "," if i < len(fields) - 1 else ""
        lines.append(f'    "{f["name"]}": <{hint}{desc}>{comma}')
    schema = "\n".join(lines)
    return (
        "\n\n【输出要求】请只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块，"
        "直接输出原始 JSON。必须包含且仅包含以下全部字段（字段名保持一致，值为对应类型的 JSON 值）：\n"
        "{\n" + schema + "\n}\n"
        "注意：数组用 [\"a\", \"b\"] 形式；数字不要加引号；不要添加未声明的字段。"
    )


def _coerce_structured_value(value, typ: str):
    """把模型输出的值强制转换为用户声明的类型，减少模型格式差异影响。"""
    if value is None:
        return None
    if typ == "string":
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    if typ == "number":
        try:
            return float(value) if isinstance(value, str) else (value if isinstance(value, (int, float)) else None)
        except (TypeError, ValueError):
            return None
    if typ == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "是")
        return bool(value)
    if typ == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [value]
            except ValueError:
                # 中文逗号/换行分隔的字符串也尝试拆成数组
                parts = [p.strip() for p in re.split(r"[,，;；\n]+", value) if p.strip()]
                return parts if parts else [value]
        return [value]
    if typ == "object":
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else None
            except ValueError:
                return None
        return None
    return value


def _parse_structured(answer: str, fields: list[dict]) -> dict:
    """从回答中解析结构化字段，兼容不同模型的输出差异；解析失败的字段值为 null。

    支持：
    - ```json ... ``` / ``` ... ``` 代码块
    - 普通 JSON 对象（大括号匹配）
    - 选择包含最多声明字段的候选块，避免取到示例/无关 JSON
    """
    import re as _re

    field_names = {f.get("name") for f in fields if isinstance(f, dict) and f.get("name")}
    candidates: list[str] = []

    # 1. markdown 代码块（带或不带 json 标签）
    for pat in (r"```json\s*\n([\s\S]*?)```", r"```\s*\n([\s\S]*?)```"):
        for block in _re.finditer(pat, answer):
            candidates.append(block.group(1).strip())

    # 2. 普通 JSON 对象（尽可能贪婪地匹配大括号）
    # 用简单计数找到最外层大括号范围
    for start in _re.finditer(r"\{", answer):
        i = start.start()
        depth = 0
        end = -1
        for j in range(i, len(answer)):
            if answer[j] == "{":
                depth += 1
            elif answer[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end > i:
            candidates.append(answer[i:end])

    best_data: dict = {}
    best_score = -1
    for raw in candidates:
        # 去除可能的 markdown 标记残余和前后说明文字
        cleaned = _re.sub(r"^```.*$|```$", "", raw, flags=_re.MULTILINE).strip()
        if not cleaned:
            continue
        try:
            data = json.loads(cleaned)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        # 优先选择包含最多声明字段的 JSON 块
        score = len(field_names & set(data.keys()))
        if score > best_score:
            best_score = score
            best_data = data

    # 3. 按声明字段输出，并做类型强制转换
    out: dict = {}
    for f in fields:
        name = f.get("name") if isinstance(f, dict) else None
        if not name:
            continue
        out[name] = _coerce_structured_value(best_data.get(name), f.get("type", "string"))
    return out


async def _exec_service(cfg: dict, context: dict, db) -> dict:
    params = render(cfg.get("params") or {}, context) or {}
    if cfg.get("entity_id"):
        result, err = await ServiceRuntimeService.invoke(
            db, cfg["entity_id"], cfg["service_id"], params,
        )
    else:
        req = TestOntologyServiceRequest(
            params=params,
            mock_entity=render(cfg.get("mock_entity") or {}, context),
        )
        result, err = await ServiceRuntimeService.test_run(db, cfg["service_id"], req)
    if err:
        raise RuntimeError(err)
    return result  # {success, data, error, stdout, duration_ms}；success=false 由条件节点判断，不在此抛错


async def _exec_llm(cfg: dict, context: dict) -> dict:
    llm = create_llm()
    if llm is None:
        raise RuntimeError("尚未配置大模型，请先在「系统配置」中激活 LLM")
    system = (cfg.get("system_prompt") or "").strip()
    prompt_text = str(render(cfg.get("prompt_template", ""), context))
    struct_fields = [f for f in (cfg.get("structured_outputs") or []) if isinstance(f, dict) and f.get("name")]
    if struct_fields:
        prompt_text += _build_structured_suffix(struct_fields)
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt_text))
    resp = await llm.ainvoke(messages)
    text = chunk_text(resp)
    out = {"text": text}
    if struct_fields:
        parsed = _parse_structured(text, struct_fields)
        logger.debug("[llm] structured parse: fields=%s, parsed=%s", [f.get("name") for f in struct_fields], parsed)
        out.update(parsed)
    return out


async def _exec_llm_stream(
    cfg: dict,
    context: dict,
    on_token=None,
    on_step=None,
    on_reasoning=None,
) -> dict:
    """大模型节点流式执行：与智能体节点一致的 token / reasoning 实时下发。

    若当前模型不支持流式（astream 抛错或无 token），自动降级为 ainvoke，
    并一次性下发完整结果，保证节点仍能正常结束。
    """
    if on_step:
        on_step("调用大模型")
    llm = create_llm()
    if llm is None:
        raise RuntimeError("尚未配置大模型，请先在「系统配置」中激活 LLM")
    system = (cfg.get("system_prompt") or "").strip()
    prompt_text = str(render(cfg.get("prompt_template", ""), context))
    struct_fields = [f for f in (cfg.get("structured_outputs") or []) if isinstance(f, dict) and f.get("name")]
    if struct_fields:
        prompt_text += _build_structured_suffix(struct_fields)
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt_text))

    parts: list[str] = []
    reasoning_parts: list[str] = []
    token_count = 0
    reasoning_seen = False
    stream_ok = True
    try:
        async for chunk in llm.astream(messages):
            content = chunk_text(chunk)
            reasoning = extract_reasoning(chunk)
            if reasoning:
                if not reasoning_seen:
                    reasoning_seen = True
                    logger.debug("[llm] 首次收到反思内容（len=%d）", len(reasoning))
                reasoning_parts.append(reasoning)
                if on_reasoning:
                    on_reasoning(reasoning)
            if content:
                token_count += 1
                parts.append(content)
                if on_token:
                    on_token(content)
    except Exception as e:
        logger.warning("[llm] 流式调用失败，将降级为非流式：%s", e)
        stream_ok = False

    text = "".join(parts)
    if not stream_ok or token_count == 0:
        # 降级：非流式调用并一次性下发完整结果
        resp = await llm.ainvoke(messages)
        text = chunk_text(resp)
        if on_token:
            on_token(text)
        r = extract_reasoning(resp)
        if r and on_reasoning:
            on_reasoning(r)

    out = {"text": text}
    if struct_fields:
        if on_step:
            on_step("解析结构化输出")
        parsed = _parse_structured(text, struct_fields)
        logger.debug("[llm stream] structured parse: fields=%s, parsed=%s", [f.get("name") for f in struct_fields], parsed)
        out.update(parsed)
    return out


async def _exec_code(cfg: dict, context: dict) -> dict:
    params = render(cfg.get("params") or {}, context) or {}
    return await execute_service(
        code_text=cfg.get("code_text", ""),
        language=cfg.get("language", "python"),
        params=params,
        entity={},
        # 把上游节点输出透传给沙箱，代码里可通过 context['节点id'] 直接读取
        context=context or {},
        timeout_seconds=cfg.get("timeout_seconds", 30),
    )


async def _execute_node(node: dict, context: dict, db) -> dict:
    t = node["type"]
    cfg = node.get("config") or {}
    if t == "start":
        return context.get("start", {})
    if t == "end":
        out = {}
        for o in cfg.get("outputs") or []:
            name = o.get("name")
            if name:
                out[name] = render(o.get("value"), context)
        return out
    if t == "agent":
        return await _exec_agent(cfg, context, db)
    if t == "service":
        return await _exec_service(cfg, context, db)
    if t == "llm":
        return await _exec_llm(cfg, context)
    if t == "condition":
        left = render(cfg.get("left"), context)
        right = render(cfg.get("right"), context)
        return {"result": _eval_condition(cfg.get("operator", "=="), left, right)}
    if t == "code":
        return await _exec_code(cfg, context)
    raise ValueError(f"未知节点类型：{t}")


# ══════════════════════ 输出投影 / 截断 / 摘要（沿用原实现） ══════════════════════

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# 各节点类型固定输出，投影时强制保留（前端同样锁定不可删）
FIXED_OUTPUTS = {
    "agent": {"answer", "chunks", "entities", "subgraph"},
    "service": {"success", "data", "error", "stdout", "duration_ms"},
    "llm": {"text"},
    "condition": {"result"},
    "code": {"success", "data", "error", "stdout", "duration_ms"},
}


def _project_output(node: dict, result) -> dict:
    """按节点声明的 output_fields 投影结果：只保留「声明的键 + 该类型固定输出 + 结构化自定义字段」。
    智能体节点的 structured_outputs 字段名强制纳入保留，消除前端 output_fields 同步遗漏导致的模型差异。
    固定输出（如智能体的 answer）强制保留，删不掉；未声明 / 结果非 dict /
    声明的键全不存在时，原样返回全部结果（兼容旧数据）。"""
    cfg = node.get("config") or {}
    fields = cfg.get("output_fields")
    if not isinstance(result, dict):
        return result
    keep = set(fields or []) | FIXED_OUTPUTS.get(node.get("type"), set())
    # agent / llm / code 节点：structured_outputs 里的字段名强制保留，确保下游结束节点能拿到自定义输出
    if node.get("type") in ("agent", "llm", "code"):
        for so in cfg.get("structured_outputs") or []:
            name = so.get("name") if isinstance(so, dict) else so
            if name:
                keep.add(name)
    projected = {k: result[k] for k in keep if k in result}
    # 代码节点：自定义字段值实际在 result["data"] 里，需要平铺到顶层供下游引用
    if node.get("type") == "code":
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict):
            for name in keep:
                if name in data and name not in projected:
                    projected[name] = data[name]
    # 声明的键全都不在结果里（用户随便写的名字）→ 保留全部，避免下游拿空对象
    return projected if projected else result


def _truncate_output(result, max_field_len: int = 4000):
    """截断输出对象中过长的字段值，优先保留结构化自定义字段的完整可读性。

    当整体 JSON 超过 WORKFLOW_NODE_OUTPUT_LIMIT 时，仅截断单字段值（如 answer/text），
    而不是把全部字段替换为一个 text 字符串，确保下游自定义输出仍能正常引用。
    """
    limit = settings.WORKFLOW_NODE_OUTPUT_LIMIT
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        s = str(result)
    if len(s) <= limit:
        return result

    if not isinstance(result, dict):
        return {"_truncated": True, "text": s[:limit] + f"\n…（共 {len(s)} 字符）"}

    truncated = {"_truncated": True}
    for k, v in result.items():
        try:
            vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            vs = str(v)
        if len(vs) > max_field_len:
            truncated[k] = vs[:max_field_len] + f"\n…（该字段共 {len(vs)} 字符）"
        else:
            truncated[k] = v
    return truncated


def _short(value, limit: int = 80) -> str:
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


def _summarize(node: dict, result) -> str:
    """节点执行结果的一句话摘要，供运行日志展示。"""
    t = node.get("type")
    if t == "start":
        return f"输入：{_short(result)}"
    if t == "end":
        return f"输出：{_short(result)}"
    if t == "agent":
        answer = (result or {}).get("answer") or ""
        chunks = (result or {}).get("chunks") or []
        entities = (result or {}).get("entities") or []
        return f"已生成回答（{len(answer)} 字符，{len(chunks)} 个来源，{len(entities)} 个实体）"
    if t == "service":
        r = result or {}
        if r.get("success"):
            return f"执行成功（耗时 {r.get('duration_ms')}ms）"
        return f"执行失败：{_short(r.get('error') or '未知错误')}"
    if t == "llm":
        r = result or {}
        text = r.get("text") or ""
        struct_count = len([k for k in r if k != "text" and k != "reasoning"])
        extra = f"，{struct_count} 个结构化字段" if struct_count else ""
        return f"已生成文本（{len(text)} 字符{extra}）"
    if t == "condition":
        return f"判断结果：{bool((result or {}).get('result'))}"
    if t == "code":
        r = result or {}
        if not r.get("success"):
            return f"代码执行失败：{_short(r.get('error') or '未知错误')}"
        struct_count = len([k for k in r if k not in FIXED_OUTPUTS.get("code", set())])
        extra = f"，{struct_count} 个自定义输出" if struct_count else ""
        return f"代码执行完成{extra}"
    return _short(result)


# ══════════════════════ LangGraph 动态建图 + 执行 ══════════════════════

def _merge_outputs(left: dict | None, right: dict | None) -> dict:
    """状态归并 reducer：并行节点同时写 outputs 时按 node_id 键合并。"""
    return {**(left or {}), **(right or {})}


class GraphState(TypedDict):
    """图状态：start 输入 + 各节点输出累积表（并行写自动合并）。"""
    start: dict
    outputs: Annotated[dict, _merge_outputs]


class _Runtime:
    """一次工作流运行的上下文：SSE 事件队列、节点状态表、DB 会话。

    每个 LangGraph 节点闭包持有同一个 _Runtime 实例，执行时经队列发事件，
    主循环（run_stream）负责把队列转成 SSE 流。
    """

    def __init__(self, run_id: str, nodes: list, edges: list):
        self.run_id = run_id
        # 前端保存时配置放在 node.data.config；统一提升到 node.config，避免各执行函数取空配置
        self.node_by_id = {}
        for n in nodes:
            node = dict(n)
            if "config" not in node and isinstance(node.get("data"), dict):
                node["config"] = node["data"].get("config") or {}
            self.node_by_id[n["id"]] = node
        self.edges = edges
        self.events: asyncio.Queue = asyncio.Queue()
        self.node_states: dict = {}
        self.db = None  # 由 run_stream 的执行任务持有并关闭
        self.finished = False

    def emit(self, payload: dict):
        self.events.put_nowait(payload)


def _node_input_view(node: dict, context: dict):
    """构造节点的「输入视图」：渲染后的关键入参（问题/参数/条件），供日志展示。"""
    t = node.get("type")
    cfg = node.get("config") or {}
    if t == "agent":
        return {"query": render(cfg.get("query_template", ""), context)}
    if t in ("service", "code"):
        return {"params": render(cfg.get("params") or {}, context)}
    if t == "llm":
        return {"prompt": render(cfg.get("prompt_template", ""), context)}
    if t == "condition":
        return {
            "left": render(cfg.get("left"), context),
            "op": cfg.get("operator", "=="),
            "right": render(cfg.get("right"), context),
        }
    return {}


def _make_node_fn(rt: _Runtime, node: dict):
    """把业务节点包装成 LangGraph 节点函数：执行 + 发 SSE 事件 + 记状态。"""
    nid = node["id"]

    async def fn(state: GraphState) -> dict:
        title = node.get("title") or node["type"]
        context = {**state.get("outputs", {}), "start": state.get("start", {})}
        logger.info("[run %s] 节点开始 %s (%s)", rt.run_id, nid, title)
        input_view = _truncate_output(_node_input_view(node, context))
        rt.emit({
            "type": "node_started", "node_id": nid, "title": title,
            "input": input_view,
        })
        rt.node_states[nid] = {"status": "running", "input": input_view, "title": title, "started_at": datetime.now().isoformat()}
        t0 = time.monotonic()

        # 智能体节点流式执行：token / reasoning 实时下发，同时保持最终 result 一致
        accumulated: list[str] = []
        reasoning_accumulated: list[str] = []
        first_token_emitted = False
        last_progress_emit = 0.0

        def _emit_progress(payload: dict):
            rt.emit({
                "type": "node_progress", "node_id": nid, "title": title,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                **payload,
            })

        def _on_step(step: str):
            _emit_progress({"step": step})

        # 智能体固定输出 answer，大模型节点固定输出 text；流式进度统一用 outputKey 构造
        output_key = "answer" if node.get("type") == "agent" else "text"

        def _on_token(token: str):
            nonlocal first_token_emitted, last_progress_emit
            accumulated.append(token)
            now = time.monotonic()
            # 每 80ms 最多发一次 progress，避免高频事件导致前端响应式批处理无法及时渲染
            if now - last_progress_emit < 0.08:
                return
            last_progress_emit = now
            _emit_progress({
                "output": {output_key: "".join(accumulated), "reasoning": "".join(reasoning_accumulated)},
                "step": "生成中..." if accumulated else "调用大模型",
            })
            if not first_token_emitted:
                first_token_emitted = True
                logger.info("[run %s] %s节点 %s 首次 token 已下发", rt.run_id, node.get("type"), nid)

        def _on_reasoning(token: str):
            nonlocal last_progress_emit
            reasoning_accumulated.append(token)
            now = time.monotonic()
            if now - last_progress_emit < 0.08:
                return
            last_progress_emit = now
            _emit_progress({
                "output": {output_key: "".join(accumulated), "reasoning": "".join(reasoning_accumulated)},
                "step": "思考中...",
            })

        if node.get("type") == "agent":
            logger.info("[run %s] 使用流式执行智能体节点 %s", rt.run_id, nid)
            task = asyncio.create_task(_exec_agent_stream(node.get("config") or {}, context, rt.db, _on_token, _on_step, _on_reasoning))
        elif node.get("type") == "llm":
            logger.info("[run %s] 使用流式执行大模型节点 %s", rt.run_id, nid)
            task = asyncio.create_task(_exec_llm_stream(node.get("config") or {}, context, _on_token, _on_step, _on_reasoning))
        else:
            task = asyncio.create_task(_execute_node(node, context, rt.db))
        try:
            # 非流式节点：执行期间每 2s 发一次 node_progress 心跳
            # 流式节点：token 到达时已由 _on_token 持续发送，这里只需等待完成
            while True:
                done, _ = await asyncio.wait({task}, timeout=2.0)
                if done:
                    break
                if node.get("type") != "agent":
                    rt.emit({
                        "type": "node_progress", "node_id": nid, "title": title,
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    })
            result = task.result()
            dur = int((time.monotonic() - t0) * 1000)
            projected = _project_output(node, result)
            if isinstance(result, dict) and node.get("type") in ("agent", "llm"):
                result.setdefault("reasoning", "".join(reasoning_accumulated))
            rt.node_states[nid] = {
                "status": "succeeded", "output": projected, "duration_ms": dur,
                "summary": _summarize(node, projected), "title": title,
            }
            logger.info("[run %s] 节点完成 %s (%s) %dms", rt.run_id, nid, title, dur)
            rt.emit({
                "type": "node_finished", "node_id": nid, "title": title,
                "summary": _summarize(node, projected),
                "duration_ms": dur,
                "output": _truncate_output(projected),
            })
            return {"outputs": {nid: projected}}
        except Exception as e:
            dur = int((time.monotonic() - t0) * 1000)
            rt.node_states[nid] = {"status": "failed", "error": str(e), "duration_ms": dur, "title": title}
            logger.error("[run %s] 节点失败 %s (%s) %dms: %s", rt.run_id, nid, title, dur, e, exc_info=True)
            rt.emit({"type": "node_failed", "node_id": nid, "title": title, "error": str(e), "duration_ms": dur})
            raise  # 上抛 → LangGraph 终止整图，fail-fast 与旧引擎一致

    return fn


def _make_condition_router(rt: _Runtime, node: dict):
    """条件节点路由函数：按 result 返回 true/false 分支的目标节点列表（多条则并行扇出）。"""
    nid = node["id"]
    succ = {"true": [], "false": []}
    for e in rt.edges:
        if e["source"] == nid:
            handle = e.get("handle") or "default"
            succ["true" if handle == "true" else "false"].append(e["target"])

    def router(state: GraphState) -> list[str]:
        result = bool((state.get("outputs") or {}).get(nid, {}).get("result"))
        targets = succ["true"] if result else succ["false"]
        logger.info("[run %s] 条件路由 %s → %s (分支: %s)", rt.run_id, nid, targets, "true" if result else "false")
        return targets

    return router


def _build_graph(rt: _Runtime):
    """把 JSON 定义翻译成 StateGraph 并编译。

    语义对照（与旧引擎一致）：
    - 非条件节点单出边 → add_edge；多入边节点由任一到达边触发（LangGraph 原生行为）；
    - 条件节点 → add_conditional_edges 按 handle 路由，未选中分支的下游不执行；
    - 无出边的非 end 节点（如截断分支的末尾）自然终止，等价 END。
    """
    from langgraph.graph import END, START, StateGraph

    logger.info("[run %s] 建图: %d 节点, %d 边", rt.run_id, len(rt.node_by_id), len(rt.edges))
    g = StateGraph(GraphState)
    nodes = list(rt.node_by_id.values())
    start_id = next((n["id"] for n in nodes if n["type"] == "start"), None)
    if not start_id:
        raise ValueError("工作流缺少「开始」节点")

    # 全部节点注册（start 也是真实节点：执行器直接返回输入，天然产出事件）
    for node in nodes:
        g.add_node(node["id"], _make_node_fn(rt, node))

    succ_of: dict[str, list[dict]] = {}
    for e in rt.edges:
        succ_of.setdefault(e["source"], []).append(e)

    g.add_edge(START, start_id)
    for nid, outs in succ_of.items():
        node = rt.node_by_id[nid]
        if node["type"] == "condition":
            g.add_conditional_edges(nid, _make_condition_router(rt, node))
        else:
            for e in outs:
                g.add_edge(nid, e["target"])
    for node in nodes:
        if node["type"] == "end":
            g.add_edge(node["id"], END)

    compiled = g.compile()
    logger.info("[run %s] 图编译完成", rt.run_id)
    return compiled


async def run_stream(workflow_id: str, definition: dict, inputs: dict,
                   trigger_source: Optional[str] = None, schedule_id: Optional[str] = None):
    """执行工作流，逐事件 yield SSE 字符串（事件契约与旧引擎完全一致）。

    trigger_source / schedule_id 用于定时调度模块标记运行来源（写入 workflow_runs）。
    """
    started = time.monotonic()
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []

    # 建运行记录
    run_id = None
    async with async_session() as db:
        run = WorkflowRun(
            workflow_id=workflow_id,
            status="running",
            inputs=json.dumps(inputs or {}, ensure_ascii=False, default=str),
            node_states="{}",
            started_at=datetime.now().isoformat(),
            trigger_source=trigger_source,
            schedule_id=schedule_id,
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    logger.info("[run %s] 工作流开始执行 workflow=%s, %d 节点, %d 边, inputs=%s",
                 run_id, workflow_id, len(nodes), len(edges), list((inputs or {}).keys()))
    yield _sse({"type": "workflow_started", "run_id": run_id, "nodes": len(nodes)})

    rt = _Runtime(run_id, nodes, edges)
    status = "succeeded"
    failed = None
    outputs: dict = {}

    # 建图失败（环/缺 start 等）→ 直接失败收尾
    try:
        graph = _build_graph(rt)
    except Exception as e:
        failed = str(e)
        status = "failed"
        logger.error("[run %s] 建图失败: %s", run_id, e, exc_info=True)
        await _finalize_run(run_id, status, {}, rt.node_states, failed, started)
        yield _sse({"type": "workflow_finished", "status": status, "outputs": {}, "duration_ms": 0, "error": failed})
        yield "data: [DONE]\n\n"
        return

    async def _drive():
        """后台驱动 LangGraph 执行；节点事件经 rt.events 队列送出。"""
        nonlocal failed, status
        try:
            async with async_session() as db:
                rt.db = db
                config = {"configurable": {"thread_id": f"wf-{run_id}"}}  # 预留 checkpoint 恢复
                async for _ in graph.astream(
                    {"start": inputs or {}, "outputs": {}},
                    config=config,
                    stream_mode="updates",
                ):
                    pass  # 事件已由节点闭包经队列发出，这里只推进执行
        except Exception as e:
            failed = f"{e}"
            status = "failed"
        finally:
            rt.finished = True

    runner = asyncio.create_task(_drive())

    # 主循环：转发事件队列，直到图执行结束
    while True:
        if rt.finished and rt.events.empty():
            break
        try:
            payload = await asyncio.wait_for(rt.events.get(), timeout=0.2)
            yield _sse(payload)
        except asyncio.TimeoutError:
            continue
    await runner

    # 补发未触达节点（条件分支未走的部分）的 node_skipped 事件
    for nid, n in rt.node_by_id.items():
        if rt.node_states.get(nid, {}).get("status") is None:
            rt.node_states[nid] = {"status": "skipped"}
            yield _sse({"type": "node_skipped", "node_id": nid, "title": n.get("title") or nid})

    # 汇总所有已执行 end 节点的输出
    for n in nodes:
        if n["type"] == "end":
            st = rt.node_states.get(n["id"])
            if st and st.get("status") == "succeeded":
                for k, v in (st.get("output") or {}).items():
                    outputs[k] = v

    duration_ms = int((time.monotonic() - started) * 1000)
    await _finalize_run(run_id, status, outputs, rt.node_states, failed, started, duration_ms)

    yield _sse({
        "type": "workflow_finished", "status": status,
        "outputs": outputs, "duration_ms": duration_ms, "error": failed,
    })
    yield "data: [DONE]\n\n"


async def _finalize_run(run_id, status: str, outputs: dict, node_states: dict, failed, started, duration_ms=None):
    """运行结束落库；失败不影响 SSE 流。"""
    if duration_ms is None:
        duration_ms = int((time.monotonic() - started) * 1000)
    try:
        async with async_session() as db:
            row = await db.get(WorkflowRun, run_id)
            if row:
                row.status = status
                row.outputs = json.dumps(outputs, ensure_ascii=False, default=str)
                row.node_states = json.dumps(node_states, ensure_ascii=False, default=str)
                row.error = failed or ""
                row.finished_at = datetime.now().isoformat()
                row.duration_ms = duration_ms
                await db.commit()
                # 写入新记录后裁剪，保持每个工作流仅保留最近 N 次
                from services.workflow_run_service import WorkflowRunService
                workflow_id = row.workflow_id
                await WorkflowRunService.trim(db, workflow_id)
    except Exception:
        pass
