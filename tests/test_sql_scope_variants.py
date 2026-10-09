"""R01 变异测试：CTE 同名不得豁免真实表（尤其是 schema 限定的真实表）。

修复前 `guard_sql` 维护一个全局的 `cte_names` 名字集合：

- 表收集循环里 `if table.name in cte_names: ... continue`，于是
  `FROM public.customers` 会因为「名字等于某个 CTE 名」被当作 CTE 跳过，连
  `SCHEMA_NOT_ALLOWED` 都不再过；
- 列校验里 `if possible[0] in cte_names: continue`，于是该表的列检查被整体跳过，
  Catalog 标为 `restricted` 的 `customers.phone` 可以被读出来。

修复后按**作用域来源对象**区分 CTE 与真实表（`cte_reference_table_ids`），带 schema 的
表永远不会被同名 CTE 豁免。

本文件只断言「放行 / 具体错误码」，不查询、不打印、不保存任何受限列的取值。
"""
import unittest

from sqlglot import exp, parse_one

from datamind.catalog import Catalog
from datamind.sql_guard import (PolicyError, cte_reference_table_ids, guard_sql)


META = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"
# 审计给出的反例：CTE 名与 schema 限定的真实表同名，且读取受限列。
COUNTEREXAMPLE = ("WITH customers AS (SELECT order_id FROM orders) "
                  "SELECT c.phone FROM public.customers c LIMIT 1")


class _GuardCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(META, "demo_retail")

    def blocked(self, sql, reason):
        """断言被**该具体原因**拒绝：区分权限拒绝与语法/其它拒绝。"""
        with self.assertRaises(PolicyError) as cm:
            guard_sql(sql, self.catalog)
        self.assertEqual(cm.exception.reason, reason,
                         f"期望 {reason}，实际 {cm.exception.reason}：{sql}")

    def allowed(self, sql):
        try:
            return guard_sql(sql, self.catalog)
        except PolicyError as error:
            self.fail(f"合法查询被误拒：{error.reason} :: {sql}")


class CTESchemaShadowingTest(_GuardCase):
    """① 同名 CTE + 带 schema 的真实表：必须按真实表的权限检查。"""

    def test_audit_counterexample_is_rejected_for_the_restricted_column(self):
        self.blocked(COUNTEREXAMPLE, "RESTRICTED_COLUMN")

    def test_schema_qualified_table_is_still_allowed_when_only_safe_columns_are_read(self):
        # 同一形状但不读受限列：必须仍可用（修复不能退化为「见同名就拒」）。
        checked = self.allowed("WITH customers AS (SELECT order_id FROM orders) "
                               "SELECT c.customer_id, c.city FROM public.customers c LIMIT 1")
        self.assertIn("customers", checked.tables)
        self.assertIn("orders", checked.tables)

    def test_schema_qualified_name_cannot_be_shadowed_even_without_alias(self):
        self.blocked("WITH customers AS (SELECT order_id FROM orders) "
                     "SELECT phone FROM public.customers LIMIT 1", "RESTRICTED_COLUMN")


class SameNameUnqualifiedTableTest(_GuardCase):
    """② 同名 CTE + 不带 schema 的同名真实表。

    不带 schema 时 SQL 语义就是把该名字解析为 CTE（CTE 遮蔽真实表），因此这里要求：
    CTE 引用可以照常使用，但 CTE **函数体里**对同名真实表的列检查必须完整执行。
    """

    def test_real_table_inside_the_cte_body_is_still_column_checked(self):
        self.blocked("WITH customers AS (SELECT phone FROM customers) "
                     "SELECT count(*) FROM customers", "RESTRICTED_COLUMN")
        self.blocked("WITH customers AS (SELECT phone FROM customers) "
                     "SELECT c.phone FROM customers c", "RESTRICTED_COLUMN")

    def test_unqualified_reference_is_genuinely_the_cte_and_stays_usable(self):
        checked = self.allowed("WITH customers AS (SELECT customer_id, city FROM customers) "
                               "SELECT count(*) FROM customers")
        self.assertEqual(checked.tables, ["customers"])

    def test_unqualified_reference_projects_the_cte_not_the_restricted_column(self):
        # `customers` 已被 CTE 遮蔽，CTE 只输出 order_id；受限列 phone 不属于该 CTE。
        # 允许放行也不构成读取：base table 只应来自 CTE 函数体里的 orders。
        checked = self.allowed("WITH customers AS (SELECT order_id FROM orders) "
                              "SELECT count(*) FROM customers")
        self.assertEqual(checked.tables, ["orders"])


