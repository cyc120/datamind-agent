# -*- coding: utf-8 -*-
"""
verify.py — 基础数据库端到端验证
覆盖: 服务健康 / 结构化库 / 只读隔离 / 元数据底座 / 知识库与向量索引 / 检索质量 / 评测集

隔离与可移植性保证（审计 F11）:
  1. 验证者不得污染被测对象：脚本自身只做 SELECT；唯一的"写入探针"
     （只读账号越权 DELETE / CREATE）在显式事务 + SAVEPOINT 内执行，
     并在结束时统一 conn.rollback()，任何副作用都会被丢弃。
  2. 期望值不写死（旧实现固定 2 个数据源 / 26 份文档，换环境即误报）：
     全部期望由 derive_expectations() 从库中动态推导，
     断言的是与规模无关的自洽不变量，而非某个环境的绝对计数。

用法: .venv/bin/python scripts/verify.py
"""
import json
import sys

import psycopg2

PG_A = dict(host="localhost", port=5432, user="datamind_app", password="datamind_app_dev")
PG_R = dict(host="localhost", port=5432, user="readonly_user", password="readonly_dev")

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {detail}")


def probe_write_blocked(conn, name, sql):
    """在可回滚事务内探测写权限：无论探针是否被拒，都不留下任何副作用。

    只读账号正常会抛 InsufficientPrivilege；若权限配置错误导致语句"成功"，
    该写入仍处于未提交事务中，会随 SAVEPOINT 回滚与最终 rollback 一起丢弃。
    """
    cur = conn.cursor()
    cur.execute("SAVEPOINT probe_write")
    blocked, detail = False, ""
    try:
        cur.execute(sql)
        detail = "(未按预期被拒绝，已回滚)"
    except psycopg2.errors.InsufficientPrivilege:
        blocked = True
    except Exception as e:  # 连接/语法等其它异常：同样视为未通过
        detail = str(e)[:60]
    try:
        cur.execute("ROLLBACK TO SAVEPOINT probe_write")
    except Exception:
        conn.rollback()
    check(name, blocked, detail)


def verify_readonly_isolation(connect=psycopg2.connect):
    """只读隔离检查：写入探针全部在可回滚事务内执行，结束统一 rollback。

    参数 connect 可注入（测试用假连接），返回所用连接对象以便断言。
    """
    conn = connect(dbname="demo_retail", **PG_R)
    conn.autocommit = False          # 关闭隐式提交，探针写入必须可回滚
    try:
        probe_write_blocked(conn, "只读账号禁止 DELETE", "DELETE FROM orders WHERE order_id = 1")
        probe_write_blocked(conn, "只读账号禁止 CREATE", "CREATE TABLE hack_x(i int)")
    finally:
        conn.rollback()              # 丢弃探针可能产生的一切写入
        conn.close()
    return conn


def derive_expectations(cur):
    """从元数据库动态推导验证期望，避免任何环境相关的硬编码计数。"""
    def scalar(sql, args=None):
        cur.execute(sql, args or ())
        return cur.fetchone()[0]

    exp = {
        "n_sources": scalar("SELECT count(*) FROM data_sources"),
        "n_snapshot_sources": scalar("SELECT count(DISTINCT source_id) FROM schema_snapshots"),
        "n_tables": scalar("SELECT count(*) FROM schema_tables"),
        "n_columns": scalar("SELECT count(*) FROM schema_columns"),
        "n_metrics": scalar("SELECT count(*) FROM business_metrics"),
        "n_aliases": scalar("SELECT count(*) FROM entity_aliases"),
        "n_docs_indexed": scalar("SELECT count(*) FROM document_assets WHERE status='indexed'"),
        "n_docs_with_chunks": scalar("SELECT count(DISTINCT document_id) FROM chunks"),
        "n_chunks": scalar("SELECT count(*) FROM chunks"),
        "n_eval_cases": scalar("SELECT count(*) FROM evaluation_cases"),
        "n_eval_suites": scalar("SELECT count(DISTINCT suite) FROM evaluation_cases"),
    }
    return exp


