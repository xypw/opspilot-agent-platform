-- 服务端退货草稿：保存用户实际确认过的订单快照和一小时截止时间。
CREATE TABLE IF NOT EXISTS return_drafts (
    order_id TEXT PRIMARY KEY,
    draft_id UUID NOT NULL UNIQUE,
    product TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    order_status TEXT NOT NULL,
    delivered_at DATE,
    eligibility_decision TEXT NOT NULL,
    eligibility_can_apply BOOLEAN NOT NULL,
    eligibility_reason_required BOOLEAN NOT NULL,
    days_since_delivery BIGINT,
    eligibility_reason TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > started_at),
    submitted_reason TEXT,
    submitted_reason_code TEXT,
    review_decision TEXT,
    review_reason TEXT,
    cancelled BOOLEAN NOT NULL DEFAULT FALSE
);

-- 正式申请与 draft_id 一对一；数据库约束防止重试或多实例重复创建。
CREATE TABLE IF NOT EXISTS return_applications (
    application_id UUID PRIMARY KEY,
    draft_id UUID NOT NULL UNIQUE
        REFERENCES return_drafts(draft_id) ON UPDATE CASCADE,
    order_id TEXT NOT NULL,
    product TEXT NOT NULL,
    refund_amount_cents INTEGER NOT NULL CHECK (refund_amount_cents >= 0),
    status TEXT NOT NULL CHECK (status IN ('SUBMITTED')),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS return_applications_order_id_idx
    ON return_applications(order_id);
