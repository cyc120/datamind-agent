-- ============================================================
-- 03_demo_retail.sql — 自建零售演示库（说明书 §11.2 / 里程碑 M2）
-- 可重置: 整库 drop 后由 init 重建；数据由 seed 脚本生成
-- ============================================================

CREATE DATABASE demo_retail;
\c demo_retail;

-- ---------- 大区 / 城市 ----------
CREATE TABLE regions (
    region_id   SERIAL PRIMARY KEY,
    region_name VARCHAR(32) NOT NULL UNIQUE,   -- 华东/华北/华南...
    capital     VARCHAR(32) NOT NULL
);

-- ---------- 产品 ----------
CREATE TABLE products (
    product_id   SERIAL PRIMARY KEY,
    product_name VARCHAR(128) NOT NULL,
    category     VARCHAR(64)  NOT NULL,        -- 家电/数码/家居/食品
    brand        VARCHAR(64),
    list_price   NUMERIC(10,2) NOT NULL,       -- 标价（元）
    cost_price   NUMERIC(10,2),                -- 成本（可 NULL：新品未定价成本）
    unit         VARCHAR(16) NOT NULL DEFAULT '件',
    launch_date  DATE,
    description  TEXT
);

-- ---------- 客户 ----------
CREATE TABLE customers (
    customer_id  SERIAL PRIMARY KEY,
    customer_name VARCHAR(64) NOT NULL,
    phone        VARCHAR(20),
    region_id    INT REFERENCES regions(region_id),   -- 可 NULL（未填地区）
    city         VARCHAR(32),
    customer_level VARCHAR(8) NOT NULL DEFAULT '普通', -- 普通/银卡/金卡/钻石
    registered_at DATE NOT NULL
);

-- ---------- 订单 ----------
CREATE TABLE orders (
    order_id     SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers(customer_id),
    order_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    -- pending/paid/shipped/completed/cancelled/refunded
    channel      VARCHAR(16) NOT NULL DEFAULT 'online', -- online/offline/app
    created_at   TIMESTAMPTZ NOT NULL,
    paid_at      TIMESTAMPTZ,
    remark       TEXT
);

-- ---------- 订单明细 ----------
CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10,2) NOT NULL,       -- 成交单价
    discount      NUMERIC(4,3) NOT NULL DEFAULT 0,  -- 折扣率 0~1（如 0.85 = 85折）
    -- 销售额口径: quantity * unit_price * (1 - discount)
    CONSTRAINT chk_discount CHECK (discount >= 0 AND discount <= 1)
);

-- ---------- 退款 ----------
CREATE TABLE refunds (
    refund_id     SERIAL PRIMARY KEY,
    order_item_id INT NOT NULL REFERENCES order_items(order_item_id),
    refund_amount NUMERIC(10,2) NOT NULL CHECK (refund_amount >= 0),
    reason        VARCHAR(64),
    refunded_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_orders_created ON orders(created_at);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);

-- ---------- 只读授权（问数账号） ----------
GRANT CONNECT ON DATABASE demo_retail TO readonly_user, datamind_app;
GRANT USAGE ON SCHEMA public TO readonly_user, datamind_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user, datamind_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_user, datamind_app;

COMMENT ON DATABASE demo_retail IS 'DataMind Agent 自建零售演示库（演示数据，非真实企业数据）';
