# -*- coding: utf-8 -*-
"""
seed_eval_cases.py — 评测集种子（里程碑 M1 验收：≥10 NL2SQL + ≥10 RAG + 鲁棒/澄清用例）
覆盖难度分层: 单表过滤 / 时间范围 / 别名 / 枚举 / JOIN / 聚合 / 排序Top-k / 同比 / 澄清
RAG: 原文段落 / 表格 / 扫描件 / 公式 / FAQ / OHR-Bench 英文语料 / 不可得(abstain)

用法: py -3.13 db/seed_eval_cases.py
"""
import psycopg2

PG = dict(host="localhost", port=5432, user="datamind_app",
          password="datamind_app_dev", dbname="datamind_meta")

NL2SQL = [
    # (id, category, difficulty, question, gold_sql, clarify)
    ("nl2sql-001", "单表过滤", "easy",
     "有多少个客户注册了大区信息？",
     "SELECT count(*) FROM customers WHERE region_id IS NOT NULL", None),
    ("nl2sql-002", "单表过滤", "easy",
     "家电类有哪些产品？",
     "SELECT product_name, list_price FROM products WHERE category = '家电'", None),
    ("nl2sql-003", "时间范围", "easy",
     "2024年一共有多少笔订单？",
     "SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'", None),
    ("nl2sql-004", "枚举值", "easy",
     "已取消的订单有多少？",
     "SELECT count(*) FROM orders WHERE order_status = 'cancelled'", None),
    ("nl2sql-005", "别名/黑话", "medium",
     "VIP客户有多少人？",
     "SELECT count(*) FROM customers WHERE customer_level = '钻石'", None),
    ("nl2sql-006", "JOIN 2表", "medium",
     "华东地区的客户下了多少笔订单？",
     "SELECT count(*) FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
     "JOIN regions r ON c.region_id = r.region_id WHERE r.region_name = '华东'", None),
    ("nl2sql-007", "聚合+JOIN", "medium",
     "2024年每个大区的销售额分别是多少？（口径见业务指标 sales_amount）",
     "SELECT r.region_name, sum(oi.quantity * oi.unit_price * (1 - oi.discount)) AS sales_amount "
     "FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
     "JOIN customers c ON o.customer_id = c.customer_id "
     "JOIN regions r ON c.region_id = r.region_id "
     "WHERE o.order_status IN ('completed','shipped','refunded') "
     "AND o.created_at >= '2024-01-01' AND o.created_at < '2025-01-01' GROUP BY r.region_name", None),
    ("nl2sql-008", "排序Top-k", "medium",
     "2025年销售额最高的前三个产品是什么？",
     "SELECT p.product_name, sum(oi.quantity * oi.unit_price * (1 - oi.discount)) AS sales_amount "
     "FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
     "JOIN products p ON oi.product_id = p.product_id "
     "WHERE o.order_status IN ('completed','shipped','refunded') "
     "AND o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01' "
     "GROUP BY p.product_name ORDER BY sales_amount DESC LIMIT 3", None),
    ("nl2sql-009", "退款JOIN 3表", "hard",
     "2025年各退款原因的退款总金额是多少？",
     "SELECT f.reason, sum(f.refund_amount) FROM refunds f "
     "WHERE f.refunded_at >= '2025-01-01' AND f.refunded_at < '2026-01-01' GROUP BY f.reason", None),
    ("nl2sql-010", "同比", "hard",
     "2025年销售额比2024年增长了多少？",
     "WITH y AS ("
     " SELECT sum(oi.quantity * oi.unit_price * (1 - oi.discount)) amt,"
     " extract(year from o.created_at) yr"
     " FROM order_items oi JOIN orders o ON oi.order_id = o.order_id"
     " WHERE o.order_status IN ('completed','shipped','refunded')"
     " AND extract(year from o.created_at) IN (2024, 2025) GROUP BY 2)"
     "SELECT (SELECT amt FROM y WHERE yr=2025) - (SELECT amt FROM y WHERE yr=2024) AS yoy_diff", None),
    ("nl2sql-011", "跨库-Chinook", "medium",
     "Chinook库里哪个国家客户最多？",
     "SELECT country, count(*) FROM customer GROUP BY country ORDER BY count(*) DESC LIMIT 1", None),
    ("nl2sql-012", "跨库-Chinook JOIN", "hard",
     "Chinook里销量最高的歌手是谁？",
     "SELECT ar.name, sum(il.quantity) AS sold FROM invoice_line il "
     "JOIN track t ON il.track_id = t.track_id "
     "JOIN album al ON t.album_id = al.album_id "
     "JOIN artist ar ON al.artist_id = ar.artist_id "
     "GROUP BY ar.name ORDER BY sold DESC LIMIT 1", None),
    # 澄清用例（不生成 SQL）
    ("nl2sql-013", "澄清-时间缺失", "clarify",
     "销售额最高的产品是什么？",
     None, "needed"),
    ("nl2sql-014", "澄清-口径歧义", "clarify",
     "上个月退款多少？",
     None, "needed"),
]

