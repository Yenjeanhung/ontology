"""
系统监控服务（Monitor Service）

职责：
  - 组件注册表：登记系统全部可插拔/可监控的后端组件；
  - 健康检查聚合：对每个组件执行连通性/可用性检测，支持超时与缓存；
  - 系统信息采集：Python 版本、平台、内存、磁盘、服务运行时长；
  - 配置脱敏：对外暴露配置摘要时隐藏密钥。

设计约定（见 doc/监控/00-监控模块设计.md）：
  - LLM 健康检查 = 真实连通性调用（不调用模型，不产生计费）；
  - 健康检查默认在后台线程池执行，单组件超时；
  - 结果短暂缓存，避免频繁压测外部组件。
"""
from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from database import async_session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# 状态定义
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"
STATUS_UNCONFIGURED = "unconfigured"

# 检测结果缓存时长（秒）
_CACHE_TTL = 10
# 最近一次全量检测结果（含时间戳）
_last_snapshot: dict | None = None
_last_snapshot_at: float = 0.0

# 进程启动时间（服务运行时长计算）
_process_start = time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════ 配置脱敏 ═══════════════════════

def mask_secret(value: str, keep: int = 4) -> str:
    """密钥脱敏：保留前 keep 位，其余以 *** 替代。空值原样返回。"""
    if not value:
        return value or ""
    if len(value) <= keep:
        return value[:1] + "***"
    return value[:keep] + "***"


def _mask_db_url(url: str) -> str:
    """对 DATABASE_URL 脱敏：隐藏密码，保留驱动与主机信息。"""
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        if parts.password:
            netloc = parts.hostname or ""
            if parts.port:
                netloc += f":{parts.port}"
            if parts.username:
                netloc = f"{parts.username}:***@{netloc}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return url


# ═══════════════════════ 各组件检测实现 ═══════════════════════

async def _check_database() -> tuple[bool, str, dict]:
    """关系数据库：执行 SELECT 1 测连通性。"""
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        return True, "SELECT 1 ok", {"url": _mask_db_url(settings.DATABASE_URL)}
    except Exception as e:
        return False, f"连接失败：{e}", {"url": _mask_db_url(settings.DATABASE_URL)}


def _check_graph_store() -> tuple[bool, str, dict]:
    """图数据库：调用当前 adapter 的 health_check()。"""
    try:
        from providers import graph_store
        ok = graph_store.graph_store_health_check()
        extra = {"provider": graph_store.get_graph_store_provider_name()}
        if settings.GRAPH_STORE_PROVIDER == "kuzu":
            extra["db_path"] = settings.KUZU_DB_PATH
        else:
            extra["uri"] = settings.NEO4J_URI
            extra["database"] = settings.NEO4J_DATABASE
        if ok:
            return True, "health check passed", extra
        return False, "health check failed", extra
    except Exception as e:
        return False, f"health check failed：{e}", {}


def _check_vector_store() -> tuple[bool, str, dict]:
    """向量数据库：调用当前 adapter 的 health_check()。"""
    try:
        from providers import vector_store
        ok, message, extra = vector_store.health_check()
        extra = dict(extra or {})
        extra["provider"] = vector_store.get_vector_store_provider_name()
        return ok, message, extra
    except Exception as e:
        return False, f"health check failed：{e}", {}


def _check_embedding() -> tuple[bool, str, dict]:
    """嵌入模型：构造实例并 embed 一段短文本（本地模型首次加载慢）。"""
    try:
        from providers import embedding
        ok, message, extra = embedding.health_check()
        extra = dict(extra or {})
        extra["provider"] = embedding.get_embedding_provider_name()
        return ok, message, extra
    except Exception as e:
        return False, f"embed failed：{e}", {}


