-- 商品类别和下单时承诺由可信业务数据提供，不从用户问题/PDF中推断。
CREATE TABLE order_return_policies (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    category TEXT NOT NULL CHECK (category IN ('STANDARD', 'CUSTOM_MADE', 'FRESH_PERISHABLE',
        'DIGITAL', 'NEWSPAPER_PERIODICAL', 'SEALED_HEALTH', 'VALUE_SENSITIVE', 'DISCLOSED_DISCOUNT', 'UNKNOWN')),
    product_condition TEXT NOT NULL CHECK (product_condition IN ('INTACT', 'REASONABLE_INSPECTION', 'NOT_INTACT', 'UNKNOWN')),
    exclusion_confirmed_at_purchase BOOLEAN,
    downloaded_or_unsealed BOOLEAN,
    activated BOOLEAN,
    merchant_no_reason_days INTEGER CHECK (merchant_no_reason_days BETWEEN 0 AND 365),
    policy_version TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE return_drafts ADD COLUMN policy_fingerprint TEXT;

-- 只新增明确标注的虚构样例；原有订单、申请和历史草稿不覆盖。
INSERT INTO orders(id, owner_user_id, status, product, delivered_at, amount_cents) VALUES
    ('O-7201', 'U-1001', 'delivered', '演示鲜活香蕉', CURRENT_DATE - 1, 1990),
    ('O-7202', 'U-1001', 'delivered', '演示定制刻字水杯', CURRENT_DATE - 1, 9900)
ON CONFLICT (id) DO NOTHING;

INSERT INTO order_return_policies(order_id, category, product_condition,
    exclusion_confirmed_at_purchase, downloaded_or_unsealed, activated, merchant_no_reason_days, policy_version)
SELECT id, CASE id WHEN 'O-7201' THEN 'FRESH_PERISHABLE' WHEN 'O-7202' THEN 'CUSTOM_MADE' ELSE 'STANDARD' END,
    'INTACT', id IN ('O-7201', 'O-7202'), false, false, 0, 'demo-v1'
FROM orders WHERE owner_user_id = 'U-1001' AND
    ((id = 'O-7101' AND product = '演示机械键盘') OR
     (id = 'O-7201' AND product = '演示鲜活香蕉') OR
     (id = 'O-7202' AND product = '演示定制刻字水杯'))
ON CONFLICT (order_id) DO NOTHING;

-- 老草稿没有政策指纹，未完成的确认会要求重新获取草稿；已成功申请仍返回原回执。
