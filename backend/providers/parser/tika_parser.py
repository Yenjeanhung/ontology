import logging
import os
from pathlib import Path

from providers.parser.base import DocumentParser, ParseResult
from config import settings

logger = logging.getLogger(__name__)


class TikaParser(DocumentParser):
    """兜底解析器：基于 Apache Tika（tika-python），覆盖绝大多数文档格式。

    本解析器 **不参与** get_parser 的精确扩展名匹配，仅作为匹配失败后的兜底分支，
    因此 supported_extensions() 返回空列表。是否进入本解析器由
    tika_runtime.tika_available() 决定（未启用 / 无 JVM / 未装 tika 时根本不会到达这里）。

    外部 Server 模式（TIKA_SERVER_ENDPOINT 非空）下，设置 TikaClientOnly 并指向远端，
    避免本地拉起 tika-server JAR。
    """

    def parse(self, file_path: Path) -> ParseResult:
        # 懒导入：保证无 tika 包 / 无 JVM 时，import providers.parser 不报错
        try:
            from tika import parser as tika_parser
            from tika import tika as tika_mod
        except ImportError as e:
            raise RuntimeError(
                "Tika 兜底解析依赖缺失：未安装 tika 包（pip install tika）"
            ) from e

        # 外部 Server 模式：阻止本地拉起 JAR，改用远端 endpoint
        if settings.TIKA_SERVER_ENDPOINT:
            os.environ["TIKA_SERVER_ENDPOINT"] = settings.TIKA_SERVER_ENDPOINT
            tika_mod.TikaClientOnly = True

        try:
            parsed = tika_parser.from_file(str(file_path))
        except Exception as e:
            raise RuntimeError(
                f"Tika 解析失败（{file_path.name}）：{e}。"
                "请检查 JRE 是否可用，或外部 Tika Server（TIKA_SERVER_ENDPOINT）是否正常。"
            ) from e

        content = (parsed.get("content") or "").strip()
        metadata = parsed.get("metadata") or {}
        return ParseResult(
            filename=file_path.name,
            format=file_path.suffix.lower(),
            content=content,
            metadata=metadata,
        )

    def supported_extensions(self) -> list[str]:
        # 纯兜底，不参与精确匹配；详见 get_parser 的兜底分支
        return []
