from pydantic import BaseModel
from typing import Optional


class CreateKBRequest(BaseModel):
    name: str


class UpdateKBRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class BatchDeleteKBRequest(BaseModel):
    kb_ids: list[str]


class QueryRequest(BaseModel):
    query: str
    kb_id: str


class AgentQueryRequest(BaseModel):
    """智能体（OAG）查询请求。"""
    query: str
    kb_id: str | None = None          # 传 agent_id 时可不传，以智能体为准
    skill_ids: list[str] = []
    agent_id: str | None = None       # 引用已配置智能体（KB + 技能 + 人设）


class AgentCreate(BaseModel):
    """创建智能体（KB + 技能 + 人设；KB 可选，空 = 问答时跟随页面选择）。"""
    name: str
    description: str = ""
    kb_id: str = ""
    system_prompt: str = ""
    skill_ids: list[str] = []
    is_enabled: int = 1


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    kb_id: str | None = None
    system_prompt: str | None = None
    skill_ids: list[str] | None = None
    is_enabled: int | None = None


# ===== 工作流 =====


class WorkflowSaveRequest(BaseModel):
    """工作流定义保存结构（创建与更新共用）。"""
    name: str
    description: str = ""
    definition: dict | None = None  # {nodes: [...], edges: [...]}


class RunWorkflowRequest(BaseModel):
    """运行工作流：开始节点入参。"""
    inputs: dict = {}


class HumanDecisionRequest(BaseModel):
    """人工任务处理：决策 + 意见 + 表单填写值。

    auto_resume=true（默认）：后端后台续跑（待办中心场景）；
    auto_resume=false：前端自行开 resume SSE 续播（编辑器场景）。
    """
    decision: str
    comment: str = ""
    data: dict = {}
    operator: str = ""
    auto_resume: bool = True


class HumanBatchDecisionRequest(BaseModel):
    """批量处理人工任务（仅审批模式任务）。"""
    task_ids: list[str]
    decision: str
    comment: str = ""
    operator: str = ""
    auto_resume: bool = True


class ResumeRunRequest(BaseModel):
    """人工任务处理后续跑。"""
    task_id: str


class HttpNodeTestRequest(BaseModel):
    """HTTP 节点测试请求：完整节点配置 + 样例变量上下文（渲染 {{变量}} 用，可空）。"""
    config: dict = {}
    context: dict = {}


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
    # 对象类型元数据
    code: str | None = None
    display_name: str | None = ""
    plural_name: str | None = ""
    title_key: str | None = ""
    primary_key: str | None = "name"
    icon: str | None = ""
    status: str | None = "active"
    visibility: str | None = "public"
    group_name: str | None = ""


class UpdateOntologyRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    sort_order: int | None = None
    code: str | None = None
    display_name: str | None = None
    plural_name: str | None = None
    title_key: str | None = None
    primary_key: str | None = None
    icon: str | None = None
    status: str | None = None
    visibility: str | None = None
    group_name: str | None = None


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
    # 属性元数据扩展
    is_edit_only: bool = False          # 仅人工编辑，不进抽取 Prompt
    render_hint: str | None = ""       # text/textarea/tag/link/image/badge
    format: str | None = ""
    unit: str | None = ""
    shared_property_id: str | None = ""


class UpdateOntologyAttributeRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    data_type: str | None = None
    description: str | None = None
    is_required: bool | None = None
    default_value: str | None = None
    sort_order: int | None = None
    is_edit_only: bool | None = None
    render_hint: str | None = None
    format: str | None = None
    unit: str | None = None
    shared_property_id: str | None = None


# ===== 共享属性 / 本体接口 =====


class CreateSharedPropertyRequest(BaseModel):
    name: str
    code: str | None = None
    data_type: str = "string"
    description: str | None = ""
    is_required: bool = False
    default_value: str | None = None
    enum_values: list[str] | None = None
    unit: str | None = ""
    format: str | None = ""


class UpdateSharedPropertyRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    data_type: str | None = None
    description: str | None = None
    is_required: bool | None = None
    default_value: str | None = None
    enum_values: list[str] | None = None
    unit: str | None = None
    format: str | None = None


class ApplySharedPropertyRequest(BaseModel):
    ontology_ids: list[str]
    overwrite: bool = False
    delete_manual_ids: list[str] | None = None  # 取消挂载时，用户选择一并删除的手工同名属性所属本体 id


class ApplyPreviewRequest(BaseModel):
    """挂载预览：传入拟挂载的本体 id 列表，返回其中已存在同名属性的本体。"""
    ontology_ids: list[str]


class DetachPreviewRequest(BaseModel):
    """取消挂载预览：传入拟取消挂载的本体 id 列表，返回其属性来源（生成/手工）。"""
    detach_ids: list[str]


class CreateInterfaceRequest(BaseModel):
    name: str
    code: str
    description: str | None = ""
    icon: str | None = ""
    extends: list[str] | None = None
    interface_kind: str = "functional"   # functional / abstract_object


class UpdateInterfaceRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    icon: str | None = None
    extends: list[str] | None = None
    interface_kind: str | None = None


class CreateInterfacePropertyRequest(BaseModel):
    name: str
    code: str
    data_type: str = "string"
    description: str | None = ""
    is_required: bool = True
    default_value: str | None = None
    enum_values: list[str] | None = None
    shared_property_id: str | None = ""
    sort_order: int = 0


class CreateInterfaceLinkRequest(BaseModel):
    name: str
    code: str
    target_interface_id: str | None = None
    target_ontology_id: str | None = ""
    cardinality: str = "ONE_TO_MANY"
    is_required: bool = False


