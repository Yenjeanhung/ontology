"""启动组件自检。

在 uvicorn 之前（或 lifespan 内）探测各外部依赖是否就绪，输出状态清单；
必需组件未就绪时给出可操作的排查建议并中止启动，避免用户面对一长串原始堆栈。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field

from config import settings

OK = "ok"
FAIL = "fail"
SKIP = "skip"


@dataclass
class ComponentStatus:
    """单个组件的探测结果。"""

    name: str
    target: str  # 连接目标（已脱敏）
    required: bool  # 必需组件失败会阻断启动
    state: str = SKIP
    detail: str = ""
    elapsed_ms: float = 0.0
    hint: str = ""
    # 是否真正发起过连通性探测；False 表示仅做了配置/本地可用性检查（显示 i，避免误读为"已启动"）
    probed: bool = True

    @property
    def ok(self) -> bool:
        return self.state == OK

    @property
    def mark(self) -> str:
        if not self.probed:
            return "i"
        return {OK: "√", FAIL: "×", SKIP: "-"}[self.state]

    @property
    def state_label(self) -> str:
        if not self.probed:
            return "未探测"
        return {OK: "就绪", FAIL: "未就绪", SKIP: "已跳过"}[self.state]


def _summarize(exc: BaseException) -> str:
    msg = str(exc).strip()
    first = msg.splitlines()[0] if msg else ""
    if not first:
        errno = getattr(exc, "errno", None)
        first = f"errno={errno}" if errno is not None else exc.__class__.__name__
    return f"{exc.__class__.__name__}: {first[:200]}"


def _elapsed_since(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


async def _tcp_probe(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    """不依赖业务驱动的端口探活：只确认进程在监听，不做协议握手。"""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception as exc:
        return False, _summarize(exc)
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    return True, f"{host}:{port} 端口可达"


async def _check_postgres(timeout: float = 5.0) -> ComponentStatus:
    from database import DatabaseUnavailableError, check_database_connectivity, masked_database_url

    t0 = time.perf_counter()
    try:
        await check_database_connectivity(timeout)
        return ComponentStatus("PostgreSQL", masked_database_url(), True, OK, "连接正常",
                               _elapsed_since(t0), "docker-compose up -d postgres；或核对 .env 的 DATABASE_URL")
    except DatabaseUnavailableError as exc:
        return ComponentStatus("PostgreSQL", exc.target, True, FAIL, exc.reason,
                               _elapsed_since(t0), "docker-compose up -d postgres；或核对 .env 的 DATABASE_URL")
    except Exception as exc:
        return ComponentStatus("PostgreSQL", masked_database_url(), True, FAIL, _summarize(exc),
                               _elapsed_since(t0), "docker-compose up -d postgres；或核对 .env 的 DATABASE_URL")


async def _check_graph_store(timeout: float = 8.0) -> ComponentStatus:
    from providers.graph_store import (
        get_graph_store_provider_name,
        graph_store_health_check,
        graph_store_hint,
        graph_store_socket_target,
        graph_store_target,
    )

    # 全部信息由当前 provider 的 adapter 自行声明，自检不感知具体后端
    name = f"图存储({get_graph_store_provider_name()})"
    target = graph_store_target()
    hint = graph_store_hint()
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(graph_store_health_check), timeout)
        return ComponentStatus(name, target, True, OK, "连接正常", _elapsed_since(t0), hint)
    except TimeoutError:
        return ComponentStatus(name, target, True, FAIL, f"探测超时（超过 {timeout:g} 秒）",
                               _elapsed_since(t0), hint)
    except ModuleNotFoundError as exc:
        return await _fallback_check_without_driver(
            name, target, True, exc, graph_store_socket_target(), t0, hint)
    except Exception as exc:
        return ComponentStatus(name, target, True, FAIL, _summarize(exc), _elapsed_since(t0), hint)


async def _check_vector_store(timeout: float = 8.0) -> ComponentStatus:
    from providers.vector_store import (
        get_vector_store_provider_name,
        health_check,
        vector_store_hint,
        vector_store_socket_target,
        vector_store_target,
    )

    # 全部信息由当前 provider 的 adapter 自行声明，自检不感知具体后端
    name = f"向量库({get_vector_store_provider_name()})"
    target = vector_store_target()
    hint = vector_store_hint()
    # 与结构化库、图库同级：检索是核心链路，任一不可用即中止启动
    required = True
    t0 = time.perf_counter()
    try:
        ok, message, _extra = await asyncio.wait_for(asyncio.to_thread(health_check), timeout)
        state = OK if ok else FAIL
        return ComponentStatus(name, target, required, state, str(message)[:200], _elapsed_since(t0), hint)
    except TimeoutError:
        return ComponentStatus(name, target, required, FAIL, f"探测超时（超过 {timeout:g} 秒）",
                               _elapsed_since(t0), hint)
    except ModuleNotFoundError as exc:
        return await _fallback_check_without_driver(
            name, target, required, exc, vector_store_socket_target(), t0, hint)
    except Exception as exc:
        return ComponentStatus(name, target, required, FAIL, _summarize(exc), _elapsed_since(t0), hint)


async def _fallback_check_without_driver(
    name: str,
    target: str,
    required: bool,
    exc: ModuleNotFoundError,
    sock: tuple[str, int] | None,
    t0: float,
    hint: str,
) -> ComponentStatus:
    """客户端驱动缺失时的降级探测：远端服务退化为 TCP 探活，本地库检查目录。

    避免"服务正常但缺客户端包"被误判为"组件未启动"。
    """
    missing = exc.name or "客户端驱动"
    install_hint = f"pip install {missing}" if exc.name else "安装对应客户端依赖"

    if sock is not None:
        reachable, probe_detail = await _tcp_probe(*sock)
        if reachable:
            return ComponentStatus(name, target, required, OK,
                                   f"端口可达（未安装 {missing}，跳过协议级健康检查）",
                                   _elapsed_since(t0),
                                   f"{install_hint} 后可获得更精确的健康检查结果")
        return ComponentStatus(name, target, required, FAIL,
                               f"端口不可达（{probe_detail}）", _elapsed_since(t0),
                               f"{hint}；{install_hint}")

    # 本地文件库：目录已存在即认为组件在位（首次使用时会自动创建则视为未初始化）
    if os.path.isdir(target):
        return ComponentStatus(name, target, required, OK,
                               f"本地目录已存在（未安装 {missing}，跳过健康检查）",
                               _elapsed_since(t0),
                               f"{install_hint} 后可获得更精确的健康检查结果")
    return ComponentStatus(name, target, required, FAIL,
                           f"缺少依赖：{install_hint}", _elapsed_since(t0), f"{install_hint}；{hint}")


async def _check_embedding() -> ComponentStatus:
    """嵌入模型：本地模型首次加载耗时不可控，这里只做配置级检查，不实际加载。"""
    provider = settings.EMBEDDING_PROVIDER
    model = settings.EMBEDDING_MODEL if provider == "local" else settings.OPENAI_EMBEDDING_MODEL
    target = f"{provider} / {model}"

    if provider == "openai" and not settings.OPENAI_API_KEY:
        return ComponentStatus("嵌入模型", target, False, FAIL, "未配置 OPENAI_API_KEY",
                               0.0, "在 .env 配置 OPENAI_API_KEY，或改用 EMBEDDING_PROVIDER=local")

    # 仅做配置检查：本地模型首次加载耗时不可控，不在这里真实探测
    if provider == "local":
        detail = "配置已就位；本地模型在首次使用时加载，未做连通性探测"
        hint = "若首次下载慢，可预置 HF_CACHE_DIR 或改用 EMBEDDING_PROVIDER=openai"
    else:
        detail = "接口型模型，凭据已配置；未做连通性探测"
        hint = "如调用失败请检查 OPENAI_BASE_URL / OPENAI_API_KEY"
    return ComponentStatus("嵌入模型", target, False, OK, detail, 0.0, hint, probed=False)


async def _check_tika(timeout: float = 2.0) -> ComponentStatus:
    """Tika 为可选兜底解析器，未启用或不可用时仅提示，不阻断启动。"""
    endpoint = (settings.TIKA_SERVER_ENDPOINT or "").strip()
    if not settings.TIKA_FALLBACK_ENABLED:
        return ComponentStatus("Tika(可选)", "未启用", False, SKIP, "TIKA_FALLBACK_ENABLED=false", 0.0, "")

    if endpoint:
        t0 = time.perf_counter()
        try:
            import httpx

            def _probe() -> str:
                resp = httpx.get(f"{endpoint.rstrip('/')}/version", timeout=timeout)
                resp.raise_for_status()
                return (resp.text or "").strip()[:60] or "就绪"

            detail = await asyncio.wait_for(asyncio.to_thread(_probe), timeout + 1)
            return ComponentStatus("Tika(可选)", endpoint, False, OK, detail,
                                   _elapsed_since(t0), "")
        except Exception as exc:
            return ComponentStatus("Tika(可选)", endpoint, False, FAIL, _summarize(exc),
                                   _elapsed_since(t0), "检查 TIKA_SERVER_ENDPOINT 是否可达（可选组件，不影响启动）")

    java = settings.TIKA_JAVA_PATH or shutil.which("java") or ""
    if java:
        return ComponentStatus("Tika(可选)", f"本地 JRE: {java}", False, OK, "已就绪", 0.0, "")
    return ComponentStatus("Tika(可选)", "本地 JRE", False, FAIL, "未找到 java，未知格式解析将跳过 Tika 兜底",
                           0.0, "可选：安装 JRE，或配置 TIKA_SERVER_ENDPOINT 走外部 Tika Server")


_LAST_RESULT: list[ComponentStatus] | None = None


def has_run() -> bool:
    """本进程是否已执行过自检（用于避免 __main__ 与 lifespan 重复探测/打印）。"""
    return _LAST_RESULT is not None


async def run_preflight(dispose_engine: bool = True) -> list[ComponentStatus]:
    """并发探测全部组件。dispose_engine 用于在临时事件循环中释放连接池。

    同一进程内只真正探测一次，后续调用直接复用结果。
    """
    global _LAST_RESULT
    if _LAST_RESULT is not None:
        return _LAST_RESULT

    results = await asyncio.gather(
        _check_postgres(),
        _check_graph_store(),
        _check_vector_store(),
        _check_embedding(),
        _check_tika(),
    )
    if dispose_engine:
        # 供 `asyncio.run(preflight)` 场景：避免残留旧事件循环的连接
        from database import engine

        await engine.dispose()

    _LAST_RESULT = list(results)
    return _LAST_RESULT


def failed_required(items: list[ComponentStatus]) -> list[ComponentStatus]:
    return [it for it in items if it.required and not it.ok]


def render_report(items: list[ComponentStatus]) -> str:
    """渲染成适合控制台阅读的清单。"""
    lines = ["=" * 68, "启动组件自检", "=" * 68]
    for it in items:
        elapsed = f"{it.elapsed_ms:.0f}ms" if it.elapsed_ms else "--"
        lines.append(f"[{it.mark}] {it.name:<16} {elapsed:>7}  {it.target}  ({it.state_label})")
        if it.detail:
            lines.append(f"      └ {it.detail}")
    lines.append("=" * 68)

    broken = failed_required(items)
    warned = [it for it in items if not it.required and not it.ok]

    if warned:
        lines.append(f"{len(warned)} 个可选组件未就绪（不影响启动，相关功能不可用）：")
        for it in warned:
            lines.append(f"  ! {it.name}: {it.detail}")
            if it.hint:
                lines.append(f"      → {it.hint}")

    if broken:
        lines.append(f"共 {len(broken)} 个必需组件未就绪：{', '.join(it.name for it in broken)}")
        for it in broken:
            if it.hint:
                lines.append(f"  → {it.name}: {it.hint}")
        lines.append("请启动上述组件后重试；服务已中止启动。")
    else:
        lines.append("全部必需组件已就绪，继续启动。")
    lines.append("标记：√=已就绪（实际探测）  ×=未就绪  i=配置/本地检查（未探测）  -=已跳过")
    lines.append("=" * 68)
    return "\n".join(lines)
