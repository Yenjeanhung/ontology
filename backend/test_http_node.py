"""HTTP 请求节点（http node）测试。

覆盖（设计文档：doc/工作流/HTTP节点_功能设计.md）：
  1. 定义校验：URL/方法/鉴权/请求体/重试/超时/变量引用拓扑
  2. 请求组装：7 种方法、变量渲染、鉴权头、请求体四种类型
  3. 执行语义：JSON/非 JSON 解析、失败语义（默认宽松 fail_on_error=false）、
     重试（超时/5xx/429）、响应超限、非 http(s) scheme 拒绝
  4. 安全：密钥脱敏（mask_secret / mask_sensitive / _node_input_view）
  5. 测试接口：exec_http_node_test 的请求回显

网络层用 httpx.MockTransport 模拟，不出网。

运行：
    python -X utf8 test_http_node.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 使用独立临时库，避免污染开发数据（必须在导入 database / models 之前设置）
_TMP_DB = "./data/_test_http_node.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

import httpx

from services import workflow_engine


# ─────────────────────────── mock 注入 ───────────────────────────

def install_mock_transport(handler):
    """把 workflow_engine 内创建的 AsyncClient 全部替换为 MockTransport 版本。

    通过替换模块属性 httpx（workflow_engine 命名空间里的引用），测试后恢复。
    """
    real_httpx = workflow_engine.httpx

    class _MockedHttpxModule:
        """仅替换 AsyncClient，其余属性透传真模块（Timeout/TransportError 等）。"""

        class AsyncClient(real_httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=real_httpx.MockTransport(handler), **kwargs)

        def __getattr__(self, name):
            return getattr(real_httpx, name)

    workflow_engine.httpx = _MockedHttpxModule()
    return lambda: setattr(workflow_engine, "httpx", real_httpx)


def echo_handler(request: httpx.Request) -> httpx.Response:
    """回显服务：返回请求方法/路径/头/体的 JSON（urlencoded 表单自动解码为 dict）。"""
    body = request.content.decode("utf-8", errors="replace") if request.content else ""
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type and body:
        from urllib.parse import parse_qs
        body = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(body).items()}
    try:
        parsed = json.loads(body) if isinstance(body, str) and body else body
    except ValueError:
        parsed = None
    return httpx.Response(200, json={
        "method": request.method,
        "path": request.url.path,
        "query": dict(request.url.params),
        "headers": {k: v for k, v in request.headers.items()},
        "body": parsed if parsed is not None else body,
    })


# ─────────────────────────── 1. 定义校验 ───────────────────────────

def test_validate():
    from services.workflow_service import validate_definition

    def _node(nid, ntype, config=None):
        return {"id": nid, "type": ntype, "title": nid, "config": config or {}}

    def _def(cfg):
        return {"nodes": [
            _node("start", "start"),
            _node("h1", "http", cfg),
            _node("end", "end"),
        ], "edges": [
            {"id": "a", "source": "start", "target": "h1"},
            {"id": "b", "source": "h1", "target": "end"},
        ]}

    ok_cases = [
        ("最小配置", _def({"url": "https://api.example.com/users"})),
        ("URL 含变量", _def({"url": "https://api.example.com/users/{{start.id}}"})),
        ("完整配置", _def({
            "url": "https://api.example.com/search",
            "method": "POST",
            "params": {"q": "{{start.keyword}}"},
            "headers": {"X-Req": "1"},
            "auth": {"type": "bearer", "token": "t"},
            "body": {"type": "json", "data": {"a": "{{start.a}}"}},
            "timeout_seconds": 10, "max_retries": 2,
        })),
        ("json body 整串变量", _def({"url": "https://x.com/a", "method": "POST",
                                    "body": {"type": "json", "data": "{{start.payload}}"}})),
        ("text body", _def({"url": "https://x.com/a", "method": "POST",
                            "body": {"type": "text", "data": "hello {{start.name}}"}})),
    ]
    bad_cases = [
        ("缺 URL", _def({}), "URL"),
        ("非 http scheme", _def({"url": "ftp://x.com/a"}), "http://"),
        ("方法非法", _def({"url": "https://x.com/a", "method": "TRACE"}), "方法"),
        ("鉴权缺 token", _def({"url": "https://x.com/a", "auth": {"type": "bearer"}}), "token"),
        ("basic 缺 username", _def({"url": "https://x.com/a", "auth": {"type": "basic", "username": ""}}), "username"),
        ("api_key 缺 key", _def({"url": "https://x.com/a", "auth": {"type": "api_key", "value": "v"}}), "key"),
        ("api_key in 非法", _def({"url": "https://x.com/a",
                                  "auth": {"type": "api_key", "key": "k", "value": "v", "in": "cookie"}}), "in"),
        ("body 类型非法", _def({"url": "https://x.com/a", "body": {"type": "grpc", "data": "x"}}), "请求体类型"),
        ("json body 非对象非整串", _def({"url": "https://x.com/a", "method": "POST",
                                        "body": {"type": "json", "data": "abc{{x}}"}}), "字符串"),
        ("text body 空", _def({"url": "https://x.com/a", "method": "POST",
                               "body": {"type": "text", "data": "  "}}), "不能为空"),
        ("重试超界", _def({"url": "https://x.com/a", "max_retries": 6}), "0~5"),
        ("超时超界", _def({"url": "https://x.com/a", "timeout_seconds": 500}), "0~300"),
        ("引用非上游节点", _def({"url": "https://x.com/{{end.x}}"}), "上游"),
        ("引用自己", _def({"url": "https://x.com/a", "params": {"q": "{{h1.data.x}}"}}), "自己"),
    ]

    failed = 0
    for label, d in ok_cases:
        err = validate_definition(d)
        passed = err is None
        print(f"  [{'ok' if passed else 'FAIL'}] 校验通过：{label}")
        failed += 0 if passed else 1
    for label, d, keyword in bad_cases:
        err = validate_definition(d)
        passed = err is not None and keyword in err
        print(f"  [{'ok' if passed else 'FAIL'}] 校验拒绝：{label}" + (f" → {err}" if not passed else ""))
        failed += 0 if passed else 1
    return failed


# ─────────────────────────── 2/3. 执行语义 ───────────────────────────

async def _run(cfg, context=None, handler=echo_handler):
    restore = install_mock_transport(handler)
    try:
        return await workflow_engine._exec_http(cfg, context or {})
    finally:
        restore()


async def test_execution():
    checks = []

    # 7 种方法全部可达
    for m in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        out = await _run({"method": m, "url": "https://x.com/a"})
        checks.append((f"方法 {m} 执行成功", out["success"] is True and out["status_code"] == 200))
        if m != "HEAD":  # HEAD 无响应体，mock 仍回了 body，但 httpx 会丢弃；data 判断只看非 HEAD
            checks.append((f"方法 {m} 回显一致", (out["data"] or {}).get("method") == m))

    # 变量渲染：URL 路径 / query / header / json body
    ctx = {"start": {"id": "u_9", "kw": "年报", "n": 5, "trace": "t-1"}}
    out = await _run({
        "url": "https://x.com/users/{{start.id}}",
        "params": {"q": "{{start.kw}}", "top": "{{start.n}}"},
        "headers": {"X-Trace": "{{start.trace}}"},
        "method": "POST",
        "body": {"type": "json", "data": {"kw": "{{start.kw}}", "n": "{{start.n}}"}},
    }, ctx)
    d = out["data"]
    checks.append(("URL 路径变量", d["path"] == "/users/u_9"))
    checks.append(("query 变量", d["query"].get("q") == "年报" and d["query"].get("top") == "5"))
    checks.append(("header 变量", d["headers"].get("x-trace") == "t-1"))
    checks.append(("json body 嵌入变量", d["body"] == {"kw": "年报", "n": 5}))  # 整串引用保留原类型

    # 整串变量保留类型：{{start.n}} 单独作为 json body → 仍是数字 5
    out = await _run({"url": "https://x.com/a", "method": "POST",
                      "body": {"type": "json", "data": "{{start.n}}"}}, ctx)
    checks.append(("整串变量保留类型", out["data"]["body"] == 5))

    # 鉴权三种
    out = await _run({"url": "https://x.com/a", "auth": {"type": "bearer", "token": "tok-123"}}, ctx)
    checks.append(("bearer 头", out["data"]["headers"].get("authorization") == "Bearer tok-123"))
    out = await _run({"url": "https://x.com/a", "auth": {"type": "basic", "username": "u", "password": "p"}}, ctx)
    import base64
    expect = "Basic " + base64.b64encode(b"u:p").decode()
    checks.append(("basic 头", out["data"]["headers"].get("authorization") == expect))
    out = await _run({"url": "https://x.com/a",
                      "auth": {"type": "api_key", "key": "X-API-Key", "value": "ak", "in": "header"}}, ctx)
    checks.append(("api_key header", out["data"]["headers"].get("x-api-key") == "ak"))
    out = await _run({"url": "https://x.com/a",
                      "auth": {"type": "api_key", "key": "ak", "value": "v", "in": "query"}}, ctx)
    checks.append(("api_key query", out["data"]["query"].get("ak") == "v"))

    # form body
    out = await _run({"url": "https://x.com/a", "method": "POST",
                      "body": {"type": "form", "data": {"a": "1", "b": "{{start.kw}}"}}}, ctx)
    checks.append(("form body urlencoded", out["data"]["body"] == {"a": "1", "b": "年报"}))

    # 非 JSON 响应 → text 兜底，data 为 null
    def html_handler(request):
        return httpx.Response(200, text="<html>hi</html>",
                              headers={"content-type": "text/html; charset=utf-8"})
    out = await _run({"url": "https://x.com/a"}, handler=html_handler)
    checks.append(("HTML 落 text", out["text"] == "<html>hi</html>" and out["data"] is None))

    # 失败语义：默认宽松，404 → success=false 不抛
    def nf_handler(request):
        return httpx.Response(404, json={"msg": "nope"})
    out = await _run({"url": "https://x.com/a"}, handler=nf_handler)
    checks.append(("404 宽松不抛", out["success"] is False and out["status_code"] == 404 and out["data"]["msg"] == "nope"))

    # 失败语义：严格模式抛 ValueError
    try:
        await _run({"url": "https://x.com/a", "fail_on_error": True}, handler=nf_handler)
        strict_ok = False
    except ValueError:
        strict_ok = True
    checks.append(("404 严格抛错", strict_ok))

    # 重试：5xx 两次后成功 → attempts=3
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(500, text="err")
        return httpx.Response(200, json={"ok": True})
    out = await _run({"url": "https://x.com/a", "max_retries": 2}, handler=flaky)
    checks.append(("5xx 重试后成功 attempts=3", out["success"] is True and out["attempts"] == 3))

    # 重试耗尽：宽松模式返回失败输出
    def always_500(request):
        return httpx.Response(500, text="err")
    out = await _run({"url": "https://x.com/a", "max_retries": 1}, handler=always_500)
    checks.append(("5xx 重试耗尽 success=false", out["success"] is False and out["attempts"] == 2 and "500" in (out["error"] or "")))

    # 4xx 不重试
    calls["n"] = 0

    def always_404(request):
        calls["n"] += 1
        return httpx.Response(404, text="x")
    out = await _run({"url": "https://x.com/a", "max_retries": 3}, handler=always_404)
    checks.append(("404 不重试 attempts=1", out["attempts"] == 1 and calls["n"] == 1))

    # 连接超时重试
    def timeout_handler(request):
        raise httpx.ConnectTimeout("timeout")
    out = await _run({"url": "https://x.com/a", "max_retries": 1}, handler=timeout_handler)
    checks.append(("超时重试 2 次后失败", out["success"] is False and out["attempts"] == 2 and out["error"] is not None))

    # 非 http(s) scheme 拒绝
    try:
        await _run({"url": "file:///etc/passwd"})
        scheme_ok = False
    except ValueError as e:
        scheme_ok = "http" in str(e)
    checks.append(("file:// 拒绝", scheme_ok))

    # 响应超限：把上限压到 0 → 任意响应都超限
    from config import settings
    old = settings.WORKFLOW_HTTP_MAX_RESPONSE_MB
    settings.WORKFLOW_HTTP_MAX_RESPONSE_MB = 0
    try:
        out = await _run({"url": "https://x.com/a"})
        checks.append(("响应超限报错", out["success"] is False and "上限" in (out["error"] or "")))
    finally:
        settings.WORKFLOW_HTTP_MAX_RESPONSE_MB = old

    # 内网策略：关闭开关后拦截 localhost
    old_allow = settings.WORKFLOW_HTTP_ALLOW_PRIVATE_NET
    settings.WORKFLOW_HTTP_ALLOW_PRIVATE_NET = False
    try:
        try:
            await _run({"url": "http://localhost:8000/api"})
            private_ok = False
        except ValueError as e:
            private_ok = "内网" in str(e)
        checks.append(("内网策略拦截 localhost", private_ok))
    finally:
        settings.WORKFLOW_HTTP_ALLOW_PRIVATE_NET = old_allow

    return checks


# ─────────────────────────── 4. 安全脱敏 ───────────────────────────

async def test_masking():
    checks = []
    checks.append(("mask_secret 保留前3",
                   workflow_engine.mask_secret("eyJhbGciOiJIUzI1") == "eyJ***"))
    checks.append(("mask_secret 短值全打码", workflow_engine.mask_secret("ab") == "***"))
    masked = workflow_engine.mask_sensitive({
        "Authorization": "Bearer tok-xyz", "X-API-Key": "ak-1",
        "Content-Type": "application/json", "X-Token": "abcdef",
    })
    checks.append(("敏感头脱敏", masked["Authorization"] == "Bea***" and masked["X-API-Key"] == "ak-***"))
    checks.append(("普通头保留", masked["Content-Type"] == "application/json"))

    # _node_input_view：HTTP 节点输入视图脱敏
    view = workflow_engine._node_input_view(
        {"type": "http", "config": {
            "method": "GET", "url": "https://x.com/a",
            "headers": {"Authorization": "Bearer secret-token", "X-Custom": "ok"},
        }}, {})
    checks.append(("输入视图脱敏", view["headers"]["Authorization"] == "Bea***" and view["headers"]["X-Custom"] == "ok"))

    # _summarize
    s = workflow_engine._summarize({"type": "http"}, {"success": True, "status_code": 200,
                                                      "duration_ms": 128, "text": "x" * 2048})
    checks.append(("摘要含状态码", "HTTP 200" in s and "128ms" in s and "KB" in s))
    return checks


# ─────────────────────────── 5. 测试接口回显 ───────────────────────────

async def test_test_endpoint_shape():
    restore = install_mock_transport(echo_handler)
    try:
        res = await workflow_engine.exec_http_node_test(
            {"method": "POST", "url": "https://x.com/a",
             "auth": {"type": "bearer", "token": "tok-xyz"},
             "body": {"type": "json", "data": {"q": "{{start.kw}}"}}},
            {"start": {"kw": "年报"}},
        )
    finally:
        restore()
    pv = res["request_preview"]
    return [
        ("回显方法与URL", pv["method"] == "POST" and pv["url"] == "https://x.com/a"),
        ("回显鉴权脱敏", pv["headers"]["Authorization"] == "Bea***"),
        ("回显body已渲染", pv["body"] == {"q": "年报"}),
        ("输出成功", res["output"]["success"] is True),
    ]


async def test_test_endpoint_failures():
    """测试模式：fail_on_error=true / 内网拦截也不抛 400，而是返回带 error 的结构化输出。"""
    from config import settings

    def always_404(request):
        return httpx.Response(404, text="not found")

    restore = install_mock_transport(always_404)
    try:
        res = await workflow_engine.exec_http_node_test(
            {"url": "https://x.com/a", "fail_on_error": True}, {})
    finally:
        restore()
    checks = [
        ("fail_on_error=true 测试不抛", res["output"]["success"] is False),
        ("仍带响应体与错误信息", res["output"]["status_code"] == 404 and res["output"]["error"] == "HTTP 404"
                                     and res["output"]["text"] == "not found"),
        ("请求回显仍返回", res["request_preview"]["url"] == "https://x.com/a"),
    ]

    old_flag = settings.WORKFLOW_HTTP_ALLOW_PRIVATE_NET
    settings.WORKFLOW_HTTP_ALLOW_PRIVATE_NET = False
    try:
        # 拦截发生在真实请求之前，无需 mock transport 即可验证
        res2 = await workflow_engine.exec_http_node_test(
            {"url": "https://127.0.0.1/a"}, {})
    finally:
        settings.WORKFLOW_HTTP_ALLOW_PRIVATE_NET = old_flag
    checks.append(("内网拦截以结构化输出返回", res2["output"]["success"] is False
                   and "内网" in (res2["output"]["error"] or "")))
    return checks


# ─────────────────────────── 6. 端到端（--e2e，LangGraph 全链路） ───────────────────────────

def _http_definition():
    """start → http → end：URL/参数/请求体用变量，输出映射引用 http 节点输出。"""
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "开始",
             "config": {"inputs": [{"name": "uid", "type": "text"}]}},
            {"id": "h1", "type": "http", "title": "查询用户",
             "config": {
                 "method": "POST",
                 "url": "https://x.com/users/{{start.uid}}",
                 "params": {"verbose": "1"},
                 "headers": {"X-Trace": "t-1"},
                 "auth": {"type": "bearer", "token": "tok-xyz"},
                 "body": {"type": "json", "data": {"tag": "{{start.uid}}"}},
                 "output_fields": ["success", "status_code", "data"],
             }},
            {"id": "end", "type": "end", "title": "结束",
             "config": {"outputs": [
                 {"name": "status", "value": "{{h1.status_code}}"},
                 {"name": "method", "value": "{{h1.data.method}}"},
                 {"name": "ok", "value": "{{h1.success}}"},
             ]}},
        ],
        "edges": [
            {"id": "a", "source": "start", "target": "h1"},
            {"id": "b", "source": "h1", "target": "end"},
        ],
    }


def _parse_sse(chunk: str):
    if not chunk.startswith("data: "):
        return None
    text = chunk[6:].strip()
    if not text or text == "[DONE]":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _collect(agen):
    events = []
    async for chunk in agen:
        evt = _parse_sse(chunk)
        if evt:
            events.append(evt)
    return events


async def test_e2e_workflow():
    from database import async_session, init_db
    from services.workflow_engine import run_stream
    from services.workflow_service import WorkflowService

    await init_db()
    d = _http_definition()
    async with async_session() as db:
        wf = await WorkflowService.create(db, {"name": "测试-HTTP节点", "definition": d})
        wf_id = wf["id"]

    restore = install_mock_transport(echo_handler)
    try:
        events = await _collect(run_stream(wf_id, d, {"uid": "u_7"}))
    finally:
        restore()

    fin = next((e for e in events if e.get("type") == "workflow_finished"), None)
    finished = next((e for e in events if e.get("type") == "node_finished" and e.get("node_id") == "h1"), None)
    started = next((e for e in events if e.get("type") == "node_started" and e.get("node_id") == "h1"), None)
    out = (finished or {}).get("output") or {}
    return [
        ("工作流成功收尾", (fin or {}).get("status") == "succeeded"),
        ("HTTP 节点成功", out.get("success") is True and out.get("status_code") == 200),
        ("URL 路径变量已渲染", (out.get("data") or {}).get("path") == "/users/u_7"),
        ("鉴权头已携带", (out.get("data") or {}).get("headers", {}).get("authorization") == "Bearer tok-xyz"),
        ("json body 变量已渲染", (out.get("data") or {}).get("body") == {"tag": "u_7"}),
        ("输入视图脱敏", ((started or {}).get("input") or {}).get("headers", {}).get("Authorization") == "Bea***"),
        ("end 映射引用 http 输出", (fin or {}).get("outputs") == {"status": 200, "method": "POST", "ok": True}),
    ]


# ─────────────────────────── 入口 ───────────────────────────

async def _run_checks(checks):
    failed = 0
    for label, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        failed += 0 if ok else 1
    return failed


async def main(e2e: bool = False):
    print("=== HTTP 节点 · 定义校验 ===")
    failed = test_validate()

    print("\n=== HTTP 节点 · 执行语义（MockTransport）===")
    failed += await _run_checks(await test_execution())

    print("\n=== HTTP 节点 · 安全脱敏 ===")
    failed += await _run_checks(await test_masking())

    print("\n=== HTTP 节点 · 测试接口回显 ===")
    failed += await _run_checks(await test_test_endpoint_shape())
    failed += await _run_checks(await test_test_endpoint_failures())

    if e2e:
        try:
            import langgraph  # noqa: F401
        except ImportError:
            print("\n(跳过端到端用例：当前解释器未安装 langgraph)")
        else:
            print("\n=== HTTP 节点 · 端到端（LangGraph）===")
            failed += await _run_checks(await test_e2e_workflow())
    else:
        print("\n(跳过端到端用例：加 --e2e 运行，需加载 LangGraph，较慢)")

    print(f"\n失败 {failed} 项")
    return failed


if __name__ == "__main__":
    code = 1
    try:
        code = 1 if asyncio.run(main("--e2e" in sys.argv)) else 0
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = _TMP_DB + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    sys.exit(code)
