"""HTTP 访问日志中间件：一处织入，记录全部请求。

日志样例（logger 名 `access`，沿用 server.setup_logging 的处理器）：
    2026-08-29 20:41:03 - access - INFO - 127.0.0.1 GET /api/workflows -> 200 12.4ms

为什么用纯 ASGI 而不是 starlette 的 BaseHTTPMiddleware：
    BaseHTTPMiddleware 会把响应体在内存中缓冲完整后再下发，工作流 /run、
    /resume 这类 SSE 流式接口会退化成"转圈半天后一次性吐出"。
    这里直接实现 ASGI 协议，仅在 http.response.start 时读取状态码，
    不触碰响应体，对 SSE 完全透明。
"""
from __future__ import annotations

import logging
import time

from config import settings

logger = logging.getLogger("access")

# 前端静态资源：数量大且无排查价值，直接跳过
STATIC_EXTENSIONS = (
    ".js", ".mjs", ".css", ".map", ".ico", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".webp", ".woff", ".woff2", ".ttf", ".eot",
)


def _skip_paths() -> set[str]:
    raw = getattr(settings, "ACCESS_LOG_SKIP_PATHS", "") or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


def _should_skip(path: str, skip: set[str]) -> bool:
    if path in skip:
        return True
    return path.lower().endswith(STATIC_EXTENSIONS)


class AccessLogMiddleware:
    """纯 ASGI 中间件：记录 IP / 方法 / 路径 / 状态码 / 耗时。

    - 5xx 记 ERROR，慢请求（> ACCESS_LOG_SLOW_MS）记 WARNING，其余记 INFO；
    - 未捕获异常：记 ERROR（含堆栈）后原样抛出，交给 FastAPI 的异常处理；
    - 非 http 作用域（websocket / lifespan）直接放行。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not getattr(settings, "ACCESS_LOG_ENABLED", True):
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        if _should_skip(path, _skip_paths()):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "-")
        client = scope.get("client")
        ip = client[0] if client else "-"
        start = time.perf_counter()
        status = 0

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            cost = (time.perf_counter() - start) * 1000
            logger.exception("%s %s %s -> 未捕获异常 %.1fms", ip, method, path, cost)
            raise
        finally:
            # status=0 表示响应头都还没发出（异常路径已在 except 记录），此处不重复输出
            if status:
                cost = (time.perf_counter() - start) * 1000
                line = f"{ip} {method} {path} -> {status} {cost:.1f}ms"
                if status >= 500:
                    logger.error(line)
                elif cost >= getattr(settings, "ACCESS_LOG_SLOW_MS", 3000):
                    logger.warning("%s（慢请求）", line)
                else:
                    logger.info(line)
