"""Security and execution regression checks for the structured question module."""
import unittest
from datetime import date

from unittest.mock import patch

from datamind import sql_engine
from datamind.catalog import Catalog
from datamind.sql_engine import ask, time_periods
from datamind.sql_guard import PolicyError, guard_sql


META = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"


class SQLModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(META, "demo_retail")

    def blocked(self, sql, reason):
        with self.assertRaises(PolicyError) as cm:
            guard_sql(sql, self.catalog)
        self.assertEqual(cm.exception.reason, reason)

    def test_mutations_and_multiple_statements(self):
        self.blocked("DELETE FROM orders", "SELECT_ONLY")
        self.blocked("SELECT count(*) FROM orders; DELETE FROM orders", "SELECT_ONLY")
        self.blocked("WITH changed AS (DELETE FROM orders RETURNING order_id) SELECT count(*) FROM changed", "MUTATION_OR_COMMAND")

    def test_schema_and_sensitive_columns(self):
        self.blocked("SELECT * FROM customers", "SELECT_STAR")
        self.blocked("SELECT phone FROM customers", "RESTRICTED_COLUMN")
        self.blocked("SELECT count(*) FROM pg_catalog.pg_user", "SCHEMA_NOT_ALLOWED")
        self.blocked("SELECT count(*) FROM unknown", "TABLE_NOT_ALLOWED")

    def test_unapproved_join(self):
        self.blocked("SELECT count(*) FROM orders o JOIN products p ON o.order_id=p.product_id", "UNAPPROVED_JOIN")
        self.blocked("SELECT count(*) FROM refunds r JOIN orders o ON r.order_item_id=o.order_id", "UNAPPROVED_JOIN")
        self.blocked("SELECT count(*) FROM orders CROSS JOIN products", "CARTESIAN_JOIN")
        self.blocked("SELECT count(*) FROM orders JOIN customers USING (customer_id)", "JOIN_USING_NOT_VERIFIED")
        self.blocked("SELECT count(*) FROM orders OFFSET 999999999", "INVALID_OFFSET")
        self.blocked("SELECT count(*) FROM orders o JOIN customers c ON o.customer_id=c.customer_id OR TRUE",
                     "AMBIGUOUS_JOIN_CONDITION")
        self.blocked("WITH x AS (SELECT order_id FROM orders), y AS (SELECT product_id FROM products) "
                     "SELECT count(*) FROM x JOIN y ON x.order_id=y.product_id", "DERIVED_JOIN_NOT_VERIFIED")

    def test_binding_respects_sql_order(self):
        checked = guard_sql("SELECT count(*) FROM orders WHERE order_status IN ('completed','shipped') "
                            "AND created_at >= '2024-01-01'", self.catalog)
        self.assertEqual(checked.params, ["completed", "shipped", "2024-01-01"])
        self.assertEqual(checked.sql.count("%s"), 3)
        self.assertTrue(checked.sql.endswith("LIMIT 100"))

    def test_approved_join_and_readonly_execution(self):
        sql = "SELECT count(*) FROM orders o JOIN customers c ON o.customer_id=c.customer_id"
        result = ask("订单关联客户后有多少条？", generator=lambda *_: {"sql": sql})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows"][0][0], 3000)

    def test_clarification_precedes_generation(self):
        def unavailable(*_):
            raise AssertionError("generator should not run")
        result = ask("销售额最高的产品是什么？", generator=unavailable)
        self.assertEqual(result["status"], "clarification_required")
        result = ask("删除所有订单数据", generator=unavailable)
        self.assertEqual(result["status"], "clarification_required")

    def test_metric_definition_gate(self):
        bad = "SELECT sum(oi.quantity * oi.unit_price) FROM order_items oi JOIN orders o ON oi.order_id=o.order_id"
        result = ask("2024年销售额是多少？", generator=lambda *_: {"sql": bad})
        self.assertEqual(result["error_code"], "REPAIR_BUDGET_EXHAUSTED")
        self.assertEqual(len(result["attempts"]), 3)

    def test_time_scope_must_match_question(self):
        wrong_year = ("SELECT count(*) FROM orders WHERE created_at >= '2025-01-01' "
                      "AND created_at < '2026-01-01'")
        result = ask("2024年一共有多少笔订单？", generator=lambda *_: {"sql": wrong_year})
        self.assertEqual(result["error_code"], "REPAIR_BUDGET_EXHAUSTED")
        self.assertEqual(result["attempts"][0]["error_code"], "TIME_FILTER_MISMATCH")
        correct = ("SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' "
                   "AND created_at < '2025-01-01'")
        result = ask("2024年一共有多少笔订单？", generator=lambda *_: {"sql": correct})
        self.assertEqual(result["status"], "completed")

    def test_case_branch_and_join_literals_are_bound(self):
        """CASE 分支与 JOIN ON 中的比较字面量必须绑定，否则时间口径校验会误判。"""
        sql = ("SELECT CASE WHEN o.created_at >= DATE '2024-01-01' AND o.created_at < DATE '2025-01-01' "
               "THEN 2024 ELSE 2025 END AS y, count(*) FROM orders o "
               "JOIN customers c ON o.customer_id = c.customer_id AND c.region_id = 3 "
               "WHERE o.order_status = 'completed' GROUP BY 1")
        checked = guard_sql(sql, self.catalog)
        for value in ("2024-01-01", "2025-01-01", "completed", 3):
            self.assertIn(value, checked.params)
        self.assertNotIn("2024-01-01", checked.sql)
        self.assertEqual(checked.sql.count("%s"), len(checked.params))

    def test_structural_literals_stay_inline(self):
        """LIMIT / OFFSET / GROUP BY 序号 / ORDER BY 序号不能被参数化。"""
        checked = guard_sql("SELECT product_id, count(*) FROM order_items GROUP BY 1 "
                            "ORDER BY 2 DESC LIMIT 5", self.catalog)
        self.assertEqual(checked.params, [])
        self.assertIn("GROUP BY 1", checked.sql)
        self.assertIn("ORDER BY 2 DESC", checked.sql)
        self.assertIn("LIMIT 5", checked.sql)

    def test_year_over_year_case_scope_passes_validation(self):
        periods = time_periods("2025年销售额比2024年增长了多少？")
        sql = ("WITH yearly AS (SELECT CASE WHEN created_at >= DATE '2024-01-01' AND created_at < DATE '2025-01-01' "
               "THEN 2024 WHEN created_at >= DATE '2025-01-01' AND created_at < DATE '2026-01-01' THEN 2025 END AS y "
               "FROM orders) SELECT count(*) FROM yearly")
        checked = guard_sql(sql, self.catalog)
        for period in periods:
            self.assertTrue(period["from"] in checked.params or period["year"] in checked.params)

    def test_policy_block_allows_one_rewrite(self):
        drafts = iter(["SELECT * FROM orders",
                       "SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'"])
        result = ask("2024年一共有多少笔订单？", generator=lambda *_: {"sql": next(drafts)})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"][0]["error_code"], "SELECT_STAR")
        self.assertEqual(result["rows"], [[997]])

    def test_write_attempt_is_hard_stop(self):
        calls = []

        def generator(*_):
            calls.append(1)
            return {"sql": "DELETE FROM orders"}

        result = ask("2024年一共有多少笔订单？", generator=generator)
        self.assertEqual(result["error_code"], "SQL_POLICY_BLOCKED")
        self.assertEqual(len(calls), 1, "写库草稿应直接终止，不进入重写循环")

    def test_policy_budget_exhausted_after_three_drafts(self):
        calls = []

        def generator(*_):
            calls.append(1)
            return {"sql": "SELECT * FROM orders"}

        result = ask("2024年一共有多少笔订单？", generator=generator)
        self.assertEqual(result["error_code"], "SQL_POLICY_BLOCKED")
        self.assertEqual(len(calls), 3)
        self.assertEqual([a["error_code"] for a in result["attempts"]], ["SELECT_STAR"] * 3)

    def test_relative_time_normalization(self):
        today = date(2026, 9, 30)
        self.assertEqual(time_periods("去年销售额", today)[0]["from"], "2025-01-01")
        self.assertEqual(time_periods("上个月退款金额", today)[0]["from"], "2026-08-01")
        self.assertEqual(time_periods("本季度订单数", today)[0]["to"], "2026-10-01")


