-- Derived semantic index only. Source chat remains authoritative and immutable.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS portal_turn_memory (
    turn_id UUID NOT NULL REFERENCES portal_turns(id) ON DELETE CASCADE,
    part INTEGER NOT NULL CHECK (part>=0),
    embedding_model TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    embedding VECTOR(512) NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(turn_id,part)
);
CREATE INDEX IF NOT EXISTS portal_turn_unknown_idx
    ON portal_turns(conversation_id) WHERE status='RESULT_UNKNOWN';
