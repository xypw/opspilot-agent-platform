-- 可重复执行，不重建向量、不改变审核状态、不删除现有片段。
ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS lexical_terms TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS knowledge_chunks_lexical_terms_gin_idx
    ON knowledge_chunks USING gin (lexical_terms);
