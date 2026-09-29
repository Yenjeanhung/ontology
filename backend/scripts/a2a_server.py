# -*- coding: utf-8 -*-
"""A2A 服务器：平台对外提供「智能体服务」（Agent↔Agent 横向协作出口）。

与 scripts/mcp_server.py（工具粒度）互补：这里按 A2A 协议（JSON-RPC 2.0
over HTTP，AgentCard 名片发现 + Task/Artifact 交付）以**任务粒度**对外——
外部 A2A 客户端/平台发来一句任务，直接回 Task.artifacts 工件。

技能面（AgentCard.skills）：
- kb_search / graph_search / data_query：与 MCP 暴露同一批内置工具
  （services/tool_registry.py，一处能力两种消费）；
- agent:<id>：智能体配置页的自定义智能体上卡（人设 + 工具白名单，
  绑定知识库的检索语义由 kb_search 工具承接），经 run_tool_loop 执行。

用法（cwd=backend）：
    python scripts/a2a_server.py                # http://127.0.0.1:9801/
    python scripts/a2a_server.py --port 9801 --token <secret>

鉴权：--token / settings.A2A_EXPOSE_TOKEN（Bearer）；名片（/.well-known/*）
按 A2A 惯例公开，RPC 端点校验。任务表为内存兜底实现（重启即清，够
P0/P1 同步交付用；长任务持久化对齐 checkpointer 属 P2）。
"""

import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request          # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from config import settings                   # noqa: E402

_PROTOCOL_VERSION = "0.3.0"

app = FastAPI(title="KnowSource A2A Server", version="1.0")

# 内存任务表：task_id → task（P0/P1 兜底；P2 落 agent_runs 对齐断点恢复）
_TASKS: dict = {}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _token_ok(request: Request, required: str) -> bool:
    if not required:
        return True
    auth = request.headers.get("authorization") or ""
    return auth == f"Bearer {required}"


def _text_of(parts: list) -> str:
    """入站 message.parts → 任务文本（TextPart 取 text，DataPart 序列化）。"""
    out = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        if p.get("text"):
            out.append(str(p["text"]))
        elif p.get("data") is not None:
            try:
                out.append(json.dumps(p["data"], ensure_ascii=False))
            except (TypeError, ValueError):
                pass
    return "\n".join(t for t in (s.strip() for s in out) if t)


def _keywords(text: str, limit: int = 6) -> list[str]:
    """任务文本 → 关键词（jieba 可选，退化按空白/标点切分）。"""
    words: list[str] = []
    try:
        import jieba
        words = [w.strip() for w in jieba.lcut(text or "")]
    except Exception:
        import re
        words = re.split(r"[\s，。；、,.;:!?？！]+", text or "")
    seen, out = set(), []
    for w in words:
        w = w.strip()
        if len(w) >= 2 and w not in seen and not w.isdigit():
            seen.add(w)
            out.append(w)
        if len(out) >= limit:
            break
    return out


# ── AgentCard（服务发现） ────────────────────────────────────

async def _build_card(base_url: str) -> dict:
    skills = [
        {"id": "kb_search", "name": "知识库检索",
         "description": "跨全部知识库的向量检索，返回语料分片与出处",
         "tags": ["kb", "rag"]},
        {"id": "graph_search", "name": "图谱核验",
         "description": "实体图谱关键词检索：命中实体 + 邻接关系事实",
         "tags": ["graph"]},
        {"id": "data_query", "name": "台账数据查询",
         "description": "实体台账结构化查询：总量/分类聚合统计 + 明细记录",
         "tags": ["data"]},
    ]
    try:   # 配置页自定义智能体上卡（agent:<id>）；查库失败仅降级不崩
        from sqlalchemy import select
        from database import async_session
        from models import Agent
        async with async_session() as db:
            rows = (await db.execute(
                select(Agent).where(Agent.is_enabled == 1, Agent.is_preset == 0)
                .order_by(Agent.name)
            )).scalars().all()
        skills += [{
            "id": f"agent:{r.id}",
            "name": r.name,
            "description": (r.description or "配置页自定义智能体（人设+工具）").strip()[:120],
            "tags": ["custom", "agent"],
        } for r in rows]
    except Exception:
        pass
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "name": "knowsource-agents",
        "description": "知识本体平台智能体服务：知识检索 / 图谱核验 / 台账查询 / 自定义智能体",
        "url": base_url,
        "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": False,
                         "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": skills,
    }