class ImplementInterfaceRequest(BaseModel):
    ontology_id: str
    property_mapping: dict[str, str] = {}   # 接口属性 code → 本体属性名
    link_mapping: dict[str, str] | None = None


class BatchSaveAttributesRequest(BaseModel):
    attributes: list[CreateOntologyAttributeRequest]


class CreateOntologyRelationRequest(BaseModel):
    name: str
    code: str | None = None
    description: str | None = ""
    # S5：链接语义（新建时即可指定）
    cardinality: str | None = None    # ONE_TO_ONE / ONE_TO_MANY / MANY_TO_MANY
    inverse_name: str | None = None
    is_symmetric: bool | None = None
    is_transitive: bool | None = None
    status: str | None = None


class UpdateOntologyRelationRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    # S5：链接语义
    cardinality: str | None = None    # ONE_TO_ONE / ONE_TO_MANY / MANY_TO_MANY
    inverse_name: str | None = None
    is_symmetric: bool | None = None
    is_transitive: bool | None = None
    status: str | None = None


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
    # S5：端点基数
    source_min: int | None = None
    source_max: int | None = None
    target_min: int | None = None
    target_max: int | None = None
    is_required: bool | None = None


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
    kb_id: str = ""
    category_id: str = ""
    ontology_id: str = ""
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


# ===== S3（P0-3）：函数 + 派生属性 =====


class SaveFunctionRequest(BaseModel):
    """新建/更新只读函数。"""
    name: str
    code: str
    description: str = ""
    ontology_id: str = ""
    params_schema: list = []
    return_schema: dict | str | None = None
    code_text: str = ""
    language: str = "python"
    timeout_seconds: int = 30
    is_deterministic: bool = True
    cache_seconds: int = 0
    is_enabled: bool = True
    sort_order: int = 0


class TestFunctionRequest(BaseModel):
    """函数测试运行。"""
    params: dict = {}
    mock_entity: dict | None = None


class InvokeFunctionRequest(BaseModel):
    """在实体上调用函数。"""
    params: dict = {}


class ResolveFunctionsRequest(BaseModel):
    """批量解析函数（对象集/视图用）。"""
    function_id: str
    entity_ids: list[str] = []
    params: dict = {}


class SaveDerivedPropertyRequest(BaseModel):
    """新建/更新派生属性。"""
    name: str
    code: str
    data_type: str = "number"
    source_kind: str = "function"        # function / graph_metric
    function_id: str = ""
    graph_metric: str = ""               # pagerank / betweenness / community / degree
    params: dict = {}
    materialize_mode: str = "virtual"    # virtual / materialized
    is_enabled: bool = True
    sort_order: int = 0


# ===== S4（P1-1）：动作规则 / 副作用 / 撤销 / 批量 =====


class SaveServiceRuleRequest(BaseModel):
    """新建/更新动作规则。"""
    rule_type: str = "precondition"      # precondition / validation / post
    expression: dict | str = {}
    error_message: str = ""
    sort_order: int = 0
    is_enabled: bool = True


class SaveServiceEffectRequest(BaseModel):
    """新建/更新动作副作用。"""
    effect_type: str = "notify"          # notify / webhook / update_property / create_relation
    config: dict = {}
    sort_order: int = 0
    is_enabled: bool = True


class BatchInvokeRequest(BaseModel):
    """批量调用动作。"""
    entity_ids: list[str] = []
    params: dict = {}


class UndoInvocationRequest(BaseModel):
    """撤销一次动作执行。"""
    undone_by: str = "user"


# ===== S5（P1-2）：链接基数 / 反向 / 关系属性 =====


class CreateOntologyRelationPropertyRequest(BaseModel):
    name: str
    code: str | None = None
    data_type: str = "string"
    description: str | None = ""
    is_required: bool = False
    enum_values: list[str] | None = None
    sort_order: int = 0


class UpdateOntologyRelationPropertyRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    data_type: str | None = None
    description: str | None = None
    is_required: bool | None = None
    enum_values: list[str] | None = None
    sort_order: int | None = None


# ===== S6（P1-3）：对象视图 =====


class ObjectViewWidget(BaseModel):
    """视图微件：properties / relations / actions / derived / chart / timeline。"""
    kind: str
    title: str | None = ""
    config: dict = {}


class ObjectViewSection(BaseModel):
    title: str = ""
    widgets: list[ObjectViewWidget] = []


class ObjectViewTab(BaseModel):
    name: str
    sections: list[ObjectViewSection] = []


class SaveObjectViewRequest(BaseModel):
    name: str
    ontology_id: str | None = ""       # 空 = 类别缺省 / 接口级视图
    interface_code: str | None = ""
    layout: dict = {}                   # {tabs: [...]}
    is_default: bool = False
    version: int | None = None
    set_default: bool = False           # 保存时是否设为该本体/类别缺省


# ===== S7（P1-4）：版本 / 提案 / 影响分析 / 回滚 =====


class CreateVersionRequest(BaseModel):
    """发布一个版本快照。"""
    note: str | None = ""
    source: str = "manual"             # manual / auto_before_change / proposal
    created_by: str = ""


class UpgradeSuggestionRequest(BaseModel):
    """把一条本体建议升级为变更提案（change）。"""
    note: str | None = ""
    reviewers: str | None = ""
    base_version: int | None = None
    diff: list | None = None            # JSON：变更项列表


class MergeProposalRequest(BaseModel):
    """合并变更提案：应用 diff 到本体类别，并生成新版本。"""
    note: str | None = ""
    created_by: str | None = ""
