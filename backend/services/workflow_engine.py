"""工作流执行引擎：拓扑调度 + 变量解析 + 逐节点执行 + SSE 进度。

调度模型：
- 先 Kahn 拓扑排序（同时检测环）；
- 按拓扑序逐个节点判断「是否被激活」：任一前驱激活且（条件分支）分支匹配即激活；
- 非条件节点单出边、条件节点 true/false 双出边；
- 任一节点抛错 → 整体失败，后续节点不再执行。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from database import async_session
from models import WorkflowRun
from providers.llm import create_llm
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


def _lookup(path: str, context: dict):
    cur = context
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


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


def _topo_order(nodes: list, edges: list) -> list[str]:
    id_set = {n["id"] for n in nodes}
    indegree = {nid: 0 for nid in id_set}
    adj: dict[str, list[str]] = {nid: [] for nid in id_set}
    for e in edges:
        indegree[e["target"]] += 1
        adj[e["source"]].append(e["target"])
    queue = [nid for nid in id_set if indegree[nid] == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for t in adj[nid]:
            indegree[t] -= 1
            if indegree[t] == 0:
                queue.append(t)
    if len(order) != len(id_set):
        raise ValueError("工作流存在循环")
    return order


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


async def _exec_agent(cfg: dict, context: dict, db) -> dict:
    query = str(render(cfg.get("query_template", ""), context))
    if cfg.get("agent_id"):
        agent = await AgentService.resolve(db, cfg["agent_id"])
        if not agent:
            raise RuntimeError("智能体不存在或已禁用")
        kb_id, skill_ids, persona = agent["kb_id"], agent["skill_ids"], agent["system_prompt"]
    else:
        kb_id = cfg.get("kb_id")
        skill_ids = cfg.get("skill_ids") or []
        persona = None
    if not kb_id:
        raise RuntimeError("智能体节点缺少 kb_id 或 agent_id")
    kb = await KBService.get(db, kb_id)
    if not kb:
        raise RuntimeError("知识库不存在")
    try:
        ontology_schema = await OntologyService.get_kb_extraction_constraints(db, kb_id)
    except Exception:
        ontology_schema = None
    skills = await SkillService.resolve(db, skill_ids)
    result = await OAGService.run(kb_id, query, kb["name"], ontology_schema, skills, persona)
    return {
        "answer": result["answer"],
        "chunks": result["chunks"],
        "entities": result["entities"],
        "subgraph": result.get("subgraph"),
    }


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
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt_text))
    resp = await llm.ainvoke(messages)
    return {"text": resp.content if hasattr(resp, "content") else str(resp)}


async def _exec_code(cfg: dict, context: dict) -> dict:
    params = render(cfg.get("params") or {}, context) or {}
    return await execute_service(
        code_text=cfg.get("code_text", ""),
        language=cfg.get("language", "python"),
        params=params,
        entity={},
        context={},
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


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _truncate_output(result):
    limit = settings.WORKFLOW_NODE_OUTPUT_LIMIT
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        s = str(result)
    if len(s) <= limit:
        return result
    return {"_truncated": True, "text": s[:limit] + f"\n…（共 {len(s)} 字符）"}


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
        text = (result or {}).get("text") or ""
        return f"已生成文本（{len(text)} 字符）"
    if t == "condition":
        return f"判断结果：{bool((result or {}).get('result'))}"
    if t == "code":
        r = result or {}
        if r.get("success"):
            return "代码执行完成"
        return f"代码执行失败：{_short(r.get('error') or '未知错误')}"
    return _short(result)


async def run_stream(workflow_id: str, definition: dict, inputs: dict):
    """执行工作流，逐事件 yield SSE 字符串。"""
    started = time.monotonic()
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []
    node_by_id = {n["id"]: n for n in nodes}

    preds: dict[str, list[tuple[str, str]]] = {n["id"]: [] for n in nodes}
    for e in edges:
        preds[e["target"]].append((e["source"], e.get("handle") or "default"))

    # 建运行记录
    run_id = None
    async with async_session() as db:
        run = WorkflowRun(
            workflow_id=workflow_id,
            status="running",
            inputs=json.dumps(inputs or {}, ensure_ascii=False, default=str),
            node_states="{}",
            started_at=datetime.now().isoformat(),
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    yield _sse({"type": "workflow_started", "run_id": run_id, "nodes": len(nodes)})

    try:
        order = _topo_order(nodes, edges)
    except ValueError as e:
        yield _sse({"type": "workflow_finished", "status": "failed", "error": str(e)})
        yield "data: [DONE]\n\n"
        return

    start_id = next((n["id"] for n in nodes if n["type"] == "start"), None)
    context: dict = {"start": inputs or {}}
    active: dict[str, bool] = {}
    node_states: dict = {}
    failed = None

    async with async_session() as db:
        for nid in order:
            node = node_by_id[nid]
            title = node.get("title") or node["type"]

            if nid == start_id:
                active[nid] = True
                context[nid] = inputs or {}
                node_states[nid] = {"status": "succeeded", "output": inputs or {}}
                yield _sse({
                    "type": "node_finished", "node_id": nid, "title": title,
                    "summary": _summarize(node, inputs or {}),
                    "duration_ms": 0,
                    "output": _truncate_output(inputs or {}),
                })
                continue

            # 激活判断：任一前驱激活且分支匹配
            activated = False
            for src, handle in preds[nid]:
                if not active.get(src):
                    continue
                src_node = node_by_id[src]
                if src_node["type"] == "condition":
                    src_result = context.get(src) or {}
                    if (handle == "true") == bool(src_result.get("result")):
                        activated = True
                        break
                else:
                    activated = True
                    break

            if not activated:
                active[nid] = False
                node_states[nid] = {"status": "skipped"}
                yield _sse({"type": "node_skipped", "node_id": nid, "title": title})
                continue

            yield _sse({"type": "node_started", "node_id": nid, "title": title})
            t0 = time.monotonic()
            try:
                result = await _execute_node(node, context, db)
                dur = int((time.monotonic() - t0) * 1000)
                context[nid] = result
                active[nid] = True
                node_states[nid] = {"status": "succeeded", "output": result, "duration_ms": dur}
                yield _sse({
                    "type": "node_finished", "node_id": nid, "title": title,
                    "summary": _summarize(node, result),
                    "duration_ms": dur,
                    "output": _truncate_output(result),
                })
            except Exception as e:
                dur = int((time.monotonic() - t0) * 1000)
                active[nid] = False
                context[nid] = {"error": str(e)}
                node_states[nid] = {"status": "failed", "error": str(e), "duration_ms": dur}
                failed = f"{title}: {e}"
                yield _sse({"type": "node_failed", "node_id": nid, "title": title, "error": str(e), "duration_ms": dur})
                break

    # 汇总结束节点输出
    outputs: dict = {}
    for n in nodes:
        if n["type"] == "end" and active.get(n["id"]):
            for k, v in (context.get(n["id"]) or {}).items():
                outputs[k] = v

    duration_ms = int((time.monotonic() - started) * 1000)
    status = "failed" if failed else "succeeded"

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

    yield _sse({
        "type": "workflow_finished", "status": status,
        "outputs": outputs, "duration_ms": duration_ms, "error": failed,
    })
    yield "data: [DONE]\n\n"
