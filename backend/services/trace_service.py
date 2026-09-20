# -*- coding: utf-8 -*-
"""链路追踪查询服务：聚合榜 / 调用列表 / 单次调用完整 span 树。

只读，直接 aiosqlite 查询 core/otel.py 写入的 data/otel_traces.db；
表不存在（OTEL 从未启用）时一律返回空结果，不报错。
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from config import settings

_NS_PER_HOUR = 3600 * 1_000_000_000
# SpanKind.SERVER 的 int 值：HTTP 入口 span。
# 注意 OTel Python 的 SpanKind 从 0 编号（INTERNAL=0/SERVER=1/CLIENT=2），
# 与 OTLP proto 规范的编号（SERVER=2）不同，以 .value 实测为准。
SERVER_KIND = 1


def _db_path() -> str:
    return str(settings.OTEL_DB_PATH)


def _now_ns() -> int:
    return datetime.now().timestamp() * 1_000_000_000


def _ns_to_iso(ns: int) -> str:
    try:
        return datetime.fromtimestamp(ns / 1_000_000_000).isoformat(timespec="milliseconds")
    except (ValueError, OSError, OverflowError):
        return ""


async def _fetch(query_sql: str, params: tuple) -> list[dict]:
    """执行只读查询；库/表不存在时返回空列表。"""
    path = Path(_db_path())
    if not path.exists():
        return []

    def _run() -> list[dict]:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query_sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


def _parse_attrs(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _endpoint_label(attrs: dict) -> str:
    """SERVER span 的接口标识：`METHOD /route`（404 等无路由时回退 path）。"""
    method = attrs.get("http.method") or attrs.get("http.request.method") or ""
    route = attrs.get("http.route") or attrs.get("http.target") or attrs.get("url.path") or ""
    return f"{method} {route}".strip()


def _status_code(attrs: dict) -> int:
    try:
        return int(attrs.get("http.status_code") or 0)
    except (TypeError, ValueError):
        return 0


def _is_error(row: dict, attrs: dict) -> bool:
    if int(row.get("status") or 0) == 2:
        return True
    code = _status_code(attrs)
    return code >= 500


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[idx], 1)


# ── 1. 接口聚合榜（慢接口定位） ─────────────────────────────────────

async def get_stats(hours: int = 24) -> dict:
    """按 HTTP 接口（SERVER span）聚合：调用数 / avg / p95 / max / 错误数 / 慢调用数。"""
    since = _now_ns() - hours * _NS_PER_HOUR
    rows = await _fetch(
        "SELECT name, kind, start_ns, duration_ms, status, attributes"
        " FROM otel_spans WHERE kind = ? AND start_ns >= ?",
        (SERVER_KIND, since),
    )
    if not rows:
        return {"hours": hours, "generated_at": _ns_to_iso(_now_ns()), "endpoints": [],
                "total_calls": 0, "slow_threshold_ms": settings.OTEL_SLOW_MS}

    groups: dict[str, list[dict]] = {}
    for row in rows:
        attrs = _parse_attrs(row.get("attributes"))
        label = _endpoint_label(attrs) or row.get("name") or "unknown"
        groups.setdefault(label, []).append(row)

    endpoints = []
    for label, items in groups.items():
        durations = [float(r["duration_ms"] or 0) for r in items]
        errors = sum(1 for r in items if _is_error(r, _parse_attrs(r.get("attributes"))))
        slow = sum(1 for d in durations if d >= settings.OTEL_SLOW_MS)
        endpoints.append({
            "endpoint": label,
            "count": len(items),
            "avg_ms": round(sum(durations) / len(durations), 1),
            "p95_ms": _p95(durations),
            "max_ms": round(max(durations), 1),
            "error_count": errors,
            "slow_count": slow,
            "last_seen": _ns_to_iso(max(int(r["start_ns"]) for r in items)),
        })
    # 默认按 p95 倒序：慢接口榜
    endpoints.sort(key=lambda e: e["p95_ms"], reverse=True)
    return {
        "hours": hours,
        "generated_at": _ns_to_iso(_now_ns()),
        "endpoints": endpoints,
        "total_calls": len(rows),
        "slow_threshold_ms": settings.OTEL_SLOW_MS,
    }


# ── 2. 调用列表（单接口 / 全站） ───────────────────────────────────

async def get_calls(
    hours: int = 24,
    endpoint: str = "",
    limit: int = 100,
    slow_only: bool = False,
) -> dict:
    """SERVER span 倒序列表，支持按接口过滤、只看慢调用。"""
    since = _now_ns() - hours * _NS_PER_HOUR
    rows = await _fetch(
        "SELECT trace_id, start_ns, duration_ms, status, status_message, attributes"
        " FROM otel_spans WHERE kind = ? AND start_ns >= ?"
        " ORDER BY start_ns DESC LIMIT ?",
        (SERVER_KIND, since, max(1, min(limit, 500))),
    )
    calls = []
    for row in rows:
        attrs = _parse_attrs(row.get("attributes"))
        label = _endpoint_label(attrs) or "unknown"
        if endpoint and label != endpoint:
            continue
        duration = float(row.get("duration_ms") or 0)
        if slow_only and duration < settings.OTEL_SLOW_MS:
            continue
        calls.append({
            "trace_id": row["trace_id"],
            "endpoint": label,
            "status_code": _status_code(attrs),
            "error": _is_error(row, attrs),
            "duration_ms": duration,
            "slow": duration >= settings.OTEL_SLOW_MS,
            "start_time": _ns_to_iso(int(row["start_ns"])),
            "error_message": row.get("status_message") or "",
        })
        if len(calls) >= max(1, min(limit, 500)):
            break
    return {"calls": calls, "count": len(calls),
            "slow_threshold_ms": settings.OTEL_SLOW_MS}


# ── 3. 单次调用完整 span 树 ────────────────────────────────────────

async def get_trace(trace_id: str) -> dict:
    """取一次调用（trace）的全部 span，按 start_ns 升序返回（前端组树 + 瀑布）。"""
    rows = await _fetch(
        "SELECT trace_id, span_id, parent_span_id, name, kind, start_ns, end_ns,"
        " duration_ms, status, status_message, attributes, events"
        " FROM otel_spans WHERE trace_id = ? ORDER BY start_ns ASC, span_id ASC",
        (trace_id,),
    )
    spans = []
    for row in rows:
        attrs = _parse_attrs(row.get("attributes"))
        try:
            events = json.loads(row.get("events") or "[]")
        except (TypeError, ValueError):
            events = []
        spans.append({
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
            "parent_span_id": row["parent_span_id"] or "",
            "name": row["name"],
            "kind": int(row.get("kind") or 0),
            "start_ns": int(row["start_ns"]),
            "duration_ms": float(row.get("duration_ms") or 0),
            "error": int(row.get("status") or 0) == 2,
            "status_message": row.get("status_message") or "",
            "attributes": attrs,
            "events": events,
        })
    root_ns = spans[0]["start_ns"] if spans else 0
    return {
        "trace_id": trace_id,
        "span_count": len(spans),
        "root_start_ns": root_ns,
        "total_ms": round(max((s["start_ns"] + int(s["duration_ms"] * 1e6) for s in spans),
                              default=root_ns) - root_ns) / 1e6 if spans else 0.0,
        "spans": spans,
    }
