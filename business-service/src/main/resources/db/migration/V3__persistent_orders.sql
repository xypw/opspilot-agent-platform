-- 兼容空库和旧 init.sql；不猜测已有订单的归属、金额或签收时间。
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    product TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS owner_user_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_at DATE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS amount_cents INTEGER;
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN ('processing', 'shipped', 'delivered', 'cancelled'));
ALTER TABLE orders ADD CONSTRAINT orders_amount_nonnegative CHECK (amount_cents >= 0);
CREATE INDEX IF NOT EXISTS orders_owner_id_idx ON orders(owner_user_id, id);

-- 新命名空间的虚构演示订单只插入一次。重启不会刷新签收日或覆盖旧订单。
-- 旧 O-2001 等记录保留原样，缺失归属的历史记录默认不可访问，需显式核实回填。
INSERT INTO orders(id, owner_user_id, status, product, delivered_at, amount_cents)
VALUES
    ('O-7101', 'U-1001', 'delivered', '演示机械键盘', CURRENT_DATE - 1, 39900),
    ('O-7102', 'U-1002', 'processing', '演示无线鼠标', NULL, 12900),
    ('O-7103', 'U-1001', 'cancelled', '演示显示器', NULL, 159900)
ON CONFLICT (id) DO NOTHING;
