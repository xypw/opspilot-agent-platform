-- OpsPilot 的本地 PostgreSQL 初始化脚本。
-- Docker 只会在空数据卷的首次启动时执行本文件。

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    priority TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('processing', 'shipped', 'cancelled')),
    product TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    page_count INTEGER NOT NULL CHECK (page_count > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL CHECK (page > 0),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    embedding vector(512) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

-- 后续做语义检索时使用余弦距离；HNSW 比顺序扫描更适合持续增长的知识库。
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw_idx
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- 先写入固定演示业务数据；ON CONFLICT 使容器重启时不会重复插入。
INSERT INTO tickets (id, status, priority) VALUES
    ('T-1001', 'open', 'high'),
    ('T-1002', 'closed', 'low'),
    ('T-1003', 'open', 'medium')
ON CONFLICT (id) DO NOTHING;

INSERT INTO orders (id, status, product) VALUES
    ('O-2001', 'shipped', '机械键盘'),
    ('O-2002', 'processing', '无线鼠标'),
    ('O-2003', 'cancelled', '显示器')
ON CONFLICT (id) DO NOTHING;
