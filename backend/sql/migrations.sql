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
