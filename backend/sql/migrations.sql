-- KnowSource 增量迁移脚本
-- 每条迁移用注释标记版本号，init_db() 逐条执行并记录已完成的版本
-- 新增迁移请追加到文件末尾

-- migration_001
ALTER TABLE file_assets ADD COLUMN sources TEXT DEFAULT NULL;

-- migration_002
ALTER TABLE crawl_jobs ADD COLUMN detail TEXT DEFAULT NULL;
ALTER TABLE crawl_jobs ADD COLUMN logs TEXT DEFAULT NULL;

-- migration_003
ALTER TABLE files ADD COLUMN asset_id VARCHAR DEFAULT NULL REFERENCES file_assets(id) ON DELETE SET NULL;
ALTER TABLE files ADD COLUMN detail TEXT DEFAULT NULL;
ALTER TABLE files ADD COLUMN logs TEXT DEFAULT NULL;

-- migration_004
CREATE TABLE IF NOT EXISTS agent_skills (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    description VARCHAR DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    is_preset INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_skills_code ON agent_skills(code);

-- migration_004b: 添加属性编码字段（重命名自重复的 migration_004，修复全新库启动时 _migrations UNIQUE 冲突）
ALTER TABLE ontology_attributes ADD COLUMN code VARCHAR(50) DEFAULT NULL;
ALTER TABLE ontology_template_attributes ADD COLUMN code VARCHAR(50) DEFAULT NULL;

-- migration_005: 添加关系编码字段
ALTER TABLE ontology_relations ADD COLUMN code VARCHAR(50) DEFAULT NULL;

-- migration_006: 本体建议表（动态生成 + 审核）
CREATE TABLE IF NOT EXISTS ontology_suggestions (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL,
    file_id TEXT,
    status TEXT NOT NULL DEFAULT 'generating',
    source_mode TEXT NOT NULL DEFAULT 'free_extraction',
    suggestion_data TEXT NOT NULL DEFAULT '{}',
    score REAL DEFAULT 0.0,
    review_notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewer TEXT
);

-- migration_007: LLM 配置表（页面配置，多套方案，单一生效）
CREATE TABLE IF NOT EXISTS llm_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'openai',
    api_key TEXT DEFAULT '',
    base_url TEXT DEFAULT '',
    model TEXT DEFAULT '',
    max_tokens INTEGER DEFAULT 4096,
    temperature REAL DEFAULT 0.7,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

-- migration_008: 技能配套文件存储（完整 ZIP 技能包导入）
ALTER TABLE agent_skills ADD COLUMN files TEXT NOT NULL DEFAULT '';

-- migration_009: 技能分组（任意嵌套）+ 挂组列 + 配套文件落盘目录 + 预设删除墓碑
CREATE TABLE IF NOT EXISTS agent_skill_groups (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    parent_id VARCHAR DEFAULT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_agent_skill_groups_parent ON agent_skill_groups(parent_id);
ALTER TABLE agent_skills ADD COLUMN group_id VARCHAR DEFAULT NULL;
ALTER TABLE agent_skills ADD COLUMN file_dir TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS agent_skill_seed_tombstones (
    code VARCHAR PRIMARY KEY,
    deleted_at VARCHAR
);

-- migration_010: 本体服务（动作）：本体级通用动作 + 实体级个性化动作
CREATE TABLE IF NOT EXISTS ontology_services (
    id VARCHAR PRIMARY KEY,
    owner_type VARCHAR NOT NULL DEFAULT 'ontology',
    ontology_id VARCHAR NOT NULL,
    entity_id VARCHAR DEFAULT NULL,
    name VARCHAR(100) NOT NULL,
    code VARCHAR(100) NOT NULL,
    description TEXT DEFAULT '',
    params_schema TEXT DEFAULT '[]',
    code_text TEXT NOT NULL DEFAULT '',
    language VARCHAR(20) NOT NULL DEFAULT 'python',
    timeout_seconds INTEGER NOT NULL DEFAULT 30,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_ontology_services_owner ON ontology_services(owner_type, ontology_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_ontology_services_code ON ontology_services(owner_type, code);

-- migration_011: 智能体配置（KB + 技能 + 人设 的可复用组合）
CREATE TABLE IF NOT EXISTS agents (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT DEFAULT '',
    kb_id VARCHAR NOT NULL,
    system_prompt TEXT DEFAULT '',
    skill_ids TEXT DEFAULT '[]',
    model VARCHAR DEFAULT '',
    temperature REAL DEFAULT 0.7,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_agents_kb ON agents(kb_id);

-- migration_012: 工作流（定义 + 运行记录）
CREATE TABLE IF NOT EXISTS workflows (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT DEFAULT '',
    definition TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    is_published INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    id VARCHAR PRIMARY KEY,
    workflow_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'running',
    inputs TEXT DEFAULT '{}',
    outputs TEXT DEFAULT '{}',
    node_states TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    started_at VARCHAR,
    finished_at VARCHAR,
    duration_ms INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_wf ON workflow_runs(workflow_id);

-- migration_013: 智能体内置标记 + kb_id 放宽（内置「默认智能体」不绑 KB）
ALTER TABLE agents ADD COLUMN is_preset INTEGER NOT NULL DEFAULT 0;

-- migration_014: 定时调度模块
-- workflow_runs 增加触发来源标记（定时触发 vs 手动运行）
ALTER TABLE workflow_runs ADD COLUMN trigger_source VARCHAR DEFAULT NULL;
ALTER TABLE workflow_runs ADD COLUMN schedule_id VARCHAR DEFAULT NULL;
-- 定时调度计划表
CREATE TABLE IF NOT EXISTS schedules (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT DEFAULT '',
    workflow_id VARCHAR NOT NULL,
    trigger VARCHAR NOT NULL,
    trigger_config TEXT NOT NULL DEFAULT '{}',
    input_params TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    muted INTEGER NOT NULL DEFAULT 0,
    next_run_at VARCHAR,
    last_run_at VARCHAR,
    last_status VARCHAR,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    max_failures_alert INTEGER NOT NULL DEFAULT 3,
    alert_on_failure INTEGER NOT NULL DEFAULT 1,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_schedules_wf ON schedules(workflow_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_schedule ON workflow_runs(schedule_id);

-- migration_015: 工作流「人工节点」模块
-- 1) workflow_runs 增加挂起/续跑支持列
ALTER TABLE workflow_runs ADD COLUMN context_snapshot TEXT DEFAULT '{}';
ALTER TABLE workflow_runs ADD COLUMN pending_node_id VARCHAR DEFAULT NULL;
ALTER TABLE workflow_runs ADD COLUMN definition_snapshot TEXT DEFAULT NULL;
ALTER TABLE workflow_runs ADD COLUMN waiting_at VARCHAR DEFAULT NULL;
-- 2) 人工任务表
CREATE TABLE IF NOT EXISTS workflow_human_tasks (
    id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    workflow_id VARCHAR NOT NULL,
    workflow_name VARCHAR DEFAULT '',
    node_id VARCHAR NOT NULL,
    node_title VARCHAR DEFAULT '',
    status VARCHAR NOT NULL DEFAULT 'pending',
    mode VARCHAR DEFAULT 'approve',
    description TEXT DEFAULT '',
    form_schema TEXT DEFAULT '{}',
    form_data TEXT DEFAULT '{}',
    filled_data TEXT DEFAULT '{}',
    comment_required INTEGER NOT NULL DEFAULT 0,
    decision VARCHAR DEFAULT NULL,
    comment TEXT DEFAULT '',
    operator VARCHAR DEFAULT '',
    assignee VARCHAR DEFAULT '',
    due_at VARCHAR DEFAULT NULL,
    timeout_action VARCHAR DEFAULT 'keep_pending',
    trigger_source VARCHAR DEFAULT NULL,
    created_at VARCHAR,
    decided_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_human_tasks_status ON workflow_human_tasks(status);
CREATE INDEX IF NOT EXISTS idx_human_tasks_run ON workflow_human_tasks(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_human_tasks_node ON workflow_human_tasks(run_id, node_id);

-- migration_016: 图分析工作台——图迁入运行记录表
CREATE TABLE IF NOT EXISTS graph_sync_runs (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    entity_count INTEGER DEFAULT 0,
    relation_count INTEGER DEFAULT 0,
    total_entities INTEGER DEFAULT 0,
    total_relations INTEGER DEFAULT 0,
    watermark VARCHAR,
    projection TEXT DEFAULT '',
    error TEXT,
    started_at VARCHAR,
    finished_at VARCHAR,
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_graph_sync_runs_cat ON graph_sync_runs(category_id);
CREATE INDEX IF NOT EXISTS idx_graph_sync_runs_status ON graph_sync_runs(status);

-- migration_017: 图分析工作台——图计算/推理任务表
CREATE TABLE IF NOT EXISTS graph_analysis_tasks (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL DEFAULT 'algorithm',
    algorithm VARCHAR NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    status VARCHAR NOT NULL,
    stats TEXT DEFAULT '{}',
    results TEXT,
    error TEXT,
    started_at VARCHAR,
    finished_at VARCHAR,
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_graph_analysis_tasks_cat ON graph_analysis_tasks(category_id);
CREATE INDEX IF NOT EXISTS idx_graph_analysis_tasks_status ON graph_analysis_tasks(status);

-- migration_018: 图推理——隐含关系建议表 + tombstone（审核闭环，姊妹篇共用）
CREATE TABLE IF NOT EXISTS relation_suggestions (
    id VARCHAR PRIMARY KEY,
    kb_id VARCHAR NOT NULL,
    category_id VARCHAR NOT NULL,
    source_entity_id VARCHAR NOT NULL,
    target_entity_id VARCHAR NOT NULL,
    suggested_relation_type VARCHAR NOT NULL,
    relation_def_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL DEFAULT 'rule',
    score FLOAT DEFAULT 0,
    confidence FLOAT DEFAULT 0,
    evidence TEXT DEFAULT '',
    reason VARCHAR DEFAULT '',
    status VARCHAR NOT NULL,
    created_at VARCHAR,
    reviewed_at VARCHAR,
    reviewer VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_relation_suggestions_kb ON relation_suggestions(kb_id);
CREATE INDEX IF NOT EXISTS idx_relation_suggestions_cat ON relation_suggestions(category_id);
CREATE INDEX IF NOT EXISTS idx_relation_suggestions_status ON relation_suggestions(status);

-- migration_019: 对象类型/属性元数据扩展 + 共享属性
ALTER TABLE ontologies ADD COLUMN code VARCHAR(64) DEFAULT NULL;
ALTER TABLE ontologies ADD COLUMN display_name VARCHAR(100) DEFAULT '';
ALTER TABLE ontologies ADD COLUMN plural_name VARCHAR(100) DEFAULT '';
ALTER TABLE ontologies ADD COLUMN title_key VARCHAR(64) DEFAULT '';
ALTER TABLE ontologies ADD COLUMN primary_key VARCHAR(64) DEFAULT 'name';
ALTER TABLE ontologies ADD COLUMN icon VARCHAR(64) DEFAULT '';
ALTER TABLE ontologies ADD COLUMN status VARCHAR(20) DEFAULT 'active';
ALTER TABLE ontologies ADD COLUMN visibility VARCHAR(20) DEFAULT 'public';
ALTER TABLE ontologies ADD COLUMN group_name VARCHAR(100) DEFAULT '';
ALTER TABLE ontology_attributes ADD COLUMN is_edit_only INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ontology_attributes ADD COLUMN render_hint VARCHAR(50) DEFAULT '';
ALTER TABLE ontology_attributes ADD COLUMN format VARCHAR(100) DEFAULT '';
ALTER TABLE ontology_attributes ADD COLUMN unit VARCHAR(32) DEFAULT '';
ALTER TABLE ontology_attributes ADD COLUMN shared_property_id VARCHAR DEFAULT '';

CREATE TABLE IF NOT EXISTS ontology_shared_properties (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    code VARCHAR(64) DEFAULT NULL,
    data_type VARCHAR(20) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_required INTEGER NOT NULL DEFAULT 0,
    default_value VARCHAR(200) DEFAULT NULL,
    enum_values TEXT DEFAULT NULL,
    unit VARCHAR(32) DEFAULT '',
    format VARCHAR(100) DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(code)
);

-- migration_020: 本体接口（Interface）：共享属性/链接契约 → 多态
CREATE TABLE IF NOT EXISTS ontology_interfaces (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    name VARCHAR(100) NOT NULL,
    code VARCHAR(64) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    icon VARCHAR(64) DEFAULT '',
    extends TEXT DEFAULT NULL,
    interface_kind VARCHAR(20) DEFAULT 'functional',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(category_id, code)
);

CREATE TABLE IF NOT EXISTS ontology_interface_properties (
    id VARCHAR PRIMARY KEY,
    interface_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    code VARCHAR(64) NOT NULL,
    data_type VARCHAR(20) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_required INTEGER NOT NULL DEFAULT 1,
    default_value VARCHAR(200) DEFAULT NULL,
    enum_values TEXT DEFAULT NULL,
    shared_property_id VARCHAR DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(interface_id, code)
);

CREATE TABLE IF NOT EXISTS ontology_interface_links (
    id VARCHAR PRIMARY KEY,
    interface_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    code VARCHAR(64) NOT NULL,
    target_interface_id VARCHAR DEFAULT NULL,
    target_ontology_id VARCHAR DEFAULT '',
    cardinality VARCHAR(16) DEFAULT 'ONE_TO_MANY',
    is_required INTEGER NOT NULL DEFAULT 0,
    UNIQUE(interface_id, code)
);

CREATE TABLE IF NOT EXISTS ontology_interface_implementations (
    id VARCHAR PRIMARY KEY,
    interface_id VARCHAR NOT NULL,
    ontology_id VARCHAR NOT NULL,
    property_mapping TEXT DEFAULT '{}',
    link_mapping TEXT DEFAULT NULL,
    status VARCHAR(20) DEFAULT 'active',
    created_at VARCHAR,
    UNIQUE(interface_id, ontology_id)
);

CREATE TABLE IF NOT EXISTS relation_suggestion_tombstones (
    kb_id VARCHAR NOT NULL,
    source_entity_id VARCHAR NOT NULL,
    target_entity_id VARCHAR NOT NULL,
    suggested_relation_type VARCHAR NOT NULL,
    category_id VARCHAR NOT NULL,
    created_at VARCHAR,
    PRIMARY KEY (kb_id, source_entity_id, target_entity_id, suggested_relation_type)
);

-- migration_021: 链接升级（S5）——基数/反向/对称/传递/状态 + 约束端点基数 + 关系实例属性
ALTER TABLE ontology_relations ADD COLUMN cardinality VARCHAR(16) DEFAULT 'MANY_TO_MANY';
ALTER TABLE ontology_relations ADD COLUMN inverse_name VARCHAR(50) DEFAULT '';
ALTER TABLE ontology_relations ADD COLUMN is_symmetric INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ontology_relations ADD COLUMN is_transitive INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ontology_relations ADD COLUMN status VARCHAR(20) DEFAULT 'active';
ALTER TABLE ontology_relation_constraints ADD COLUMN source_min INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN source_max INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN target_min INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN target_max INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN is_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE relations ADD COLUMN properties TEXT DEFAULT NULL;

-- migration_022: 关系属性定义表（S5）
CREATE TABLE IF NOT EXISTS ontology_relation_properties (
    id VARCHAR PRIMARY KEY,
    relation_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    code VARCHAR(64) DEFAULT NULL,
    data_type VARCHAR(20) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_required INTEGER NOT NULL DEFAULT 0,
    enum_values TEXT DEFAULT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    UNIQUE(relation_id, code)
);

-- migration_023: 对象视图表（S6）
CREATE TABLE IF NOT EXISTS ontology_object_views (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    ontology_id VARCHAR DEFAULT '',
    interface_code VARCHAR DEFAULT '',
    name VARCHAR(100) NOT NULL,
    layout TEXT NOT NULL DEFAULT '{}',
    is_default INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at VARCHAR,
    updated_at VARCHAR
);

-- migration_024: 本体版本快照表（S7）
CREATE TABLE IF NOT EXISTS ontology_versions (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    version_no INTEGER NOT NULL,
    snapshot TEXT NOT NULL DEFAULT '{}',
    source VARCHAR(20) DEFAULT 'manual',
    note VARCHAR(1000) DEFAULT '',
    created_by VARCHAR(64) DEFAULT '',
    merged_suggestion_id VARCHAR DEFAULT '',
    created_at VARCHAR
);

-- migration_025: 本体建议提案化（S7）——suggestion / change 共用一张表
ALTER TABLE ontology_suggestions ADD COLUMN proposal_type VARCHAR(20) DEFAULT 'suggestion';
ALTER TABLE ontology_suggestions ADD COLUMN base_version INTEGER DEFAULT 0;
ALTER TABLE ontology_suggestions ADD COLUMN diff TEXT DEFAULT NULL;
ALTER TABLE ontology_suggestions ADD COLUMN reviewers VARCHAR DEFAULT '';
ALTER TABLE ontology_suggestions ADD COLUMN merged_version_id VARCHAR DEFAULT '';

-- migration_026: 共享属性挂载来源标记（取消挂载时仅删除由共享属性生成的属性）
-- 注意：只加列、不回填历史数据。历史已挂载属性默认 is_shared_created=0（视为手工/绑定属性），
-- 取消挂载时只解绑、不删除，避免误删用户手工创建的属性。新挂载（apply 新建）会显式置 1。
ALTER TABLE ontology_attributes ADD COLUMN is_shared_created INTEGER NOT NULL DEFAULT 0;

-- migration_027: 动作编排（execution_mode / flow），只加列、不回填历史数据
ALTER TABLE ontology_services ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(10) NOT NULL DEFAULT 'code';
ALTER TABLE ontology_services ADD COLUMN IF NOT EXISTS flow TEXT DEFAULT NULL;

-- migration_028: 工作流归属本体类别（顶层模块维度管理；空串 = 未分类）
ALTER TABLE workflows ADD COLUMN category_id VARCHAR DEFAULT '';

-- migration_029: 用户与权限体系（用户 / 用户组 / 角色 / 权限 / 会话 / 认证日志 / 操作日志 / 安全策略）
-- 约定：与既有 45 张表一致，无数据库外键（service 层维护逻辑关联）；布尔用 INTEGER；时间用 VARCHAR(ISO8601)
CREATE TABLE IF NOT EXISTS users (
    id                   VARCHAR PRIMARY KEY,
    username             VARCHAR(64)  NOT NULL UNIQUE,
    password_hash        VARCHAR(255) NOT NULL,
    nickname             VARCHAR(64)  DEFAULT '',
    email                VARCHAR(128) DEFAULT '',
    phone                VARCHAR(32)  DEFAULT '',
    avatar               VARCHAR(255) DEFAULT '',
    status               VARCHAR(20)  DEFAULT 'active',
    token_version        INTEGER      NOT NULL DEFAULT 1,
    is_system            INTEGER      NOT NULL DEFAULT 0,
    failed_attempts      INTEGER      NOT NULL DEFAULT 0,
    locked_until         VARCHAR,
    last_login_at        VARCHAR,
    last_login_ip        VARCHAR(64)  DEFAULT '',
    password_changed_at  VARCHAR,
    must_change_password INTEGER      NOT NULL DEFAULT 0,
    remark               VARCHAR(500) DEFAULT '',
    created_by           VARCHAR(64)  DEFAULT '',
    created_at           VARCHAR,
    updated_at           VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

CREATE TABLE IF NOT EXISTS user_groups (
    id         VARCHAR PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    code       VARCHAR(64)  DEFAULT '',
    parent_id  VARCHAR      DEFAULT NULL,
    sort_order INTEGER      NOT NULL DEFAULT 0,
    is_system  INTEGER      NOT NULL DEFAULT 0,
    remark     VARCHAR(500) DEFAULT '',
    created_at VARCHAR,
    updated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS user_group_members (
    id         VARCHAR PRIMARY KEY,
    group_id   VARCHAR NOT NULL,
    user_id    VARCHAR NOT NULL,
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_ugm_group ON user_group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_ugm_user  ON user_group_members(user_id);

CREATE TABLE IF NOT EXISTS roles (
    id          VARCHAR PRIMARY KEY,
    code        VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_system   INTEGER      NOT NULL DEFAULT 0,
    sort_order  INTEGER      NOT NULL DEFAULT 0,
    created_at  VARCHAR,
    updated_at  VARCHAR
);

CREATE TABLE IF NOT EXISTS permissions (
    id         VARCHAR PRIMARY KEY,
    code       VARCHAR(100) NOT NULL UNIQUE,
    name       VARCHAR(100) NOT NULL,
    module     VARCHAR(50)  NOT NULL,
    type       VARCHAR(20)  NOT NULL DEFAULT 'api',
    resource   VARCHAR(200) DEFAULT '',
    is_system  INTEGER      NOT NULL DEFAULT 0,
    sort_order INTEGER      NOT NULL DEFAULT 0,
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_perm_module ON permissions(module);

CREATE TABLE IF NOT EXISTS role_permissions (
    id            VARCHAR PRIMARY KEY,
    role_id       VARCHAR NOT NULL,
    permission_id VARCHAR NOT NULL,
    created_at    VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_rp_role ON role_permissions(role_id);

CREATE TABLE IF NOT EXISTS user_roles (
    id         VARCHAR PRIMARY KEY,
    user_id    VARCHAR NOT NULL,
    role_id    VARCHAR NOT NULL,
    created_by VARCHAR DEFAULT '',
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_ur_user ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_ur_role ON user_roles(role_id);

CREATE TABLE IF NOT EXISTS group_roles (
    id         VARCHAR PRIMARY KEY,
    group_id   VARCHAR NOT NULL,
    role_id    VARCHAR NOT NULL,
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_gr_group ON group_roles(group_id);

CREATE TABLE IF NOT EXISTS user_permissions (
    id            VARCHAR PRIMARY KEY,
    user_id       VARCHAR NOT NULL,
    permission_id VARCHAR NOT NULL,
    effect        VARCHAR(10) NOT NULL DEFAULT 'allow',
    created_by    VARCHAR DEFAULT '',
    created_at    VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_up_user ON user_permissions(user_id);

CREATE TABLE IF NOT EXISTS user_sessions (
    id             VARCHAR PRIMARY KEY,
    user_id        VARCHAR NOT NULL,
    username       VARCHAR(64)  DEFAULT '',
    status         VARCHAR(20)  NOT NULL DEFAULT 'online',
    ip             VARCHAR(64)  DEFAULT '',
    user_agent     VARCHAR(500) DEFAULT '',
    device         VARCHAR(100) DEFAULT '',
    login_at       VARCHAR,
    last_active_at VARCHAR,
    logout_at      VARCHAR,
    expires_at     VARCHAR,
    kicked_by      VARCHAR(64)  DEFAULT '',
    kick_reason    VARCHAR(200) DEFAULT '',
    created_at     VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_sess_user   ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sess_status ON user_sessions(status);

CREATE TABLE IF NOT EXISTS auth_logs (
    id         VARCHAR PRIMARY KEY,
    user_id    VARCHAR DEFAULT '',
    username   VARCHAR(64) DEFAULT '',
    action     VARCHAR(32) NOT NULL,
    result     VARCHAR(16) NOT NULL,
    reason     VARCHAR(200) DEFAULT '',
    session_id VARCHAR DEFAULT '',
    ip         VARCHAR(64)  DEFAULT '',
    user_agent VARCHAR(500) DEFAULT '',
    created_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_authlog_user ON auth_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_authlog_time ON auth_logs(created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id            VARCHAR PRIMARY KEY,
    user_id       VARCHAR DEFAULT '',
    username      VARCHAR(64)  DEFAULT '',
    nickname      VARCHAR(64)  DEFAULT '',
    session_id    VARCHAR DEFAULT '',
    module        VARCHAR(50)  DEFAULT '',
    action        VARCHAR(64)  DEFAULT '',
    action_label  VARCHAR(100) DEFAULT '',
    target_type   VARCHAR(64)  DEFAULT '',
    target_id     VARCHAR      DEFAULT '',
    target_name   VARCHAR(200) DEFAULT '',
    method        VARCHAR(10)  DEFAULT '',
    path          VARCHAR(300) DEFAULT '',
    params        TEXT,
    before_value  TEXT,
    after_value   TEXT,
    result        VARCHAR(16)   DEFAULT 'success',
    status_code   INTEGER       DEFAULT 0,
    error_msg     VARCHAR(1000) DEFAULT '',
    ip            VARCHAR(64)   DEFAULT '',
    user_agent    VARCHAR(500)  DEFAULT '',
    request_id    VARCHAR(64)   DEFAULT '',
    duration_ms   INTEGER       DEFAULT 0,
    source        VARCHAR(20)   DEFAULT 'http',
    created_at    VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_audit_time   ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_user   ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_module ON audit_logs(module);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);

CREATE TABLE IF NOT EXISTS security_settings (
    key        VARCHAR(64) PRIMARY KEY,
    value      VARCHAR(500) DEFAULT '',
    updated_by VARCHAR(64)  DEFAULT '',
    updated_at VARCHAR
);
