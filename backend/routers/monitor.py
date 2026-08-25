"""系统监控路由：组件全景 + 健康检查 + SSE 定时推送 + LLM 流式调用。"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from database import async_session
from services import monitor_service
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()

# ═══════════════════════ SSE 客户端广播 ═══════════════════════
# 每个已连接的 SSE 客户端持有一个 asyncio.Queue，广播时将快照放入所有队列。
_clients: set[asyncio.Queue] = set()
_ticker_task: asyncio.Task | None = None


def _sse_named(event: str, payload: dict) -> str:
    """标准 SSE 命名事件：event: <name> + data: <json>。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_data(payload: dict) -> str:
    """data 内嵌 event 字段的 SSE 消息（供 fetch 手动解析的流使用）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _broadcast(snapshot: dict):
    for queue in list(_clients):
        try:
            queue.put_nowait(snapshot)
        except Exception:
            _clients.discard(queue)


async def _ticker():
    """定时推送：有客户端连接时每 MONITOR_PUSH_INTERVAL_SECONDS 检测一次并广播。"""
    interval = settings.MONITOR_PUSH_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(interval)
        if not _clients:
            break
        try:
            snapshot = await monitor_service.build_snapshot(use_cache=False)
            _broadcast(snapshot)
        except Exception as e:
            logger.warning("monitor ticker check failed: %s", e)


def _ensure_ticker():
    global _ticker_task
    if _ticker_task is None or _ticker_task.done():
        _ticker_task = asyncio.create_task(_ticker())


def _stop_ticker_if_idle():
    global _ticker_task
    if not _clients and _ticker_task and not _ticker_task.done():
        _ticker_task.cancel()
        _ticker_task = None


async def _sse_stream():
    """SSE 长连接生成器：首连推快照，周期推快照，空闲发心跳保活。"""
    queue: asyncio.Queue = asyncio.Queue()
    _clients.add(queue)
    _ensure_ticker()
    try:
        # 首次连接立即推送一次（走缓存，避免重复检测）
        snapshot = await monitor_service.build_snapshot(use_cache=True)
        yield _sse_named("snapshot", snapshot)

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
                yield _sse_named("snapshot", item)
            except asyncio.TimeoutError:
                yield _sse_named("heartbeat", {"ts": monitor_service._now_iso()})
    finally:
        _clients.discard(queue)
        _stop_ticker_if_idle()


# ═══════════════════════ 监控 API ═══════════════════════


@router.get("/monitor/overview")
async def monitor_overview():
    """组件全景 + 状态摘要 + 系统信息（页面首次加载用，快速快照）。"""
    return await monitor_service.build_snapshot(use_cache=True)


@router.get("/monitor/components")
async def monitor_components():
    """仅组件清单（含状态）。"""
    snapshot = await monitor_service.build_snapshot(use_cache=True)
    return {"checked_at": snapshot["checked_at"], "components": snapshot["components"]}


@router.get("/monitor/system")
async def monitor_system():
    """仅系统运行信息。"""
    return monitor_service._collect_system()


@router.post("/monitor/check")
async def monitor_check(key: str | None = Query(None, description="仅检测指定组件 key")):
    """手动触发健康检查；结果广播到所有已连接的 SSE 客户端。

    key 给定时只检测该组件并直接返回其状态。
    """
    if key:
        comp = next((c for c in monitor_service._COMPONENTS if c["key"] == key), None)
        if not comp:
            raise HTTPException(404, f"未知组件 key: {key}")
        return await monitor_service._run_one(comp)

    snapshot = await monitor_service.build_snapshot(use_cache=False)
    _broadcast(snapshot)
    return snapshot


@router.get("/monitor/stream")
async def monitor_stream():
    """SSE 长连接：服务端定时推送组件状态与系统信息。"""
    return StreamingResponse(
        _sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ═══════════════════════ LLM 流式调用 ═══════════════════════


class LlmStreamRequest(BaseModel):
    prompt: str = "你好，请介绍一下你自己"


class DbQueryRequest(BaseModel):
    """在关系数据库中执行只读 SQL。"""
    sql: str = "SELECT 1"


def _is_readonly_sql(sql: str) -> bool:
    """仅允许以 SELECT / PRAGMA / WITH(select) 开头，禁止写/改/删/DDL。"""
    s = sql.strip().lower()
    if not s:
        return False
    if s.startswith(("select", "pragma", "with", "explain")):
        return True
    return False


# ═══════════════════════ 关系数据库手动测试 ═══════════════════════


@router.post("/monitor/database/schemas")
async def db_schemas():
    """列出全部 schema（库）及其下的表结构，供前端下拉展示。"""
    from database import async_session

    result = {"schemas": [], "error": None}
    try:
        async with async_session() as db:
            # sqlite_master 中 type in (table,view) 即库内所有对象
            rows = await db.execute(text(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table','view') ORDER BY type, name"
            ))
            objects = [{"name": r[0], "type": r[1]} for r in rows]
            schemas = []
            for obj in objects:
                cols = await db.execute(text(f"PRAGMA table_info('{obj['name']}')"))
                columns = [
                    {
                        "name": c[1],
                        "type": c[2],
                        "notnull": bool(c[3]),
                        "pk": bool(c[5]),
                    }
                    for c in cols
                ]
                schemas.append({
                    "name": obj["name"],
                    "type": obj["type"],
                    "columns": columns,
                })
            result["schemas"] = schemas
    except Exception as e:
        logger.warning("monitor db schemas failed: %s", e)
        result["error"] = str(e)
    return result


@router.post("/monitor/database/query")
async def db_query(req: DbQueryRequest):
    """在关系数据库中执行只读 SQL，返回行数据（最多 200 行）。"""
    from database import async_session

    sql = (req.sql or "").strip()
    if not _is_readonly_sql(sql):
        raise HTTPException(400, "仅支持只读 SQL（SELECT / PRAGMA / WITH / EXPLAIN）")

    try:
        async with async_session() as db:
            rows = await db.execute(text(sql))
            keys = list(rows.keys())
            data = [dict(zip(keys, row)) for row in rows.fetchmany(200)]
        return {
            "columns": keys,
            "rows": data,
            "row_count": len(data),
            "truncated": False,
            "error": None,
        }
    except Exception as e:
        logger.warning("monitor db query failed: %s", e)
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False, "error": str(e)}


@router.post("/monitor/llm/stream")
async def llm_stream(req: LlmStreamRequest):
    """SSE 流式调用当前 LLM：思考链（reasoning）+ 回答（content）分区输出。"""
    from langchain_core.messages import HumanMessage
    from providers.llm import chunk_text, create_llm, extract_reasoning

    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt 不能为空")

    async def _generate():
        llm = create_llm()
        if llm is None:
            yield _sse_data({"event": "content", "content": "尚未配置大模型，请先在「系统配置」中激活 LLM。"})
            yield _sse_data({"event": "done", "content": ""})
            return

        try:
            async for chunk in llm.astream([HumanMessage(content=prompt)]):
                reasoning = extract_reasoning(chunk)
                if reasoning:
                    yield _sse_data({"event": "reasoning", "content": reasoning})
                text = chunk_text(chunk)
                if text:
                    yield _sse_data({"event": "content", "content": text})
        except Exception as e:
            logger.warning("monitor llm stream failed: %s", e)
            yield _sse_data({"event": "content", "content": f"\n\n[生成回答时出错] {e}"})
        yield _sse_data({"event": "done", "content": ""})

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