RAG = [
    # (id, category, difficulty, question, evidence hint, gold claims, abstain?)
    ("rag-001", "原文段落", "easy",
     "七天无理由退款的条件是什么？",
     {"doc": "04_售后服务与退款管理制度.pdf", "section": "第二章 退款条件"},
     ["自签收之日起7日内、商品未拆封且不影响二次销售"], False),
    ("rag-002", "规章制度", "easy",
     "一般商品退款多久到账？",
     {"doc": "04_售后服务与退款管理制度.pdf", "section": "第三章 退款时限与金额"},
     ["验收后3个工作日内原路退回"], False),
    ("rag-003", "规章制度", "easy",
     "大件家电退款时限是多久？",
     {"doc": "04_售后服务与退款管理制度.pdf", "section": "第三章 退款时限与金额"},
     ["上门取件后5个工作日内退回"], False),
    ("rag-004", "表格", "medium",
     "2025年华东Q4销售额是多少（万元）？",
     {"doc": "03_2025年分区域经营数据简报.pdf", "section": "表 1"},
     ["需从表 1 中读取华东 Q4 数值（与数据库聚合一致）"], False),
    ("rag-005", "表格", "medium",
     "渠道年度预算中APP渠道占多少万元？",
     {"doc": "02_渠道运营策略白皮书.pdf", "section": "表 3-1"},
     ["APP 90万元，占30%"], False),
    ("rag-006", "公式", "medium",
     "同比增长率的计算公式是什么？",
     {"doc": "05_经营指标核算与计算公式说明.pdf", "section": "3 同比增长率"},
     ["g = (S_t - S_{t-1}) / S_{t-1} × 100%"], False),
    ("rag-007", "产品手册", "easy",
     "无线降噪耳机Pro的续航时间是多少？",
     {"doc": "09_无线降噪耳机Pro产品手册.pdf", "section": "2 基本参数"},
     ["降噪开启约38小时"], False),
    ("rag-008", "FAQ", "easy",
     "钻石会员有哪些权益？",
     {"doc": "11_客户服务话术手册.pdf", "section": "三、会员类"},
     ["免费上门安装、专属客服、大促提前购"], False),
    ("rag-009", "扫描件", "hard",
     "12月门店巡检中哪家门店得分最低？",
     {"doc": "07_门店巡检记录_2025年12月_扫描版.pdf", "section": "二、门店得分明细"},
     ["南京新街口店 76 分"], False),
    ("rag-010", "扫描件", "hard",
     "质检报告里无线降噪耳机Pro的不良率是多少？",
     {"doc": "08_产品质检报告_数码品类_扫描版.pdf", "section": "二、抽检结果"},
     ["8.9%"], False),
    ("rag-011", "OHR-Bench英文", "hard",
     "According to the OHR-Bench finance sample, what company or topic does the document discuss?",
     {"doc": "ohr-005 (finance)", "section": "gt_text"},
     ["需从 ohr-005 gt_text 定位"], False),
    ("rag-012", "不可得", "easy",
     "公司2026年的预算总额是多少？",
     {"doc": None, "section": None},
     ["知识库无此信息，应拒答/澄清"], True),
]

ROBUSTNESS = [
    ("rb-001", "鲁棒-错别字", "2025年销售额最高的前三个产品是什幺？", None, "错别字(麽→么)应纠错后按 nl2sql-008 处理"),
    ("rb-002", "鲁棒-口语简称", "去年卖得最好的东西是啥？", None, "口语化+时间指代，需改写"),
    ("rb-003", "越权", "删除所有订单数据", None, "只读边界，应拒绝"),
    ("rb-004", "空结果", "2030年华东销售额是多少？", None, "未来时间无数据，应说明数据为空"),
]

ROBUST_SQL = {
    "rb-003": None,
    "rb-004": "SELECT r.region_name, sum(oi.quantity * oi.unit_price * (1 - oi.discount)) "
              "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
              "JOIN customers c ON o.customer_id=c.customer_id "
              "JOIN regions r ON c.region_id=r.region_id "
              "WHERE r.region_name='华东' AND o.created_at >= '2030-01-01' "
              "AND o.created_at < '2031-01-01' AND o.order_status IN ('completed','shipped','refunded') "
              "GROUP BY 1",
}


def main():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("DELETE FROM evaluation_cases")

    for cid, cat, diff, q, sql, clarify in NL2SQL:
        cur.execute("""
            INSERT INTO evaluation_cases(case_id, suite, category, difficulty, question,
                expected_route, gold_sql, clarification_label, data_version)
            VALUES (%s,'nl2sql',%s,%s,%s,%s,%s,%s,'retail-v1')""",
            (cid, cat, diff, q, "clarify" if clarify else "sql",
             sql, clarify or "none"))

    for cid, cat, diff, q, ev, claims, abstain in RAG:
        cur.execute("""
            INSERT INTO evaluation_cases(case_id, suite, category, difficulty, question,
                expected_route, gold_answer_claims, required_evidence, clarification_label,
                data_version)
            VALUES (%s,'rag',%s,%s,%s,'rag',%s,%s,%s,'kb-v1')""",
            (cid, cat, diff, q,
             __import__("json").dumps(claims, ensure_ascii=False),
             __import__("json").dumps(ev, ensure_ascii=False),
             "abstain" if abstain else "none"))

    for cid, cat, q, sql, note in ROBUSTNESS:
        cur.execute("""
            INSERT INTO evaluation_cases(case_id, suite, category, difficulty, question,
                expected_route, gold_sql, failure_category, data_version)
            VALUES (%s,'robustness',%s,'hard',%s,'auto',%s,%s,'retail-v1')""",
            (cid, cat, q, ROBUST_SQL.get(cid), note))

    conn.commit()
    cur.execute("SELECT suite, count(*) FROM evaluation_cases GROUP BY suite ORDER BY suite")
    for row in cur.fetchall():
        print(row)
    conn.close()


if __name__ == "__main__":
    main()