def _check_llm() -> tuple[bool, str, dict]:
    """LLM：真实连通性调用（不调模型）——校验 API Key / Base URL / Model 有效。

    对 OpenAI 兼容接口请求 /models；对 Anthropic 请求 /v1/models。
    不产生模型生成计费。
    """
    provider = settings.LLM_PROVIDER
    api_key = settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL
    model = settings.LLM_MODEL
    if not api_key or not model:
        return False, "未配置（缺 API Key 或 Model）", {"provider": provider or "openai"}

    import httpx

    headers: dict[str, str]
    if provider == "anthropic":
        url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        ok = resp.status_code < 400
        msg = f"HTTP {resp.status_code}" if ok else f"HTTP {resp.status_code}：{resp.text[:120]}"
        return ok, msg, {"provider": provider or "openai", "model": model, "base_url": base_url or "(default)"}
    except Exception as e:
        return False, f"连接失败：{e}", {"provider": provider or "openai", "model": model, "base_url": base_url or "(default)"}


def _check_parser() -> tuple[bool, str, dict]:
    """文档解析器：专用解析库可导入 + Tika 兜底状态。"""
    extras: dict = {"tika_fallback_enabled": settings.TIKA_FALLBACK_ENABLED}
    try:
        from providers.parser.tika_runtime import tika_available
        extras["tika_available"] = tika_available()
    except Exception:
        extras["tika_available"] = False

    ok = extras["tika_available"] or extras["tika_fallback_enabled"] is not None
    message = f"Tika 兜底 {'可用' if extras['tika_available'] else '不可用'}"
    if settings.TIKA_SERVER_ENDPOINT:
        extras["tika_server_endpoint"] = settings.TIKA_SERVER_ENDPOINT
    return ok, message, extras


def _check_crawl() -> tuple[bool, str, dict]:
    """爬虫采集器：CRAWL_ENABLED 开关 + 依赖可用性。"""
    extras = {
        "enabled": settings.CRAWL_ENABLED,
        "jina_fallback": settings.CRAWL_JINA_FALLBACK,
        "max_pages": settings.CRAWL_MAX_PAGES,
    }
    if not settings.CRAWL_ENABLED:
        return False, "未启用（CRAWL_ENABLED=false）", extras
    try:
        import requests  # noqa: F401
        return True, "requests 可用", extras
    except Exception as e:
        return False, f"依赖缺失：{e}", extras


def _check_search() -> tuple[bool, str, dict]:
    """搜索引擎：SEARCH_PROVIDER + 对应 API Key 配置完整性。"""
    provider = settings.SEARCH_PROVIDER.strip().lower()
    extras = {"provider": provider}
    if provider not in ("tavily", "bing", "duckduckgo"):
        return False, f"未知 provider：{provider}", extras
    if provider == "tavily":
        if not settings.TAVILY_API_KEY:
            return False, "已启用但未配置 TAVILY_API_KEY", extras
        extras["api_key"] = mask_secret(settings.TAVILY_API_KEY)
        return True, "配置完整（Tavily）", extras
    # bing / duckduckgo 无需 API Key
    return True, f"配置完整（{provider}）", extras


def _check_scheduler() -> tuple[bool, str, dict]:
    """调度引擎：is_running() + job 数。"""
    from services import scheduler_engine

    extras = {"enabled": settings.SCHEDULER_ENABLED}
    if not settings.SCHEDULER_ENABLED:
        return False, "未启用（SCHEDULER_ENABLED=false）", extras
    try:
        running = scheduler_engine.is_running()
        extras["running"] = running
        if running:
            extras["job_count"] = len(scheduler_engine._scheduler.get_jobs())
            return True, "调度引擎运行中", extras
        return False, "调度引擎未运行", extras
    except Exception as e:
        return False, f"检测失败：{e}", extras


def _check_workflow() -> tuple[bool, str, dict]:
    """工作流引擎：模块可导入 + 配置就绪。"""
    extras = {
        "max_nodes": settings.WORKFLOW_MAX_NODES,
        "max_steps": settings.WORKFLOW_MAX_STEPS,
        "run_timeout_seconds": settings.WORKFLOW_RUN_TIMEOUT_SECONDS,
    }
    try:
        from services import workflow_engine  # noqa: F401
        return True, "工作流引擎可用", extras
    except Exception as e:
        return False, f"模块导入失败：{e}", extras


# ═══════════════════════ 组件注册表 ═══════════════════════