if __name__ == "__main__":
    unittest.main()


class ColumnScopeTest(unittest.TestCase):
    """F01: column permission must be resolved per query scope, not by a global alias map.

    The two bypasses below were reachable because a projection alias exempted its own
    column and an inner CTE alias could shadow an outer table alias.
    """

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(META, "demo_retail")

    def blocked(self, sql, reason):
        with self.assertRaises(PolicyError) as cm:
            guard_sql(sql, self.catalog)
        self.assertEqual(cm.exception.reason, reason)

    def allowed(self, sql):
        try:
            return guard_sql(sql, self.catalog)
        except PolicyError as error:  # surfaced with the reason, not a bare assertion
            self.fail(f"正常查询被误拒：{error.reason} :: {sql}")

    def test_projection_alias_cannot_exempt_its_own_column(self):
        # `c` is the table alias, so this selects the whole row; the alias `c` must not
        # exempt it from the column check.
        self.blocked("SELECT c AS c FROM customers c LIMIT 1", "COLUMN_NOT_ALLOWED")

    def test_inner_cte_alias_cannot_shadow_an_outer_table_alias(self):
        self.blocked("SELECT c.phone FROM customers c WHERE EXISTS (WITH c AS "
                     "(SELECT customer_id FROM customers) SELECT customer_id FROM c)",
                     "RESTRICTED_COLUMN")

    def test_restricted_columns_stay_blocked_in_every_shape(self):
        self.blocked("SELECT customers.phone FROM customers LIMIT 1", "RESTRICTED_COLUMN")
        self.blocked("SELECT x.phone FROM (SELECT phone FROM customers) x LIMIT 1", "RESTRICTED_COLUMN")
        self.blocked("SELECT c.* FROM customers c LIMIT 1", "SELECT_STAR")

    def test_sort_clause_aliases_are_not_false_positives(self):
        self.allowed("SELECT customer_name AS name FROM customers ORDER BY name LIMIT 5")
        self.allowed("SELECT city, count(*) FROM customers GROUP BY city ORDER BY 2 LIMIT 5")
        self.allowed("SELECT c.customer_name, c.city FROM customers c ORDER BY c.city LIMIT 5")

    def test_legitimate_cte_references_still_pass(self):
        self.allowed("WITH c AS (SELECT customer_id, city FROM customers) SELECT customer_id FROM c LIMIT 5")
        self.allowed("WITH c AS (SELECT customer_id, city FROM customers) SELECT c.city FROM c LIMIT 5")

    def test_a_projection_alias_is_not_a_licence_to_select_the_alias_name(self):
        # `name` exists only as an output alias, never as a base column.
        self.blocked("SELECT name FROM customers LIMIT 1", "COLUMN_NOT_ALLOWED")


