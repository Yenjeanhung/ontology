from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParseResult:
    filename: str
    format: str
    content: str
    metadata: dict = field(default_factory=dict)


class DocumentParser(ABC):
    """文档解析抽象接口。"""

    @abstractmethod
    def parse(self, file_path: Path) -> ParseResult:
        """解析文档，返回统一格式。"""
        ...

    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """返回支持的文件扩展名列表。"""
        ...
