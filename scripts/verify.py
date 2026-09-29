# -*- coding: utf-8 -*-
"""
verify.py — 基础数据库端到端验证
覆盖: 服务健康 / 结构化库 / 只读隔离 / 元数据底座 / 知识库与向量索引 / 检索质量 / 评测集
用法: py -3.13 scripts/verify.py
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
    for db, expect in [("demo_retail", {"orders": 3000, "customers": 500}),
                       ("chinook", {"track": 3503, "invoice": 412})]:
        conn = psycopg2.connect(dbname=db, **PG_R)
        cur = conn.cursor()
        for t, n in expect.items():
            cur.execute(f"SELECT count(*) FROM {t}")
            check(f"{db}.{t} 行数", cur.fetchone()[0] == n, f"(期望 {n})")
        conn.close()

    print("== 3. 只读隔离 ==")
    conn = psycopg2.connect(dbname="demo_retail", **PG_R)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM orders WHERE order_id = 1")
        check("只读账号禁止 DELETE", False)
    except psycopg2.errors.InsufficientPrivilege:
        check("只读账号禁止 DELETE", True)
    except Exception as e:
        check("只读账号禁止 DELETE", False, str(e)[:60])
    try:
        cur.execute("CREATE TABLE hack_x(i int)")
        check("只读账号禁止 CREATE", False)
    except psycopg2.errors.InsufficientPrivilege:
        check("只读账号禁止 CREATE", True)
    except Exception as e:
        check("只读账号禁止 CREATE", False, str(e)[:60])
    conn.close()

    print("== 4. 元数据底座 ==")
    conn = psycopg2.connect(dbname="datamind_meta", **PG_R)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM schema_snapshots")
    check("SchemaSnapshot", cur.fetchone()[0] == 2)
    cur.execute("SELECT count(*) FROM schema_columns")
    check("schema_columns ≥ 90", cur.fetchone()[0] >= 90)
    cur.execute("SELECT count(*) FROM business_metrics")
    check("业务指标 ≥ 8", cur.fetchone()[0] >= 8)
    cur.execute("SELECT count(*) FROM entity_aliases")
    check("实体别名表", cur.fetchone()[0] >= 14)
    cur.execute("SELECT count(*) FROM document_assets WHERE status='indexed'")
    n_docs = cur.fetchone()[0]
    check("已索引文档 = 26", n_docs == 26, f"(实际 {n_docs})")
    cur.execute("SELECT count(*) FROM chunks")
    n_chunks = cur.fetchone()[0]
    check("切片 ≥ 100", n_chunks >= 100, f"(实际 {n_chunks})")
    cur.execute("SELECT count(*) FROM evaluation_cases")
    check("评测用例 = 30", cur.fetchone()[0] == 30)
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