@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
async def agent_card(request: Request):
    base = str(request.base_url).rstrip("/")
    return JSONResponse(await _build_card(base + "/"))


# ── 技能执行 ─────────────────────────────────────────────────

async def _run_skill(skill_id: str, text: str) -> tuple[str, list[dict]]:
    """技能路由 → (给调用方的文本结论, artifacts 工件列表)。"""
    from services.tool_registry import (builtin_data_query, builtin_graph_search,
                                        builtin_kb_search)
    if skill_id == "kb_search":
        res = await builtin_kb_search(query=text)
        items = res.get("items") or []
        text_out = "\n".join(
            f"[{i + 1}] {it.get('kb_name', '')}·{it.get('source', '')}: "
            f"{str(it.get('content', ''))[:160]}"
            for i, it in enumerate(items[:5])) or str(res.get("note") or "无命中")
        return text_out, [{"artifactId": _new_id("art"), "name": "kb_search 结果",
                           "parts": [{"kind": "text", "text": text_out},
                                     {"kind": "data", "data": res}]}]
    if skill_id == "graph_search":
        res = await builtin_graph_search(keywords=_keywords(text))
        ents = res.get("entities") or []
        text_out = "；".join(
            f"{e.get('name', '')}（{e.get('entity_type', '')}）"
            for e in ents[:6]) or str(res.get("note") or "图谱无命中")
        return f"图谱命中 {len(ents)} 个实体：{text_out}", [
            {"artifactId": _new_id("art"), "name": "graph_search 结果",
             "parts": [{"kind": "text", "text": text_out},
                       {"kind": "data", "data": res}]}]
    if skill_id == "data_query":
        res = await builtin_data_query(keywords=_keywords(text))
        total = (res.get("stats") or {}).get("total")
        text_out = f"台账命中 {total if total is not None else '0'} 条" + \
            str(res.get("note") or "")
        return text_out, [{"artifactId": _new_id("art"), "name": "data_query 结果",
                           "parts": [{"kind": "text", "text": text_out},
                                     {"kind": "data", "data": res}]}]
    if skill_id.startswith("agent:"):
        return await _run_custom_agent(skill_id.split(":", 1)[1], text)
    raise ValueError(f"未知技能 {skill_id}，可用：kb_search/graph_search/data_query/agent:<id>")


async def _run_custom_agent(agent_id: str, text: str) -> tuple[str, list[dict]]:
    """agent:<id>：配置页智能体（人设 + 工具白名单）单轮执行。"""
    from sqlalchemy import select
    from database import async_session
    from models import Agent
    from providers.llm import create_llm
    from services.agent_loop import run_tool_loop
    from services.tool_registry import PlatformTools

    async with async_session() as db:
        row = (await db.execute(
            select(Agent).where(Agent.id == agent_id, Agent.is_enabled == 1)
        )).scalar_one_or_none()
    if row is None:
        raise ValueError(f"智能体不存在或已停用：{agent_id}")
    llm = create_llm()
    if llm is None:
        raise RuntimeError("LLM 未配置（.env OPENAI_* / 本地服务）")

    include = None
    raw_names = getattr(row, "tool_names", None)
    try:
        parsed = json.loads(raw_names) if isinstance(raw_names, str) else raw_names
        if isinstance(parsed, list) and parsed:
            include = [str(x) for x in parsed]
    except (TypeError, ValueError):
        include = None

    async with PlatformTools(include=include) as pt:
        loop = await run_tool_loop(
            llm, pt.registry,
            system=(row.system_prompt or "你是知识本体平台的智能体助手。")
                   + "\n回答使用中文，基于工具返回的真实数据，禁止编造。",
            user=text,
        )
    answer = (loop.final_text or "").strip() or "（智能体未返回内容）"
    calls = [{"name": getattr(c, "name", ""), "arguments": getattr(c, "arguments", None)}
             for c in (loop.calls or [])]
    return answer, [{"artifactId": _new_id("art"), "name": row.name,
                     "parts": [{"kind": "text", "text": answer},
                               {"kind": "data", "data": {"tool_calls": calls}}]}]


# ── Task 组装 ────────────────────────────────────────────────

def _new_task(context_id: str) -> dict:
    return {"id": _new_id("t"), "contextId": context_id, "kind": "task",
            "status": {"state": "submitted", "timestamp": _now()},
            "artifacts": [], "history": [], "metadata": {}}


