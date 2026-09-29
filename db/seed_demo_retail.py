# -*- coding: utf-8 -*-
"""
seed_demo_retail.py — 自建零售演示库种子数据（可重置、可重复生成）
对应技术说明书里程碑 M2：覆盖 JOIN、退款、空值、不同时间口径等边界。

用法:
    py -3.13 db/seed_demo_retail.py            # 生成并写入 demo_retail
    py -3.13 db/seed_demo_retail.py --dry-run  # 仅打印统计
"""
import argparse
import random
from datetime import datetime, timedelta

import psycopg2

# ---------- 固定随机种子，保证可重复 ----------
RNG = random.Random(20260928)

REGIONS = [
    ("华东", "上海"), ("华北", "北京"), ("华南", "广州"),
    ("华中", "武汉"), ("西南", "成都"), ("东北", "沈阳"),
]

PRODUCTS = [
    # (名称, 类别, 品牌, 标价, 成本, 单位, 上市日期)
    ("智能变频空调 1.5匹", "家电", "凉风", 3299.00, 2400.00, "台", "2022-03-15"),
    ("对开门冰箱 450L",   "家电", "凉风", 4599.00, 3300.00, "台", "2021-11-02"),
    ("滚筒洗衣机 10kg",   "家电", "净旋", 2899.00, 2050.00, "台", "2022-06-20"),
    ("65英寸4K电视",      "家电", "视界", 3999.00, 2900.00, "台", "2023-01-10"),
    ("扫地机器人 X2",     "家电", "净旋", 1899.00, 1250.00, "台", "2023-04-18"),
    ("无线降噪耳机 Pro",  "数码", "声澜", 1299.00, 780.00,  "副", "2023-05-20"),
    ("真无线耳机 Air",    "数码", "声澜", 499.00,  280.00,  "副", "2022-09-01"),
    ("智能手表 S6",       "数码", "时迹", 1599.00, 950.00,  "块", "2023-08-15"),
    ("平板电脑 T11",      "数码", "光屿", 2499.00, 1700.00, "台", "2023-10-01"),
    ("便携蓝牙音箱",      "数码", "声澜", 299.00,  160.00,  "台", "2021-07-12"),
    ("机械键盘 87键",     "数码", "键坊", 399.00,  220.00,  "把", "2022-02-14"),
    ("人体工学椅",        "家居", "坐云", 899.00,  560.00,  "把", "2022-05-30"),
    ("乳胶枕",            "家居", "眠月", 219.00,  120.00,  "个", "2021-09-09"),
    ("空气炸锅 5L",       "家电", "膳美", 459.00,  290.00,  "台", "2023-03-22"),
    ("破壁机",            "家电", "膳美", 599.00,  380.00,  "台", "2022-12-08"),
    ("智能门锁",          "家居", "守门", 1099.00, 690.00,  "把", "2023-06-06"),
    ("混合坚果礼盒",      "食品", "果语", 89.00,   52.00,   "盒", "2023-11-11"),
    ("有机绿茶 250g",     "食品", "山野", 128.00,  75.00,   "罐", "2024-01-15"),
    ("冻干咖啡 40条",     "食品", "醒山", 79.00,   45.00,   "盒", "2024-03-01"),
    ("全麦面包 1kg",      "食品", "麦语", 35.00,   None,    "袋", "2025-01-01"),  # 成本 NULL: 新品未录入
]

SURNAMES = "王李张刘陈杨黄赵周吴徐孙马朱胡郭何高林郑谢罗宋唐韩冯于董萧程曹袁邓许傅沈曾彭吕"
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "涛",
         "明", "超", "秀英", "霞", "平", "刚", "桂英", "文轩", "雨桐", "子涵",
         "思远", "欣怡", "浩然", "梓萱", "一诺", "天佑"]

LEVELS = ["普通", "普通", "普通", "银卡", "银卡", "金卡", "钻石"]  # 加权
CITIES = {"华东": ["上海", "杭州", "南京", "苏州"], "华北": ["北京", "天津", "石家庄"],
          "华南": ["广州", "深圳", "厦门"], "华中": ["武汉", "长沙", "郑州"],
          "西南": ["成都", "重庆", "昆明"], "东北": ["沈阳", "大连", "长春"]}
STATUS_WEIGHTS = [("completed", 0.62), ("shipped", 0.10), ("paid", 0.08),
                  ("pending", 0.08), ("cancelled", 0.07), ("refunded", 0.05)]
CHANNELS = [("online", 0.5), ("app", 0.3), ("offline", 0.2)]

START = datetime(2023, 1, 1)
END = datetime(2025, 12, 31)
DAYS = (END - START).days


