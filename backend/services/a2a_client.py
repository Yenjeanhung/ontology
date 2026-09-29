# -*- coding: utf-8 -*-
"""A2A 客户端（Agent-to-Agent 协议，JSON-RPC over HTTP 薄实现）。

协议面小，不引官方 a2a-sdk（可选依赖思路同 OTel：SDK 缺失自动降级），
httpx 直连覆盖四个最稳定的方法：message/send、message/stream、
tasks/get、tasks/cancel + 服务发现（/.well-known/agent-card.json，
404 回落 v0.2 路径 agent.json）。

哲学与 MCP/数据源一致：**错误永远是数据，不抛异常**——所有函数返回
{ok, error, ...}，网络/协议失败不中断调用方（节点层据此降级）。
"""

import json
import time
import uuid
from typing import AsyncIterator, Optional

import httpx
from urllib.parse import urlsplit

from config import settings


def _is_local(url: str) -> bool:
    """回环地址判定：走代理会把 127.0.0.1 请求转发出去 → 502 Bad Gateway。"""
    try:
        return (urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")
    except Exception:
        return False


def _async_client(url: str, **kw) -> httpx.AsyncClient:
    """按目标地址决定是否信任环境代理：本地直连，外网保留代理。"""
    return httpx.AsyncClient(trust_env=not _is_local(url), **kw)


def _headers(token: str) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _rpc(endpoint: str, method: str, params: dict,
         token: str = "", timeout: float = 60.0) -> dict:
    return {
        "endpoint": endpoint,
        "payload": {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        "headers": _headers(token),
        "timeout": timeout,
    }


def _new_task_id() -> str:
    return f"t_{uuid.uuid4().hex[:10]}"


# ── 服务发现 ─────────────────────────────────────────────────

async def fetch_agent_card(base_url: str, token: str = "",
                           timeout: Optional[float] = None) -> dict:
    """拉取 AgentCard：v0.3 路径优先，404 回落 v0.2（agent.json）。

    返回 {ok, card, error, elapsed_ms}；失败 ok=false（探测接口直接可用）。
    """
    base_url = (base_url or "").strip().rstrip("/")
    timeout = float(timeout or getattr(settings, "A2A_CARD_TIMEOUT", 8.0))
    t0 = time.monotonic()
    if not base_url.lower().startswith(("http://", "https://")):
        return {"ok": False, "card": {}, "error": "base_url 必须以 http(s):// 开头",
                "elapsed_ms": 0}
    last_err = ""
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        try:
            async with _async_client(base_url + path, timeout=timeout,
                                     follow_redirects=True) as cli:
                resp = await cli.get(base_url + path, headers=_headers(token))
            if resp.status_code == 404:
                last_err = "404（名片路径不存在）"
                continue
            resp.raise_for_status()
            card = resp.json()
            if not isinstance(card, dict) or not card.get("name"):
                last_err = "名片格式不合法（缺 name）"
                continue
            return {"ok": True, "card": card, "error": "",
                    "elapsed_ms": int((time.monotonic() - t0) * 1000)}
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
    return {"ok": False, "card": {}, "error": last_err or "名片拉取失败",
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


# ── 任务执行 ─────────────────────────────────────────────────

def _parts_text(parts: list) -> str:
    """parts[] → 拼接文本（TextPart 取 text；DataPart 序列化 JSON）。"""
    out: list[str] = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        if p.get("kind") == "data" or ("data" in p and "text" not in p):
            try:
                out.append(json.dumps(p.get("data") or {}, ensure_ascii=False))
            except Exception:
                pass
        elif p.get("text"):
            out.append(str(p["text"]))
    return "\n".join(t for t in (s.strip() for s in out) if t)


def _parse_rpc_result(result) -> dict:
    """JSON-RPC result → 归一化 {state, text, artifacts, task_id}。

    result 可能是 Task（任务粒度交付）或 Message（直答，部分实现不发 Task）。
    """
    if not isinstance(result, dict):
        return {"state": "unknown", "text": "", "artifacts": [], "task_id": ""}
    if result.get("kind") == "message":
        return {"state": "completed", "text": _parts_text(result.get("parts")),
                "artifacts": [], "task_id": str(result.get("taskId") or "")}
    status = result.get("status") or {}
    state = str(status.get("state") or "unknown")
    artifacts = [a for a in (result.get("artifacts") or []) if isinstance(a, dict)]
    msg_text = _parts_text((status.get("message") or {}).get("parts"))
    return {"state": state, "text": msg_text, "artifacts": artifacts,
            "task_id": str(result.get("id") or "")}


async def send_task(base_url: str, text: str, skill_id: str = "",
                    token: str = "", card: Optional[dict] = None,
                    timeout: Optional[float] = None,
                    task_id: str = "") -> dict:
    """message/send 同步委派：阻塞直至终态（completed/failed/canceled）。

    返回 {ok, state, text, artifacts, task_id, error, elapsed_ms}。
    endpoint 取 AgentCard.url（名片声明的 RPC 根），缺省回落 base_url。
    """
    base_url = (base_url or "").strip().rstrip("/")
    card = card or {}
    endpoint = str(card.get("url") or base_url or "").rstrip("/")
    timeout = float(timeout or getattr(settings, "A2A_TIMEOUT", 60.0))
    t0 = time.monotonic()
    if not endpoint.lower().startswith(("http://", "https://")):
        return {"ok": False, "state": "unknown", "text": "", "artifacts": [],
                "task_id": "", "error": "远端地址不合法", "elapsed_ms": 0}
    message: dict = {
        "role": "user",
        "kind": "message",
        "messageId": _new_task_id(),
        "parts": [{"kind": "text", "text": text}],
    }
    if task_id:
        message["taskId"] = task_id        # 续跑同一任务（tasks/get 轮询前先 send 空文本触发）
    if skill_id:
        message["metadata"] = {"skillId": skill_id}
    req = _rpc(endpoint, "message/send", {"message": message}, token, timeout)
    try:
        async with _async_client(req["endpoint"], timeout=timeout + 5.0,
                                 follow_redirects=True) as cli:
            resp = await cli.post(req["endpoint"], json=req["payload"],
                                  headers=req["headers"])
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return {"ok": False, "state": "unknown", "text": "", "artifacts": [],
                "task_id": "",
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return {"ok": False, "state": "unknown", "text": "", "artifacts": [],
                "task_id": "", "error": f"远端 RPC 错误：{msg}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    parsed = _parse_rpc_result((body or {}).get("result"))
    ok = parsed["state"] in ("completed",) and (
        bool(parsed["text"]) or bool(parsed["artifacts"]))
    return {"ok": ok, **parsed,
            "error": "" if ok else f"任务未完成（state={parsed['state']}）",
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


async def get_task(base_url: str, task_id: str, token: str = "",
                   timeout: Optional[float] = None) -> dict:
    """tasks/get：长任务轮询（P2 push notification 前的取态入口）。"""
    endpoint = (base_url or "").strip().rstrip("/")
    timeout = float(timeout or getattr(settings, "A2A_TIMEOUT", 60.0))
    t0 = time.monotonic()
    req = _rpc(endpoint, "tasks/get", {"id": task_id}, token, timeout)
    try:
        async with _async_client(req["endpoint"], timeout=timeout,
                                 follow_redirects=True) as cli:
            resp = await cli.post(req["endpoint"], json=req["payload"],
                                  headers=req["headers"])
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return {"ok": False, "state": "unknown", "text": "", "artifacts": [],
                "task_id": task_id, "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return {"ok": False, "state": "unknown", "text": "", "artifacts": [],
                "task_id": task_id, "error": f"远端 RPC 错误：{msg}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    parsed = _parse_rpc_result((body or {}).get("result"))
    return {"ok": True, **parsed, "error": "",
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


async def stream_task(base_url: str, text: str, skill_id: str = "",
                      token: str = "", card: Optional[dict] = None,
                      timeout: Optional[float] = None,
                      ) -> AsyncIterator[dict]:
    """message/stream：SSE 逐帧 yield，供节点做过程可见（working 进度）。

    yield 帧：{type:"status", state, final} / {type:"artifact", artifact} /
    收尾 {type:"done", ok, state, text, artifacts, error, elapsed_ms}。
    远端不支持流式 / 任何失败 → 单帧 done 降级（调用方零特判）。
    """
    t0 = time.monotonic()
    base_url = (base_url or "").strip().rstrip("/")
    card = card or {}
    endpoint = str(card.get("url") or base_url or "").rstrip("/")
    timeout = float(timeout or getattr(settings, "A2A_TIMEOUT", 60.0))
    message: dict = {
        "role": "user", "kind": "message", "messageId": _new_task_id(),
        "parts": [{"kind": "text", "text": text}],
    }
    if skill_id:
        message["metadata"] = {"skillId": skill_id}
    req = _rpc(endpoint, "message/stream", {"message": message}, token, timeout)

    state, artifacts, text_out, err = "submitted", [], "", ""
    try:
        async with _async_client(req["endpoint"], timeout=timeout + 5.0,
                                 follow_redirects=True) as cli:
            async with cli.stream("POST", req["endpoint"], json=req["payload"],
                                  headers=req["headers"]) as resp:
                if resp.status_code >= 400:
                    err = f"HTTP {resp.status_code}"
                else:
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            evt = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        result = evt.get("result") or {}
                        kind = str(result.get("kind") or "")
                        if kind == "status-update":
                            state = str((result.get("status") or {}).get("state")
                                        or state)
                            msg = (result.get("status") or {}).get("message")
                            if isinstance(msg, dict) and not text_out:
                                text_out = _parts_text(msg.get("parts"))
                            final = bool(result.get("final"))
                            yield {"type": "status", "state": state, "final": final}
                            if final and state not in ("completed",):
                                err = err or f"任务未完成（state={state}）"
                        elif kind == "artifact-update":
                            art = result.get("artifact")
                            if isinstance(art, dict):
                                artifacts.append(art)
                                yield {"type": "artifact", "artifact": art}
                        elif kind == "task":
                            parsed = _parse_rpc_result(result)
                            state = parsed["state"] or state
                            artifacts.extend(parsed["artifacts"])
                            if parsed["text"] and not text_out:
                                text_out = parsed["text"]
    except Exception as exc:
        err = err or f"{type(exc).__name__}: {exc}"

    full_text = clip_text(text_out or "\n".join(
        artifact_text(a) for a in artifacts).strip())
    ok = (state == "completed" or bool(artifacts)) and not err and bool(full_text or artifacts)
    yield {"type": "done",
           "ok": ok,
           "state": state,
           "text": full_text if ok else "",
           "artifacts": artifacts if ok else [],
           "task_id": "",
           "error": err or ("" if ok else "远端未返回有效产出"),
           "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def artifact_text(artifact: dict) -> str:
    """工件 → 拼接文本（前端事实卡/黑板素材用）。"""
    return _parts_text(artifact.get("parts"))


def clip_text(text: str) -> str:
    """回灌黑板前截断（上限 settings.A2A_RESULT_MAX_CHARS，防黑板爆炸）。"""
    text = (text or "").strip()
    max_chars = int(getattr(settings, "A2A_RESULT_MAX_CHARS", 4000) or 4000)
    if len(text) > max_chars:
        text = text[:max_chars] + f"…（截断，原始 {len(text)} 字符）"
    return text