def _set_state(task: dict, state: str, message: str = "") -> None:
    task["status"] = {"state": state, "timestamp": _now()}
    if message:
        task["status"]["message"] = {"role": "agent", "kind": "message",
                                     "parts": [{"kind": "text", "text": message}]}


# ── JSON-RPC 2.0 端点（message/send | message/stream | tasks/*） ──

def _rpc_err(req_id, code: int, msg: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                         "error": {"code": code, "message": msg}})


@app.post("/")
async def rpc(request: Request):
    required = (getattr(settings, "A2A_EXPOSE_TOKEN", "") or "").strip()
    if not _token_ok(request, required):
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32001, "message": "未授权（Bearer 令牌缺失或不匹配）"}},
                            status_code=401)
    try:
        body = await request.json()
    except Exception:
        return _rpc_err(None, -32700, "请求体不是合法 JSON")
    method = str(body.get("method") or "")
    params = body.get("params") or {}
    req_id = body.get("id")

    if method == "message/send":
        msg = params.get("message") or {}
        text = _text_of(msg.get("parts"))
        skill = str(((msg.get("metadata") or {}).get("skillId")) or "")
        task = _new_task(str(msg.get("contextId") or _new_id("c")))
        _TASKS[task["id"]] = task
        _set_state(task, "working")
        try:
            text_out, artifacts = await _run_skill(skill, text)
            task["artifacts"] = artifacts
            _set_state(task, "completed", text_out)
        except Exception as exc:
            _set_state(task, "failed", f"{type(exc).__name__}: {exc}")
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": task})

    if method == "message/stream":
        msg = params.get("message") or {}
        text = _text_of(msg.get("parts"))
        skill = str(((msg.get("metadata") or {}).get("skillId")) or "")
        task = _new_task(str(msg.get("contextId") or _new_id("c")))
        _TASKS[task["id"]] = task
        tid, cid = task["id"], task["contextId"]

        async def _sse():
            def _status(state: str, final: bool, message: str = "") -> str:
                st = {"state": state, "timestamp": _now()}
                if message:
                    st["message"] = {"role": "agent", "kind": "message",
                                     "parts": [{"kind": "text", "text": message}]}
                return "data: " + json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"kind": "status-update", "taskId": tid,
                               "contextId": cid, "status": st, "final": final},
                }, ensure_ascii=False) + "\n\n"

            yield _status("working", False)
            try:
                text_out, artifacts = await _run_skill(skill, text)
            except Exception as exc:
                _set_state(task, "failed", f"{type(exc).__name__}: {exc}")
                yield _status("failed", True, task["status"].get("message", "")
                              or "任务失败")
                return
            for art in artifacts:
                task["artifacts"].append(art)
                yield "data: " + json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"kind": "artifact-update", "taskId": tid,
                               "contextId": cid, "artifact": art},
                }, ensure_ascii=False) + "\n\n"
            _set_state(task, "completed", text_out)
            yield _status("completed", True, text_out)

        return StreamingResponse(_sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    if method in ("tasks/get", "tasks/cancel"):
        task = _TASKS.get(str(params.get("id") or ""))
        if task is None:
            return _rpc_err(req_id, -32001, "任务不存在或已过期（内存任务表）")
        if method == "tasks/cancel" and task["status"]["state"] in ("submitted", "working"):
            _set_state(task, "canceled")
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": task})

    return _rpc_err(req_id, -32601, f"未知方法 {method}")


@app.get("/health")
async def health():
    return {"ok": True, "protocol": f"a2a/{_PROTOCOL_VERSION}"}


def _load_llm_config() -> None:
    """独立进程启动时把库里生效的模型配置载入 settings（agent:<id> 技能依赖）。"""
    import asyncio
    try:
        from database import async_session
        from services.config_service import load_active_into_settings

        async def _load():
            async with async_session() as db:
                await load_active_into_settings(db)

        asyncio.run(_load())
    except Exception:
        pass  # 库不可达时保持 .env 兜底


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser(description="KnowSource A2A Server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9801)
    ap.add_argument("--token", default="",
                    help="Bearer 令牌（缺省读 settings.A2A_EXPOSE_TOKEN）")
    args = ap.parse_args()
    if args.token:
        settings.A2A_EXPOSE_TOKEN = args.token
    _load_llm_config()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
