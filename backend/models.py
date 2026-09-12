from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    files = relationship("File", back_populates="kb", cascade="all, delete-orphan")


class FileDirectory(Base):
    __tablename__ = "file_directories"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    parent_id = Column(String, ForeignKey("file_directories.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    parent = relationship("FileDirectory", remote_side=[id], back_populates="children")
    children = relationship("FileDirectory", back_populates="parent", cascade="all, delete-orphan")
    assets = relationship("FileAsset", back_populates="directory")


class FileAsset(Base):
    __tablename__ = "file_assets"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    directory_id = Column(String, ForeignKey("file_directories.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False, default=0)
    ext = Column(String, default="")
    mime_type = Column(String, default="")
    sha256 = Column(String, default="")
    path = Column(String, nullable=True)
    source_type = Column(String, default="upload")
    source_url = Column(String, nullable=True)
    source_keyword = Column(String, nullable=True)
    sources = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    status = Column(String, default="ready")
    message = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())

    directory = relationship("FileDirectory", back_populates="assets")
    kb_files = relationship("File", back_populates="asset")


class File(Base):
    __tablename__ = "files"

    id = Column(String, primary_key=True)
    asset_id = Column(String, ForeignKey("file_assets.id", ondelete="SET NULL"), nullable=True)
    kb_id = Column(String, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    total_chunks = Column(Integer, nullable=False, default=0)
    status = Column(String, default="uploading")
    progress = Column(Integer, default=0)
    message = Column(String, nullable=True)
    detail = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)
    path = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    kb = relationship("KnowledgeBase", back_populates="files")
    asset = relationship("FileAsset", back_populates="kb_files")
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    file_id = Column(String, ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    embedding_id = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())

    file = relationship("File", back_populates="chunks")


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    keyword = Column(String, nullable=False)
    directory_id = Column(String, ForeignKey("file_directories.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, default="queued")
    progress = Column(Integer, default=0)
    message = Column(String, nullable=True)
    urls = Column(Text, nullable=True)
    file_count = Column(Integer, default=0)
    detail = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    finished_at = Column(String, nullable=True)


# ===== 本体定义层（无外键，逻辑关联由 service 层维护）=====

class OntologyCategory(Base):
    __tablename__ = "ontology_categories"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    is_system = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Ontology(Base):
    __tablename__ = "ontologies"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    color = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # ── 对象类型元数据（对标 Palantir Object type）──
    code = Column(String, nullable=True)                      # 类型 API 名，如 person
    display_name = Column(String, default="")                 # 显示名（可与 name 不同）
    plural_name = Column(String, default="")                  # 复数名
    title_key = Column(String, default="")                    # 标题属性名，空则回落 name
    primary_key = Column(String, default="name")              # 主键属性名
    icon = Column(String, default="")                         # 图标标识
    status = Column(String, default="active")                 # draft / active / deprecated
    visibility = Column(String, default="public")             # public / restricted（预留权限）
    group_name = Column(String, default="")                   # 对象类型组（前端分组）
    # ── 抽取规则（对象类型级，设计文档 §3.3）──
    name_pattern = Column(String(200), default="")            # 实体名必须匹配的正则，空=不限
    min_confidence = Column(Float, nullable=True)             # 实体级最低置信度，NULL=用全局 GRAPH_MIN_ENTITY_CONFIDENCE
    min_valid_attributes = Column(Integer, nullable=False, default=0)  # 有效属性数少于此值则进复核，0=不限
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyAttribute(Base):
    __tablename__ = "ontology_attributes"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    ontology_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    data_type = Column(String, nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=0)
    default_value = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # ── 属性元数据扩展 ──
    is_edit_only = Column(Integer, nullable=False, default=0)   # 仅人工编辑，不进抽取 Prompt
    render_hint = Column(String, default="")                    # text/textarea/tag/link/image/badge
    format = Column(String, default="")                         # 值格式化，如 #,##0.00 / YYYY-MM-DD
    unit = Column(String, default="")                           # 单位
    shared_property_id = Column(String, default="")             # 绑定的共享属性（可空）
    is_shared_created = Column(Integer, nullable=False, default=0)  # 由共享属性挂载生成（取消挂载时删除）
    # ── 抽取规则（属性级，见 doc/知识库/实体抽取属性级规则与人工复核设计.md §3.2）──
    enum_values = Column(Text, nullable=True)                   # 允许值集合 JSON 数组，NULL=不校验
    value_pattern = Column(String(200), default="")             # 正则约束（Python re 语法）
    min_value = Column(String(64), default="")                  # 下界（number 按数值 / date 按 ISO 串比较）
    max_value = Column(String(64), default="")                  # 上界
    min_length = Column(Integer, nullable=False, default=0)     # 字符串最小长度，0=不限
    max_length = Column(Integer, nullable=False, default=0)     # 字符串最大长度，0=不限
    confidence_threshold = Column(Float, nullable=True)         # 属性值置信度门槛，NULL=继承对象类型级
    on_violation = Column(String(16), nullable=False, default="drop_attribute")  # drop_attribute/review/drop_entity
    extraction_hint = Column(Text, default="")                  # 抽取提示，注入 Prompt
    extraction_examples = Column(Text, nullable=True)           # 正例 JSON 数组
    negative_examples = Column(Text, nullable=True)             # 反例 JSON 数组
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyRelation(Base):
    __tablename__ = "ontology_relations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    description = Column(String, default="")
    # ── 链接语义（S5：对标 Palantir Link type）──
    cardinality = Column(String(16), default="MANY_TO_MANY")  # ONE_TO_ONE/ONE_TO_MANY/MANY_TO_MANY
    inverse_name = Column(String(50), default="")             # 反向展示名：任职于 ↔ 雇佣
    is_symmetric = Column(Integer, nullable=False, default=0)  # 如"合作"
    is_transitive = Column(Integer, nullable=False, default=0)  # 如"位于/属于"
    status = Column(String(20), default="active")             # active / deprecated
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyRelationConstraint(Base):
    __tablename__ = "ontology_relation_constraints"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    source_ontology_id = Column(String, nullable=False)
    relation_id = Column(String, nullable=False)
    target_ontology_id = Column(String, nullable=False)
    description = Column(String, default="")
    # ── 端点基数（S5）──
    source_min = Column(Integer, default=0)   # 0 = 不限
    source_max = Column(Integer, default=0)   # 0 = 不限
    target_min = Column(Integer, default=0)
    target_max = Column(Integer, default=0)
    is_required = Column(Integer, nullable=False, default=0)  # 该起点必须存在此关系
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class KbOntologyBinding(Base):
    __tablename__ = "kb_ontology_bindings"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    category_id = Column(String, nullable=False)
    strict_mode = Column(Integer, nullable=False, default=0)   # 严格模式：属性级 drop_attribute 升级为 review
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 本体建议（动态生成 + 审核）=====

class OntologySuggestion(Base):
    __tablename__ = "ontology_suggestions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    file_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="generating")  # generating | ready | approved | rejected
    source_mode = Column(String, nullable=False, default="free_extraction")  # free_extraction | auto_cluster | manual
    suggestion_data = Column(Text, nullable=False, default="{}")  # JSON blob: category/ontologies/relations/constraints/stats
    score = Column(Float, default=0.0)
    review_notes = Column(String, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    reviewed_at = Column(String, nullable=True)
    reviewer = Column(String, nullable=True)
    # ── S7：提案化（suggestion / change 共用一张表，两种 proposal_type）──
    proposal_type = Column(String(20), default="suggestion")  # suggestion / change
    base_version = Column(Integer, default=0)                 # 提案基于的版本号
    diff = Column(Text, nullable=True)                         # JSON：变更项列表
    reviewers = Column(String, default="")                     # 逗号分隔的审核人
    merged_version_id = Column(String, default="")             # 合并后生成的版本 id


# ===== S6：对象视图（可配置详情页布局）=====


class OntologyObjectView(Base):
    __tablename__ = "ontology_object_views"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    ontology_id = Column(String, default="")         # 空 = 该类别的缺省视图 / 接口级视图
    interface_code = Column(String, default="")      # 非空 = 接口级视图（与 ontology_id 二选一）
    name = Column(String(100), nullable=False)
    layout = Column(Text, nullable=False, default="{}")  # JSON: {tabs:[{name, sections:[{title, widgets}]}]}
    is_default = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== S7：本体版本快照 =====


class OntologyVersion(Base):
    __tablename__ = "ontology_versions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    version_no = Column(Integer, nullable=False)
    snapshot = Column(Text, nullable=False, default="{}")  # JSON：定义层完整快照
    source = Column(String(20), default="manual")          # manual / auto_before_change / proposal
    note = Column(String, default="")
    created_by = Column(String, default="")
    merged_suggestion_id = Column(String, default="")       # 来源提案（proposal 合并时回填）
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 属性模板（全局，跨本体类别复用）=====

class OntologyAttributeTemplate(Base):
    __tablename__ = "ontology_attribute_templates"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    description = Column(String, default="")
    is_system = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyTemplateAttribute(Base):
    __tablename__ = "ontology_template_attributes"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    template_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    data_type = Column(String, nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=0)
    default_value = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyTemplateBinding(Base):
    __tablename__ = "ontology_template_bindings"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    ontology_id = Column(String, nullable=False)
    template_id = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 共享属性（跨本体统一定义，值各自独立）=====

class OntologySharedProperty(Base):
    __tablename__ = "ontology_shared_properties"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    data_type = Column(String, nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=0)
    default_value = Column(String, nullable=True)
    enum_values = Column(Text, nullable=True)      # JSON 数组，仅 data_type=enum
    unit = Column(String, default="")
    format = Column(String, default="")
    is_system = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 本体接口（Interface）：共享属性/链接契约 → 多态 =====

class OntologyInterface(Base):
    __tablename__ = "ontology_interfaces"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)               # party / monitorable
    description = Column(String, default="")
    icon = Column(String, default="")
    extends = Column(Text, nullable=True)               # JSON 数组：[interface_id]
    interface_kind = Column(String, default="functional")  # functional / abstract_object
    is_system = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyInterfaceProperty(Base):
    __tablename__ = "ontology_interface_properties"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    interface_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    data_type = Column(String, nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=1)
    default_value = Column(String, nullable=True)
    enum_values = Column(Text, nullable=True)
    shared_property_id = Column(String, default="")
    sort_order = Column(Integer, nullable=False, default=0)


class OntologyInterfaceLink(Base):
    __tablename__ = "ontology_interface_links"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    interface_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    target_interface_id = Column(String, nullable=True)
    target_ontology_id = Column(String, default="")
    cardinality = Column(String, default="ONE_TO_MANY")
    is_required = Column(Integer, nullable=False, default=0)


class OntologyInterfaceImplementation(Base):
    __tablename__ = "ontology_interface_implementations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    interface_id = Column(String, nullable=False)
    ontology_id = Column(String, nullable=False)
    property_mapping = Column(Text, default="{}")   # JSON: {接口属性code: 本体属性名}
    link_mapping = Column(Text, nullable=True)      # JSON: {接口链接code: relation_id}
    status = Column(String, default="active")       # active / partial
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 本体服务（动作）：本体级通用动作，实体级个性化动作（无外键）=====

class OntologyService(Base):
    __tablename__ = "ontology_services"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    owner_type = Column(String, nullable=False, default="ontology")  # ontology | entity
    ontology_id = Column(String, nullable=False)                     # 所属本体（entity 服务冗余记录，便于展示来源）
    entity_id = Column(String, nullable=True)                        # owner_type=entity 时必填
    name = Column(String(100), nullable=False)
    code = Column(String(100), nullable=False)                       # 动作标识，同一 owner 下唯一
    description = Column(Text, default="")
    params_schema = Column(Text, default="[]")                       # JSON: [{name,label,type,required,default,description}]
    code_text = Column(Text, nullable=False, default="")             # Python 源码
    language = Column(String(20), nullable=False, default="python")  # 预留多语言
    timeout_seconds = Column(Integer, nullable=False, default=30)
    is_enabled = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    execution_mode = Column(String(10), nullable=False, default="code")   # code | flow
    flow = Column(Text, nullable=True)                                    # 编排图 JSON（execution_mode=flow）
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== S3：函数 + 派生属性 + 运行时调用记录（无外键）=====


class OntologyFunction(Base):
    """只读计算函数（不写数据、可缓存、可被动作/视图/派生属性复用）。"""

    __tablename__ = "ontology_functions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, default="")     # 空 = 全局函数
    ontology_id = Column(String, default="")     # 空 = 类别级；有值 = 挂在本体下
    name = Column(String(100), nullable=False)
    code = Column(String(64), nullable=False)
    description = Column(String(500), default="")
    params_schema = Column(Text, default="[]")
    return_schema = Column(Text, default="")
    code_text = Column(Text, nullable=False, default="")
    language = Column(String(20), nullable=False, default="python")
    timeout_seconds = Column(Integer, nullable=False, default=30)
    is_deterministic = Column(Integer, nullable=False, default=1)
    cache_seconds = Column(Integer, nullable=False, default=0)
    is_enabled = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyDerivedProperty(Base):
    """派生属性：来源为函数（function）或图计算指标（graph_metric）。"""

    __tablename__ = "ontology_derived_properties"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    ontology_id = Column(String, nullable=False)
    name = Column(String(50), nullable=False)
    code = Column(String(64), nullable=False)
    data_type = Column(String(20), nullable=False, default="number")
    source_kind = Column(String(20), nullable=False, default="function")   # function / graph_metric
    function_id = Column(String, default="")
    graph_metric = Column(String(32), default="")   # pagerank/betweenness/community/degree
    params = Column(Text, default="{}")
    materialize_mode = Column(String(20), default="virtual")   # virtual / materialized
    last_materialized_at = Column(String, default="")
    is_enabled = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class OntologyRuntimeInvocation(Base):
    """函数/动作调用记录（轻量审计，函数与动作共用）。"""

    __tablename__ = "ontology_runtime_invocations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kind = Column(String(20), nullable=False)          # function / action / dp_test
    ref_id = Column(String, nullable=False)            # function_id / service_id
    entity_id = Column(String, default="")
    params = Column(Text)
    result = Column(Text)
    status = Column(String(20), nullable=False)        # success / error / timeout
    error = Column(Text)
    duration_ms = Column(Integer, default=0)
    triggered_by = Column(String(64), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== S4：动作规则 / 副作用 / 执行记录（无外键）=====


class OntologyServiceRule(Base):
    """动作规则：precondition（提交条件）/ validation（参数校验）/ post（提交后）。"""

    __tablename__ = "ontology_service_rules"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    service_id = Column(String, nullable=False)
    rule_type = Column(String(20), nullable=False)
    expression = Column(Text, nullable=False, default="")   # JSON：规则树 或 {kind:'python', code}
    error_message = Column(String(300), default="")
    sort_order = Column(Integer, nullable=False, default=0)
    is_enabled = Column(Integer, nullable=False, default=1)


class OntologyServiceEffect(Base):
    """动作副作用：notify / webhook / update_property / create_relation。"""

    __tablename__ = "ontology_service_effects"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    service_id = Column(String, nullable=False)
    effect_type = Column(String(20), nullable=False)
    config = Column(Text, nullable=False, default="{}")
    is_enabled = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)


class OntologyServiceInvocation(Base):
    """动作执行记录（含撤销所需前像）。"""

    __tablename__ = "ontology_service_invocations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    service_id = Column(String, nullable=False)
    entity_id = Column(String, default="")
    params = Column(Text)
    result = Column(Text)
    status = Column(String(20), nullable=False)   # success / error / timeout / undone
    error = Column(Text)
    duration_ms = Column(Integer, default=0)
    undo_payload = Column(Text)                   # JSON：属性旧值 / 新建关系 id
    triggered_by = Column(String(64), default="")
    undone_at = Column(String, default="")
    undone_by = Column(String(64), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 实体实例层（抽取后生成，无外键）=====

class Entity(Base):
    __tablename__ = "entities"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    ontology_id = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    properties = Column(Text, nullable=True)
    source_file_id = Column(String, nullable=True)
    source_chunk_id = Column(String, nullable=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Relation(Base):
    __tablename__ = "relations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    relation_def_id = Column(String, nullable=False)
    relation_type = Column(String, nullable=False)
    source_entity_id = Column(String, nullable=False)
    target_entity_id = Column(String, nullable=False)
    description = Column(String, default="")
    source_file_id = Column(String, nullable=True)
    source_chunk_id = Column(String, nullable=True)
    properties = Column(Text, nullable=True)               # 关系实例属性 JSON（S5：链接可带属性）
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 抽取复核队列：未通过规则、待人工审核的实体 =====
# 设计见 doc/知识库/实体抽取属性级规则与人工复核设计.md §5


class ExtractionReview(Base):
    __tablename__ = "extraction_reviews"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False)
    file_id = Column(String, default="")
    chunk_id = Column(String, default="")          # 来源分片，便于回溯原文
    ontology_id = Column(String, default="")
    entity_type = Column(String, nullable=False)   # 已归一化的类型 code
    entity_name = Column(String, nullable=False)
    description = Column(Text, default="")
    properties = Column(Text, nullable=True)       # 清洗后（违规值已按策略处理）
    raw_properties = Column(Text, nullable=True)   # 原始抽取结果，供人工对照
    confidence = Column(Float, default=1.0)        # 证据计算的实体置信度
    violations = Column(Text, nullable=False, default="[]")  # JSON 数组
    primary_rule = Column(String, default="")      # 汇总主因，便于筛选
    status = Column(String, default="pending")     # pending/approved/rejected/expired
    reviewer = Column(String, default="")
    review_notes = Column(Text, default="")
    reviewed_at = Column(String, default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== S5：关系属性定义（链接本身可带属性）=====


class OntologyRelationProperty(Base):
    __tablename__ = "ontology_relation_properties"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    relation_id = Column(String, nullable=False)     # 逻辑关联 ontology_relations.id
    name = Column(String(50), nullable=False)
    code = Column(String(64), nullable=False)
    data_type = Column(String(20), nullable=False)
    description = Column(String, default="")
    is_required = Column(Integer, nullable=False, default=0)
    enum_values = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 图分析（图迁入 / 图计算 / 图推理）=====

class GraphSyncRun(Base):
    """图迁入运行记录：PostgreSQL 权威数据 → Neo4j 分析图（按本体类别）。

    进度轮询字段：status/entity_count/relation_count 由后台任务滚动更新。
    """
    __tablename__ = "graph_sync_runs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False)            # full / incremental
    status = Column(String, nullable=False)          # pending / running / done / failed
    dry_run = Column(Integer, nullable=False, default=0)
    entity_count = Column(Integer, default=0)        # 进度计数（已写入）
    relation_count = Column(Integer, default=0)
    total_entities = Column(Integer, default=0)      # 预检总量（dry_run 与正式一致）
    total_relations = Column(Integer, default=0)
    watermark = Column(String)                       # 增量水位 max(updated_at)（P2 使用）
    projection = Column(Text, default="")            # 迁入后投影重建结果（JSON）
    error = Column(Text)
    started_at = Column(String)
    finished_at = Column(String)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class GraphAnalysisTask(Base):
    """图计算/推理任务记录（P1：5 算法；P2：规则推理 kind='inference'）。

    results 存 top-N 榜单（JSON，上限 100 行/50 社区）；stats 存规模与耗时；
    计算分值只写回 Neo4j 节点属性（派生数据），不回 PostgreSQL 权威库。
    """
    __tablename__ = "graph_analysis_tasks"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    category_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False, default="algorithm")   # algorithm / inference（P2）
    algorithm = Column(String, nullable=False)                   # pagerank/betweenness/louvain/node_similarity/degree
    params = Column(Text, nullable=False, default="{}")          # JSON：top_n/write_back/label_filter
    status = Column(String, nullable=False)                      # pending/running/done/failed
    stats = Column(Text, default="{}")                           # JSON：投影规模/耗时/写回数
    results = Column(Text)                                       # JSON：榜单/社区/相似对
    error = Column(Text)
    started_at = Column(String)
    finished_at = Column(String)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class RelationSuggestion(Base):
    """隐含关系建议（图推理 / 知识补全共用审核闭环，姊妹篇 6.2 + source 扩展）。

    source 来源三家：rule（规则推理）/ gds_analysis（结构推理，P3）/ completion（补全，姊妹篇）。
    推理路径建议的 kb_id 取源实体所属 KB（批准时直接可用）；category_id 用于类别维度轮询。
    """
    __tablename__ = "relation_suggestions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False, index=True)
    category_id = Column(String, nullable=False, index=True)
    source_entity_id = Column(String, nullable=False)
    target_entity_id = Column(String, nullable=False)
    suggested_relation_type = Column(String, nullable=False)
    relation_def_id = Column(String, nullable=False)             # 批准时走 create_relation 双写
    source = Column(String, nullable=False, default="rule")      # rule / gds_analysis / completion
    score = Column(Float, default=0)                             # 结构分
    confidence = Column(Float, default=0)                        # 规则/LLM 置信度
    evidence = Column(Text, default="")                          # JSON：推理路径（审核者回看）
    reason = Column(String, default="")
    status = Column(String, nullable=False)                      # pending / approved / rejected
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    reviewed_at = Column(String)
    reviewer = Column(String)


class RelationSuggestionTombstone(Base):
    """建议 tombstone：拒绝过的 (kb, 源, 目标, 关系类型) 组合不再重推。"""
    __tablename__ = "relation_suggestion_tombstones"

    kb_id = Column(String, primary_key=True)
    source_entity_id = Column(String, primary_key=True)
    target_entity_id = Column(String, primary_key=True)
    suggested_relation_type = Column(String, primary_key=True)
    category_id = Column(String, nullable=False)                 # 类别维度闸门查询用
    created_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 大模型配置（页面配置，多套方案，同一时间仅一条生效）=====

class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False, default="openai")   # openai | anthropic
    api_key = Column(String, default="")
    base_url = Column(String, default="")
    model = Column(String, default="")
    max_tokens = Column(Integer, default=4096)
    temperature = Column(Float, default=0.7)
    is_active = Column(Integer, nullable=False, default=0)        # 1 = 当前生效
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    description = Column(String, default="")
    instructions = Column(Text, nullable=False, default="")
    files = Column(Text, nullable=False, default="")  # 配套文件清单（JSON 数组，仅 path/size/is_text 元数据）
    group_id = Column(String, nullable=True)          # 所属分组（agent_skill_groups.id），NULL = 未分组
    is_enabled = Column(Integer, nullable=False, default=1)
    is_preset = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    file_dir = Column(Text, nullable=False, default="")  # 配套文件磁盘目录（相对运行目录，如 data/skills/<code>）
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class AgentSkillGroup(Base):
    """技能分组：任意层级嵌套（parent_id 指向父分组，NULL = 根级）。全局，不按知识库隔离。"""

    __tablename__ = "agent_skill_groups"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String(100), nullable=False)
    parent_id = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class AgentSkillSeedTombstone(Base):
    """已删除预设技能的 code 墓碑：seed_presets 跳过这些 code，防止重启复活。"""

    __tablename__ = "agent_skill_seed_tombstones"

    code = Column(String, primary_key=True)
    deleted_at = Column(String, default=lambda: datetime.now().isoformat())


class Agent(Base):
    """智能体：知识库(KB) + 技能(Skills) + 人设(System Prompt) 的可复用组合。

    v1 仅暴露 KB/技能/人设；model/temperature 预留（后续覆盖全局 LLM 配置）。
    is_preset=1 为内置智能体（seed 生成，不可删除，如「默认智能体」）。
    """

    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    kb_id = Column(String, nullable=False, default="")
    system_prompt = Column(Text, default="")
    skill_ids = Column(Text, default="[]")    # JSON 数组，默认启用的技能 id
    model = Column(String, default="")        # 空 = 用当前激活的 LLM 配置
    temperature = Column(Float, default=0.7)
    is_preset = Column(Integer, nullable=False, default=0)  # 1 = 内置（不可删除）
    is_enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Workflow(Base):
    """工作流定义：节点 + 边的 DAG 图（definition 存 JSON blob）。"""

    __tablename__ = "workflows"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    definition = Column(Text, nullable=False, default='{"nodes":[],"edges":[]}')
    # 所属本体类别（顶层模块，逻辑关联 ontology_categories.id，无外键；空串 = 未分类）
    category_id = Column(String, nullable=False, default="")
    is_published = Column(Integer, nullable=False, default=0)  # 预留：发布为端点（v2）
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class WorkflowRun(Base):
    """工作流运行记录：一次执行一行，节点状态存 JSON blob。"""

    __tablename__ = "workflow_runs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    workflow_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="running")  # running | succeeded | failed | cancelled
    inputs = Column(Text, default="{}")
    outputs = Column(Text, default="{}")
    node_states = Column(Text, default="{}")
    error = Column(Text, default="")
    started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)
    duration_ms = Column(Integer, default=0)
    # 触发来源标记（定时调度模块写入）：schedule=定时触发 / manual=手动运行；NULL 为旧记录
    trigger_source = Column(String, nullable=True)
    schedule_id = Column(String, nullable=True)
    # ---- 人工节点：挂起 / 续跑支持 ----
    context_snapshot = Column(Text, default="{}")      # 挂起点 LangGraph state 全量 {start, outputs}
    pending_node_id = Column(String, nullable=True)    # 挂起在哪个节点
    definition_snapshot = Column(Text, nullable=True)  # 挂起时的定义快照（续跑优先使用）
    waiting_at = Column(String, nullable=True)         # 进入等待的时间


class WorkflowHumanTask(Base):
    """人工节点任务：运行到人工节点时挂起，产出一条待处理任务，处理后工作流续跑。"""

    __tablename__ = "workflow_human_tasks"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    run_id = Column(String, nullable=False)
    workflow_id = Column(String, nullable=False)
    workflow_name = Column(String, default="")
    node_id = Column(String, nullable=False)
    node_title = Column(String, default="")
    # pending | approved | rejected | submitted | cancelled | expired
    status = Column(String, nullable=False, default="pending")
    mode = Column(String, default="approve")           # approve | form
    description = Column(Text, default="")             # 渲染后的说明文本
    form_schema = Column(Text, default="{}")           # JSON: {display_fields, form_fields, decisions, comment, submit_text}
    form_data = Column(Text, default="{}")             # JSON: 渲染后的只读待审内容快照
    filled_data = Column(Text, default="{}")           # JSON: 人工填写结果（form 模式）
    comment_required = Column(Integer, nullable=False, default=0)
    decision = Column(String, nullable=True)           # approved | rejected | submitted
    comment = Column(Text, default="")
    operator = Column(String, default="")
    assignee = Column(String, default="")
    due_at = Column(String, nullable=True)             # NULL = 不设超时
    timeout_action = Column(String, default="keep_pending")
    trigger_source = Column(String, nullable=True)     # manual | schedule
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    decided_at = Column(String, nullable=True)
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Schedule(Base):
    """定时调度计划：绑定一个工作流 + 触发规则 + 固定入参，到点自动执行。"""

    __tablename__ = "schedules"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String(100), nullable=False)
    description = Column(Text, default="")
    workflow_id = Column(String, nullable=False)
    trigger = Column(String, nullable=False)                 # cron | interval | once
    trigger_config = Column(Text, nullable=False, default="{}")
    input_params = Column(Text, nullable=False, default="{}")
    enabled = Column(Integer, nullable=False, default=1)     # 1 启用 / 0 停用
    muted = Column(Integer, nullable=False, default=0)       # 1 静默（仍执行，不告警）
    next_run_at = Column(String, nullable=True)
    last_run_at = Column(String, nullable=True)
    last_status = Column(String, nullable=True)              # succeeded | failed | running | none
    consecutive_failures = Column(Integer, nullable=False, default=0)
    max_failures_alert = Column(Integer, nullable=False, default=3)
    alert_on_failure = Column(Integer, nullable=False, default=1)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


# ===== 用户与权限体系（migration_029，无外键）=====


class User(Base):
    """系统用户。status: active / disabled / locked。"""

    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    username = Column(String(64), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(64), default="")
    email = Column(String(128), default="")
    phone = Column(String(32), default="")
    avatar = Column(String(255), default="")
    status = Column(String(20), default="active")
    token_version = Column(Integer, nullable=False, default=1)
    is_system = Column(Integer, nullable=False, default=0)
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(String, nullable=True)
    last_login_at = Column(String, nullable=True)
    last_login_ip = Column(String(64), default="")
    password_changed_at = Column(String, nullable=True)
    must_change_password = Column(Integer, nullable=False, default=0)
    remark = Column(String(500), default="")
    created_by = Column(String(64), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class UserGroup(Base):
    """用户组（部门/团队）：授权推荐挂到组上，而非逐人授权。"""

    __tablename__ = "user_groups"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    name = Column(String(100), nullable=False)
    code = Column(String(64), default="")
    parent_id = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_system = Column(Integer, nullable=False, default=0)
    remark = Column(String(500), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class UserGroupMember(Base):
    """用户组成员（用户可属多组）。"""

    __tablename__ = "user_group_members"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    group_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class Role(Base):
    """角色 = 权限集合（非层级，对标 Palantir Foundry 的 Roles）。"""

    __tablename__ = "roles"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), default="")
    is_system = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class Permission(Base):
    """权限点：type = menu（菜单可见）/ api（接口）/ action（按钮）。

    resource 用于接口匹配，形如 `POST /api/kb/*`，支持 `*` 单段与 `**` 多段通配。
    """

    __tablename__ = "permissions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    code = Column(String(100), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    module = Column(String(50), nullable=False)
    type = Column(String(20), nullable=False, default="api")
    resource = Column(String(200), default="")
    is_system = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class RolePermission(Base):
    """角色 ↔ 权限。"""

    __tablename__ = "role_permissions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    role_id = Column(String, nullable=False)
    permission_id = Column(String, nullable=False)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class UserRole(Base):
    """用户 ↔ 角色（直接授权，推荐优先用组授权）。"""

    __tablename__ = "user_roles"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String, nullable=False)
    role_id = Column(String, nullable=False)
    created_by = Column(String(64), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class GroupRole(Base):
    """用户组 ↔ 角色（推荐授权方式）。"""

    __tablename__ = "group_roles"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    group_id = Column(String, nullable=False)
    role_id = Column(String, nullable=False)
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class UserPermission(Base):
    """例外授权：直接给某个用户加减权限点（effect: allow / deny）。"""

    __tablename__ = "user_permissions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String, nullable=False)
    permission_id = Column(String, nullable=False)
    effect = Column(String(10), nullable=False, default="allow")
    created_by = Column(String(64), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class UserSession(Base):
    """登录会话：支撑在线用户查看与强制下线（JWT 无状态之外的服务端权威状态）。"""

    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True)                       # sid
    user_id = Column(String, nullable=False)
    username = Column(String(64), default="")
    status = Column(String(20), nullable=False, default="online")  # online/offline/kicked/expired
    ip = Column(String(64), default="")
    user_agent = Column(String(500), default="")
    device = Column(String(100), default="")
    login_at = Column(String, nullable=True)
    last_active_at = Column(String, nullable=True)
    logout_at = Column(String, nullable=True)
    expires_at = Column(String, nullable=True)
    kicked_by = Column(String(64), default="")
    kick_reason = Column(String(200), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class AuthLog(Base):
    """认证日志：登录/登出/失败/被踢/过期/改密/锁定等安全事件。"""

    __tablename__ = "auth_logs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String(64), default="")
    username = Column(String(64), default="")
    action = Column(String(32), nullable=False)
    result = Column(String(16), nullable=False)
    reason = Column(String(200), default="")
    session_id = Column(String(64), default="")
    ip = Column(String(64), default="")
    user_agent = Column(String(500), default="")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class AuditLog(Base):
    """操作审计日志：业务增删改、权限变更、配置变更等。"""

    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String(64), default="")
    username = Column(String(64), default="")
    nickname = Column(String(64), default="")
    session_id = Column(String(64), default="")
    module = Column(String(50), default="")
    action = Column(String(64), default="")
    action_label = Column(String(100), default="")
    target_type = Column(String(64), default="")
    target_id = Column(String, default="")
    target_name = Column(String(200), default="")
    method = Column(String(10), default="")
    path = Column(String(300), default="")
    params = Column(Text, nullable=True)
    before_value = Column(Text, nullable=True)
    after_value = Column(Text, nullable=True)
    result = Column(String(16), default="success")
    status_code = Column(Integer, default=0)
    error_msg = Column(String(1000), default="")
    ip = Column(String(64), default="")
    user_agent = Column(String(500), default="")
    request_id = Column(String(64), default="")
    duration_ms = Column(Integer, default=0)
    source = Column(String(20), default="http")
    created_at = Column(String, default=lambda: datetime.now().isoformat())


class SecuritySetting(Base):
    """安全与登录策略（key-value，运行时可改）。"""

    __tablename__ = "security_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(500), default="")
    updated_by = Column(String(64), default="")
    updated_at = Column(String, default=lambda: datetime.now().isoformat())


class AppSetting(Base):
    """系统偏好设置（key-value，运行时可改，页面开关；如分片自动分析）。"""

    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(500), default="")
    updated_by = Column(String(64), default="")
    updated_at = Column(String, default=lambda: datetime.now().isoformat())

