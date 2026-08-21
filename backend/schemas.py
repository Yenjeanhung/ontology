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


class AgentQueryRequest(BaseModel):
    """智能体（OAG）查询请求。"""
    query: str
    kb_id: str
    skill_ids: list[str] = []


class AgentSkillCreate(BaseModel):
    name: str
    code: str
    description: str = ""
    instructions: str = ""
    sort_order: int = 0
    files: Optional[list] = None     # ZIP 技能包配套文件（通常仅导入链路写入）
    group_id: Optional[str] = None   # 所属分组；NULL/缺省 = 未分组


class AgentSkillUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    instructions: str | None = None
    sort_order: int | None = None
    is_enabled: int | None = None
    files: Optional[list] = None     # None = 不修改；[] = 清空
    group_id: Optional[str] = None   # 显式传 null = 移到未分组；不传 = 不修改


class AgentSkillGroupCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None  # NULL/缺省 = 根级分组
    sort_order: int = 0


class AgentSkillGroupUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None  # 显式传 null = 移到根级；不传 = 不修改
    sort_order: Optional[int] = None


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


# ===== 本体服务（动作）=====


class ServiceParamDef(BaseModel):
    name: str
    label: str | None = ""
    type: str = "string"  # string/number/boolean/date/datetime/text
    required: bool = False
    default: str | None = None
    description: str | None = ""


class SaveOntologyServiceRequest(BaseModel):
    """本体服务 / 实体服务统一保存结构（创建与更新共用）。"""

    name: str
    code: str
    description: str | None = ""
    params: list[ServiceParamDef] = []
    code_text: str = ""
    language: str = "python"
    timeout_seconds: int = 30
    is_enabled: bool = True
    sort_order: int = 0


class TestOntologyServiceRequest(BaseModel):
    params: dict = {}
    mock_entity: dict | None = None  # 本体级测试运行时的模拟实体 {name, entity_type, properties}


class InvokeEntityServiceRequest(BaseModel):
    params: dict = {}


class AiAssistChatMessage(BaseModel):
    """AI 辅助对话历史消息。"""

    role: str  # user / assistant
    content: str = ""


class AiAssistServiceCodeRequest(BaseModel):
    """AI 辅助编写服务代码。"""

    prompt: str  # 需求描述
    name: str | None = ""  # 当前表单中的服务名（供 LLM 上下文）
    code: str | None = ""
    description: str | None = ""
    owner_name: str | None = ""  # 所属本体名 / 实体名
    current_code: str | None = ""  # 当前代码区内容（供 LLM 在其基础上修改）
    selected_code: str | None = ""  # 用户选中的代码片段（重点上下文）
    history: list[AiAssistChatMessage] = []  # 多轮对话历史


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


# ===== 图谱清洗 / 实体合并 =====


class MergeEntitiesRequest(BaseModel):
    canonical_id: str
    merged_ids: list[str]
    kb_id: str


class BatchDeleteRequest(BaseModel):
    ids: list[str]


class CleanupMergeItem(BaseModel):
    canonical_id: str
    merged_ids: list[str]


class ApplyCleanupRequest(BaseModel):
    kb_id: str
    merges: list[CleanupMergeItem] = []
    delete_entity_ids: list[str] = []
    delete_relation_ids: list[str] = []


# ===== 大模型（LLM）配置 =====


class LLMConfigUpdate(BaseModel):
    """更新大模型配置。api_key 为空 / None / 掩码占位时表示不修改密钥。"""
    provider: str = "openai"            # openai(OpenAI 兼容) | anthropic
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7


class LLMConfigTest(BaseModel):
    """测试连接：所有字段均为本次测试所用的值。api_key 为空表示沿用已保存的密钥。"""
    provider: str = "openai"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7


class LLMPlanRequest(BaseModel):
    """保存/更新一套 LLM 配置方案。api_key 为空时沿用当前已激活的密钥。"""
    name: str
    provider: str = "openai"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
