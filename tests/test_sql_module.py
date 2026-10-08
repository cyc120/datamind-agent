"""Security and execution regression checks for the structured question module."""
import unittest
from datetime import date

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