# 每个组件：
#   key / name / category：标识与分类
#   provider_func：当前 provider 名
#   providers_available：可选 provider 列表
#   enabled_func：是否启用（False -> disabled；True 但配置不全 -> unconfigured 由 check 自行判定）
#   check_func：同步检测函数，返回 (ok, message, extra)；抛异常视为 error
#   timeout：检测超时（秒）
#   config_func：配置摘要（键值，值已脱敏）
_COMPONENTS: list[dict] = [
    {
        "key": "database",
        "name": "关系数据库",
        "category": "data_store",
        "provider_func": lambda: settings.DATABASE_URL.split(":")[0].split("+")[0] if settings.DATABASE_URL else "sqlite",
        "providers_available": ["sqlite", "postgresql", "mysql"],
        "enabled_func": lambda: True,
        "check_func": _check_database,
        "timeout": 5.0,
        "config_func": lambda: {"DATABASE_URL": _mask_db_url(settings.DATABASE_URL)},
    },
    {
        "key": "graph_store",
        "name": "图数据库",
        "category": "data_store",
        "provider_func": lambda: settings.GRAPH_STORE_PROVIDER,
        "providers_available": ["kuzu", "neo4j"],
        "enabled_func": lambda: True,
        "check_func": _check_graph_store,
        "timeout": 10.0,
        "config_func": lambda: {
            "GRAPH_STORE_PROVIDER": settings.GRAPH_STORE_PROVIDER,
            "KUZU_DB_PATH": settings.KUZU_DB_PATH if settings.GRAPH_STORE_PROVIDER == "kuzu" else None,
            "NEO4J_URI": settings.NEO4J_URI if settings.GRAPH_STORE_PROVIDER == "neo4j" else None,
        },
    },
    {
        "key": "vector_store",
        "name": "向量数据库",
        "category": "data_store",
        "provider_func": lambda: settings.VECTOR_STORE_PROVIDER,
        "providers_available": ["chroma", "milvus"],
        "enabled_func": lambda: True,
        "check_func": _check_vector_store,
        "timeout": 10.0,
        "config_func": lambda: {
            "VECTOR_STORE_PROVIDER": settings.VECTOR_STORE_PROVIDER,
            "CHROMA_PERSIST_DIR": settings.CHROMA_PERSIST_DIR if settings.VECTOR_STORE_PROVIDER == "chroma" else None,
            "MILVUS_HOST": settings.MILVUS_HOST if settings.VECTOR_STORE_PROVIDER == "milvus" else None,
            "MILVUS_PORT": settings.MILVUS_PORT if settings.VECTOR_STORE_PROVIDER == "milvus" else None,
        },
    },
    {
        "key": "embedding",
        "name": "嵌入模型",
        "category": "ai",
        "provider_func": lambda: settings.EMBEDDING_PROVIDER,
        "providers_available": ["local", "openai"],
        "enabled_func": lambda: True,
        "check_func": _check_embedding,
        "timeout": 60.0,
        "config_func": lambda: {
            "EMBEDDING_PROVIDER": settings.EMBEDDING_PROVIDER,
            "EMBEDDING_MODEL": settings.EMBEDDING_MODEL if settings.EMBEDDING_PROVIDER == "local" else settings.OPENAI_EMBEDDING_MODEL,
            "OPENAI_EMBEDDING_DIMENSION": settings.OPENAI_EMBEDDING_DIMENSION,
        },
    },
    {
        "key": "llm",
        "name": "LLM",
        "category": "ai",
        "provider_func": lambda: settings.LLM_PROVIDER,
        "providers_available": ["openai", "anthropic"],
        "enabled_func": lambda: True,
        "check_func": _check_llm,
        "timeout": 15.0,
        "config_func": lambda: {
            "LLM_PROVIDER": settings.LLM_PROVIDER,
            "LLM_MODEL": settings.LLM_MODEL,
            "OPENAI_API_KEY": mask_secret(settings.OPENAI_API_KEY),
            "OPENAI_BASE_URL": settings.OPENAI_BASE_URL or "(default)",
            "LLM_MAX_TOKENS": settings.LLM_MAX_TOKENS,
        },
    },
    {
        "key": "parser",
        "name": "文档解析器",
        "category": "parse",
        "provider_func": lambda: "tika" if settings.TIKA_SERVER_ENDPOINT else ("tika_local" if settings.TIKA_FALLBACK_ENABLED else "builtin"),
        "providers_available": ["builtin", "tika"],
        "enabled_func": lambda: True,
        "check_func": _check_parser,
        "timeout": 5.0,
        "config_func": lambda: {
            "TIKA_FALLBACK_ENABLED": settings.TIKA_FALLBACK_ENABLED,
            "TIKA_SERVER_ENDPOINT": settings.TIKA_SERVER_ENDPOINT or "(未配置，本地 JRE 模式)",
        },
    },
    {
        "key": "crawl",
        "name": "爬虫采集器",
        "category": "parse",
        "provider_func": lambda: "jina" if settings.CRAWL_JINA_FALLBACK else "requests",
        "providers_available": ["requests", "jina"],
        "enabled_func": lambda: settings.CRAWL_ENABLED,
        "check_func": _check_crawl,
        "timeout": 5.0,
        "config_func": lambda: {
            "CRAWL_ENABLED": settings.CRAWL_ENABLED,
            "CRAWL_JINA_FALLBACK": settings.CRAWL_JINA_FALLBACK,
            "CRAWL_MAX_PAGES": settings.CRAWL_MAX_PAGES,
            "CRAWL_TIMEOUT_SECONDS": settings.CRAWL_TIMEOUT_SECONDS,
        },
    },
    {
        "key": "search",
        "name": "搜索引擎",
        "category": "parse",
        "provider_func": lambda: settings.SEARCH_PROVIDER,
        "providers_available": ["tavily", "bing", "duckduckgo"],
        "enabled_func": lambda: True,
        "check_func": _check_search,
        "timeout": 5.0,
        "config_func": lambda: {
            "SEARCH_PROVIDER": settings.SEARCH_PROVIDER,
            "TAVILY_API_KEY": mask_secret(settings.TAVILY_API_KEY),
        },
    },
    {
        "key": "scheduler",
        "name": "调度引擎",
        "category": "service",
        "provider_func": lambda: "apscheduler",
        "providers_available": ["apscheduler"],
        "enabled_func": lambda: settings.SCHEDULER_ENABLED,
        "check_func": _check_scheduler,
        "timeout": 5.0,
        "config_func": lambda: {
            "SCHEDULER_ENABLED": settings.SCHEDULER_ENABLED,
            "SCHEDULER_TIMEZONE": settings.SCHEDULER_TIMEZONE,
            "SCHEDULER_MAX_CONCURRENT_RUNS": settings.SCHEDULER_MAX_CONCURRENT_RUNS,
        },
    },
    {
        "key": "workflow",
        "name": "工作流引擎",
        "category": "service",
        "provider_func": lambda: "builtin",
        "providers_available": ["builtin"],
        "enabled_func": lambda: True,
        "check_func": _check_workflow,
        "timeout": 5.0,
        "config_func": lambda: {
            "WORKFLOW_MAX_NODES": settings.WORKFLOW_MAX_NODES,
            "WORKFLOW_MAX_STEPS": settings.WORKFLOW_MAX_STEPS,
            "WORKFLOW_RUN_TIMEOUT_SECONDS": settings.WORKFLOW_RUN_TIMEOUT_SECONDS,
        },
    },
]


