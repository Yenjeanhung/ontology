"""服务层方法级日志：等价于 Java AOP 的 @Around 通知。

两种用法
--------
1. 显式织入（精确控制单个方法）::

       from core.tracing import traced

       class FooService:
           @staticmethod
           @traced
           async def bar(db, x): ...

2. 自动织入（启动时统一给 services 包下所有类的公共方法打点）::

       # server.py lifespan 启动阶段
       from core.tracing import instrument_services
       instrument_services()

日志样例（logger 名 `trace`）::

    DEBUG trace HumanTaskService.list(db=<AsyncSession>, status='pending') -> dict(len=2) 1.8ms
    WARN  trace WorkflowService.save(...) -> dict(len=1) 2310.4ms（慢调用）
    ERROR trace KbService.get(...) -> 异常 ValueError: kb not found 0.4ms

设计取舍
--------
- **只织入类方法**，不动模块级函数：模块级函数常被其他模块 `from x import f` 持有引用，
  运行期替换类属性不会同步这些引用，容易织了一半导致行为不一致。
- **值摘要而非原样打印**：长文本/大对象只打类型和长度，避免单条日志几十 KB。
- **敏感值脱敏**：字段名含 password/token/api_key 等一律打 `***`。
- **日志级别未启用时零开销**：先判断 logger 是否开启，否则直接透传原函数。
"""
from __future__ import annotations

import fnmatch
import functools
import importlib
import inspect
import logging
import pkgutil
import time

from config import settings

logger = logging.getLogger("trace")

_WRAPPED_FLAG = "__traced__"

# 字段名包含这些关键词 → 值一律脱敏
SENSITIVE_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_key", "authorization", "cookie", "credential", "private_key",
)

# 永不织入的方法名（元信息 / 高频无意义调用）
EXCLUDE_METHODS = frozenset({"__wrapped__"})


# ══════════════════════════ 值摘要 ══════════════════════════

def _is_sensitive(name: str) -> bool:
    low = str(name).lower()
    return any(k in low for k in SENSITIVE_KEYS)


def summarize(value, max_len: int = 200) -> str:
    """把任意值压成一行摘要：长文本截断、大对象只给类型与长度、不调用 __str__。"""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if len(value) > max_len:
            return f"str(len={len(value)}) {value[:max_len]!r}…"
        return repr(value)
    if isinstance(value, dict):
        keys = [str(k) for k in list(value.keys())[:6]]
        more = "…" if len(value) > 6 else ""
        return f"dict(len={len(value)}) keys={keys}{more}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"{type(value).__name__}(len={len(value)})"
    if isinstance(value, bytes):
        return f"bytes(len={len(value)})"
    # 任意对象：只给类名，避免 __str__ 的副作用或超大字符串
    cls = type(value)
    return f"<{cls.__module__}.{cls.__name__}>"


def _format_call(func, args, kwargs, max_len: int, log_args: bool) -> str:
    """渲染调用参数：`(db=<AsyncSession>, status='pending', keyword=None)`。"""
    if not log_args:
        return "()"
    try:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        parts = []
        for name, val in bound.arguments.items():
            if name in ("self", "cls"):
                continue
            parts.append(f"{name}=***" if _is_sensitive(name) else f"{name}={summarize(val, max_len)}")
        return "(" + ", ".join(parts) + ")"
    except Exception:
        return f"(<{len(args)} args, {len(kwargs)} kwargs>)"


# ══════════════════════════ 装饰器本体 ══════════════════════════

def _level() -> int:
    return getattr(logging, (settings.SERVICE_TRACE_LEVEL or "DEBUG").upper(), logging.DEBUG)


def _emit(qualname: str, call: str, result, start: float) -> None:
    cost = (time.perf_counter() - start) * 1000
    ret = summarize(result, 120)
    if cost >= max(int(settings.SERVICE_TRACE_SLOW_MS or 0), 0):
        logger.warning("%s%s -> %s %.1fms（慢调用）", qualname, call, ret, cost)
    else:
        logger.log(_level(), "%s%s -> %s %.1fms", qualname, call, ret, cost)


