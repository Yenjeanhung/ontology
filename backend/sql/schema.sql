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