def get_components() -> list[dict]:
    """返回组件元信息（不含状态）。"""
    return [
        {
            "key": c["key"],
            "name": c["name"],
            "category": c["category"],
            "providers_available": c["providers_available"],
        }
        for c in _COMPONENTS
    ]


# ═══════════════════════ 健康检查聚合 ═══════════════════════

async def _run_one(comp: dict) -> dict:
    """执行单个组件检测，返回结果 dict。"""
    started = time.perf_counter()
    key = comp["key"]
    enabled = comp["enabled_func"]()
    if not enabled:
        return {
            "key": key,
            "name": comp["name"],
            "category": comp["category"],
            "provider": comp["provider_func"](),
            "providers_available": comp["providers_available"],
            "status": STATUS_DISABLED,
            "latency_ms": 0,
            "message": "未启用",
            "config": _safe_config(comp),
            "extra": {},
        }

    timeout = comp["timeout"]
    check = comp["check_func"]
    try:
        if asyncio.iscoroutinefunction(check):
            ok, message, extra = await asyncio.wait_for(check(), timeout=timeout)
        else:
            ok, message, extra = await asyncio.wait_for(asyncio.to_thread(check), timeout=timeout)
        status = STATUS_OK if ok else STATUS_UNCONFIGURED
        # 检测函数返回 False 但 message 以"未配置/未启用"开头视为 unconfigured，
        # 其余视为 error（组件已启用但不可用）。
        if not ok:
            low = (message or "").lower()
            if ("未配置" in low or "未启用" in low or "未知 provider" in low or "缺 api key" in low
                    or "not configured" in low or "disabled" in low):
                status = STATUS_UNCONFIGURED if "未配置" in low or "缺 api key" in low or "not configured" in low else STATUS_DISABLED
            else:
                status = STATUS_ERROR
    except asyncio.TimeoutError:
        ok, message, extra = False, f"检测超时（>{timeout}s）", {}
        status = STATUS_ERROR
    except Exception as e:
        logger.warning("monitor component %s check failed: %s", key, e)
        ok, message, extra = False, f"检测异常：{e}", {}
        status = STATUS_ERROR

    return {
        "key": key,
        "name": comp["name"],
        "category": comp["category"],
        "provider": comp["provider_func"](),
        "providers_available": comp["providers_available"],
        "status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "message": message,
        "config": _safe_config(comp),
        "extra": extra,
    }


