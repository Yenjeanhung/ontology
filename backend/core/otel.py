# -*- coding: utf-8 -*-
"""OpenTelemetry 链路追踪：SDK 初始化 + SQLite 自托管存储 + 统一埋点工具。

定位（doc/监控/OpenTelemetry/00-OTel接口链路追踪方案.md）：
- OTel 管「系统为什么慢/哪步慢」；LangSmith 管「LLM 为什么这么答」，两者互不相通；
- 存储自托管轻量实现：独立 SQLite 文件（data/otel_traces.db，与业务主库隔离），
  将来切 Jaeger/Grafana 只需在 init_otel 里换 exporter，业务埋点零改动；
- OTEL_ENABLED=false 或 opentelemetry-sdk 未安装时自动降级：不初始化 SDK，
  tracer 为 API 层 no-op，所有手动 span 零开销，业务行为完全不变。

用法：
    async with async_span("rag.retrieve", {"rag.kb_id": kb_id}) as span:
        ...
        span.set_attribute("rag.chunks", len(chunks))
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

# ── OpenTelemetry 依赖按需导入：未安装时不影响启动 ─────────────────────────
try:
    from opentelemetry import trace as _otel_trace
    _OTEL_API_AVAILABLE = True
except ImportError:  # pragma: no cover - API 层缺失时埋点全量降级
    _otel_trace = None
    _OTEL_API_AVAILABLE = False

try:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )
    _OTEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - SDK 未安装时只降级导出，API 层 no-op
    _OTEL_SDK_AVAILABLE = False

_TRACER_NAME = "ontology"

# status_code 枚举值（与 SDK 的 StatusCode 对齐，落库为 int）
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2
# SpanKind.SERVER 的 int 值（OTel Python 从 0 编号：INTERNAL=0/SERVER=1），
# HTTP 入口 span，即聚合榜/调用列表的统计对象
SPAN_KIND_SERVER = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS otel_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    kind INTEGER NOT NULL DEFAULT 1,
    start_ns INTEGER NOT NULL,
    end_ns INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    status INTEGER NOT NULL DEFAULT 0,
    status_message TEXT NOT NULL DEFAULT '',
    attributes TEXT NOT NULL DEFAULT '{}',
    events TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON otel_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_start ON otel_spans(start_ns);
CREATE INDEX IF NOT EXISTS idx_spans_kind_start ON otel_spans(kind, start_ns);
"""

