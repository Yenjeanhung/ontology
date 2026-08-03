from pathlib import Path

from providers.parser.base import DocumentParser, ParseResult


class DocxParser(DocumentParser):
    """Word 文档解析器。"""

    def parse(self, file_path: Path) -> ParseResult:
        import docx

        doc = docx.Document(str(file_path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return ParseResult(
            filename=file_path.name,
            format=".docx",
            content="\n\n".join(paragraphs),
            metadata={"paragraphs": len(paragraphs)},
        )

    def supported_extensions(self) -> list[str]:
        return [".docx"]
