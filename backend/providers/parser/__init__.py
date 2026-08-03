from pathlib import Path

from providers.parser.base import DocumentParser
from providers.parser.txt_parser import TxtParser
from providers.parser.pdf_parser import PdfParser
from providers.parser.docx_parser import DocxParser

_parsers: list[DocumentParser] = [TxtParser(), PdfParser(), DocxParser()]


def get_parser(file_path: Path) -> DocumentParser:
    """根据文件扩展名选择对应的解析器。"""
    ext = file_path.suffix.lower()
    for parser in _parsers:
        if ext in parser.supported_extensions():
            return parser
    raise ValueError(f"不支持的文件格式: {ext}")
