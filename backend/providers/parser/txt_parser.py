from pathlib import Path

from providers.parser.base import DocumentParser, ParseResult


class TxtParser(DocumentParser):
    """纯文本和 Markdown 解析器。"""

    def parse(self, file_path: Path) -> ParseResult:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return ParseResult(
            filename=file_path.name,
            format=file_path.suffix.lower(),
            content=content,
            metadata={"size": file_path.stat().st_size},
        )

    def supported_extensions(self) -> list[str]:
        return [".txt", ".md"]
