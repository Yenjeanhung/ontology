from pathlib import Path

from providers.parser.base import DocumentParser, ParseResult


class PdfParser(DocumentParser):
    """PDF 文档解析器，使用 pypdfium2，记录每页文本及字符偏移量。"""

    def parse(self, file_path: Path) -> ParseResult:
        import pypdfium2

        pdf = pypdfium2.PdfDocument(str(file_path))
        pages = []
        for page in pdf:
            text_page = page.get_textpage()
            text = text_page.get_text_range()
            pages.append(text or "")
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
