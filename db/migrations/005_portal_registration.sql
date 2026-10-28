-- Non-destructive account metadata; NULL retains legacy configured identities until bootstrap.
ALTER TABLE portal_accounts ADD COLUMN IF NOT EXISTS roles TEXT[];
ALTER TABLE portal_accounts ADD COLUMN IF NOT EXISTS demo_order_id TEXT;
CREATE TABLE IF NOT EXISTS portal_admin_events (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    target TEXT NOT NULL,
    action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
