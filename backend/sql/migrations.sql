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

-- migration_004: 添加属性编码字段
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
