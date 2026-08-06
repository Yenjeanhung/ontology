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


# ===== 本体管理 =====


class CreateOntologyCategoryRequest(BaseModel):
    name: str
    description: str | None = ""


class UpdateOntologyCategoryRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class CreateOntologyRequest(BaseModel):
    name: str
    description: str | None = ""
    color: str | None = None
    sort_order: int = 0


class UpdateOntologyRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    sort_order: int | None = None


class BatchCreateOntologiesRequest(BaseModel):
    ontologies: list[CreateOntologyRequest]


class CreateOntologyAttributeRequest(BaseModel):
    name: str
    code: str | None = None
    data_type: str  # string/number/boolean/date/datetime/text
    description: str | None = ""
    is_required: bool = False
    default_value: str | None = None
    sort_order: int = 0


class UpdateOntologyAttributeRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    data_type: str | None = None
    description: str | None = None
    is_required: bool | None = None
    default_value: str | None = None
    sort_order: int | None = None


class BatchSaveAttributesRequest(BaseModel):
    attributes: list[CreateOntologyAttributeRequest]


class CreateOntologyRelationRequest(BaseModel):
    name: str
    code: str | None = None
    description: str | None = ""


class UpdateOntologyRelationRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None


class BatchCreateRelationsRequest(BaseModel):
    relations: list[CreateOntologyRelationRequest]


class CreateRelationConstraintRequest(BaseModel):
    source_ontology_id: str
    relation_id: str
    target_ontology_id: str
    description: str | None = ""


class UpdateRelationConstraintRequest(BaseModel):
    source_ontology_id: str | None = None
    relation_id: str | None = None
    target_ontology_id: str | None = None
    description: str | None = None


class BatchCreateConstraintsRequest(BaseModel):
    constraints: list[CreateRelationConstraintRequest]


class BindKbOntologyRequest(BaseModel):
    category_id: str


class SuggestOntologyAttr(BaseModel):
    name: str
    code: str | None = None
    data_type: str = "string"
    is_required: bool = False


class SuggestOntology(BaseModel):
    name: str
    description: str | None = ""
    attributes: list[SuggestOntologyAttr] = []


class SuggestRelation(BaseModel):
    name: str
    code: str | None = None
    description: str | None = ""


class SuggestConstraint(BaseModel):
    source: str
    relation: str
    target: str


class SuggestionData(BaseModel):
    category: dict | None = None
    ontologies: list[SuggestOntology] = []
    relations: list[SuggestRelation] = []
    constraints: list[SuggestConstraint] = []
    stats: dict | None = None


class GenerateOntologySuggestionRequest(BaseModel):
    kb_id: str
    file_id: str | None = None


class UpdateOntologySuggestionRequest(BaseModel):
    suggestion_data: SuggestionData | None = None
    status: str | None = None
    review_notes: str | None = None
    score: float | None = None


class ApproveSuggestionRequest(BaseModel):
    reviewer: str | None = None


class CreateAttributeTemplateRequest(BaseModel):
    name: str
    description: str | None = ""


class UpdateAttributeTemplateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class CreateTemplateAttributeRequest(BaseModel):
    name: str
    code: str | None = None
    data_type: str
    description: str | None = ""
    is_required: bool = False
    default_value: str | None = None
    sort_order: int = 0


class BatchSaveTemplateAttributesRequest(BaseModel):
    attributes: list[CreateTemplateAttributeRequest]


class BindOntologyTemplatesRequest(BaseModel):
    template_ids: list[str]


# ===== 实体/关系实例管理 =====


class UpdateEntityRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    properties: dict | None = None


class UpdateRelationRequest(BaseModel):
    relation_type: str | None = None
    description: str | None = None


class CreateEntityRequest(BaseModel):
    kb_id: str
    ontology_id: str
    entity_type: str
    name: str
    description: str | None = ""
    properties: dict | None = None


class CreateRelationRequest(BaseModel):
    kb_id: str
    relation_def_id: str
    relation_type: str
    source_entity_id: str
    target_entity_id: str
    description: str | None = ""