_INSERT_SQL = (
    "INSERT INTO otel_spans (trace_id, span_id, parent_span_id, name, kind,"
    " start_ns, end_ns, duration_ms, status, status_message, attributes, events)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _json_safe(value):
    """递归转换为 JSON 可序列化结构（bytes / 未知对象兜底转字符串）。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


class SQLiteSpanExporter(SpanExporter):
    """把 SDK 的 ReadableSpan 批量写入独立 SQLite 文件。

    线程模型：BatchSpanProcessor 在单个后台工作线程调用 export()，
    主线程只会在 purge / shutdown 时触碰连接，统一用锁串行化。
    """

    def __init__(self, db_path: str):
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ── SpanExporter 接口 ────────────────────────────────────────
    def export(self, spans):
        rows = [self._to_row(s) for s in spans]
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            with self._lock:
                self._conn.executemany(_INSERT_SQL, rows)
                self._conn.commit()
            return SpanExportResult.SUCCESS
        except Exception:
            logger.exception("otel span export failed (%d spans dropped)", len(rows))
            return SpanExportResult.FAILURE

    def shutdown(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def force_flush(self, timeout_millis: int = 3000) -> bool:
        return True  # 每次 export 已逐批 commit，无需额外缓冲

    # ── 行转换与运维 ─────────────────────────────────────────────
    @staticmethod
    def _enum_int(value, default: int = 0) -> int:
        """OTel 枚举（StatusCode/SpanKind，1.x 为普通 Enum）→ int。"""
        if value is None:
            return default
        v = getattr(value, "value", value)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _to_row(cls, span) -> tuple:
        ctx = span.get_span_context()
        parent = span.parent
        start_ns = span.start_time or 0
        end_ns = span.end_time or start_ns
        status = span.status
        code = cls._enum_int(getattr(status, "status_code", None), STATUS_UNSET)
        kind = cls._enum_int(getattr(span, "kind", None), 1)
        message = getattr(status, "description", "") or ""
        attrs = json.dumps(_json_safe(dict(span.attributes or {})), ensure_ascii=False)
        events = json.dumps(
            [
                {
                    "name": e.name,
                    "timestamp_ns": e.timestamp,
                    "attributes": _json_safe(dict(e.attributes or {})),
                }
                for e in span.events
            ],
            ensure_ascii=False,
        )
        return (
            format(ctx.trace_id, "032x"),
            format(ctx.span_id, "016x"),
            format(parent.span_id, "016x") if parent else None,
            span.name,
            kind,
            start_ns,
            end_ns,
            round((end_ns - start_ns) / 1e6, 3),
            code,
            message[:500],
            attrs,
            events,
        )

    def purge_before(self, cutoff_ns: int) -> int:
        """删除保留期之外的 span，返回删除行数。"""
        with self._lock:
            if self._conn is None:
                return 0
            cur = self._conn.execute("DELETE FROM otel_spans WHERE start_ns < ?", (cutoff_ns,))
            self._conn.commit()
            return cur.rowcount or 0


# ── 初始化 / 关闭 ─────────────────────────────────────────────────

_exporter: "SQLiteSpanExporter | None" = None
_initialized = False
_cleanup_task: "asyncio.Task | None" = None


def init_otel(app=None) -> None:
    """应用启动时调用：安装 TracerProvider + SQLite exporter（幂等）。

    传入 FastAPI app 时同时挂 FastAPIInstrumentor（每个 HTTP 请求自动建
    SERVER span，业务手动 span 挂其下形成调用树）。
    OTEL_ENABLED=false 或 SDK 未安装时直接返回，tracer 保持 no-op。

    ⚠️ 时机约束：必须在 app 创建后、首个请求（含 uvicorn lifespan 握手）之前
    在模块级调用。FastAPIInstrumentor 通过替换 app.build_middleware_stack 挂载
    HTTP 中间件，Starlette 首次 __call__ 就会构建并缓存中间件栈；若放到 lifespan
    startup 里调用，栈已构建、patch 永不生效，SERVER span 静默丢失（手动 span 仍
    正常，极具迷惑性）。
    """
    global _initialized, _exporter
    if _initialized:
        return
    _initialized = True
    if not settings.OTEL_ENABLED:
        logger.info("OTel tracing disabled (OTEL_ENABLED=false)")
        return
    if not _OTEL_SDK_AVAILABLE:
        logger.warning(
            "opentelemetry-sdk 未安装，链路追踪自动关闭"
            "（pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi）"
        )
        return
    try:
        _exporter = SQLiteSpanExporter(settings.OTEL_DB_PATH)
        provider = TracerProvider(
            resource=Resource.create({"service.name": "ontology-backend"})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                _exporter,
                schedule_delay_millis=max(1000, settings.OTEL_EXPORT_INTERVAL_MS),
            )
        )
        _otel_trace.set_tracer_provider(provider)
        purge_expired_spans()
        logger.info(
            "OTel tracing enabled → %s（保留 %d 天）",
            settings.OTEL_DB_PATH, settings.OTEL_RETENTION_DAYS,
        )
        if app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                excluded = [
                    u.strip() for u in settings.OTEL_EXCLUDED_URLS.split(",")
                    if u.strip()
                ]
                FastAPIInstrumentor.instrument_app(app, excluded_urls=",".join(excluded))
                logger.info("OTel FastAPI instrumentation on (excluded: %s)", excluded)
            except ImportError:
                logger.warning(
                    "opentelemetry-instrumentation-fastapi 未安装，HTTP 自动埋点关闭"
                )
            except Exception:
                logger.exception("FastAPI instrumentation failed, 已跳过")
    except Exception:
        _exporter = None
        logger.exception("OTel init failed, tracing disabled")


def shutdown_otel() -> None:
    """应用退出时调用：强制刷新 BatchSpanProcessor 缓冲并释放连接。"""
    global _cleanup_task, _exporter
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        _cleanup_task = None
    if _OTEL_SDK_AVAILABLE:
        provider = _otel_trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()  # 内部会 flush 并回调 exporter.shutdown()
            except Exception:
                logger.exception("OTel provider shutdown failed")
    if _exporter is not None:
        _exporter.shutdown()
        _exporter = None


def purge_expired_spans() -> int:
    """清理超过保留期的 span（启动时与清理循环共用）。"""
    if _exporter is None:
        return 0
    cutoff = time.time_ns() - settings.OTEL_RETENTION_DAYS * 86400 * 1_000_000_000
    try:
        return _exporter.purge_before(cutoff)
    except Exception:
        logger.exception("otel purge failed")
        return 0


async def _cleanup_loop() -> None:
    """每日清理一次过期 span（由 server lifespan 启动/取消）。"""
    while True:
        await asyncio.sleep(6 * 3600)
        removed = purge_expired_spans()
        if removed:
            logger.info("OTel cleanup: removed %d expired spans", removed)


def start_cleanup_task() -> None:
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop())


# ── 业务埋点统一入口 ───────────────────────────────────────────────

class _NullSpan:
    """API 缺失时的哑 span：所有方法为 no-op，保证调用方无需判空。"""

    def set_attribute(self, *args, **kwargs):
        pass

    def record_exception(self, *args, **kwargs):
        pass

    def set_status(self, *args, **kwargs):
        pass

    def is_recording(self):
        return False


@asynccontextmanager
async def async_span(name: str, attributes: dict | None = None):
    """异步 span 上下文管理器：异常自动记录并标 ERROR，退出自动落库。

    SDK 未初始化时 tracer 为 no-op，此包装零开销（属性设置同样为 no-op）。
    """
    if not _OTEL_API_AVAILABLE:
        yield _NullSpan()
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name) as span:
        if attributes and span.is_recording():
            for key, value in attributes.items():
                if value is not None:
                    try:
                        span.set_attribute(key, value)
                    except Exception:
                        pass
        try:
            yield span
        except Exception as exc:
            if span.is_recording():
                span.record_exception(exc)
                span.set_status(
                    _otel_trace.Status(
                        _otel_trace.StatusCode.ERROR, description=str(exc)[:500]
                    )
                )
            raise


def set_llm_usage(span, response) -> None:
    """从 LangChain 响应中提取 token 用量写入 span 属性（拿不到则跳过）。"""
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        return
    span.set_attribute("llm.model", str(getattr(response, "model", "") or ""))
    span.set_attribute("llm.input_tokens", int(usage.get("input_tokens") or 0))
    span.set_attribute("llm.output_tokens", int(usage.get("output_tokens") or 0))
