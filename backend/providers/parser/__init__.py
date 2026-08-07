from pathlib import Path

from providers.parser.base import DocumentParser
from providers.parser.txt_parser import TxtParser
from providers.parser.pdf_parser import PdfParser
from providers.parser.docx_parser import DocxParser
from providers.parser.tika_parser import TikaParser
from providers.parser.tika_runtime import tika_available

# 精确匹配的轻量解析器（扩展名互斥，顺序无关）
_parsers: list[DocumentParser] = [TxtParser(), PdfParser(), DocxParser()]
# 兜底解析器：仅当 tika_available() 为真时由 get_parser 返回
_tika_parser = TikaParser()


def get_parser(file_path: Path) -> DocumentParser:
    """根据文件扩展名选择解析器。

    解析顺序：
    1. 精确匹配轻量解析器（txt/md/pdf/docx）；
    2. 匹配失败 → 若 Tika 兜底可用（已启用且有 JVM 或外部 Server）则返回 TikaParser；
    3. 否则抛 ValueError，由文件处理管线标记为失败并给出指引文案。
    """
    ext = file_path.suffix.lower()

    for parser in _parsers:
        if ext in parser.supported_extensions():
            return parser

    if tika_available():
        return _tika_parser

    raise ValueError(
        f"不支持的文件格式: {ext}。如需解析该格式，请安装 JRE 并启用 Tika 兜底"
        f"（TIKA_FALLBACK_ENABLED=true），或部署外部 Tika Server（TIKA_SERVER_ENDPOINT）。"
    )
