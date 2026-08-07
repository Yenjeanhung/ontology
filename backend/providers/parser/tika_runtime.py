"""Tika 兜底解析的运行时探测：JVM 可用性、Tika 可用性。

设计要点（见 doc/文档解析/设计文档.md 第三章）：
- import 本模块不触发任何 JVM/Tika 动作，保证无 JRE 也能启动应用；
- JVM 用 ``shutil.which('java')`` 探测，进程级缓存，零启动开销；
- Tika 可用性 = 配置开关 ∧ tika 包可导入 ∧ (外部 Server 模式 ∨ 本地 JVM 可用)；
- 探测结果一次性 INFO/WARNING 记录，便于运维定位。
"""
import importlib.util
import logging
import shutil
from functools import lru_cache

from config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def jvm_available(java_path: str = "java") -> bool:
    """JVM 是否可用（PATH 中存在 java 可执行文件）。结果进程级缓存。"""
    return shutil.which(java_path) is not None


@lru_cache(maxsize=1)
def _tika_importable() -> bool:
    """tika 包是否已安装（仅查找模块规格，不实际导入，不触发 JVM）。"""
    return importlib.util.find_spec("tika") is not None


@lru_cache(maxsize=1)
def tika_available() -> bool:
    """Tika 兜底是否可用。结果进程级缓存，并在首次探测时记录一次日志。

    判定顺序：
    1. 配置开关关闭 → False；
    2. tika 包未安装 → False（给出安装提示）；
    3. 外部 Server 模式（TIKA_SERVER_ENDPOINT 非空）→ True，JVM 由 sidecar 承担；
    4. 本地模式 → 取决于 JVM 是否可用。
    """
    if not settings.TIKA_FALLBACK_ENABLED:
        logger.info("Tika fallback: disabled by config (TIKA_FALLBACK_ENABLED=false)")
        return False

    if not _tika_importable():
        logger.warning("Tika fallback: disabled, 'tika' package not installed (pip install tika)")
        return False

    # 外部 Server 模式：JVM 由独立 sidecar 承担，跳过本地探测
    if settings.TIKA_SERVER_ENDPOINT:
        logger.info(
            "Tika fallback: enabled, mode=remote, endpoint=%s", settings.TIKA_SERVER_ENDPOINT
        )
        return True

    # 本地模式：必须有 JVM
    java_path = settings.TIKA_JAVA_PATH or "java"
    if jvm_available(java_path):
        logger.info("Tika fallback: enabled, mode=local, jvm=available (java=%s)", java_path)
        return True

    logger.warning(
        "Tika fallback: disabled — local mode requires JRE but '%s' not found on PATH. "
        "未知文档格式（pptx/xlsx/html 等）将无法解析。可安装 JRE 或配置 TIKA_SERVER_ENDPOINT。",
        java_path,
    )
    return False
