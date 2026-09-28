-- 对已有数据卷手动运行一次；新数据卷直接使用 db/init.sql。
-- 旧片段标为 published，以保持原有检索；无法补造旧 PDF 原件和审核记录。
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_pdf BYTEA;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_audit JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_version TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS publication_status TEXT NOT NULL DEFAULT 'published';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS origin_review_id TEXT;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS source_start INTEGER;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS source_end INTEGER;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS heading_path JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS parser_version TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS validation_status TEXT NOT NULL DEFAULT 'validated';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS validation_reasons JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS publication_status TEXT NOT NULL DEFAULT 'published';

CREATE TABLE IF NOT EXISTS document_review_queue (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_pdf BYTEA NOT NULL,
    ingestion_result JSONB NOT NULL,
    approved_pages INTEGER[] NOT NULL DEFAULT '{}',
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_review_events (
    id BIGSERIAL PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document_review_queue(id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL,
    approved_pages INTEGER[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