class NestedAndCorrelatedScopeTest(_GuardCase):
    """③ 嵌套 CTE（外层/内层同名） ④ 相关子查询里的同名表。"""

    def test_nested_same_name_cte_does_not_exempt_the_outer_real_table(self):
        self.blocked(
            "WITH customers AS (SELECT order_id FROM orders) "
            "SELECT c.phone FROM public.customers c WHERE EXISTS "
            "(WITH customers AS (SELECT customer_id FROM customers) "
            "SELECT customer_id FROM customers)",
            "RESTRICTED_COLUMN")

    def test_nested_same_name_cte_body_is_still_column_checked(self):
        self.blocked(
            "WITH customers AS (SELECT order_id FROM orders) "
            "SELECT count(*) FROM public.customers c WHERE EXISTS "
            "(WITH customers AS (SELECT phone FROM customers) "
            "SELECT customer_id FROM customers)",
            "RESTRICTED_COLUMN")

    def test_correlated_subquery_referencing_the_same_name_stays_blocked(self):
        self.blocked(
            "WITH customers AS (SELECT order_id FROM orders) "
            "SELECT c.phone FROM public.customers c WHERE c.customer_id IN "
            "(SELECT customer_id FROM customers)",
            "RESTRICTED_COLUMN")

    def test_subquery_restricted_column_stays_blocked(self):
        # 子查询里的同名 schema 限定表必须照常做列检查（此处不含外层别名引用，
        # 因为「相关子查询引用外层别名」在本 Guard 里本来就会以 UNKNOWN_TABLE_ALIAS
        # 失败关闭，那是更早的既有行为，不属于本次修复范围）。
        self.blocked(
            "WITH customers AS (SELECT order_id FROM orders) "
            "SELECT c.city FROM public.customers c WHERE c.customer_id IN "
            "(SELECT p.customer_id FROM public.customers p WHERE p.phone IS NOT NULL)",
            "RESTRICTED_COLUMN")
        self.blocked(
            "WITH customers AS (SELECT order_id FROM orders) "
            "SELECT count(*) FROM orders o WHERE o.order_id IN "
            "(SELECT p.phone FROM public.customers p)",
            "RESTRICTED_COLUMN")


class RestrictedColumnBaselineTest(_GuardCase):
    """⑤ CTE 名与真实表名不同但列受限：基线不能被修复破坏。"""

    def test_restricted_column_is_blocked_without_any_cte(self):
        self.blocked("SELECT c.phone FROM public.customers c LIMIT 1", "RESTRICTED_COLUMN")

    def test_restricted_column_is_blocked_with_an_unrelated_cte_name(self):
        self.blocked("WITH recent AS (SELECT order_id FROM orders) "
                     "SELECT c.phone FROM public.customers c LIMIT 1", "RESTRICTED_COLUMN")
        self.blocked("WITH recent AS (SELECT order_id FROM orders) "
                     "SELECT phone FROM customers LIMIT 1", "RESTRICTED_COLUMN")


