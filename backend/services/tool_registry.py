# -*- coding: utf-8 -*-
"""平台工具注册表（Function Calling / Tool Use 的工具侧基座）。

职责：
- 把平台既有能力封装为符合 OpenAI tools 规范的工具（JSON Schema 参数 + 异步执行器）；
- 通过 MCP（Model Context Protocol）动态接入外部工具服务器，远程工具与内置工具
  在同一注册表内无差别参与 LLM 工具调用循环（内外部工具统一接入）；
- 为 ToolAgent 等智能体节点、/api/agent/multi/tools 工具清单接口、scripts/mcp_server.py
  提供统一的工具发现与执行入口。

设计要点：
- 工具 handler 返回结构化 dict（raw），注册表负责序列化为给 LLM 的文本并截断，
  raw 同时透传给调用方（用于前端素材卡渲染）；
- MCP 连接生命周期由 PlatformTools 异步上下文统一管理，单个服务器连接失败
  只记录状态、不阻断其余工具（降级可用）；
- mcp SDK 为可选依赖：未安装时 MCP 部分自动跳过，内置工具不受影响。
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from config import settings

# 单次工具结果回填 LLM 的字符上限（超出截断，防上下文爆炸）
RESULT_MAX_CHARS = 4000


@dataclass
class Tool:
    """一个可被 LLM Function Calling 调用的工具。"""
    name: str
    description: str
    parameters: dict                      # JSON Schema（OpenAI function parameters 格式）
    handler: Callable[..., Awaitable[Any]]
    source: str = "built-in"              # built-in | mcp:<server名>

    def spec(self) -> dict:
        """OpenAI tools 数组元素格式（LangChain bind_tools 直接可用）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表：注册 / 发现 / 执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ── 注册与发现 ──────────────────────────────────────────
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def describe(self) -> list[dict]:
        """工具清单（接口展示用）：规范 + 来源。"""
        return [{**t.spec()["function"], "source": t.source} for t in self._tools.values()]

    # ── 执行 ────────────────────────────────────────────────
    async def call(self, name: str, arguments: Any,
                   timeout: Optional[float] = None) -> tuple[str, Any]:
        """执行工具，返回 (给 LLM 的文本, 结构化 raw)。

        参数解析容错：LLM 给的 arguments 可能是 JSON 字符串；
        异常统一包装为 {"error": ...} 文本（让 LLM 自行调整调用，而非中断循环）。
        """
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"未知工具 {name}，可用：{self.names()}"},
                              ensure_ascii=False), {"error": f"未知工具 {name}"}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        arguments = arguments if isinstance(arguments, dict) else {}
        timeout = timeout or getattr(settings, "TOOL_TIMEOUT", 25.0)
        t0 = time.monotonic()
        try:
            raw = await asyncio.wait_for(tool.handler(**arguments), timeout=timeout)
            text = json.dumps(raw, ensure_ascii=False)
            if len(text) > RESULT_MAX_CHARS:
                text = text[:RESULT_MAX_CHARS] + f"…（截断，原始 {len(text)} 字符）"
            return text, raw
        except TypeError as exc:      # 参数名/数量不符
            return json.dumps({"error": f"参数错误：{exc}",
                               "expected": tool.parameters.get("properties", {})},
                              ensure_ascii=False), {"error": f"参数错误：{exc}"}
        except asyncio.TimeoutError:
            return json.dumps({"error": f"工具 {name} 执行超时（>{timeout}s）"},
                              ensure_ascii=False), {"error": "timeout"}
        except Exception as exc:      # 工具内部失败 → 错误信息回传 LLM
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"},
                              ensure_ascii=False), {"error": str(exc)}


# ═════════════════════════════════════════════════════════════
# 内置工具：平台既有能力的工具化封装（业务无关，直接查平台数据）
# ═════════════════════════════════════════════════════════════