def _safe_config(comp: dict) -> dict:
    """取配置摘要并剔除空值（None / 空串）。"""
    try:
        cfg = comp["config_func"]()
    except Exception as e:
        logger.warning("monitor component %s config failed: %s", comp["key"], e)
        return {}
    return {k: v for k, v in cfg.items() if v is not None and v != ""}


async def run_health_checks() -> list[dict]:
    """并发执行全部组件健康检查。"""
    results = await asyncio.gather(*(_run_one(c) for c in _COMPONENTS))
    return list(results)


# ═══════════════════════ 系统信息 ═══════════════════════

def _collect_system() -> dict:
    """采集系统运行信息。"""
    info: dict = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "started_at": datetime.fromtimestamp(_process_start, tz=timezone.utc).isoformat(),
        "uptime_seconds": int(time.time() - _process_start),
    }

    # 内存 / CPU（psutil 可选）
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["memory"] = {
            "total_mb": round(vm.total / 1024 / 1024),
            "used_mb": round(vm.used / 1024 / 1024),
            "percent": round(vm.percent, 1),
        }
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
    except Exception:
        pass

    # 磁盘（根目录 + data 目录）
    try:
        for label, path in (("root", Path(".")), ("data", Path("./data"))):
            usage = shutil.disk_usage(path if path.exists() else Path("."))
            info.setdefault("disk", {})[label] = {
                "total_gb": round(usage.total / 1024 ** 3, 1),
                "used_gb": round(usage.used / 1024 ** 3, 1),
                "free_gb": round(usage.free / 1024 ** 3, 1),
            }
    except Exception:
        pass

    # 数据目录体积
    try:
        data_dir = Path("./data")
        size = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())
        info["data_dir_gb"] = round(size / 1024 ** 3, 2)
    except Exception:
        pass

    return info


# ═══════════════════════ 快照与缓存 ═══════════════════════

async def build_snapshot(use_cache: bool = True) -> dict:
    """构建监控总览快照：组件状态 + 摘要 + 系统信息。

    use_cache=True 且缓存未过期时直接返回缓存，避免反复压测外部组件。
    """
    global _last_snapshot, _last_snapshot_at
    now = time.time()
    if use_cache and _last_snapshot is not None and (now - _last_snapshot_at) < _CACHE_TTL:
        return _last_snapshot

    components = await run_health_checks()
    summary = {"total": len(components), "ok": 0, "error": 0, "disabled": 0, "unconfigured": 0}
    for comp in components:
        summary[comp["status"]] = summary.get(comp["status"], 0) + 1

    snapshot = {
        "status": "ok",
        "checked_at": _now_iso(),
        "summary": summary,
        "components": components,
        "system": _collect_system(),
    }
    _last_snapshot = snapshot
    _last_snapshot_at = now
    return snapshot
