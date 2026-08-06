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