async def builtin_kb_search(query: str, top_k: int = 5) -> dict:
    """跨全部知识库的向量检索（与 Retriever 同源的取数路径，业务无关）。"""
    from database import async_session
    from models import KnowledgeBase
    from services.vector_data_service import VectorDataService

    top_k = max(1, min(int(top_k or 5), 10))
    try:
        async with async_session() as db:
            kbs = (await db.execute(
                # 台账口径的业务库可能很多，工具检索只取前 6 个库控制耗时
                select_kb_stmt()
            )).scalars().all()
    except Exception:
        return {"items": [], "note": "知识库列表查询失败（数据库不可用）"}

    items: list[dict] = []
    for kb in kbs:
        try:
            data = await VectorDataService.similarity_test(kb.id, query, top_k=top_k)
        except Exception:
            continue   # 向量库未配置 / 单库失败 → 跳过
        for it in data.get("items", []):
            text = (it.get("chunk_text") or "").strip()
            score = it.get("score") or 0
            if not text:
                continue
            items.append({
                "kb_name": kb.name,
                "file_name": it.get("file_name") or "未命名文件",
                "page": it.get("page_number"),
                "score": round(score, 4),
                "text": text[:600],
            })
    items.sort(key=lambda x: x["score"], reverse=True)
    items = items[: 12]
    return {
        "items": items,
        "note": f"全库向量检索，命中 {len(items)} 条" if items else "全库向量检索，未命中语料",
    }


def select_kb_stmt():
    from sqlalchemy import select
    from models import KnowledgeBase
    return select(KnowledgeBase).limit(6)


async def builtin_graph_search(keywords: list[str], limit: int = 5) -> dict:
    """实体图谱关键词检索：命中实体 + 邻接关系（业务无关）。"""
    from database import async_session
    from models import Entity, Relation
    from sqlalchemy import or_, select

    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()][:6]
    if not kws:
        return {"entities": [], "note": "keywords 为空，未执行查询"}
    limit = max(1, min(int(limit or 5), 10))
    try:
        async with async_session() as db:
            cond = or_(*[
                or_(Entity.name.ilike(f"%{kw}%"), Entity.description.ilike(f"%{kw}%"))
                for kw in kws
            ])
            entities = (await db.execute(
                select(Entity).where(cond).limit(limit)
            )).scalars().all()
            out: list[dict] = []
            for e in entities:
                rels = (await db.execute(
                    select(Relation).where(or_(Relation.source_entity_id == e.id,
                                               Relation.target_entity_id == e.id))
                )).scalars().all()
                peer_ids = sorted({r.source_entity_id if r.target_entity_id == e.id
                                   else r.target_entity_id for r in rels})[:6]
                peers: dict[str, str] = {}
                if peer_ids:
                    for p in (await db.execute(
                        select(Entity).where(Entity.id.in_(peer_ids))
                    )).scalars().all():
                        peers[p.id] = f"{p.entity_type}「{p.name}」"
                links = [
                    f"—{r.relation_type}→ {peers[r.target_entity_id]}"
                    if r.target_entity_id in peers and r.source_entity_id == e.id
                    else f"←{r.relation_type}— {peers[r.source_entity_id]}"
                    for r in rels
                    if (r.source_entity_id in peers or r.target_entity_id in peers)
                ][:4]
                out.append({
                    "entity_type": e.entity_type,
                    "name": e.name,
                    "description": (e.description or "")[:120],
                    "relations": links,
                })
            return {"entities": out,
                    "note": f"图谱命中 {len(out)} 个实体" if out else "图谱未命中实体"}
    except Exception as exc:
        return {"entities": [], "note": f"图谱查询失败：{type(exc).__name__}"}


