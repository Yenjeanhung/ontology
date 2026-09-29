# -*- coding: utf-8 -*-
"""开源风格 A2A 智能体（--role 起多个独立人设实例）。

A2A 生态现状：公网没有可直接注册的稳定开放端点，主流开源实现
（google/A2A 仓库 samples：LangGraph currency agent / ADK / CrewAI
示例等）都需自行 clone 部署。本脚本按同一协议（AgentCard 名片 +
JSON-RPC message/send|stream + Task/Artifact 交付）提供三个本地
开源风格智能体，供组队面板勾选体验横向协作：

    python scripts/a2a_open_agents.py --role writer     --port 9802
    python scripts/a2a_open_agents.py --role translator --port 9803
    python scripts/a2a_open_agents.py --role coder      --port 9804

技能全部为「单轮 LLM 直答」（providers.llm.create_llm，读 .env 主
模型配置），不依赖平台知识库/图谱/台账——体现 A2A 的不透明协作：
调用方只交付任务与接收工件，不感知对端内部实现。
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

# ── 角色注册表：人设 + 技能面（全部单轮 LLM） ─────────────────

ROLES: dict = {
    "writer": {
        "name": "open-writer",
        "description": "开源风格写作智能体：撰写 / 摘要 / 润色",
        "fallback_system": "你是专业写作助手，用中文输出高质量文本。",
        "skills": {
            "compose": {
                "name": "文案撰写", "tags": ["writing"],
                "description": "按主题与要求撰写文案 / 文章 / 邮件",
                "system": "你是专业文案撰写师。根据用户给定的主题、场景与要求，"
                          "直接产出成品文本（结构清晰、可直接使用），不要输出解释性废话。",
            },
            "summarize": {
                "name": "长文摘要", "tags": ["summarize"],
                "description": "把长文本压缩成要点摘要",
                "system": "你是摘要专家。把用户给的文本压缩为要点式摘要："
                          "先一行总括，再 3~5 条要点，保留关键数字与结论。",
            },
            "polish": {
                "name": "文本润色", "tags": ["writing", "edit"],
                "description": "润色改写，提升表达质量",
                "system": "你是文字润色师。改写用户文本使其更通顺、专业、有说服力，"
                          "直接输出润色后的成品，可在末尾用一行说明改动思路。",
            },
        },
    },
    "translator": {
        "name": "open-translator",
        "description": "开源风格翻译智能体：中英互译 / 译文校对",
        "fallback_system": "你是专业翻译，中英互译，译文自然流畅。",
        "skills": {
            "translate": {
                "name": "中英互译", "tags": ["translate"],
                "description": "中译英 / 英译中（自动识别方向）",
                "system": "你是专业译者。自动识别用户文本语言：中文译英文，英文译中文。"
                          "只输出译文，保留原文格式与术语准确性。",
            },
            "review": {
                "name": "译文校对", "tags": ["translate", "review"],
                "description": "校对译文质量并给出修改建议",
                "system": "你是翻译审校专家。用户会给出原文与译文（或仅译文），"
                          "逐点指出误译、生硬与术语问题，最后给出修订版译文。",
            },
        },
    },
    "coder": {
        "name": "open-coder",
        "description": "开源风格代码智能体：生成 / 审查 / 解释",
        "fallback_system": "你是资深工程师，输出可运行的代码与准确解释。",
        "skills": {
            "code_gen": {
                "name": "代码生成", "tags": ["code"],
                "description": "按需求生成代码片段",
                "system": "你是资深软件工程师。按需求直接给出可运行代码（Markdown 代码块），"
                          "附最少量的必要说明；未指明语言时默认 Python。",
            },
            "code_review": {
                "name": "代码审查", "tags": ["code", "review"],
                "description": "审查代码缺陷与改进点",
                "system": "你是代码审查专家。按 正确性 / 边界条件 / 性能 / 可读性 "
                          "逐项给出审查意见，重要问题标注 [严重]，最后给修订建议。",
            },
            "explain": {
                "name": "代码解释", "tags": ["code", "explain"],
                "description": "逐段解释代码逻辑",
                "system": "你是技术讲解者。逐段解释用户给的代码：先一句概括整体功能，"
                          "再按逻辑块解释，指出关键细节与潜在坑。",
            },
        },
    },
}

_MEMORY_TASKS: dict = {}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _token_ok(request: Request, required: str) -> bool:
    if not required:
        return True
    return (request.headers.get("authorization") or "") == f"Bearer {required}"


def _text_of(parts: list) -> str:
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


# ── AgentCard（服务发现） ────────────────────────────────────

def _build_card(base_url: str, role: str) -> dict:
    conf = ROLES[role]
    skills = [{"id": sid, "name": sk["name"], "description": sk["description"],
               "tags": sk["tags"]} for sid, sk in conf["skills"].items()]
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "name": conf["name"],
        "description": conf["description"],
        "url": base_url,
        "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": False,
                         "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
    }


# ── 技能执行：单轮 LLM 直答 ──────────────────────────────────

async def _run_skill(role: str, skill_id: str, text: str) -> tuple[str, list[dict]]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from providers.llm import create_llm
    conf = ROLES[role]
    skill = conf["skills"].get(skill_id)
    if skill_id and skill is None:
        raise ValueError(f"未知技能 {skill_id}，可用：{'/'.join(conf['skills'])}")
    llm = create_llm()
    if llm is None:
        raise RuntimeError("LLM 未配置（.env OPENAI_* / 本地服务）")
    resp = await llm.ainvoke([
        SystemMessage(content=skill["system"] if skill else conf["fallback_system"]),
        HumanMessage(content=text or "（空任务）"),
    ])
    out = str(resp.content or "").strip() or "（远端智能体未返回内容）"
    return out, [{"artifactId": _new_id("art"),
                  "name": skill["name"] if skill else conf["name"],
                  "parts": [{"kind": "text", "text": out}]}]


# ── App 工厂（role 固化进路由闭包） ──────────────────────────

def create_app(role: str) -> FastAPI:
    conf = ROLES[role]
    app = FastAPI(title=f"A2A Open Agent · {conf['name']}", version="1.0")

    @app.get("/.well-known/agent-card.json")
    @app.get("/.well-known/agent.json")
    async def agent_card(request: Request):
        return JSONResponse(_build_card(str(request.base_url).rstrip("/") + "/", role))

    def _rpc_err(req_id, code: int, msg: str) -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                             "error": {"code": code, "message": msg}})

    @app.post("/")
    async def rpc(request: Request):
        required = (getattr(settings, "A2A_EXPOSE_TOKEN", "") or "").strip()
        if not _token_ok(request, required):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32001, "message": "未授权（Bearer 令牌缺失或不匹配）"}},
                status_code=401)
        try:
            body = await request.json()
        except Exception:
            return _rpc_err(None, -32700, "请求体不是合法 JSON")
        method = str(body.get("method") or "")
        params = body.get("params") or {}
        req_id = body.get("id")

        def _new_task(context_id: str) -> dict:
            return {"id": _new_id("t"), "contextId": context_id, "kind": "task",
                    "status": {"state": "submitted", "timestamp": _now()},
                    "artifacts": [], "history": [], "metadata": {}}

        if method == "message/send":
            msg = params.get("message") or {}
            text = _text_of(msg.get("parts"))
            skill = str((msg.get("metadata") or {}).get("skillId") or "")
            task = _new_task(str(msg.get("contextId") or _new_id("c")))
            _MEMORY_TASKS[task["id"]] = task
            task["status"]["state"] = "working"
            try:
                text_out, artifacts = await _run_skill(role, skill, text)
                task["artifacts"] = artifacts
                task["status"] = {"state": "completed", "timestamp": _now(),
                                  "message": {"role": "agent", "kind": "message",
                                              "parts": [{"kind": "text", "text": text_out}]}}
            except Exception as exc:
                task["status"] = {"state": "failed", "timestamp": _now(),
                                  "message": {"role": "agent", "kind": "message",
                                              "parts": [{"kind": "text",
                                                         "text": f"{type(exc).__name__}: {exc}"}]}}
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": task})

        if method == "message/stream":
            msg = params.get("message") or {}
            text = _text_of(msg.get("parts"))
            skill = str((msg.get("metadata") or {}).get("skillId") or "")
            task = _new_task(str(msg.get("contextId") or _new_id("c")))
            _MEMORY_TASKS[task["id"]] = task
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
                    text_out, artifacts = await _run_skill(role, skill, text)
                except Exception as exc:
                    yield _status("failed", True, f"{type(exc).__name__}: {exc}")
                    return
                for art in artifacts:
                    task["artifacts"].append(art)
                    yield "data: " + json.dumps({
                        "jsonrpc": "2.0", "id": req_id,
                        "result": {"kind": "artifact-update", "taskId": tid,
                                   "contextId": cid, "artifact": art},
                    }, ensure_ascii=False) + "\n\n"
                yield _status("completed", True, text_out)

            return StreamingResponse(_sse(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})

        if method in ("tasks/get", "tasks/cancel"):
            task = _MEMORY_TASKS.get(str(params.get("id") or ""))
            if task is None:
                return _rpc_err(req_id, -32001, "任务不存在或已过期（内存任务表）")
            if method == "tasks/cancel" and task["status"]["state"] in ("submitted", "working"):
                task["status"] = {"state": "canceled", "timestamp": _now()}
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": task})

        return _rpc_err(req_id, -32601, f"未知方法 {method}")

    @app.get("/health")
    async def health():
        return {"ok": True, "role": role, "protocol": f"a2a/{_PROTOCOL_VERSION}"}

    return app


def _load_llm_config() -> None:
    """独立进程启动时把库里生效的模型配置载入 settings（对齐 8000 后端 lifespan）。

    主模型走配置方案存库（is_active=1），.env 未必有 OPENAI_API_KEY；
    不加载则 create_llm() 恒为 None。
    """
    import asyncio
    try:
        from database import async_session
        from services.config_service import load_active_into_settings

        async def _load():
            async with async_session() as db:
                await load_active_into_settings(db)

        asyncio.run(_load())
    except Exception:
        pass  # 库不可达时保持 .env 兜底，LLM 缺配由技能层报错


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser(description="KnowSource Open-style A2A Agents")
    ap.add_argument("--role", required=True, choices=sorted(ROLES))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    _load_llm_config()
    uvicorn.run(create_app(args.role), host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
