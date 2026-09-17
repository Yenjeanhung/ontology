# -*- coding: utf-8 -*-
"""KnowSource 平台 MCP 服务器（stdio / streamable-http 双传输）。

把平台既有数据能力（知识库检索 / 图谱查询 / 台账统计）通过 MCP
（Model Context Protocol）暴露给外部 Agent 宿主（Claude Desktop、Cursor、
其它 MCP Client）——与 services/tool_registry.py 的内置工具共用同一批
handler 实现，一处能力、两种消费方式：
- 平台内部：ToolAgent 的 Function Calling 循环直接调用；
- 平台外部：任何 MCP 客户端经 stdio / streamable-http 接入后以工具形式调用。

用法：
    cd backend
    python scripts/mcp_server.py                          # stdio（默认）
    python scripts/mcp_server.py --http --port 9800       # streamable-http
    # 端点：http://<host>:9800/mcp （可填入「MCP 工具管理」注册中心自消费）

鉴权（仅 HTTP 模式）：--token <密钥>（或环境变量 MCP_EXPOSE_TOKEN），
客户端请求需带 Authorization: Bearer <密钥>；不设置则不鉴权（建议仅内网/反代后使用）。
stdio 模式由客户端拉起进程，属进程级信任，不涉及鉴权。

客户端接入示例（settings.MCP_SERVERS 或 Claude Desktop 配置）：
    {"name": "knowsource", "transport": "stdio",
     "command": "python", "args": ["scripts/mcp_server.py"]}
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # backend 根入 path

from mcp.server.fastmcp import FastMCP                          # noqa: E402

from services.tool_registry import (                            # noqa: E402
    builtin_data_query,
    builtin_graph_search,
    builtin_kb_search,
)

mcp = FastMCP(
    "knowsource-tools",
    instructions=(
        "KnowSource 知识库平台工具集：kb_search 知识库向量检索、"
        "graph_search 实体图谱查询、data_query 实体台账统计。"
    ),
)


@mcp.tool()
async def kb_search(query: str, top_k: int = 5) -> dict:
    """跨全部知识库的向量语义检索，返回最相关语料片段（含来源知识库/文件/页码/相似度）。"""
    return await builtin_kb_search(query=query, top_k=top_k)


@mcp.tool()
async def graph_search(keywords: list[str], limit: int = 5) -> dict:
    """实体图谱关键词检索，返回命中实体（类型/描述）及其邻接关系链。"""
    return await builtin_graph_search(keywords=keywords, limit=limit)


@mcp.tool()
async def data_query(keywords: list[str], limit: int = 8) -> dict:
    """实体台账结构化查询：命中总量、按实体类型聚合计数与最新明细记录（真实数据）。"""
    return await builtin_data_query(keywords=keywords, limit=limit)


class BearerMiddleware:
    """极简 ASGI Bearer Token 校验（HTTP 模式可选鉴权，不引入额外依赖）。"""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = str(token).strip()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            expected = f"Bearer {self.token}".encode()
            if headers.get(b"authorization") != expected:
                await send({
                    "type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized: invalid or missing bearer token"}',
                })
                return
        await self.app(scope, receive, send)


def main() -> None:
    parser = argparse.ArgumentParser(description="KnowSource MCP 服务器（对外提供平台能力）")
    parser.add_argument("--http", action="store_true",
                        help="以 streamable-http 启动（默认 stdio）")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP 监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=9800, help="HTTP 监听端口，默认 9800")
    parser.add_argument("--token", default=os.getenv("MCP_EXPOSE_TOKEN", ""),
                        help="Bearer Token（HTTP 模式可选鉴权；缺省读 MCP_EXPOSE_TOKEN）")
    args = parser.parse_args()

    if not args.http:
        # stdio 传输：由 MCP 客户端拉起本进程，经标准输入输出通信
        # （stdout 是协议通道，此处不得 print 任何内容）
        mcp.run()
        return

    try:
        app = mcp.streamable_http_app()
    except AttributeError as exc:
        raise SystemExit(
            "当前 mcp SDK 不支持 streamable-http（需 >=1.8），"
            "请升级：pip install -U 'mcp>=1.8,<2'"
        ) from exc
    if args.token:
        app = BearerMiddleware(app, args.token)
        print(f"[mcp_server] streamable-http 已启动: http://{args.host}:{args.port}/mcp （Bearer 鉴权已启用）")
    else:
        print(f"[mcp_server] streamable-http 已启动: http://{args.host}:{args.port}/mcp （未鉴权，仅限内网使用）")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