class LegitimateUsageTest(_GuardCase):
    """⑥ 合法 CTE 仍放行 ⑦ 排序别名 / 表别名仍放行。"""

    def test_legitimate_ctes_are_still_allowed(self):
        self.allowed("WITH t AS (SELECT order_id, customer_id FROM orders) "
                     "SELECT t.customer_id FROM t LIMIT 5")
        self.allowed("WITH t AS (SELECT customer_id, city FROM customers) "
                     "SELECT customer_id FROM t LIMIT 5")
        self.allowed("WITH t AS (SELECT customer_id FROM public.customers) "
                     "SELECT t.customer_id FROM t LIMIT 5")
        self.allowed("WITH t AS (SELECT order_id FROM orders) SELECT count(*) FROM t")

    def test_chained_ctes_still_resolve(self):
        self.allowed("WITH a AS (SELECT customer_id FROM customers), "
                     "b AS (SELECT customer_id FROM a) SELECT b.customer_id FROM b LIMIT 5")

    def test_sort_aliases_and_table_aliases_still_work(self):
        self.allowed("SELECT customer_name AS name FROM customers ORDER BY name LIMIT 5")
        self.allowed("SELECT c.customer_name, c.city FROM customers c ORDER BY c.city LIMIT 5")
        self.allowed("SELECT city, count(*) FROM customers GROUP BY city ORDER BY 2 LIMIT 5")
        self.allowed("WITH t AS (SELECT customer_id, city FROM customers) "
                     "SELECT x.city FROM t x ORDER BY x.city LIMIT 5")

    def test_cte_join_is_still_refused_as_unverified(self):
        self.blocked(
            "WITH x AS (SELECT order_id FROM orders), y AS (SELECT product_id FROM products) "
            "SELECT count(*) FROM x JOIN y ON x.order_id=y.product_id",
            "DERIVED_JOIN_NOT_VERIFIED")


class DisallowedSchemaTest(_GuardCase):
    """⑧ schema 限定为未允许 schema 时必须拒，且不能被同名 CTE 掩盖。"""

    def test_disallowed_schema_is_rejected_without_cte(self):
        self.blocked("SELECT count(*) FROM pg_catalog.pg_user", "SCHEMA_NOT_ALLOWED")

    def test_disallowed_schema_is_rejected_even_with_same_name_cte(self):
        self.blocked("WITH customers AS (SELECT order_id FROM orders) "
                     "SELECT count(*) FROM pg_catalog.customers", "SCHEMA_NOT_ALLOWED")
        self.blocked("WITH customers AS (SELECT order_id FROM orders) "
                     "SELECT c.phone FROM pg_catalog.customers c", "SCHEMA_NOT_ALLOWED")


class ScopeClassificationTest(unittest.TestCase):
    """直接锁定修复机制本身，证明上面的用例不是「反正都会抛异常」的空转。

    `cte_reference_table_ids` 只把「本作用域内解析为 CTE 且无 schema 限定」的 `exp.Table`
    节点算作 CTE 引用。若把这条判定退回「按名字集合豁免」，本类与反例类会同时失败。
    """

    def ids_of(self, sql):
        tree = parse_one(sql, read="postgres")
        return cte_reference_table_ids(tree)

    def test_schema_qualified_real_table_is_not_a_cte_reference(self):
        tree = parse_one(COUNTEREXAMPLE, read="postgres")
        ids = cte_reference_table_ids(tree)
        schema_qualified = [table for table in tree.find_all(exp.Table)
                            if table.db == "public"]
        self.assertEqual(len(schema_qualified), 1)
        self.assertNotIn(id(schema_qualified[0]), ids,
                         "public.customers 带 schema，绝不能算作 CTE 引用")

    def test_genuine_cte_reference_is_classified_as_cte(self):
        tree = parse_one("WITH t AS (SELECT order_id FROM orders) SELECT count(*) FROM t",
                         read="postgres")
        ids = cte_reference_table_ids(tree)
        references = [table for table in tree.find_all(exp.Table) if table.name == "t"]
        self.assertEqual(len(references), 1)
        self.assertIn(id(references[0]), ids,
                      "真正的 CTE 引用必须被识别，否则合法 CTE 会被拒")

    def test_inner_same_name_cte_is_classified_per_scope(self):
        tree = parse_one(
            "WITH customers AS (SELECT order_id FROM orders) "
            "SELECT count(*) FROM public.customers c WHERE EXISTS "
            "(WITH customers AS (SELECT customer_id FROM customers) "
            "SELECT customer_id FROM customers)",
            read="postgres")
        ids = cte_reference_table_ids(tree)
        for table in tree.find_all(exp.Table):
            if table.db == "public":
                self.assertNotIn(id(table), ids, table.sql())
        inner = [table for table in tree.find_all(exp.Table)
                 if table.name == "customers" and not table.db]
        self.assertTrue(inner, "用例应包含不带 schema 的同名 CTE 引用")
        self.assertTrue(any(id(table) in ids for table in inner),
                        "内层同名 CTE 引用必须仍被识别为 CTE")


if __name__ == "__main__":
    unittest.main()
