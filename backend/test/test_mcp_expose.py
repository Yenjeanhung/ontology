"""对外 MCP 服务器（backend/scripts/mcp_server.py）冒烟测试：工具注册 + Bearer 鉴权中间件。

运行：cd backend && python -m pytest test/test_mcp_expose.py -q
不连真实数据库 / 不起网络服务：只验证工具注册与中间件逻辑。
"""
import asyncio
import os
import sys

_BACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACK)
sys.path.insert(0, os.path.join(_BACK, "scripts"))

import mcp_server  # noqa: E402


def _tool_names(mcp) -> set:
    tm = getattr(mcp, "_tool_manager", None)
    assert tm is not None, "FastMCP 内部 _tool_manager 不存在（SDK 结构变化，需检查）"
    return {t.name for t in tm.list_tools()}


def test_server_registers_three_tools():
    """对外 MCP 服务器注册且仅注册平台三能力工具（与 ToolAgent 内置同源）。"""
    names = _tool_names(mcp_server.mcp)
    assert {"kb_search", "graph_search", "data_query"} <= names
    assert all(n in ("kb_search", "graph_search", "data_query") for n in names)


def test_tool_descriptions_present():
    """docstring 已成为工具描述（外部客户端依赖描述选工具）。"""
    tools = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}
    for name in ("kb_search", "graph_search", "data_query"):
        assert tools[name].description.strip(), f"{name} 缺少描述"


def test_bearer_middleware_rejects_missing_or_wrong_token():
    class FakeApp:
        called = False

        async def __call__(self, scope, receive, send):
            FakeApp.called = True

    async def _run(headers):
        sent = []
        app = mcp_server.BearerMiddleware(FakeApp(), "s3cret")

        async def receive():
            raise AssertionError("401 路径不应读 body")

        async def send(msg):
            sent.append(msg)

        await app({"type": "http", "headers": headers}, receive, send)
        return sent

    # 无 Authorization → 401，业务 app 不被调用
    sent = asyncio.run(_run([]))
    assert sent[0]["status"] == 401 and not FakeApp.called
    # token 错误 → 401
    FakeApp.called = False
    bad = [("authorization", b"Bearer wrong"), ("content-type", b"application/json")]
    sent = asyncio.run(_run(bad))
    assert sent[0]["status"] == 401 and not FakeApp.called


def test_bearer_middleware_passes_valid_token():
    class FakeApp:
        called = False

        async def __call__(self, scope, receive, send):
            FakeApp.called = True

    async def _run():
        app = mcp_server.BearerMiddleware(FakeApp(), "s3cret")

        async def receive():
            raise AssertionError("not used")

        async def send(msg):
            raise AssertionError("放行路径不应 send")

        await app({"type": "http",
                   "headers": [(b"authorization", b"Bearer s3cret")]}, receive, send)

    asyncio.run(_run())
    assert FakeApp.called


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