async def builtin_data_query(keywords: list[str], limit: int = 8) -> dict:
    """实体台账结构化查询：命中统计（总量+按类型聚合）+ 明细记录（业务无关）。

    口径与 DataAgent 一致：类型/名称级强信号命中 >= 3 时仅用强口径，
    排除仅描述全文命中的跨库误召回。
    """
    from database import async_session
    from models import Entity
    from sqlalchemy import func, or_, select

    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()][:6]
    limit = max(1, min(int(limit or 8), 20))
    try:
        async with async_session() as db:
            def _match(*fields):
                return or_(*[or_(*[f.ilike(f"%{kw}%") for f in fields]) for kw in kws])

            cond, scope = None, "全库口径"
            if kws:
                strong = _match(Entity.entity_type, Entity.name)
                strong_n = (await db.execute(
                    select(func.count()).select_from(Entity).where(strong)
                )).scalar_one()
                if strong_n >= 3:
                    cond = strong
                    scope = "类型/名称口径（仅描述命中已排除）"
                else:
                    cond = _match(Entity.entity_type, Entity.name, Entity.description)
                    scope = "全字段口径"

            cnt = select(func.count()).select_from(Entity)
            if cond is not None:
                cnt = cnt.where(cond)
            total = (await db.execute(cnt)).scalar_one()
            if total == 0:
                return {"total": 0, "scope": scope,
                        "note": f"台账无命中（关键词：{' / '.join(kws)}）"}

            type_q = (select(Entity.entity_type, func.count())
                      .group_by(Entity.entity_type)
                      .order_by(func.count().desc()))
            if cond is not None:
                type_q = type_q.where(cond)
            type_rows = (await db.execute(type_q)).all()
            type_counts = {str(t or "未分类"): c for t, c in type_rows}

            stmt = select(Entity)
            if cond is not None:
                stmt = stmt.where(cond)
            rows = (await db.execute(
                stmt.order_by(Entity.created_at.desc()).limit(limit)
            )).scalars().all()
            records = []
            for e in rows:
                props = _safe_props(e.properties)
                records.append({
                    "entity_type": e.entity_type,
                    "name": e.name,
                    "description": (e.description or "")[:100],
                    "properties": props,
                    "created_at": str(e.created_at or "")[:10],
                })
            primary: dict = {}
            note = f"台账命中 {total} 条（{scope}），返回最新 {len(records)} 条"
            if (len(type_rows) > 1 and total > 0
                    and type_rows[0][1] * 10 >= total * 6):
                primary = {"type": str(type_rows[0][0] or "未分类"),
                           "count": int(type_rows[0][1])}
                note += (f"；主类型「{primary['type']}」{primary['count']} 条，"
                         f"其余 {total - primary['count']} 条为关键词关联实体"
                         "（统计以主类型为准）")
            return {"total": total, "scope": scope, "type_counts": type_counts,
                    "primary": primary, "records": records, "note": note}
    except Exception as exc:
        return {"total": 0, "records": [], "note": f"台账查询失败：{type(exc).__name__}"}


def _safe_props(raw: Any) -> dict:
    """properties JSON 列的容错解析（坏数据不抛异常）。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v)[:40] for k, v in list(raw.items())[:6]}
    try:
        obj = json.loads(raw)
        return {str(k): str(v)[:40] for k, v in list(obj.items())[:6]} \
            if isinstance(obj, dict) else {}
    except Exception:
        return {}


# ═════════════════════════════════════════════════════════════
# MCP：外部工具服务器接入（Model Context Protocol）
# ═════════════════════════════════════════════════════════════

def parse_mcp_config(raw: str) -> list[dict]:
    """解析 MCP_SERVERS 配置（JSON 数组），坏配置返回空并在日志可见。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        arr = json.loads(raw)
        return [s for s in arr if isinstance(s, dict) and s.get("name")] if isinstance(arr, list) else []
    except json.JSONDecodeError:
        return []