def main():
    import urllib.request

    print("== 1. 服务健康 ==")
    try:
        qd = json.loads(urllib.request.urlopen("http://localhost:6333/collections/datamind_knowledge", timeout=5).read())
        check("Qdrant 集合存在", qd["result"]["status"] == "green",
              f"points={qd['result'].get('points_count')}")
    except Exception as e:
        check("Qdrant 集合存在", False, str(e)[:80])
    try:
        conn = psycopg2.connect(dbname="datamind_meta", **PG_R)
        check("PostgreSQL 元数据库可连", True)
    except Exception as e:
        check("PostgreSQL 元数据库可连", False, str(e)[:80])
        print(f"\n结果: {PASS} pass, {len(FAIL)} fail")
        sys.exit(1)

    print("== 2. 结构化库 ==")
    # 不写死行数（换环境/重播种即失配），改为校验库内自洽的结构不变量。
    demo_invariants = [
        ("demo_retail.orders 非空", "SELECT count(*) FROM orders"),
        ("demo_retail 每张订单都有明细",
         "SELECT (SELECT count(*) FROM orders) = (SELECT count(DISTINCT order_id) FROM order_items)"),
        ("demo_retail 买家数不超过客户数",
         "SELECT (SELECT count(DISTINCT customer_id) FROM orders) <= (SELECT count(*) FROM customers)"),
    ]
    chinook_invariants = [
        ("chinook.track 非空", "SELECT count(*) FROM track"),
        ("chinook.invoice 非空", "SELECT count(*) FROM invoice"),
        ("chinook 发票均有明细行",
         "SELECT NOT EXISTS (SELECT 1 FROM invoice i"
         " WHERE NOT EXISTS (SELECT 1 FROM invoice_line il WHERE il.invoice_id = i.invoice_id))"),
    ]
    for db, invariants in [("demo_retail", demo_invariants), ("chinook", chinook_invariants)]:
        conn = psycopg2.connect(dbname=db, **PG_R)
        cur = conn.cursor()
        for name, sql in invariants:
            cur.execute(sql)
            val = cur.fetchone()[0]
            if name.endswith("非空"):
                check(name, val > 0, f"(实际 {val})")
            else:
                check(name, bool(val))
        conn.close()

    print("== 3. 只读隔离 ==")
    # 写入探针全部在可回滚事务内执行（审计 F11：验证者不得污染被测对象）。
    verify_readonly_isolation()

    print("== 4. 元数据底座 ==")
    conn = psycopg2.connect(dbname="datamind_meta", **PG_R)
    cur = conn.cursor()
    exp = derive_expectations(cur)
    print(f"  (动态推导: {exp})")
    check("SchemaSnapshot 覆盖全部数据源",
          exp["n_sources"] >= 1 and exp["n_snapshot_sources"] == exp["n_sources"],
          f"(数据源 {exp['n_sources']}, 有快照 {exp['n_snapshot_sources']})")
    check("schema_columns 覆盖 schema_tables",
          exp["n_tables"] >= 1 and exp["n_columns"] >= exp["n_tables"],
          f"(表 {exp['n_tables']}, 列 {exp['n_columns']})")
    check("业务指标非空", exp["n_metrics"] >= 1, f"(实际 {exp['n_metrics']})")
    check("实体别名表非空", exp["n_aliases"] >= 1, f"(实际 {exp['n_aliases']})")
    check("已索引文档非空", exp["n_docs_indexed"] >= 1, f"(实际 {exp['n_docs_indexed']})")
    check("切片覆盖不超过已索引文档",
          0 < exp["n_docs_with_chunks"] <= exp["n_docs_indexed"],
          f"(已索引 {exp['n_docs_indexed']}, 有切片 {exp['n_docs_with_chunks']})")
    check("切片数与文档数自洽",
          exp["n_chunks"] >= exp["n_docs_with_chunks"] >= 1,
          f"(切片 {exp['n_chunks']})")
    check("评测用例非空", exp["n_eval_cases"] >= 1, f"(实际 {exp['n_eval_cases']})")
    check("评测套件非空", exp["n_eval_suites"] >= 1, f"(套件 {exp['n_eval_suites']})")
    conn.close()

    print("== 5. 向量检索冒烟 ==")
    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient
    model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
    qc = QdrantClient(url="http://localhost:6333")
    cases = [
        ("退货多久能到账", "11_客户服务话术手册"),
        ("销售额的计算公式", "05_经营指标核算与计算公式说明"),
        ("2025年哪个大区卖得最好", "03_2025年分区域经营数据简报"),
        ("耳机的保修期", "09_无线降噪耳机Pro产品手册"),
    ]
    for q, expect_doc in cases:
        vec = list(model.embed([q]))[0]
        hits = qc.query_points("datamind_knowledge", query=list(vec), limit=1).points
        conn = psycopg2.connect(dbname="datamind_meta", **PG_R)
        cur = conn.cursor()
        cur.execute("SELECT title FROM document_assets WHERE document_id=%s",
                    (hits[0].payload["document_id"],))
        title = cur.fetchone()[0]
        conn.close()
        check(f"Top-1 命中: {q}", expect_doc in title, f"→ {title[:28]} score={hits[0].score:.3f}")

    print(f"\n结果: {PASS} pass, {len(FAIL)} fail")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