def traced(_func=None):
    """方法/函数级 @Around 通知：记录入参、返回值摘要与耗时，异常照常向上抛。

    可用作 `@traced` 或 `@traced()`。对已织入的函数重复装饰是幂等的。
    """
    def decorate(func):
        if getattr(func, _WRAPPED_FLAG, False):
            return func

        qualname = f"{getattr(func, '__module__', '?')}.{getattr(func, '__qualname__', func)}"

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not settings.SERVICE_TRACE_ENABLED or not _worth_logging():
                    return await func(*args, **kwargs)
                start = time.perf_counter()
                call = _format_call(func, args, kwargs,
                                    int(settings.SERVICE_TRACE_MAX_ARG_LEN or 300),
                                    bool(settings.SERVICE_TRACE_LOG_ARGS))
                try:
                    result = await func(*args, **kwargs)
                except Exception as e:
                    cost = (time.perf_counter() - start) * 1000
                    logger.error("%s%s -> 异常 %s: %s %.1fms",
                                 qualname, call, type(e).__name__, e, cost)
                    raise
                _emit(qualname, call, result, start)
                return result
            wrapper = async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                if not settings.SERVICE_TRACE_ENABLED or not _worth_logging():
                    return func(*args, **kwargs)
                start = time.perf_counter()
                call = _format_call(func, args, kwargs,
                                    int(settings.SERVICE_TRACE_MAX_ARG_LEN or 300),
                                    bool(settings.SERVICE_TRACE_LOG_ARGS))
                try:
                    result = func(*args, **kwargs)
                except Exception as e:
                    cost = (time.perf_counter() - start) * 1000
                    logger.error("%s%s -> 异常 %s: %s %.1fms",
                                 qualname, call, type(e).__name__, e, cost)
                    raise
                _emit(qualname, call, result, start)
                return result
            wrapper = sync_wrapper

        setattr(wrapper, _WRAPPED_FLAG, True)
        return wrapper

    if _func is None:
        return decorate
    return decorate(_func)


def _worth_logging() -> bool:
    """日志级别全关时直接透传，省掉格式化开销。"""
    level = _level()
    return logger.isEnabledFor(level) or logger.isEnabledFor(logging.WARNING)


# ══════════════════════════ 自动织入 ══════════════════════════

def _excluded(name: str) -> bool:
    raw = getattr(settings, "SERVICE_TRACE_EXCLUDE", "") or ""
    patterns = [p.strip() for p in raw.split(",") if p.strip()]
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _instrument_class(cls) -> int:
    """给类的公共方法织入日志，返回织入数量。只处理该类自身定义的方法。"""
    count = 0
    for name, attr in list(vars(cls).items()):
        if name.startswith("_") or name in EXCLUDE_METHODS:
            continue
        if _excluded(f"{cls.__name__}.{name}") or _excluded(cls.__name__):
            continue
        if isinstance(attr, staticmethod):
            setattr(cls, name, staticmethod(traced(attr.__func__)))
            count += 1
        elif isinstance(attr, classmethod):
            setattr(cls, name, classmethod(traced(attr.__func__)))
            count += 1
        elif inspect.isfunction(attr):
            setattr(cls, name, traced(attr))
            count += 1
    return count


def instrument_module(module) -> int:
    """给模块内定义的类的公共方法织入日志（跳过从别处 import 进来的类）。"""
    count = 0
    for name, obj in list(vars(module).items()):
        if not inspect.isclass(obj) or name.startswith("_"):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        if _excluded(name):
            continue
        try:
            count += _instrument_class(obj)
        except Exception as e:  # 单个类织入失败不影响整体
            logger.debug("[trace] 织入 %s.%s 失败：%s", module.__name__, name, e)
    return count


def _iter_service_modules():
    import services  # 延迟导入，避免在模块加载期形成循环依赖

    for info in pkgutil.iter_modules(services.__path__, prefix="services."):
        if not info.ispkg:
            yield info.name


def instrument_services() -> int:
    """扫描 services 包并统一织入。返回织入的方法总数。

    单个模块导入失败（如循环依赖）只跳过该模块，不影响启动。
    """
    if not settings.SERVICE_TRACE_ENABLED:
        return 0
    total = 0
    for mod_name in _iter_service_modules():
        try:
            module = importlib.import_module(mod_name)
        except Exception as e:
            logger.debug("[trace] 跳过模块 %s：%s", mod_name, e)
            continue
        try:
            total += instrument_module(module)
        except Exception as e:
            logger.debug("[trace] 织入模块 %s 失败：%s", mod_name, e)
    logger.info("[trace] 服务层日志已织入 %d 个方法", total)
    return total
