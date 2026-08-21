-- KnowSource 全量建表脚本（首次初始化时执行）
-- 由 init_db() 在数据库为空时自动调用

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT DEFAULT '',
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS file_directories (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    parent_id VARCHAR DEFAULT NULL REFERENCES file_directories(id) ON DELETE CASCADE,
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS file_assets (
    id VARCHAR PRIMARY KEY,
    directory_id VARCHAR DEFAULT NULL REFERENCES file_directories(id) ON DELETE SET NULL,
    name VARCHAR NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    ext VARCHAR DEFAULT '',
    mime_type VARCHAR DEFAULT '',
    sha256 VARCHAR DEFAULT '',
    path VARCHAR,
    source_type VARCHAR DEFAULT 'upload',
    source_url VARCHAR,
    source_keyword VARCHAR,
    sources TEXT,
    summary TEXT,
    status VARCHAR DEFAULT 'ready',
    message VARCHAR,
    created_at VARCHAR,
    updated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS files (
    id VARCHAR PRIMARY KEY,
    asset_id VARCHAR DEFAULT NULL REFERENCES file_assets(id) ON DELETE SET NULL,
    kb_id VARCHAR NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    status VARCHAR DEFAULT 'uploading',
    progress INTEGER DEFAULT 0,
    message VARCHAR,
    detail TEXT,
    logs TEXT,
    path VARCHAR,
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS chunks (
    id VARCHAR PRIMARY KEY,
    file_id VARCHAR NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    embedding_id VARCHAR,
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id VARCHAR PRIMARY KEY,
    keyword VARCHAR NOT NULL,
    directory_id VARCHAR DEFAULT NULL REFERENCES file_directories(id) ON DELETE SET NULL,
    status VARCHAR DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    message VARCHAR,
    urls TEXT,
    file_count INTEGER DEFAULT 0,
    detail TEXT,
    logs TEXT,
    created_at VARCHAR,
    finished_at VARCHAR
);

-- ===== 本体定义层（无外键，逻辑关联由 service 层维护）=====

CREATE TABLE IF NOT EXISTS ontology_categories (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS ontologies (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    color VARCHAR(20) DEFAULT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(category_id, name)
);

CREATE TABLE IF NOT EXISTS ontology_attributes (
    id VARCHAR PRIMARY KEY,
    ontology_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    code VARCHAR(50) DEFAULT NULL,
    data_type VARCHAR(20) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_required INTEGER NOT NULL DEFAULT 0,
    default_value VARCHAR(200) DEFAULT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(ontology_id, name)
);

CREATE TABLE IF NOT EXISTS ontology_relations (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(category_id, name)
);

CREATE TABLE IF NOT EXISTS ontology_relation_constraints (
    id VARCHAR PRIMARY KEY,
    category_id VARCHAR NOT NULL,
    source_ontology_id VARCHAR NOT NULL,
    relation_id VARCHAR NOT NULL,
    target_ontology_id VARCHAR NOT NULL,
    description VARCHAR(500) DEFAULT '',
    created_at VARCHAR,
    UNIQUE(category_id, source_ontology_id, relation_id, target_ontology_id)
);

CREATE TABLE IF NOT EXISTS kb_ontology_bindings (
    id VARCHAR PRIMARY KEY,
    kb_id VARCHAR NOT NULL,
    category_id VARCHAR NOT NULL,
    created_at VARCHAR,
    UNIQUE(kb_id)
);

-- ===== 属性模板（全局，跨本体类别复用）=====

CREATE TABLE IF NOT EXISTS ontology_attribute_templates (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS ontology_template_attributes (
    id VARCHAR PRIMARY KEY,
    template_id VARCHAR NOT NULL,
    name VARCHAR(50) NOT NULL,
    code VARCHAR(50) DEFAULT NULL,
    data_type VARCHAR(20) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    is_required INTEGER NOT NULL DEFAULT 0,
    default_value VARCHAR(200) DEFAULT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(template_id, name)
);

CREATE TABLE IF NOT EXISTS ontology_template_bindings (
    id VARCHAR PRIMARY KEY,
    ontology_id VARCHAR NOT NULL,
    template_id VARCHAR NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    UNIQUE(ontology_id, template_id)
);

-- ===== 本体服务（动作）：本体级通用动作 + 实体级个性化动作（无外键）=====
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

-- ===== 实体实例层（抽取后生成，无外键）=====

CREATE TABLE IF NOT EXISTS entities (
    id VARCHAR PRIMARY KEY,
    kb_id VARCHAR NOT NULL,
    ontology_id VARCHAR NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    name VARCHAR(200) NOT NULL,
    description VARCHAR(1000) DEFAULT '',
    properties TEXT DEFAULT NULL,
    source_file_id VARCHAR DEFAULT NULL,
    source_chunk_id VARCHAR DEFAULT NULL,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(kb_id, entity_type, name)
);

CREATE TABLE IF NOT EXISTS relations (
    id VARCHAR PRIMARY KEY,
    kb_id VARCHAR NOT NULL,
    relation_def_id VARCHAR NOT NULL,
    relation_type VARCHAR(50) NOT NULL,
    source_entity_id VARCHAR NOT NULL,
    target_entity_id VARCHAR NOT NULL,
    description VARCHAR(1000) DEFAULT '',
    source_file_id VARCHAR DEFAULT NULL,
    source_chunk_id VARCHAR DEFAULT NULL,
    created_at VARCHAR,
    updated_at VARCHAR,
    UNIQUE(kb_id, source_entity_id, relation_type, target_entity_id)
);

CREATE TABLE IF NOT EXISTS llm_configs (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    provider VARCHAR NOT NULL DEFAULT 'openai',
    api_key VARCHAR DEFAULT '',
    base_url VARCHAR DEFAULT '',
    model VARCHAR DEFAULT '',
    max_tokens INTEGER DEFAULT 4096,
    temperature REAL DEFAULT 0.7,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS agent_skills (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    description VARCHAR DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '',
    group_id VARCHAR DEFAULT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    is_preset INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    file_dir TEXT NOT NULL DEFAULT '',
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_skills_code ON agent_skills(code);

-- ===== 技能分组（全局，任意层级嵌套）=====

CREATE TABLE IF NOT EXISTS agent_skill_groups (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    parent_id VARCHAR DEFAULT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_agent_skill_groups_parent ON agent_skill_groups(parent_id);

-- 已删除预设技能的墓碑（阻止 seed_presets 复活）
CREATE TABLE IF NOT EXISTS agent_skill_seed_tombstones (
    code VARCHAR PRIMARY KEY,
    deleted_at VARCHAR
);
