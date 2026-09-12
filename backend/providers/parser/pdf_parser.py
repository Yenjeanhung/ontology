import re
from pathlib import Path

from core.chunker import find_heading_info
from providers.parser.base import DocumentParser, ParseResult

# 行尾句末标点（含闭合引号/括号）：视为句子结束，可在此断段
_LINE_SENT_END_RE = re.compile(r'[.!?。！？…”"』」)\]]$')
# 列表行：独立成段，不与上下行合并
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*•·]|\d{1,3}[.)、])\s+\S")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_cjk(ch: str) -> bool:
    return bool(ch) and bool(_CJK_RE.match(ch))


def _reflow_page(raw: str) -> str:
    """重建 PDF 排版文本的段落结构。

    pypdfium2 提取的文本每个排版行末尾都带换行，句子被行宽打碎。
    此处把行重新合并成以完整句子为单位的段落：
    - 空行 → 段落边界；
    - 标题行 / 列表行 → 独立成段（供 heading 策略识别）；
    - 行尾连字符 + 下行小写开头 → 英文断词合并（去连字符）；
    - 中文相邻行直接拼接，西文以空格拼接；
    - 行尾为句末标点 → 断段。
    段落间以空行分隔，保证下游 sentence/recursive/heading 的边界都落在真实句子处。
    """
    lines = [
        ln.strip()
        for ln in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    paragraphs: list[str] = []
    buf = ""
    buf_is_heading = False
    for line in lines:
        if not line:
            if buf:
                paragraphs.append(buf)
                buf = ""
                buf_is_heading = False
            continue
        is_heading = bool(find_heading_info(line))
        # 新段：空缓冲 / 标题行 / 上一段是标题行（标题不吸收正文） / 列表行
        if not buf or is_heading or buf_is_heading or _LIST_LINE_RE.match(line):
            if buf:
                paragraphs.append(buf)
            buf = line
            buf_is_heading = is_heading
            continue
        if buf.endswith("-"):
            buf = buf + line  # 英文断词合并：保留连字符，避免误删复合词（time-aware）
        elif _is_cjk(buf[-1]) or _is_cjk(line[0]):
            buf += line
        else:
            buf += " " + line
        if _LINE_SENT_END_RE.search(line):
            paragraphs.append(buf)
            buf = ""
    if buf:
        paragraphs.append(buf)
    return "\n\n".join(paragraphs)


class PdfParser(DocumentParser):
    """PDF 文档解析器，使用 pypdfium2，记录每页文本及字符偏移量。"""

    def parse(self, file_path: Path) -> ParseResult:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(str(file_path))
        pages = []
        for page in pdf:
            text_page = page.get_textpage()
            text = text_page.get_text_range()
            pages.append(_reflow_page(text or ""))
            text_page.close()
            page.close()
        pdf.close()

        # 计算每页在拼接文本中的字符偏移量
        full_text_parts = []
        page_map = []  # [{page_number, start, end}]
        offset = 0
        for i, page_text in enumerate(pages):
            if i > 0:
                sep = "\n\n"
                offset += len(sep)
            start = offset
            end = start + len(page_text)
            page_map.append({"page_number": i + 1, "start": start, "end": end})
            full_text_parts.append(page_text)
            offset = end

        return ParseResult(
            filename=file_path.name,
            format=".pdf",
            content="\n\n".join(pages),
            metadata={
                "pages": len(pages),
                "page_map": page_map,
            },
        )

    def supported_extensions(self) -> list[str]:
        return [".pdf"]
