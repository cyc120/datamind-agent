"""R02 变异测试：AST 里「存在一个正确的 SUM」不等于最终结果与业务过滤正确。

修复前 `_semantic_findings` 只做了三件弱事：

- `any(_canonical_metric_expression(node.this) == approved for node in tree.find_all(exp.Sum))`
  —— 只要任意位置存在匹配的 SUM 节点就通过；
- 时间检查是「期间值是否出现在 params 里」；
- 状态检查是 `"order_status" not in sql.lower()` 的子串匹配。

因此下列变体全部被误判为「通过(无 finding)」：正确 SUM 再乘 0、再加常量、原 WHERE 追加
`OR TRUE`、把正确 SUM 挪进不参与输出的子查询。

本文件用**注入 metric_registry + 注入 stub catalog** 的方式离线复现（不连库、不调模型），
只断言 finding 代码与验证结论，不执行任何 SQL。params 必须与 SQL 文本里的 `%s` 出现顺序
一致——这正是 `guard_sql` 渲染结果与 psycopg2 传参的契约。
"""
import unittest

from sqlglot import exp, parse_one

from datamind.catalog import JoinEdge
from datamind.sql_engine import (_canonical_metric_expression, _semantic_findings,
                                 _top_level_projection_is_metric, semantic_verification)


SALES_QUESTION = "2024年销售额是多少？"
COUNT_QUESTION = "2024年一共有多少笔订单？"

# business_metrics 中 demo_retail.sales_amount 的批准公式（与元数据库一致）。
INNER = "oi.quantity * oi.unit_price * (1 - oi.discount)"
FORMULA = f"sum({INNER})"
REAL_REGISTRY = [{"metric_id": "demo_retail.sales_amount", "name": "销售额",
                  "aliases": ["销售金额", "营收", "GMV", "成交额", "业绩"],
                  "expression_sql": FORMULA},
                 {"metric_id": "demo_retail.refund_amount", "name": "退款金额",
                  "aliases": ["退款额", "退货金额"],
                  "expression_sql": "sum(r.refund_amount)"}]


def registry(_source):
    return [dict(metric) for metric in REAL_REGISTRY]


class _StubCatalog:
    """最小目录替身：只需要 join 方向与列归属，用于离线检查连接粒度。"""

    def __init__(self):
        self.relations = [
            JoinEdge("rel-oi-orders", "order_items", ("order_id",), "orders", ("order_id",), 1.0, ""),
            JoinEdge("rel-orders-customers", "orders", ("customer_id",), "customers", ("customer_id",), 1.0, ""),
            JoinEdge("rel-refunds-oi", "refunds", ("order_item_id",), "order_items", ("order_item_id",), 1.0, ""),
        ]
        self.columns = {("order_items", "quantity"): {"sensitivity": "normal"},
                        ("order_items", "unit_price"): {"sensitivity": "normal"},
                        ("order_items", "discount"): {"sensitivity": "normal"},
                        ("orders", "order_id"): {"sensitivity": "normal"},
                        ("customers", "customer_id"): {"sensitivity": "normal"},
                        ("refunds", "order_item_id"): {"sensitivity": "normal"}}


STUB_CATALOG = _StubCatalog()

FROM_JOIN = "FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
STATUS = "o.order_status IN (%s, %s, %s)"
TIME = "o.created_at >= %s AND o.created_at < %s"
PARAMS = ["completed", "shipped", "refunded", "2024-01-01", "2025-01-01"]


def query(select_list=None, where=STATUS, extra=""):
    select_list = select_list if select_list is not None else f"SUM({INNER})"
    return f"SELECT {select_list} {FROM_JOIN}WHERE {where} AND {TIME}{extra}"


def old_style_would_pass(sql, params):
    """复刻修复前的弱判据，用来证明下面的用例不是空转。"""
    approved = _canonical_metric_expression(parse_one(FORMULA, read="postgres").this)
    tree = parse_one(sql, read="postgres")
    has_sum = any(_canonical_metric_expression(node.this) == approved
                  for node in tree.find_all(exp.Sum))
    statuses = {"completed", "shipped", "refunded"}.issubset(set(map(str, params)))
    time_ok = "2024-01-01" in params and "2025-01-01" in params
    return has_sum and "order_status" in sql.lower() and statuses and time_ok


