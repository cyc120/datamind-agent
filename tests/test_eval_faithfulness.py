# -*- coding: utf-8 -*-
"""R3 单文档 RAG 忠实度评测（E2/E6）的离线单项测试。

全部不调上游模型、不写仓内报告：
1. 归一化口径与 `datamind.knowledge.normalize` 一致；
2. 数字 token 抽取（丢掉 450L / 4K 这类型号数字）；
3. 结论切句（引用标记、拒答句、版式残片的分流）；
4. 三态标注规则（supported / contradicted / no_evidence）逐条；
5. 汇总口径（零分母报 None、拒答与误拒分子分母同给）；
6. 冻结集结构（≥20 题、≥4 拒答、每题有判据）；
7. 只读元数据库：冻结集的每条引用片段必须逐字来自目标文档（连不上库如实跳过）。
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


faith = _load("r3_eval_faithfulness_under_test", ROOT / "scripts/eval_faithfulness.py")

SUITE = json.loads((ROOT / "data/evaluation/faithfulness_v1.json").read_text(encoding="utf-8"))


def mini_case(**overrides) -> dict:
    case = {"case_id": "unit-001", "question": "一般商品退款多久到账？",
            "expected": "answer", "required_values": ["3"], "allowed_values": ["3"],
            "required_term_groups": [["工作日"]], "evidence_quotes": ["一般退款在验收后 3 个工作日内原路退回"]}
    case.update(overrides)
    return case


def citation(quote: str, document_id: str = "doc-x") -> dict:
    return {"document_id": document_id, "quote": quote, "title": "t", "page_start": 1}


class NormalizationTests(unittest.TestCase):
    def test_normalize_matches_product_helper(self):
        from datamind.knowledge import normalize as product_normalize
        samples = ["Ａ Ｂ\t c", "2025 年度", "全角：７日内", "half-width 5.3"]
        for sample in samples:
            self.assertEqual(faith.normalize(sample), product_normalize(sample), sample)

    def test_number_tokens_skip_model_codes(self):
        self.assertEqual(faith.number_tokens("对开门冰箱450L售出190台"), ["190"])
        # 4K 的 4 紧邻 ASCII 字母被丢掉；65 是中文单位前的尺寸数字，仍算断言数字（需要 allowed_values 覆盖）
        self.assertEqual(faith.number_tokens("65英寸4K电视"), ["65"])
        self.assertEqual(faith.number_tokens("蓝牙版本 5.3，重量 252 g"), ["5.3", "252"])
        self.assertEqual(faith.number_tokens("Pro3 与 4K"), [])

    def test_number_tokens_skip_reference_numbers(self):
        self.assertEqual(faith.number_tokens("该列在OCR结果中的表头为“未识别列5”"), [])
        self.assertEqual(faith.number_tokens("第三条 自签收之日起 7 日内"), ["7"])
        self.assertEqual(faith.number_tokens("表2 大区排名"), [])


class SplitTests(unittest.TestCase):
    def test_citation_markers_and_refusals_are_separated(self):
        answer = "自签收之日起7日内支持退款。 [1] 当前知识库没有足够证据回答该问题。由《制度》[2]"
        split = faith.split_conclusions(answer)
        self.assertEqual(split["conclusions"], ["自签收之日起7日内支持退款"])
        self.assertEqual(len(split["refusal_sentences"]), 1)
        self.assertEqual(len(split["boilerplate_sentences"]), 1)


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.case = mini_case()
        self.question_numbers = set()

    def label(self, sentence, evidence="一般退款在验收后 3 个工作日内原路退回"):
        return faith.label_conclusion(sentence, self.case, self.question_numbers,
                                      faith.normalize(evidence))["label"]

    def test_supported_when_required_value_present(self):
        self.assertEqual(self.label("一般退款在验收后3个工作日内原路退回"), "supported")

    def test_contradicted_when_on_topic_value_conflicts(self):
        self.assertEqual(self.label("大件家电退款需要5个工作日"), "contradicted")

    def test_contradicted_on_hallucinated_number(self):
        self.assertEqual(self.label("一般退款在验收后9个工作日内原路退回"), "contradicted")

    def test_no_evidence_when_required_value_missing_but_value_allowed(self):
        case = mini_case(allowed_values=["3", "30"])
        label = faith.label_conclusion("占比30%", case, set(), faith.normalize("3 个工作日"))["label"]
        self.assertEqual(label, "no_evidence")

    def test_question_numbers_are_context_not_claims(self):
        case = mini_case(question="2025年销售额是多少？", required_values=["214.4"],
                         allowed_values=["214.4"], required_term_groups=[["销售额"]])
        label = faith.label_conclusion("2025年销售额约214.4万元", case, {"2025"},
                                       faith.normalize("公司整体销售额约 214.4 万元"))["label"]
        self.assertEqual(label, "supported")

    def test_non_numeric_conclusion_needs_a_term_group(self):
        case = mini_case(required_values=[], allowed_values=[],
                         required_term_groups=[["定制"], ["人为损坏"]])
        self.assertEqual(faith.label_conclusion("定制类商品不予退款", case, set(), "")["label"],
                         "supported")
        self.assertEqual(faith.label_conclusion("会员可以积分抵现", case, set(), "")["label"],
                         "no_evidence")


class LabelAnswerTests(unittest.TestCase):
    def test_counts_and_frozen_evidence_coverage(self):
        case = mini_case()
        result = faith.label_answer(case, "一般退款在验收后3个工作日内原路退回。 [1]",
                                    [citation("一般退款在验收后 3 个工作日内原路退回")])
        self.assertEqual(result["conclusion_count"], 1)
        self.assertEqual(result["supported"], 1)
        self.assertEqual(result["unfaithful"], 0)
        self.assertTrue(result["frozen_evidence_covered"])

    def test_coverage_false_when_quote_absent(self):
        case = mini_case()
        result = faith.label_answer(case, "一般退款在验收后3个工作日内原路退回。 [1]",
                                    [citation("七天无理由退款的条件")])
        self.assertFalse(result["frozen_evidence_covered"])


class SummarizeTests(unittest.TestCase):
    def test_zero_denominator_is_none_not_zero(self):
        summary = faith.summarize([])
        self.assertEqual(summary["conclusion_total"], 0)
        self.assertIsNone(summary["faithfulness_rate"])
        self.assertIsNone(summary["refusal_accuracy"])

    def test_molecular_and_denominator_are_both_reported(self):
        cases = [
            {"expected": "answer", "supported": 1, "contradicted": 0, "no_evidence": 0,
             "conclusion_count": 1, "unfaithful": 0, "boilerplate_sentences": [],
             "refusal_sentences": [], "abstained": False, "frozen_evidence_covered": True,
             "upstream_error": False},
            {"expected": "answer", "supported": 1, "contradicted": 1, "no_evidence": 0,
             "conclusion_count": 2, "unfaithful": 1, "boilerplate_sentences": [],
             "refusal_sentences": [], "abstained": False, "frozen_evidence_covered": False,
             "upstream_error": False},
            {"expected": "refuse", "supported": 0, "contradicted": 0, "no_evidence": 0,
             "conclusion_count": 0, "unfaithful": 0, "boilerplate_sentences": [],
             "refusal_sentences": [], "abstained": True, "refusal_correct": True,
             "upstream_error": False},
        ]
        summary = faith.summarize(cases)
        self.assertEqual(summary["conclusion_total"], 3)
        self.assertEqual(summary["conclusion_supported"], 2)
        self.assertEqual(summary["conclusion_contradicted"], 1)
        self.assertAlmostEqual(summary["faithfulness_rate"], round(2 / 3, 4))
        self.assertEqual(summary["refusal_cases"], 1)
        self.assertEqual(summary["refusal_correct"], 1)
        self.assertEqual(summary["citation_coverage_cases"], 1)
        self.assertEqual(summary["citation_coverage_denominator"], 2)


class SuiteStructureTests(unittest.TestCase):
    def test_size_and_refusal_floor(self):
        cases = SUITE["cases"]
        self.assertGreaterEqual(len(cases), 20)
        refusals = [case for case in cases if case["expected"] == "refuse"]
        self.assertGreaterEqual(len(refusals), 4)

    def test_every_case_has_a_criterion_and_a_document(self):
        for case in SUITE["cases"]:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case.get("document_title"))
                self.assertTrue(case.get("question"))
                if case["expected"] == "answer":
                    self.assertTrue(case.get("required_values") or case.get("required_term_groups"))
                else:
                    self.assertEqual(case.get("evidence_quotes"), [])

    def test_labeling_rules_are_declared(self):
        rules = SUITE["labeling_rules"]
        for key in ("basis", "supported", "contradicted", "no_evidence", "model_judgment"):
            self.assertIn(key, rules)


class LiveCorpusConsistencyTests(unittest.TestCase):
    """冻结集的引用片段必须逐字来自目标文档（只读元数据库；连不上如实跳过）。"""

    def test_frozen_quotes_are_verbatim(self):
        try:
            documents = faith.read_documents()
        except Exception as error:                       # 无法连库时如实跳过
            self.skipTest(f"元数据库不可用：{error}")
        problems = faith.validate_suite(SUITE, documents)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