async def load_mcp_servers() -> list[dict]:
    """MCP 服务器清单（热加载）：数据库注册中心优先，库空/异常回退环境变量。

    - 库里有记录（含停用）即以库为唯一事实源——管理界面删光服务器意味着
      「不接入」，环境变量配置不会复活；
    - 库不可达（脚本 / 测试等无库场景）或从未在界面配置过 → 环境变量兜底，
      向后兼容既有部署。
    每次团队运行（PlatformTools 构建）都重新读取，管理界面改完即生效，无需重启。
    """
    try:
        from database import async_session
        from services.mcp_store import has_any_server, load_enabled_servers
        async with async_session() as db:
            if await has_any_server(db):
                return await load_enabled_servers(db)
    except Exception:
        pass   # 库不可用（脚本场景）→ 环境变量兜底
    return parse_mcp_config(getattr(settings, "MCP_SERVERS", ""))


async def probe_server(server: dict) -> dict:
    """单台 MCP 服务器连通性探测（注册中心「测试连接 / 巡检」用）。

    连接 → initialize 握手 → 拉取工具清单 → 关闭；任何失败都包装为
    ok=false + error 文本（不抛异常，接口层直接可用）。
    """
    conn = McpConnection(server)
    t0 = time.monotonic()
    try:
        await conn.connect()
        tools = await conn.list_tools()
        return {
            "ok": True,
            "tools": [{"name": t.name, "description": t.description} for t in tools],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "tools": [],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        await conn.close()


class McpConnection:
    """单个 MCP 服务器的连接与会话（stdio / streamable_http 两种传输）。"""

    def __init__(self, server: dict) -> None:
        self.server = server
        self.name = str(server["name"])
        self._stack: Optional[Any] = None   # contextlib.AsyncExitStack
        self.session: Optional[Any] = None

    async def connect(self) -> None:
        """建立连接并完成 MCP initialize 握手。失败抛异常由上层记状态。"""
        from contextlib import AsyncExitStack

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("未安装 mcp SDK（pip install mcp）") from exc

        transport = str(self.server.get("transport", "stdio")).lower()
        stack = AsyncExitStack()
        try:
            if transport == "stdio":
                params = StdioServerParameters(
                    command=str(self.server["command"]),
                    args=[str(a) for a in self.server.get("args", [])],
                    env=self.server.get("env") or None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif transport in ("streamable_http", "http", "sse"):
                try:
                    # mcp 1.x：驼峰 http（<2）
                    from mcp.client.streamable_http import streamablehttp_client
                except ImportError:      # mcp 2.x：更名 snake_case
                    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

                # v1 返回 (read, write, get_session_id)，v2 只返回 (read, write)
                read, write, *_ = await stack.enter_async_context(
                    streamablehttp_client(str(self.server["url"]))
                )
            else:
                raise ValueError(f"不支持的 MCP 传输类型：{transport}")

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._stack, self.session = stack, session
        except BaseException:
            await stack.aclose()
            raise

    async def list_tools(self) -> list[Tool]:
        """拉取远程工具清单并包装为注册表 Tool（inputSchema 即 JSON Schema，直接透传）。"""
        resp = await self.session.list_tools()
        tools: list[Tool] = []
        for t in getattr(resp, "tools", []):
            params = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
            tools.append(Tool(
                name=f"mcp_{self.name}_{t.name}",
                description=f"[MCP:{self.name}] {getattr(t, 'description', '') or t.name}",
                parameters=params,
                handler=self._make_handler(t.name),
                source=f"mcp:{self.name}",
            ))
        return tools

    def _make_handler(self, remote_name: str) -> Callable[..., Awaitable[Any]]:
        async def _h(**kwargs) -> Any:
            timeout = getattr(settings, "MCP_TOOL_TIMEOUT", 30.0)
            result = await asyncio.wait_for(
                self.session.call_tool(remote_name, arguments=kwargs), timeout=timeout
            )
            if getattr(result, "isError", False):
                texts = [getattr(c, "text", "") for c in (result.content or [])]
                raise RuntimeError("; ".join(t for t in texts if t) or "MCP 工具返回错误")
            parts = []
            for c in (result.content or []):
                text = getattr(c, "text", None)
                if text:
                    parts.append(text)
            return {"text": "\n".join(parts)[:RESULT_MAX_CHARS] or "（空结果）"}
        return _h

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack, self.session = None, None


class PlatformTools:
    """平台工具集（异步上下文）：内置工具 + MCP 远程工具，统一注册表。

    用法：
        async with PlatformTools() as pt:
            specs = pt.registry.specs()
            text, raw = await pt.registry.call("kb_search", {"query": "..."})
            status = pt.mcp_status   # 各 MCP 服务器连接状态（接口展示用）
    """

    def __init__(self, include: Optional[list[str]] = None) -> None:
        """include：工具白名单（内置工具名 + "mcp:<服务器名>"）；None/空 = 全部可用。

        白名单语义：非空列表时，未列入的内置工具不注册、未列入的 MCP 服务器整台
        跳过（配置粒度=服务器级，避免逐工具巡检）。供智能体配置页按智能体勾选。
        """
        self.include = {str(x) for x in include} if include else None
        self.registry = ToolRegistry()
        self.mcp_status: list[dict] = []
        self._conns: list[McpConnection] = []

    def _want(self, name: str) -> bool:
        return self.include is None or name in self.include

    async def __aenter__(self) -> "PlatformTools":
        # 1) 内置工具（按智能体白名单过滤；include=None = 全部，向后兼容既有调用方）
        if self._want("kb_search"):
            self.registry.register(Tool(
                name="kb_search",
                description="跨全部知识库的向量语义检索，返回最相关的语料片段（含来源知识库/文件/页码/相似度）。适用于需要平台文档依据的任何问题。",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索问题或关键词"},
                        "top_k": {"type": "integer", "description": "每库取回条数，默认 5"},
                    },
                    "required": ["query"],
                },
                handler=builtin_kb_search,
            ))
        if self._want("graph_search"):
            self.registry.register(Tool(
                name="graph_search",
                description="实体图谱关键词检索，返回命中实体（类型/描述）及其邻接关系链。适用于实体关联、依赖结构类问题。",
                parameters={
                    "type": "object",
                    "properties": {
                        "keywords": {"type": "array", "items": {"type": "string"},
                                     "description": "实体名/描述的关键词列表，1~6 个"},
                        "limit": {"type": "integer", "description": "返回实体数上限，默认 5"},
                    },
                    "required": ["keywords"],
                },
                handler=builtin_graph_search,
            ))
        if self._want("data_query"):
            self.registry.register(Tool(
                name="data_query",
                description="实体台账结构化查询：返回命中总量、按实体类型的聚合计数与最新明细记录（真实数据，禁止编造数字时使用）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "keywords": {"type": "array", "items": {"type": "string"},
                                     "description": "实体类型/名称/描述的关键词列表，可为空（全库口径）"},
                        "limit": {"type": "integer", "description": "明细记录条数上限，默认 8"},
                    },
                    "required": [],
                },
                handler=builtin_data_query,
            ))

        # 2) MCP 外部工具（可选依赖 + 逐服务器降级；注册中心热加载，改配置即生效）；
        #    白名单按 "mcp:<server>" 服务器级过滤，未勾选的整台不连接。
        servers = await load_mcp_servers()
        if not servers:
            return self
        for server in servers:
            if not self._want(f"mcp:{server['name']}"):
                continue
            status = {"server": server["name"], "ok": False, "tools": 0, "error": ""}
            conn = McpConnection(server)
            try:
                await conn.connect()
                tools = await conn.list_tools()
                for t in tools:
                    self.registry.register(t)   # 与内置工具同表注册，循环内无差别调用
                self._conns.append(conn)
                status.update(ok=True, tools=len(tools))
            except Exception as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                await conn.close()
            self.mcp_status.append(status)
        return self

    async def __aexit__(self, *exc_info) -> None:
        for conn in self._conns:
            try:
                await conn.close()
            except Exception:
                pass
        self._conns.clear()
