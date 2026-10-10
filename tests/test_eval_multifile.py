# -*- coding: utf-8 -*-
"""R3 多文档问答评测（F5）的离线单项测试。

全部不调上游模型、不写仓内报告：
1. 逐题判定：正确性（required_values / 术语锚点）、引用覆盖全选、cite_all_expected 门控；
2. 拒答题判定：必须 ABSTAINED 且无引用；
3. 汇总口径：分子分母同给、零分母 None；
4. 冻结集结构：≥8 题、每题 2–4 份文档、含 ≥1 个三文档题、含拒答题、每题有判据；
5. 只读元数据库：题集引用片段必须逐字来自指定文档（连不上库如实跳过）。
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


mf = _load("r3_eval_multifile_under_test", ROOT / "scripts/eval_multifile.py")

SUITE = json.loads((ROOT / "data/evaluation/multifile_v1.json").read_text(encoding="utf-8"))
CASES = {case["case_id"]: case for case in SUITE["cases"]}


def prepare(case_id: str, document_ids=None):
    case = dict(CASES[case_id])
    case["_selected_document_ids"] = document_ids or ["doc-a", "doc-b"]
    return case


def citation(document_id: str, quote: str = "一般退款在验收后 3 个工作日内原路退回"):
    return {"document_id": document_id, "quote": quote, "title": document_id, "page_start": 1}


class EvaluateCaseTests(unittest.TestCase):
    def test_answerable_case_passes_with_all_values_and_citations(self):
        case = prepare("mf-002")
        payload = {"status": "COMPLETED",
                   "answer": "一般商品退款在验收后3个工作日内原路退回。"}
        verdict = mf.evaluate_case(case, payload, [citation("doc-a"), citation("doc-b")])
        self.assertTrue(verdict["passed"], verdict["correctness"])
        self.assertTrue(verdict["citation_covers_all_selected"])

    def test_missing_required_value_fails(self):
        case = prepare("mf-002")
        payload = {"status": "COMPLETED", "answer": "一般商品退款在验收后5个工作日内原路退回。"}
        verdict = mf.evaluate_case(case, payload, [citation("doc-a"), citation("doc-b")])
        self.assertFalse(verdict["passed"])
        self.assertFalse(verdict["correctness"]["all_values_present"])

    def test_cite_all_expected_gates_pass(self):
        case = prepare("mf-002")
        payload = {"status": "COMPLETED", "answer": "一般商品退款在验收后3个工作日内原路退回。"}
        verdict = mf.evaluate_case(case, payload, [citation("doc-a")])
        self.assertFalse(verdict["passed"])
        self.assertFalse(verdict["citation_covers_all_selected"])
        # 同一份答案，若本题不要求引用全部指定文件，则应通过
        relaxed = prepare("mf-005")
        relaxed_payload = {"status": "COMPLETED", "answer": "物流损坏50笔。"}
        relaxed_verdict = mf.evaluate_case(relaxed, relaxed_payload,
                                          [citation("doc-a", "|1|物流损坏|50|")])
        self.assertTrue(relaxed_verdict["passed"], relaxed_verdict["correctness"])

    def test_refusal_requires_abstained_and_no_citation(self):
        case = prepare("mf-009")
        good = mf.evaluate_case(case, {"status": "ABSTAINED", "answer": "知识库没有足够证据。"}, [])
        self.assertTrue(good["passed"])
        bad = mf.evaluate_case(case, {"status": "ABSTAINED", "answer": "…"}, [citation("doc-a")])
        self.assertFalse(bad["passed"])
        bad2 = mf.evaluate_case(case, {"status": "COMPLETED", "answer": "保修 12 个月。"}, [])
        self.assertFalse(bad2["passed"])

    def test_conclusion_labels_come_from_cited_quotes(self):
        case = prepare("mf-001")
        payload = {"status": "COMPLETED", "answer": "华东大区2025年全年销售额为28.3万元。"}
        verdict = mf.evaluate_case(case, payload, [citation("doc-a", "|6|华东|28.3|14.4%|")])
        self.assertEqual(verdict["conclusion_count"], 1)
        self.assertEqual(verdict["supported"], 1)


class SummarizeTests(unittest.TestCase):
    def test_numerators_and_denominators(self):
        cases = [
            {"expected": "answer", "passed": True, "cite_all_expected": True,
             "citation_covers_all_selected": True, "supported": 1, "contradicted": 0,
             "no_evidence": 0, "upstream_error": False},
            {"expected": "answer", "passed": False, "cite_all_expected": True,
             "citation_covers_all_selected": False, "supported": 0, "contradicted": 1,
             "no_evidence": 0, "upstream_error": False},
            {"expected": "refuse", "passed": True, "cite_all_expected": False,
             "citation_covers_all_selected": False, "supported": 0, "contradicted": 0,
             "no_evidence": 0, "upstream_error": False},
        ]
        summary = mf.summarize(cases)
        self.assertEqual(summary["answerable_cases"], 2)
        self.assertEqual(summary["answerable_correct"], 1)
        self.assertAlmostEqual(summary["answerable_accuracy"], 0.5)
        self.assertEqual(summary["citation_covers_all_selected_cases"], 1)
        self.assertEqual(summary["citation_covers_all_selected_denominator"], 2)
        self.assertEqual(summary["refusal_correct"], 1)
        self.assertEqual(summary["conclusion_total"], 2)
        self.assertAlmostEqual(summary["faithfulness_rate"], 0.5)

    def test_zero_denominator_is_none(self):
        summary = mf.summarize([])
        self.assertIsNone(summary["answerable_accuracy"])
        self.assertIsNone(summary["refusal_accuracy"])
        self.assertIsNone(summary["citation_covers_all_selected_rate"])


class SuiteStructureTests(unittest.TestCase):
    def test_size_document_counts_and_refusals(self):
        cases = SUITE["cases"]
        self.assertGreaterEqual(len(cases), 8)
        self.assertTrue(any(len(case["document_titles"]) >= 3 for case in cases))
        self.assertTrue(any(case["expected"] == "refuse" for case in cases))
        self.assertTrue(any(case.get("cross_document") for case in cases))
        for case in cases:
            with self.subTest(case=case["case_id"]):
                self.assertGreaterEqual(len(case["document_titles"]), 2)
                self.assertLessEqual(len(case["document_titles"]), 4)
                if case["expected"] == "answer":
                    self.assertTrue(case.get("required_values") or case.get("required_term_groups"))


class LiveCorpusConsistencyTests(unittest.TestCase):
    """引用片段/指定文档必须与只读元数据库一致（连不上如实跳过）。"""

    def test_frozen_quotes_are_verbatim(self):
        try:
            documents = mf.read_documents()
        except Exception as error:
            self.skipTest(f"元数据库不可用：{error}")
        problems = mf.validate_suite(SUITE, documents)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
