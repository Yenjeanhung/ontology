from pydantic import BaseModel
from typing import Optional


class CreateKBRequest(BaseModel):
    name: str


class UpdateKBRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class QueryRequest(BaseModel):
    query: str
    kb_id: str


class CreateDirectoryRequest(BaseModel):
    name: str
    parent_id: Optional[str] = None


class UpdateDirectoryRequest(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None


class UpdateAssetRequest(BaseModel):
    name: Optional[str] = None
    directory_id: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None


class AttachAssetsRequest(BaseModel):
    asset_ids: list[str]
    auto_process: bool = False
    extract_graph: bool = True


class CreateCrawlJobRequest(BaseModel):
    keyword: str
    directory_id: Optional[str] = None
    max_pages: Optional[int] = None
    auto_attach_kb_id: Optional[str] = None
    auto_process: bool = False
    extract_graph: bool = True
    analysis_depth: str = "medium"
