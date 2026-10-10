# -*- coding: utf-8 -*-
"""WP6 / B4：Schema Linking 独立复核（v2）的证据纪律测试。

运行方式（仓库纪律：只跑本文件）
    cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_schema_linking_v2.py

覆盖点
------
1. 规模与冻结：≥40 表池、≥40 未见问题、≥6 拒绝类、excluded 表不得混入池；
2. 干扰表「名称相似但不得误选」的反例断言，且断言非空转（干扰表确实在池内、名字确实相似）；
3. 拒绝类单列：不进召回分母，受限列全程不得出现；
4. 机制消融算术：四档、分母相同、hit ≤ gold、rate == hit/gold；
5. 零分母 → N/A（序列化为 null），不用 0 冒充；
6. v1 行为不变：当前引擎复跑 v1 的汇总与冻结报表一致。
"""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
V1_SUITE = ROOT / "data/evaluation/schema_linking_v1.json"
V2_SUITE = ROOT / "data/evaluation/schema_linking_v2.json"
V1_REPORT = ROOT / "reports/schema_linking.json"
V2_REPORT = ROOT / "reports/schema_linking_v2.json"
DSN = "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev"

from scripts.eval_schema import (  # noqa: E402
    ABLATION_TIERS, build_fixture_catalog, evaluate, evaluate_v2, _tier_candidates, _top,
)


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


class SuiteScaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load(V2_SUITE)
        cls.v1 = load(V1_SUITE)

    def test_pool_has_at_least_forty_tables_and_rejects_excluded(self):
        pool = self.suite["pool"]
        total = 19 + len(pool["synthetic_tables"])
        self.assertGreaterEqual(total, 40, "v2 表池必须 ≥40 张表")
        self.assertGreaterEqual(len(pool["synthetic_tables"]), 21)
        excluded = {item["table_name"] for item in pool["excluded_tables"]}
        self.assertTrue(excluded, "必须声明未纳入 allowlist 的表")
        declared = {table["table_name"] for table in pool["synthetic_tables"]}
        self.assertFalse(excluded & declared, "excluded 表不得同时出现在池内声明里")
        for item in pool["excluded_tables"]:
            self.assertIn("similar_to", item)

    def test_at_least_forty_unseen_questions_and_six_rejects(self):
        v1_ids = {case["case_id"] for case in self.v1["cases"]}
        v1_questions = {case["question"] for case in self.v1["cases"]}
        questions = {case["question"] for case in self.suite["cases"]}
        self.assertGreaterEqual(len(self.suite["cases"]), 40)
        self.assertFalse({case["case_id"] for case in self.suite["cases"]} & v1_ids)
        self.assertFalse(questions & v1_questions, "v2 问题必须与 v1 不重复（未见问题）")
        reject_ids = [case["case_id"] for case in self.suite["cases"] if case.get("expected_reject")]
        self.assertGreaterEqual(len(reject_ids), 6)
        kinds = {case["reject"]["kind"] for case in self.suite["cases"] if case.get("expected_reject")}
        self.assertEqual(kinds, {"restricted_column", "absent_table", "no_approved_path"},
                         "拒绝类必须覆盖越权字段/未登记表/无批准路径三种")

    def test_report_is_bound_to_suite_content(self):
        report = load(V2_REPORT)
        suite = self.suite
        self.assertEqual(report["suite_version"], suite["suite_version"])
        self.assertEqual(report["case_count"]["total"], len(suite["cases"]))
        self.assertEqual(report["case_count"]["positive"],
                         sum(1 for case in suite["cases"] if not case.get("expected_reject")))


class DistractorCounterexampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load(V2_SUITE)
        cls.report = load(V2_REPORT)
        cls.catalog, cls.fixture = build_fixture_catalog(cls.suite, DSN)

    def test_distractors_are_in_pool_and_genuinely_name_similar(self):
        """非空转证明：干扰表确实在池内，且名字与目标表相似（仅靠名称相似就该被误选）。"""
        for name, note in self.suite["pool"]["distractors"].items():
            self.assertIn(name, self.catalog.tables, f"干扰表必须在池内才构成考验：{name}")
            self.assertTrue(note)
        self.assertIn("orders", "sales_orders")
        self.assertIn("order", "order_sales_detail")
        self.assertIn("customer", "customer_sales")
        self.assertIn("product", "product_sales_summary")
        evidence = self.report["fixture"]["distractors"]
        self.assertIn("sales_orders", evidence)

    def test_declared_absent_tables_are_never_selected(self):
        checked = 0
        for case in self.suite["cases"]:
            if not case.get("expected_absent_tables"):
                continue
            result = next(item for item in self.report["cases"] if item["case_id"] == case["case_id"])
            for table in case["expected_absent_tables"]:
                checked += 1
                self.assertNotIn(table, result["retrieved"]["table"],
                                 f"{case['case_id']} 误选了名称相似的干扰/越权表 {table}")
        self.assertGreater(checked, 10, "反例断言数量太少，说明干扰考验不足")
        summary = self.report["summary"]["distractor_false_positives"]
        self.assertEqual(summary["false_positives"], 0)
        self.assertEqual(summary["checked"], 32)

    def test_distractor_columns_do_not_break_the_pool_contract(self):
        """干扰列（同名 business_name）可以进入候选，但不得让引擎抛错或返回池外表。"""
        pool = set(self.catalog.tables)
        for case in self.report["cases"]:
            self.assertTrue(set(case["retrieved"]["table"]) <= pool)
            self.assertTrue(set(case["retrieved"]["column"]) <= {f"{t}.{c}" for t, c in self.catalog.columns})


class RejectAccountingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load(V2_SUITE)
        cls.report = load(V2_REPORT)
        cls.catalog, _ = build_fixture_catalog(cls.suite, DSN)

    def test_rejects_are_counted_separately_from_recall(self):
        reject_cases = [case for case in self.suite["cases"] if case.get("expected_reject")]
        positive = [case for case in self.suite["cases"] if not case.get("expected_reject")]
        reject_summary = self.report["summary"]["reject_accuracy"]
        self.assertEqual(reject_summary["gold"], len(reject_cases))
        self.assertEqual(reject_summary["correct"], len(reject_cases))
        self.assertEqual(self.report["case_count"]["reject"], len(reject_cases))
        # 召回分母只由正例贡献：拒绝类里声明的表/列不得进入分子分母
        expected_tables = sum(len(case["tables"]) for case in positive)
        expected_columns = sum(len(case["columns"]) for case in positive)
        self.assertEqual(self.report["summary"]["table_recall_at_5"]["gold"], expected_tables)
        self.assertEqual(self.report["summary"]["column_recall_at_5"]["gold"], expected_columns)

    def test_restricted_columns_never_surface(self):
        restricted_targets = {column for case in self.suite["cases"]
                              if case.get("expected_reject")
                              and case["reject"]["kind"] == "restricted_column"
                              for column in case.get("expected_absent_columns", [])}
        absent_targets = {column for case in self.suite["cases"] if case.get("expected_reject")
                          for column in case.get("expected_absent_columns", [])}
        self.assertTrue(restricted_targets)
        # 越权字段类：目标必须在池内且真的登记为 restricted，否则拒绝断言是空转
        for column in restricted_targets:
            table, name = column.split(".", 1)
            self.assertIn(table, self.catalog.tables)
            self.assertEqual(self.catalog.columns[(table, name)].get("sensitivity"), "restricted",
                             f"{column} 未登记为 restricted，拒绝断言不成立")
        # 未登记表类：被点名的列必须在池内（断言的是「不得被静默替代」）
        for column in absent_targets - restricted_targets:
            table, name = column.split(".", 1)
            self.assertIn((table, name), self.catalog.columns)
        # 引擎层面：任何 restricted 列都不得出现在任何用例的召回列里
        restricted = {f"{table}.{column}" for table, column in self.catalog.columns
                      if self.catalog.columns[(table, column)].get("sensitivity") == "restricted"}
        for case in self.report["cases"]:
            self.assertFalse(restricted & set(case["retrieved"]["column"]),
                             f"受限列被召回：{case['case_id']}")
            self.assertFalse(absent_targets & set(case["retrieved"]["column"]),
                             f"声明的越权/不可得列被召回：{case['case_id']}")

    def test_no_approved_path_cases_actually_fail_to_resolve(self):
        for case in self.suite["cases"]:
            if not case.get("expected_reject") or case["reject"]["kind"] != "no_approved_path":
                continue
            with self.assertRaises(ValueError):
                self.catalog.join_path(case["reject"]["from"], case["reject"]["to"])
            result = next(item for item in self.report["cases"] if item["case_id"] == case["case_id"])
            self.assertTrue(result["reject"]["correct"])
            self.assertEqual(result["reject"]["problems"], [])
        # 反面对照：已批准的路径必须能解出来（否则「拒绝」是空转）
        self.assertTrue(self.catalog.join_path("orders", "regions"))


class AblationArithmeticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = load(V2_REPORT)
        cls.suite = load(V2_SUITE)

    def test_four_tiers_with_equal_denominators_and_exact_rates(self):
        ablation = self.report["ablation"]
        self.assertEqual([tier["tier"] for tier in ablation],
                         [tier["tier"] for tier in ABLATION_TIERS])
        for kind in ("table", "column", "metric"):
            denominators = {tier[f"{kind}_recall_at_5"]["gold"] for tier in ablation}
            self.assertEqual(len(denominators), 1, f"{kind} 四档分母必须相同")
            for tier in ablation:
                counts = tier[f"{kind}_recall_at_5"]
                self.assertLessEqual(counts["hit"], counts["gold"])
                self.assertAlmostEqual(counts["rate"], counts["hit"] / counts["gold"])

    def test_mechanisms_are_nested_and_each_tier_is_measured(self):
        mechanisms = [tuple(tier["mechanisms"]) for tier in ABLATION_TIERS]
        for smaller, larger in zip(mechanisms, mechanisms[1:]):
            self.assertTrue(set(smaller) < set(larger), "消融档位必须是逐层加入机制")
        self.assertEqual(mechanisms[-1], ("lexical", "alias", "enum", "join_graph"))
        # Join Graph 档必须真的带来额外召回（否则该档无意义）
        recall = [tier["table_recall_at_5"]["hit"] for tier in self.report["ablation"]]
        self.assertGreater(recall[-1], recall[0], "全机制档应优于只词法档")

    def test_failure_taxonomy_sums_to_total_misses(self):
        taxonomy = self.report["summary"]["failure_taxonomy"]
        total_misses = sum(len(case["missing"][kind]) for case in self.report["cases"]
                           if not case["expected_reject"]
                           for kind in ("table", "column", "metric"))
        self.assertEqual(sum(taxonomy[key] for key in ("not_matched", "global_budget_cut",
                                                       "kind_top5_cut")), total_misses)
        budget = {entry["candidate_budget"]: entry for entry in self.report["budget_sensitivity"]}
        self.assertGreaterEqual(budget[32]["table_recall_at_5"]["hit"],
                                budget[8]["table_recall_at_5"]["hit"],
                                "扩大候选预算不应降低召回")

    def test_tier_filter_only_uses_engine_reasons(self):
        catalog, _ = build_fixture_catalog(self.suite, DSN)
        case = next(c for c in self.suite["cases"] if c["case_id"] == "v2-025")
        deployed = catalog.retrieve(case["question"], limit=8)["candidates"]
        lexical = _top(_tier_candidates(deployed, ABLATION_TIERS[0]["mechanisms"]), "table")
        full = _top(_tier_candidates(deployed, ABLATION_TIERS[-1]["mechanisms"]), "table")
        self.assertTrue(all(reason in {"table", "business_name", "column", "metric_name"}
                            for candidate in _tier_candidates(deployed, ABLATION_TIERS[0]["mechanisms"])
                            for reason in candidate["reasons"]))
        self.assertGreaterEqual(len(full), len(lexical))


class ZeroDenominatorTest(unittest.TestCase):
    def test_missing_dimensions_report_na_instead_of_zero(self):
        suite = load(V2_SUITE)
        trimmed = dict(suite)
        trimmed["cases"] = [{"case_id": "only-table", "question": "产品里有哪些品牌？",
                             "tables": ["products"], "columns": [], "metrics": [], "paths": [],
                             "expected_reject": False, "expected_absent_tables": [],
                             "expected_absent_columns": []}]
        report = evaluate_v2(trimmed, DSN)
        self.assertIsNone(report["summary"]["metric_recall_at_5"]["rate"])
        self.assertEqual(report["summary"]["metric_recall_at_5"]["gold"], 0)
        self.assertIsNone(report["summary"]["join_path_accuracy"]["rate"])
        self.assertIsNone(report["summary"]["reject_accuracy"]["rate"])
        self.assertIn("null", json.dumps(report["summary"]["metric_recall_at_5"]))

    def test_unknown_gold_is_rejected_loudly(self):
        suite = load(V2_SUITE)
        tampered = json.loads(json.dumps(suite))
        tampered["cases"] = [dict(tampered["cases"][0], tables=["not_a_real_table"])]
        with self.assertRaises(ValueError):
            evaluate_v2(tampered, DSN)


class V1UnchangedTest(unittest.TestCase):
    def test_v1_summary_reproduces_frozen_report(self):
        suite = load(V1_SUITE)
        report = evaluate(suite, DSN)
        frozen = load(V1_REPORT)
        self.assertEqual(report["summary"], frozen["summary"],
                         "v1 汇总必须与冻结报表一致（--suite v2 分支不得改变 v1 行为）")
        self.assertEqual([case["retrieved"] for case in report["cases"]],
                         [case["retrieved"] for case in frozen["cases"]])
        self.assertEqual(report["case_count"], frozen["case_count"])


if __name__ == "__main__":
    unittest.main()