class MetricSemanticsMatrixTest(unittest.TestCase):
    def findings(self, sql, params=PARAMS, catalog=None):
        return _semantic_findings(SALES_QUESTION, sql, list(params), "demo_retail",
                                  metric_registry=registry, catalog=catalog)

    # --- 正向：正确业务查询不得退化为一律拒答 -------------------------------
    def test_correct_query_is_clean_and_verified(self):
        self.assertEqual(self.findings(query()), [])
        verdict = semantic_verification("demo_retail", SALES_QUESTION, [])
        self.assertTrue(verdict["semantically_verified"])
        self.assertEqual(verdict["verification_contract"], "retail_metric_ast_contract")

    def test_whitelisted_wrappers_aliases_and_grouping_still_pass(self):
        for select_list in (f"ROUND(SUM({INNER}), 2)",
                            f"COALESCE(SUM({INNER}), 0)",
                            f"SUM({INNER}) AS total",
                            f"SUM({INNER})::numeric"):
            self.assertEqual(self.findings(query(select_list)), [], select_list)
        self.assertEqual(self.findings(query(f"o.customer_id, SUM({INNER})") + " GROUP BY o.customer_id"), [])
        self.assertEqual(self.findings(query("SUM(quantity * unit_price * (1 - discount))")), [])

    # --- 反例矩阵：四种变体 + 谓词变体都不能获得 verified --------------------
    def test_sum_times_zero_is_rejected(self):
        sql = query(f"SUM({INNER}) * 0")
        self.assertEqual(self.findings(sql), ["METRIC_EXPRESSION_MISMATCH"])
        self.assertTrue(old_style_would_pass(sql, PARAMS), "旧判据本应放行，测试才有意义")

    def test_sum_plus_constant_is_rejected(self):
        sql = query(f"SUM({INNER}) + 1")
        self.assertEqual(self.findings(sql), ["METRIC_EXPRESSION_MISMATCH"])
        self.assertTrue(old_style_would_pass(sql, PARAMS))

    def test_scalar_projection_is_rejected(self):
        self.assertEqual(self.findings(query("1")), ["METRIC_EXPRESSION_MISMATCH"])

    def test_correct_column_cannot_mask_a_tampered_sibling(self):
        sql = query(f"SUM({INNER}) * 0 AS tampered, SUM({INNER}) AS total")
        self.assertEqual(self.findings(sql), ["METRIC_EXPRESSION_MISMATCH"])

    def test_sum_hidden_in_an_unrelated_subquery_is_rejected(self):
        sql = query("1", extra=f" AND EXISTS (SELECT SUM({INNER}) FROM order_items oi2)")
        self.assertEqual(self.findings(sql), ["METRIC_EXPRESSION_MISMATCH"])
        self.assertTrue(old_style_would_pass(sql, PARAMS), "旧判据本应放行，测试才有意义")

    def test_or_true_is_rejected(self):
        sql = query() + " OR TRUE"
        self.assertEqual(self.findings(sql), ["METRIC_QUERY_HAS_OR"])
        self.assertTrue(old_style_would_pass(sql, PARAMS))

    def test_or_one_equals_one_is_rejected(self):
        self.assertEqual(self.findings(query() + " OR 1 = 1"), ["METRIC_QUERY_HAS_OR"])

    def test_time_condition_moved_into_a_subquery_is_rejected(self):
        sql = (f"SELECT SUM({INNER}) {FROM_JOIN}WHERE {STATUS} AND EXISTS "
               "(SELECT 1 FROM orders o2 WHERE o2.created_at >= %s AND o2.created_at < %s)")
        self.assertEqual(self.findings(sql), ["TIME_FILTER_MISMATCH"])

    def test_time_values_merely_appearing_in_params_are_rejected(self):
        # 期间值确实出现在 params 里（旧的 membership 检查会放行），但真实谓词用的是别的年份。
        sql = query() + " AND o.customer_id::text <> %s"
        params = ["completed", "shipped", "refunded", "2025-01-01", "2026-01-01", "2024-01-01"]
        self.assertEqual(self.findings(sql, params), ["TIME_FILTER_MISMATCH"])

    def test_status_condition_moved_into_a_subquery_is_rejected(self):
        sql = (f"SELECT SUM({INNER}) {FROM_JOIN}WHERE {TIME} AND EXISTS "
               "(SELECT 1 FROM orders o2 WHERE o2.order_status IN (%s, %s, %s))")
        params = ["2024-01-01", "2025-01-01", "completed", "shipped", "refunded"]
        self.assertEqual(self.findings(sql, params), ["METRIC_STATUS_FILTER_MISSING"])

    def test_status_or_true_is_rejected(self):
        self.assertEqual(self.findings(query() + " OR o.order_status IS NOT NULL"),
                         ["METRIC_QUERY_HAS_OR"])

    def test_narrow_status_filter_is_rejected(self):
        sql = f"SELECT SUM({INNER}) {FROM_JOIN}WHERE o.order_status = %s AND {TIME}"
        self.assertEqual(self.findings(sql, ["completed", "2024-01-01", "2025-01-01"]),
                         ["METRIC_STATUS_FILTER_MISSING"])

    def test_extra_status_filter_is_rejected(self):
        sql = f"SELECT SUM({INNER}) {FROM_JOIN}WHERE o.order_status IN (%s, %s, %s, %s) AND {TIME}"
        params = ["completed", "shipped", "refunded", "pending", "2024-01-01", "2025-01-01"]
        self.assertEqual(self.findings(sql, params), ["METRIC_STATUS_FILTER_MISSING"])

    def test_missing_status_filter_is_rejected(self):
        sql = f"SELECT SUM({INNER}) {FROM_JOIN}WHERE {TIME}"
        self.assertEqual(self.findings(sql, ["2024-01-01", "2025-01-01"]),
                         ["METRIC_STATUS_FILTER_MISSING"])

    # --- 参数顺序 / 数量不一致时失败关闭 -------------------------------------
    def test_swapped_time_params_fail_closed(self):
        swapped = ["completed", "shipped", "refunded", "2025-01-01", "2024-01-01"]
        self.assertEqual(self.findings(query(), swapped), ["TIME_FILTER_MISMATCH"])

    def test_param_count_mismatch_fails_closed(self):
        self.assertEqual(self.findings(query(), PARAMS[:-1]), ["TIME_FILTER_MISMATCH"])

    # --- 连接粒度 -----------------------------------------------------------
    def test_fanout_join_is_flagged(self):
        sql = (f"SELECT SUM({INNER}) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
               f"JOIN refunds r ON r.order_item_id = oi.order_item_id WHERE {STATUS} AND {TIME}")
        self.assertEqual(self.findings(sql, catalog=STUB_CATALOG), ["METRIC_JOIN_GRAIN_RISK"])

    def test_duplicated_fact_table_is_flagged(self):
        sql = (f"SELECT SUM({INNER}) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id "
               f"JOIN refunds r ON r.order_item_id = oi.order_item_id "
               f"JOIN order_items oi2 ON r.order_item_id = oi2.order_item_id WHERE {STATUS} AND {TIME}")
        self.assertEqual(self.findings(sql, catalog=STUB_CATALOG), ["METRIC_JOIN_GRAIN_RISK"])

    def test_dimension_joins_are_not_flagged(self):
        sql = (f"SELECT c.customer_id, SUM({INNER}) FROM order_items oi "
               f"JOIN orders o ON oi.order_id = o.order_id "
               f"JOIN customers c ON o.customer_id = c.customer_id "
               f"WHERE {STATUS} AND {TIME} GROUP BY c.customer_id")
        self.assertEqual(self.findings(sql, catalog=STUB_CATALOG), [])

    # --- 诚实降级 -----------------------------------------------------------
    def test_executed_is_separated_from_semantically_verified(self):
        self.assertEqual(semantic_verification("demo_retail", SALES_QUESTION, []),
                         {"executed": True, "semantically_verified": True,
                          "verification_contract": "retail_metric_ast_contract",
                          "verification_reasons": []})
        weak = semantic_verification("demo_retail", COUNT_QUESTION, [])
        self.assertTrue(weak["executed"])
        self.assertFalse(weak["semantically_verified"],
                         "只校验了时间参数与字段时不得声称语义已验证")
        self.assertEqual(weak["verification_contract"], "time_scope_only")
        self.assertTrue(any("SEMANTIC_CONTRACT_WEAK" in reason
                            for reason in weak["verification_reasons"]))
        failed = semantic_verification("demo_retail", SALES_QUESTION,
                                       ["METRIC_EXPRESSION_MISMATCH"])
        self.assertTrue(failed["executed"])
        self.assertFalse(failed["semantically_verified"])

    def test_behavior_of_the_variants_through_the_verdict(self):
        for sql in (query(f"SUM({INNER}) * 0"), query("1"), query() + " OR TRUE"):
            findings = self.findings(sql)
            self.assertTrue(findings, sql)
            self.assertFalse(semantic_verification("demo_retail", SALES_QUESTION, findings)
                             ["semantically_verified"], sql)


class MechanismNotVacuousTest(unittest.TestCase):
    """直接锁定新机制：这些变体的「外层输出」不是批准指标。

    旧判据（`old_style_would_pass`）对它们成立，说明矩阵用例命中的正是本次新增的检查，
    而不是碰巧被别的规则拒绝。
    """

    def approved_canonical(self):
        return _canonical_metric_expression(parse_one(FORMULA, read="postgres").this)

    def test_top_level_projection_check_rejects_tampering(self):
        approved = self.approved_canonical()
        for select_list in (f"SUM({INNER}) * 0", f"SUM({INNER}) + 1", "1"):
            tree = parse_one(query(select_list), read="postgres")
            self.assertFalse(_top_level_projection_is_metric(tree, approved), select_list)

    def test_top_level_projection_check_accepts_the_approved_shapes(self):
        approved = self.approved_canonical()
        for select_list in (f"SUM({INNER})", f"ROUND(SUM({INNER}), 2)", f"SUM({INNER}) AS total"):
            tree = parse_one(query(select_list), read="postgres")
            self.assertTrue(_top_level_projection_is_metric(tree, approved), select_list)


if __name__ == "__main__":
    unittest.main()