def wpick(pairs):
    r, acc = RNG.random(), 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = psycopg2.connect(dbname="demo_retail", user="postgres",
                            password="datamind_dev", host="localhost", port=5432)
    cur = conn.cursor()

    if args.dry_run:
        print("dry-run: regions=6 products=%d customers=500 orders≈3000" % len(PRODUCTS))
        return

    # 清空（保持自增从1开始，保证可重置）
    for t in ["refunds", "order_items", "orders", "customers", "products", "regions"]:
        cur.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")

    # regions
    cur.executemany("INSERT INTO regions(region_name, capital) VALUES (%s,%s)", REGIONS)
    cur.execute("SELECT region_id, region_name FROM regions")
    region_ids = cur.fetchall()

    # products（含 NULL 成本、部分缺 description）
    prod_rows = []
    for name, cat, brand, lp, cp, unit, ld in PRODUCTS:
        desc = f"{brand}出品的{cat}类产品{name}。" if RNG.random() > 0.15 else None
        prod_rows.append((name, cat, brand, lp, cp, unit, ld, desc))
    cur.executemany(
        """INSERT INTO products(product_name,category,brand,list_price,cost_price,unit,launch_date,description)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", prod_rows)
    cur.execute("SELECT product_id FROM products")
    product_ids = [r[0] for r in cur.fetchall()]

    # customers（约8%无地区 -> region_id NULL）
    cust_rows = []
    for i in range(1, 501):
        name = RNG.choice(SURNAMES) + RNG.choice(GIVEN) + ("" if RNG.random() > 0.3 else str(i % 10))
        region = RNG.choice(region_ids)[0] if RNG.random() > 0.08 else None
        city = None
        if region:
            rname = next(n for rid, n in region_ids if rid == region)
            city = RNG.choice(CITIES[rname])
        phone = "1{:09d}".format(RNG.randint(300000000, 999999999)) if RNG.random() > 0.05 else None
        reg_date = START + timedelta(days=RNG.randint(0, 400))
        cust_rows.append((name, phone, region, city, RNG.choice(LEVELS), reg_date))
    cur.executemany(
        """INSERT INTO customers(customer_name,phone,region_id,city,customer_level,registered_at)
           VALUES (%s,%s,%s,%s,%s,%s)""", cust_rows)
    cur.execute("SELECT customer_id FROM customers")
    customer_ids = [r[0] for r in cur.fetchall()]

    # orders + order_items + refunds
    # 走季节性销量: 每年递增 + 11/12 月高峰（供同比环比/高峰期类问题）
    n_orders = 3000
    order_rows, item_rows, refund_rows = [], [], []
    item_seq = 1
    for oid in range(1, n_orders + 1):
        # 日期倾向年底旺季
        while True:
            d = START + timedelta(days=RNG.randint(0, DAYS))
            boost = 2.2 if d.month in (11, 12) else (1.2 if d.month in (6, 7) else 1.0)
            if RNG.random() < boost / 2.2:
                break
        cust = RNG.choice(customer_ids)
        status = wpick(STATUS_WEIGHTS)
        channel = wpick(CHANNELS)
        created = d.replace(hour=RNG.randint(8, 23), minute=RNG.randint(0, 59))
        paid = created + timedelta(minutes=RNG.randint(1, 600)) if status not in ("pending", "cancelled") else None
        remark = RNG.choice(["加急", "礼品包装", None, None, None, "发票抬头变更"])
        order_rows.append((cust, status, channel, created, paid, remark))

        for _ in range(RNG.randint(1, 4)):
            pid = RNG.choice(product_ids)
            qty = RNG.randint(1, 3)
            cur.execute("SELECT list_price FROM products WHERE product_id=%s", (pid,))
            lp = float(cur.fetchone()[0])
            disc = RNG.choice([0, 0, 0.9, 0.85, 0.8, 0.75])
            unit_price = round(lp * (1 - disc * 0.2), 2)  # 少量让利
            item_rows.append((oid, pid, qty, unit_price, disc))
            item_seq += 1

    cur.executemany(
        """INSERT INTO orders(customer_id,order_status,channel,created_at,paid_at,remark)
           VALUES (%s,%s,%s,%s,%s,%s)""", order_rows)
    cur.executemany(
        """INSERT INTO order_items(order_id,product_id,quantity,unit_price,discount)
           VALUES (%s,%s,%s,%s,%s)""", item_rows)

    # 退款: 约 7% 明细产生部分退款
    n_items = len(item_rows)
    refund_ids = RNG.sample(range(1, n_items + 1), int(n_items * 0.07))
    for iid in refund_ids:
        cur.execute("SELECT quantity,unit_price,order_id FROM order_items WHERE order_item_id=%s", (iid,))
        qty, price, oid = cur.fetchone()
        amt = round(float(price) * float(qty) * RNG.uniform(0.2, 1.0), 2)
        cur.execute("SELECT paid_at FROM orders WHERE order_id=%s", (oid,))
        paid_at = cur.fetchone()[0]
        base = paid_at or START
        refund_rows.append((iid, amt,
                            RNG.choice(["质量问题", "七天无理由", "物流损坏", "尺寸不符"]),
                            base + timedelta(days=RNG.randint(1, 30))))
    cur.executemany(
        """INSERT INTO refunds(order_item_id,refund_amount,reason,refunded_at)
           VALUES (%s,%s,%s,%s)""", refund_rows)

    conn.commit()

    counts = {}
    for t in ["regions", "products", "customers", "orders", "order_items", "refunds"]:
        cur.execute(f"SELECT count(*) FROM {t}")
        counts[t] = cur.fetchone()[0]
    print("seeded:", counts)

    # 汇总口径示例（验证用）: 2024 年已完成订单销售额
    cur.execute("""
        SELECT round(sum(oi.quantity * oi.unit_price * (1 - oi.discount))::numeric, 2)
        FROM order_items oi JOIN orders o USING (order_id)
        WHERE o.order_status IN ('completed','shipped','refunded')
          AND o.created_at >= '2024-01-01' AND o.created_at < '2025-01-01'""")
    print("2024 sales_amount(口径校验参考):", cur.fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
