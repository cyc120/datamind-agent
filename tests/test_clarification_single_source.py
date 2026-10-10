"""L4 守护：澄清槽位契约只有一份实现（生产包），评测脚本只做再导出。

第 1 轮独立复查（``reports/review_round1_security.md`` L4）指出：``datamind/api.py`` 曾在
**请求期** ``from scripts.eval_clarification import clarification_contract_for_answer``，
而 ``scripts/`` 没有 ``__init__.py``——生产服务路径耦合评测脚本，脚本一旦被移动/删除，
线上就只剩空兜底。修复方式是把契约下沉到 ``datamind/clarification.py``，``scripts/`` 再导出。

本文件钉住四件事（都是「有第二份实现就会红」的断言）：

1. 同一个函数对象：``scripts.eval_clarification`` 与 ``datamind.clarification`` 的
   ``clarification_contract*`` 必须是 ``is`` 同一对象，常量逐值相等；
2. 源码里没有第二份槽位表 / 正则表 / 检测器定义（scripts 只 import，不再 def/赋值）；
3. API 源码不再引用 ``scripts.eval_clarification``；
4. 对同一输入，API 投影与脚本投影逐键一致，且四个 ``clarification_*`` 键一个不少。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import datamind.clarification as core
import scripts.eval_clarification as script_eval
from datamind import sql_engine
from datamind.api import _with_clarification_contract

API_SOURCE = (ROOT / "datamind" / "api.py").read_text(encoding="utf-8")
SCRIPT_SOURCE = (ROOT / "scripts" / "eval_clarification.py").read_text(encoding="utf-8")

CONTRACT_KEYS = ("clarification_candidates", "clarification_contract_version",
                 "clarification_missing_slots", "clarification_slot_questions")

#: 契约里的槽位表 / 正则表 / 检测器：这些定义只允许出现在 datamind/clarification.py。
FORBIDDEN_REDEFINITIONS = (
    "SLOT_ORDER = (",
    "SLOT_QUESTIONS = {",
    "DEFAULT_PRODUCTS = [",
    "CONTRACT_VERSION = ",
    "TIME_RE = re.compile",
    "RANK_TREND_RE = re.compile",
    "METRIC_REFUND_RE = re.compile",
    "RULE_ANSWER_SLOT_PATTERNS = (",
    "def has_time(",
    "def detect_missing_slots(",
    "def slot_candidates(",
    "def clarification_contract(",
    "def clarification_contract_for_answer(",
    "def rule_slots_for_answer(",
)

SAMPLE_QUESTIONS = (
    "销售额最高的品类是哪个？",          # time_range
    "退款情况怎么样？",                  # metric
    "2024年人均消费是多少？",            # grain
    "最新的制度文档里怎么规定的？",       # document_version
    "那款旗舰耳机这个月卖得怎么样？",     # object_alias
    "2025年华东地区的销售额是多少？",     # 完整问题，零槽位
    "",                                  # 空输入
)


class SingleImplementationTest(unittest.TestCase):
    def test_scripts_reexports_the_very_same_objects(self):
        for name in ("clarification_contract", "clarification_contract_for_answer",
                     "detect_missing_slots", "slot_candidates", "rule_slots_for_answer",
                     "has_time"):
            self.assertIs(getattr(script_eval, name), getattr(core, name),
                          "%s 在 scripts 与 datamind 里不是同一对象——出现了第二份实现" % name)

    def test_shared_constants_are_identical_value_for_value(self):
        self.assertEqual(script_eval.CONTRACT_VERSION, core.CONTRACT_VERSION)
        self.assertEqual(script_eval.SLOT_ORDER, core.SLOT_ORDER)
        self.assertEqual(script_eval.SLOT_QUESTIONS, core.SLOT_QUESTIONS)
        self.assertEqual(script_eval.DEFAULT_PRODUCTS, core.DEFAULT_PRODUCTS)

    def test_the_eval_script_defines_no_second_slot_or_regex_table(self):
        for token in FORBIDDEN_REDEFINITIONS:
            self.assertNotIn(token, SCRIPT_SOURCE,
                             "scripts/eval_clarification.py 又自己定义了 %r，违背单一实现" % token)
        self.assertIn("from datamind.clarification import", SCRIPT_SOURCE)

    def test_the_contract_implementation_lives_in_the_production_package(self):
        self.assertTrue((ROOT / "datamind" / "clarification.py").is_file())
        self.assertIn("clarification-slots-v1", core.CONTRACT_VERSION)

    def test_the_api_no_longer_imports_the_eval_script(self):
        self.assertNotIn("eval_clarification", API_SOURCE,
                         "生产服务路径仍在引用评测脚本（L4 回归）")
        self.assertIn("from .clarification import clarification_contract_for_answer", API_SOURCE)


class ApiScriptParityTest(unittest.TestCase):
    """同一输入 → API 投影与脚本投影逐键一致（脚本调用面不变）。"""

    def test_detector_contract_is_key_for_key_identical(self):
        for question in SAMPLE_QUESTIONS:
            with self.subTest(question=question):
                from_api = core.clarification_contract(question)
                from_script = script_eval.clarification_contract(question)
                self.assertEqual(sorted(from_api), sorted(CONTRACT_KEYS))
                self.assertEqual(from_api, from_script)
                for key in CONTRACT_KEYS:
                    self.assertEqual(from_api[key], from_script[key], key)

    def test_answer_contract_matches_on_real_rule_wording(self):
        # 用真实规则式 clarify() 的原话去投影，确认两条导入路径给出同一份结果。
        for question in SAMPLE_QUESTIONS:
            answer = sql_engine.clarify(question) or ""
            with self.subTest(question=question, answer=answer):
                from_api = core.clarification_contract_for_answer(question, answer)
                from_script = script_eval.clarification_contract_for_answer(question, answer)
                self.assertEqual(from_api, from_script)
                self.assertEqual(sorted(from_api), sorted(CONTRACT_KEYS))

    def test_unknown_wording_yields_empty_slots_through_both_paths(self):
        fields = script_eval.clarification_contract_for_answer("随便问一句", "请补充更多信息。")
        self.assertEqual(fields["clarification_missing_slots"], [])
        self.assertEqual(fields["clarification_candidates"], {})
        self.assertEqual(fields, core.clarification_contract_for_answer("随便问一句", "请补充更多信息。"))


class ApiWiringUsesTheSingleSourceTest(unittest.TestCase):
    """``_with_clarification_contract`` 必须调用同一份实现，并且只做加法。"""

    CLARIFY_PAYLOAD = {"status": "NEEDS_CLARIFICATION", "error_code": "AMBIGUOUS_QUERY",
                       "answer": "请指定销售额的统计时间范围。", "confidence_level": "insufficient",
                       "turns_used": 0}

    def test_the_projection_is_delegated_to_the_shared_implementation(self):
        with patch("datamind.api.clarification_contract_for_answer",
                   side_effect=core.clarification_contract_for_answer) as spy:
            merged = _with_clarification_contract(dict(self.CLARIFY_PAYLOAD), "销售额最高的品类是哪个？")
        spy.assert_called_once_with("销售额最高的品类是哪个？", "请指定销售额的统计时间范围。")
        self.assertEqual(merged["clarification_missing_slots"], ["time_range"])
        for key in CONTRACT_KEYS:
            self.assertIn(key, merged)
        for key, value in self.CLARIFY_PAYLOAD.items():
            self.assertEqual(merged[key], value, "既有键 %s 的含义与取值不得变化" % key)

    def test_the_api_projection_equals_the_script_projection(self):
        merged = _with_clarification_contract(dict(self.CLARIFY_PAYLOAD), "销售额最高的品类是哪个？")
        expected = script_eval.clarification_contract_for_answer(
            "销售额最高的品类是哪个？", self.CLARIFY_PAYLOAD["answer"])
        for key in CONTRACT_KEYS:
            self.assertEqual(merged[key], expected[key], key)

    def test_non_clarification_payload_is_returned_untouched(self):
        payload = {"status": "COMPLETED", "answer": "42"}
        self.assertIs(_with_clarification_contract(payload, "问题"), payload)

    def test_a_failing_projection_never_breaks_the_clarification_response(self):
        with patch("datamind.api.clarification_contract_for_answer", side_effect=RuntimeError("boom")):
            merged = _with_clarification_contract(dict(self.CLARIFY_PAYLOAD), "问题")
        self.assertEqual(merged, self.CLARIFY_PAYLOAD)

    def test_the_api_and_the_eval_suite_agree_on_the_frozen_cases(self):
        import json
        suite = json.loads((ROOT / "data" / "evaluation" / "clarification_v1.json")
                           .read_text(encoding="utf-8"))
        for case in suite["cases"]:
            question, answer = case["question"], sql_engine.clarify(case["question"]) or ""
            with self.subTest(case_id=case["case_id"]):
                api_view = core.clarification_contract_for_answer(question, answer)
                script_view = script_eval.clarification_contract_for_answer(question, answer)
                merged = _with_clarification_contract(
                    {"status": "NEEDS_CLARIFICATION", "answer": answer}, question)
                self.assertEqual(api_view, script_view)
                for key in CONTRACT_KEYS:
                    self.assertEqual(merged[key], script_view[key], key)


if __name__ == "__main__":
    unittest.main()
