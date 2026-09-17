# -*- coding: utf-8 -*-
"""KnowSource 平台 MCP 服务器（stdio 传输）。

把平台既有数据能力（知识库检索 / 图谱查询 / 台账统计）通过 MCP
（Model Context Protocol）暴露给外部 Agent 宿主（Claude Desktop、Cursor、
其它 MCP Client）——与 services/tool_registry.py 的内置工具共用同一批
handler 实现，一处能力、两种消费方式：
- 平台内部：ToolAgent 的 Function Calling 循环直接调用；
- 平台外部：任何 MCP 客户端经 stdio 接入后以工具形式调用。

用法：
    cd backend
    python scripts/mcp_server.py

客户端接入示例（settings.MCP_SERVERS 或 Claude Desktop 配置）：
    {"name": "knowsource", "transport": "stdio",
     "command": "python", "args": ["scripts/mcp_server.py"]}
"""

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


if __name__ == "__main__":
    # stdio 传输：由 MCP 客户端拉起本进程，经标准输入输出通信
    mcp.run()