SALES_QUESTION = "2024年销售额是多少？"
SALES_WHERE = ("FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
               "WHERE o.order_status IN ('completed','shipped','refunded') "
               "AND o.created_at >= '2024-01-01' AND o.created_at < '2025-01-01'")
# business_metrics 中 demo_retail.sales_amount 的批准公式。
APPROVED_SALES_SQL = "SELECT sum(oi.quantity * oi.unit_price * (1 - oi.discount)) " + SALES_WHERE


class MetricExpressionRegistryTest(unittest.TestCase):
    """F02: 指标校验必须按 business_metrics 的批准公式做结构比对，且失败关闭。

    旧实现只检查 SUM 里是否出现 quantity/unit_price/discount 三个列名，
    因此 `SUM(quantity + unit_price + discount)` 这种运算符错误的表达式会被放行。
    """

    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(META, "demo_retail")

    def findings(self, sql, question=SALES_QUESTION, **kwargs):
        from datamind.sql_engine import _semantic_findings
        checked = guard_sql(sql, self.catalog)
        return _semantic_findings(question, checked.sql, checked.params, "demo_retail", **kwargs)

    def test_wrong_operator_is_rejected(self):
        findings = self.findings("SELECT sum(oi.quantity + oi.unit_price + oi.discount) " + SALES_WHERE)
        self.assertIn("METRIC_EXPRESSION_MISMATCH", findings)

    def test_ask_rejects_wrong_operator_expression(self):
        bad = "SELECT sum(oi.quantity + oi.unit_price + oi.discount) " + SALES_WHERE
        result = ask(SALES_QUESTION, generator=lambda *_: {"sql": bad})
        self.assertEqual(result["error_code"], "REPAIR_BUDGET_EXHAUSTED")
        self.assertEqual(result["attempts"][0]["error_code"], "METRIC_EXPRESSION_MISMATCH")

    def test_approved_formula_passes_metric_check(self):
        findings = self.findings(APPROVED_SALES_SQL)
        self.assertNotIn("METRIC_EXPRESSION_MISMATCH", findings)
        self.assertNotIn("METRIC_EXPRESSION_NOT_VERIFIABLE", findings)
        self.assertEqual(findings, [])

    def test_table_qualifiers_are_ignored(self):
        qualified = self.findings(APPROVED_SALES_SQL)
        unqualified = self.findings("SELECT sum(quantity * unit_price * (1 - discount)) " + SALES_WHERE)
        self.assertEqual(unqualified, [])
        self.assertEqual(unqualified, qualified, "oi.quantity 与 quantity 必须等价")

    def test_commutative_multiplication_is_accepted(self):
        reordered = self.findings(
            "SELECT sum(oi.unit_price * (1 - oi.discount) * oi.quantity) " + SALES_WHERE)
        self.assertEqual(reordered, [], "乘法交换律应被低成本地接受")

    def test_missing_discount_is_still_rejected(self):
        findings = self.findings("SELECT sum(oi.quantity * oi.unit_price) " + SALES_WHERE)
        self.assertIn("METRIC_EXPRESSION_MISMATCH", findings)

    def test_registry_unavailable_fails_closed(self):
        def broken(_source):
            raise RuntimeError("registry down")

        findings = self.findings(APPROVED_SALES_SQL, metric_registry=broken)
        self.assertIn("METRIC_EXPRESSION_NOT_VERIFIABLE", findings)

    def test_registry_without_the_metric_fails_closed(self):
        findings = self.findings(APPROVED_SALES_SQL, metric_registry=lambda _source: [])
        self.assertIn("METRIC_EXPRESSION_NOT_VERIFIABLE", findings)

    def test_compound_metric_that_references_other_metrics_is_not_silently_passed(self):
        # 净销售额公式 `sales_amount - refund_amount` 引用其他指标，无法按列级结构比对，
        # 必须失败关闭，而不是假装已强制。
        findings = self.findings(APPROVED_SALES_SQL, question="2024年净销售额是多少？")
        self.assertIn("METRIC_EXPRESSION_NOT_VERIFIABLE", findings)

    def test_status_filter_behavior_is_preserved(self):
        without_status = ("SELECT sum(oi.quantity * oi.unit_price * (1 - oi.discount)) "
                          "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
                          "WHERE o.created_at >= '2024-01-01' AND o.created_at < '2025-01-01'")
        self.assertIn("METRIC_STATUS_FILTER_MISSING", self.findings(without_status))

    def test_time_findings_are_preserved(self):
        from datamind.sql_engine import _semantic_findings
        status = ["completed", "shipped", "refunded"]
        wrong_year = ("SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) FROM order_items oi "
                      "WHERE oi.order_id IN (SELECT order_id FROM orders WHERE created_at >= %s AND created_at < %s)")
        self.assertEqual(
            _semantic_findings(SALES_QUESTION, wrong_year,
                               status + ["2025-01-01", "2026-01-01"], "demo_retail"),
            ["TIME_FILTER_MISMATCH"])
        self.assertEqual(
            _semantic_findings(SALES_QUESTION, wrong_year, status, "demo_retail"),
            ["TIME_FILTER_MISMATCH"])
        # 销售额必须按 created_at 过滤；同样的时间参数落在别的字段上时必须报 TIME_FIELD_MISMATCH。
        self.assertEqual(
            _semantic_findings(SALES_QUESTION, "SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount)) "
                               "FROM order_items oi", status + ["2024-01-01", "2025-01-01"], "demo_retail"),
            ["TIME_FIELD_MISMATCH"])


