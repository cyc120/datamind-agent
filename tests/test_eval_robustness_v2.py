# -*- coding: utf-8 -*-
"""R3 鲁棒性扩展评测（E9/F7）的离线单项测试。

全部不调上游模型、不写仓内报告：
1. 长题构造（rb2-016 在 2000 字符内、rb2-017 超限）；
2. 逐判据机械判定（http_status / statuses / 错误码 / 答案词 / SQL / 引用 / 空结果）；
3. 类别汇总的分子分母与失败样本保留；
4. 冻结集结构（≥16 题、覆盖全部声明类别、每题有判据）；
5. 只读元数据库：题集引用的文档必须是真实可检索文档（连不上库如实跳过）。
"""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rb = _load("r3_eval_robustness_v2_under_test", ROOT / "scripts/eval_robustness_v2.py")

SUITE = json.loads((ROOT / "data/evaluation/robustness_v2.json").read_text(encoding="utf-8"))
CASES = {case["case_id"]: case for case in SUITE["cases"]}


def result(*, http_status=200, status="COMPLETED", error_code=None, answer="查询结果：997。",
           sql=None, citations=None, transport_error=None):
    payload = {"status": status, "error_code": error_code, "answer": answer}
    if sql is not None:
        payload["sql_result"] = sql
    if citations is not None:
        payload["rag_result"] = {"citations": citations}
    return {"http_status": http_status, "error_code": error_code, "payload": payload,
            "transport_error": transport_error}


class BuildQuestionTests(unittest.TestCase):
    def test_long_question_lengths(self):
        self.assertLess(len(rb.build_question(CASES["rb2-016"])), 2000)
        self.assertGreater(len(rb.build_question(CASES["rb2-017"])), 2000)

    def test_plain_question_passthrough(self):
        self.assertEqual(rb.build_question(CASES["rb2-001"]), CASES["rb2-001"]["question"])


class EvaluateTests(unittest.TestCase):
    def test_control_case_passes_on_real_shape(self):
        verdict = rb.evaluate(CASES["rb2-021"],
                             result(sql={"sql": "SELECT 1", "row_count": 1}))
        self.assertTrue(verdict["passed"], verdict["failures"])

    def test_status_mismatch_is_a_failure(self):
        verdict = rb.evaluate(CASES["rb2-021"], result(status="ABSTAINED", sql={"sql": "SELECT 1"}))
        self.assertFalse(verdict["passed"])
        self.assertIn("status", [item["check"] for item in verdict["failures"]])

    def test_forbidden_error_code_is_a_failure(self):
        verdict = rb.evaluate(CASES["rb2-021"],
                             result(status="FAILED_WITH_EVIDENCE", error_code="INTERNAL_ERROR",
                                    answer="内部错误", sql={"sql": "SELECT 1"}))
        self.assertIn("forbidden_error_codes", [item["check"] for item in verdict["failures"]])

    def test_expected_error_code_list(self):
        good = rb.evaluate(CASES["rb2-011"],
                          result(status="FAILED_WITH_EVIDENCE", error_code="SOURCE_NOT_ALLOWED",
                                 answer="当前请求范围不允许访问该数据源。"))
        self.assertTrue(good["passed"], good["failures"])
        bad = rb.evaluate(CASES["rb2-011"],
                         result(status="FAILED_WITH_EVIDENCE", error_code="OTHER_CODE",
                                answer="当前请求范围不允许访问该数据源。"))
        self.assertIn("error_codes_any", [item["check"] for item in bad["failures"]])

    def test_answer_any_and_none(self):
        verdict = rb.evaluate(CASES["rb2-019"],
                             result(status="NEEDS_CLARIFICATION",
                                    answer="本环境为只读，不支持导出整表；password= 与 credit_code= 均不可用。"))
        self.assertIn("answer_none", [item["check"] for item in verdict["failures"]])
        ok = rb.evaluate(CASES["rb2-019"],
                        result(status="NEEDS_CLARIFICATION",
                               answer="orders 表没有 password、credit_code 等字段，且本环境只读。"))
        self.assertTrue(ok["passed"], ok["failures"])

    def test_require_citation(self):
        verdict = rb.evaluate(CASES["rb2-006"], result(answer="续航约38小时。"))
        self.assertIn("require_citation", [item["check"] for item in verdict["failures"]])
        ok = rb.evaluate(CASES["rb2-006"],
                        result(answer="续航约38小时。",
                               citations=[{"title": "09_无线降噪耳机Pro产品手册"}]))
        self.assertTrue(ok["passed"], ok["failures"])

    def test_empty_or_zero_answer(self):
        ok = rb.evaluate(CASES["rb2-014"], result(answer="0 笔", sql={"sql": "SELECT count(*)", "row_count": 0}))
        self.assertTrue(ok["passed"], ok["failures"])
        bad = rb.evaluate(CASES["rb2-014"],
                         result(status="FAILED_WITH_EVIDENCE", error_code="REPAIR_BUDGET_EXHAUSTED",
                                answer="查询未能完成。"))
        self.assertIn("empty_or_zero_answer", [item["check"] for item in bad["failures"]])

    def test_oversized_input_requires_422(self):
        self.assertTrue(rb.evaluate(CASES["rb2-017"], result(http_status=422, status=None))["passed"])
        self.assertFalse(rb.evaluate(CASES["rb2-017"], result(http_status=200))["passed"])


class SummarizeTests(unittest.TestCase):
    def test_category_numerators_and_failure_retention(self):
        details = [
            {"category": "错别字", "case_id": "a", "passed": True},
            {"category": "错别字", "case_id": "b", "passed": False},
            {"category": "空结果", "case_id": "c", "passed": False},
        ]
        summary = rb.summarize(details)
        self.assertEqual(summary["case_total"], 3)
        self.assertEqual(summary["case_passed"], 1)
        self.assertEqual(summary["by_category"]["错别字"], {
            "total": 2, "passed": 1, "failed_ids": ["b"], "pass_rate": 0.5})
        self.assertEqual(summary["by_category"]["空结果"]["pass_rate"], 0.0)

    def test_zero_denominator(self):
        summary = rb.summarize([])
        self.assertIsNone(summary["pass_rate"])


class SuiteStructureTests(unittest.TestCase):
    def test_size_and_category_coverage(self):
        self.assertGreaterEqual(len(SUITE["cases"]), 16)
        categories = {case["category"] for case in SUITE["cases"]}
        self.assertEqual(categories, set(SUITE["categories"]))
        for name in ("错别字", "口语简称", "中英混写", "全角半角", "多实体歧义", "越权数据源",
                     "越权列", "空结果", "超长输入", "注入样式"):
            self.assertIn(name, categories)

    def test_every_case_has_expectations(self):
        for case in SUITE["cases"]:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case.get("expect"))
                self.assertIn("note", case["expect"])


class LiveDocumentReferenceTests(unittest.TestCase):
    """题集里按标题指定的文档必须是真实可检索文档（只读；连不上如实跳过）。"""

    def test_referenced_documents_are_searchable(self):
        try:
            documents = rb.read_documents()
        except Exception as error:
            self.skipTest(f"元数据库不可用：{error}")
        problems = rb.validate_suite(SUITE, documents)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
