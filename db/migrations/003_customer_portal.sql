-- 用户、可撤销登录会话与可见聊天历史；不保存模型密钥或服务间令牌。
CREATE TABLE IF NOT EXISTS portal_accounts (
    username TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS portal_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES portal_accounts(username),
    csrf_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS portal_session_expiry_idx ON portal_sessions(expires_at);
CREATE TABLE IF NOT EXISTS portal_login_attempts (
    scope TEXT PRIMARY KEY,
    window_start BIGINT NOT NULL,
    attempts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS portal_conversations (
    id UUID PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    active_turn_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS portal_conversation_owner_idx
    ON portal_conversations(owner_user_id, updated_at DESC, id);
CREATE TABLE IF NOT EXISTS portal_images (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES portal_conversations(id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png', 'image/jpeg')),
    content BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 6000),
    height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 6000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (octet_length(content) BETWEEN 1 AND 4194304)
);
CREATE INDEX IF NOT EXISTS portal_image_conversation_idx ON portal_images(conversation_id);
CREATE TABLE IF NOT EXISTS portal_turns (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES portal_conversations(id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    request_hash TEXT NOT NULL,
    message TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('mock', 'live')),
    image_ids UUID[] NOT NULL DEFAULT '{}',
    kind TEXT NOT NULL CHECK (kind IN ('agent', 'vision')),
    status TEXT NOT NULL,
    result JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (conversation_id, request_id)
);
CREATE INDEX IF NOT EXISTS portal_turn_conversation_idx ON portal_turns(conversation_id, created_at, id);