class VerifiedJudgmentTest(unittest.TestCase):
    """F02: 「结果非空」不等于 verified，任何指标/口径类 finding 都必须让它为假。"""

    def test_definition_findings_block_verification(self):
        from datamind.sql_engine import verification_passed
        for findings in (["METRIC_EXPRESSION_MISMATCH"], ["METRIC_EXPRESSION_NOT_VERIFIABLE"],
                         ["METRIC_STATUS_FILTER_MISSING"], ["TIME_FILTER_MISMATCH"],
                         ["EMPTY_RESULT"], ["EMPTY_RESULT", "METRIC_EXPRESSION_MISMATCH"]):
            self.assertFalse(verification_passed(findings), findings)

    def test_clean_and_row_limit_results_stay_verified(self):
        from datamind.sql_engine import verification_passed
        self.assertTrue(verification_passed([]))
        self.assertTrue(verification_passed(["ROW_LIMIT_REACHED"]))

    def test_agent_uses_the_shared_verdict(self):
        import datamind.agent as agent_module
        from datamind.sql_engine import verification_passed
        self.assertIs(agent_module.verification_passed, verification_passed)


class AdvisoryMetricFindingTests(unittest.TestCase):
    """F02: 'we verified it is wrong' blocks; 'we could not verify it' only lowers the verdict.

    Refusing to answer a legitimate composite metric (net sales references two other
    metrics) would replace one defect with another, so it is surfaced instead of blocked.
    """

    def generator(self, calls):
        def generate(question, catalog, feedback):
            calls.append(feedback)
            return {'sql': 'SELECT count(*) FROM customers LIMIT 5', 'interpretation': 'count'}
        return generate

    def test_an_unverifiable_expression_is_recorded_and_not_blocking(self):
        calls = []
        with patch.object(sql_engine, '_semantic_findings',
                          return_value=['METRIC_EXPRESSION_NOT_VERIFIABLE']):
            result = ask('销售额是多少', 'demo_retail', generator=self.generator(calls))
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(calls), 1, '提示类 finding 不应触发重写')
        self.assertIn('METRIC_EXPRESSION_NOT_VERIFIABLE', result['validator_findings'])
        self.assertFalse(sql_engine.verification_passed(result['validator_findings']))

    def test_a_verified_mismatch_still_blocks_after_the_rewrite_budget(self):
        calls = []
        with patch.object(sql_engine, '_semantic_findings',
                          return_value=['METRIC_EXPRESSION_MISMATCH']):
            result = ask('销售额是多少', 'demo_retail', generator=self.generator(calls))
        self.assertNotEqual(result['status'], 'completed')
        self.assertEqual(len(calls), 3, '口径错误应触发全部重写机会')

    def test_advisory_and_blocking_findings_are_declared_explicitly(self):
        self.assertIn('METRIC_EXPRESSION_NOT_VERIFIABLE', sql_engine.ADVISORY_FINDINGS)
        self.assertNotIn('METRIC_EXPRESSION_MISMATCH', sql_engine.ADVISORY_FINDINGS)
