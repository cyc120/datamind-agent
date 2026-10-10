"""B3 语义校验与澄清闭环：缺项检测 / 补槽合并 / 正向对照 / 指标算术 / API 契约。

纪律（与 reports/cards/wp4_b3_clarification.md 一致）：

* 正向对照（``expected_action=answer``）是防「一律反问」伪修的关键：系统必须直接回答，
  不允许用「多问一句更安全」把不必要澄清率做成 0。
* 零分母报 ``N/A``，不用 0 或 100% 冒充。
* API 层只验证新增键；``status`` / ``answer`` / ``error_code`` 的既有含义必须不变。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg2
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datamind import sql_engine
from datamind.api import app
from scripts.eval_clarification import (
    SLOT_ORDER,
    _metrics,
    clarification_contract,
    clarification_contract_for_answer,
    detect_missing_slots,
    merge_slot_state,
    merge_supplement,
    slot_candidates,
    stub_reply,
)

import auth_fixtures

ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "data/evaluation/clarification_v1.json").read_text(encoding="utf-8"))


class SuiteIntegrityTest(unittest.TestCase):
    def test_the_frozen_suite_covers_six_categories_and_enough_controls(self):
        cases = SUITE["cases"]
        self.assertGreaterEqual(len(cases), 20, "冻结集至少 20 条")
        categories = {case["category"] for case in cases}
        self.assertEqual(categories, {"time_missing", "metric_ambiguity", "grain_ambiguity",
                                      "version_ambiguity", "object_alias_ambiguity", "must_not_ask"})
        controls = [case for case in cases if case["expected_action"] == "answer"]
        self.assertGreaterEqual(len(controls), 5, "正向对照至少 5 条")
        for case in controls:
            self.assertTrue(case["must_not_ask"], case["case_id"])
            self.assertEqual(case["expected_missing_slots"], [], case["case_id"])
        for case in cases:
            self.assertIn(case["expected_action"], ("clarify", "answer"), case["case_id"])
            for slot in case["expected_missing_slots"]:
                self.assertIn(slot, SLOT_ORDER, case["case_id"])


class MissingSlotDetectionTest(unittest.TestCase):
    def test_each_category_is_detected_with_its_documented_slot(self):
        expected = {
            "time_missing": "time_range",
            "metric_ambiguity": "metric",
            "grain_ambiguity": "grain",
            "version_ambiguity": "document_version",
            "object_alias_ambiguity": "object_alias",
        }
        for case in SUITE["cases"]:
            if case["category"] == "must_not_ask":
                continue
            detected = detect_missing_slots(case["question"])
            for slot in case["expected_missing_slots"]:
                self.assertIn(slot, detected, case["case_id"])
            self.assertEqual(set(detected), set(case["expected_missing_slots"]),
                             "%s 检测到的槽位与冻结期望必须一致" % case["case_id"])
        # 五类都要真的出现过，避免某一类被静默写进文档却没实现。
        self.assertEqual(set(expected.values()),
                         {slot for case in SUITE["cases"] if case["category"] != "must_not_ask"
                          for slot in case["expected_missing_slots"]})

    def test_time_detection_does_not_fire_when_a_period_is_present(self):
        self.assertEqual(detect_missing_slots("哪个产品的销量最高？"), ["time_range"])
        self.assertEqual(detect_missing_slots("2025年哪个产品的销量最高？"), [])

    def test_named_metric_removes_the_ambiguity(self):
        self.assertIn("metric", detect_missing_slots("退款情况怎么样？"))
        self.assertNotIn("metric", detect_missing_slots("2025年退款金额是多少？"))

    def test_contract_exposes_slots_candidates_and_questions(self):
        contract = clarification_contract("2024年人均消费是多少？")
        self.assertEqual(contract["clarification_contract_version"], "clarification-slots-v1")
        self.assertEqual(contract["clarification_missing_slots"], ["grain"])
        self.assertIn("按客户", contract["clarification_candidates"]["grain"])
        self.assertTrue(contract["clarification_slot_questions"]["grain"])

    def test_candidates_are_slot_specific_and_grounded(self):
        self.assertIn("2025年", slot_candidates("time_range", "随便"))
        self.assertEqual(slot_candidates("metric", "退款情况怎么样？"), ["退款金额", "退款笔数"])
        self.assertEqual(slot_candidates("metric", "销售表现怎么样？"), ["销售额", "订单数"])
        self.assertIn("无线降噪耳机 Pro", slot_candidates("object_alias", "那款旗舰耳机怎么样？"))


class SlotMergeTest(unittest.TestCase):
    def test_this_round_reply_replaces_same_slot_and_keeps_history(self):
        history = {"time_range": "2024年", "metric": "销售额"}
        merged = merge_slot_state(history, {"time_range": "2025年"})
        self.assertEqual(merged, {"time_range": "2025年", "metric": "销售额"})

    def test_supplement_text_accumulates_every_filled_slot(self):
        values = merge_slot_state({}, {"time_range": "2025年"})
        values = merge_slot_state(values, {"grain": "按客户"})
        merged = merge_supplement("人均消费趋势如何？", values)
        self.assertEqual(merged, "人均消费趋势如何？ 2025年 按客户")
        # 本轮补充覆盖上一轮未提的槽位：两轮之后历史缺项全部消失。
        self.assertNotIn("time_range", detect_missing_slots(merged))
        self.assertNotIn("grain", detect_missing_slots(merged))

    def test_multi_slot_case_needs_two_rounds_with_the_one_answer_at_a_time_stub(self):
        case = next(c for c in SUITE["cases"] if c["case_id"] == "cl-grain-04")
        remaining = detect_missing_slots(case["question"])
        self.assertEqual(set(remaining), {"time_range", "grain"})
        values, text = {}, case["question"]
        rounds = 0
        while remaining and rounds < 3:
            reply, missing = stub_reply(case, remaining)
            self.assertEqual(missing, [])
            values = merge_slot_state(values, reply)
            text = merge_supplement(case["question"], values)
            rounds += 1
            remaining = [slot for slot in remaining if slot in detect_missing_slots(text)]
        self.assertEqual(rounds, 2)
        self.assertEqual(detect_missing_slots(text), [])

    def test_stub_reports_unfillable_slots_instead_of_inventing_values(self):
        case = next(c for c in SUITE["cases"] if c["case_id"] == "cl-time-01")
        reply, missing = stub_reply(case, ["grain"])
        self.assertEqual(reply, {})
        self.assertEqual(missing, ["grain"])


class MustNotAskTest(unittest.TestCase):
    """正向对照：问题已完整时必须直接回答，不许反问。"""

    def test_detector_never_flags_the_positive_controls(self):
        for case in SUITE["cases"]:
            if case["expected_action"] != "answer":
                continue
            self.assertEqual(detect_missing_slots(case["question"]), [],
                             "%s 是完整问题，检测器不得报缺项" % case["case_id"])

    def test_the_deployed_rule_never_flags_the_positive_controls(self):
        for case in SUITE["cases"]:
            if case["expected_action"] != "answer":
                continue
            self.assertIsNone(sql_engine.clarify(case["question"]),
                              "%s 触发了线上规则式澄清" % case["case_id"])
            self.assertEqual(clarification_contract_for_answer(case["question"], "")[
                "clarification_missing_slots"], [])

    def test_paired_control_answers_once_the_time_is_supplied(self):
        # cl-time-01 与 cl-answer-05 是一对：同一问题补上时间后必须不再澄清。
        self.assertIsNotNone(sql_engine.clarify("销售额最高的品类是哪个？"))
        self.assertIsNone(sql_engine.clarify("2025年销售额最高的品类是哪个？"))
        self.assertEqual(detect_missing_slots("销售额最高的品类是哪个？ 2025年"), [])


def _detail(case_id, action, asked, rounds=0, landed=True, unresolved=None):
    return {"case_id": case_id, "expected_action": action, "asked": asked,
            "supplement_rounds": rounds, "unresolved_slots": unresolved or [],
            "frame_landed": landed}


class MetricArithmeticTest(unittest.TestCase):
    def test_unnecessary_clarification_share_and_absolute_count(self):
        details = [_detail("a", "answer", False), _detail("b", "answer", True),
                   _detail("c", "clarify", True, rounds=1)]
        metrics = _metrics(details)
        unnecessary = metrics["unnecessary_clarification"]
        self.assertEqual(unnecessary["absolute"], 1)
        self.assertEqual(unnecessary["share_of_answer_cases"],
                         {"numerator": 1, "denominator": 2, "rate": 0.5})
        self.assertEqual(unnecessary["share_of_all_cases"],
                         {"numerator": 1, "denominator": 3, "rate": 0.3333})
        self.assertEqual(unnecessary["answer_case_ids"], ["b"])
        self.assertEqual(metrics["clarification_success"],
                         {"numerator": 1, "denominator": 1, "rate": 1.0})
        self.assertEqual(metrics["average_clarification_rounds"]["total_rounds"], 1)

    def test_zero_denominators_report_na_not_zero(self):
        metrics = _metrics([_detail("a", "answer", False)])
        self.assertEqual(metrics["clarification_success"]["denominator"], 0)
        self.assertEqual(metrics["clarification_success"]["rate"], "N/A")
        self.assertEqual(metrics["average_clarification_rounds"]["value"], "N/A")
        self.assertEqual(metrics["slot_landing"]["rate"], "N/A")

    def test_asked_but_unresolved_is_not_a_success(self):
        details = [_detail("a", "clarify", True, rounds=1, unresolved=["grain"])]
        metrics = _metrics(details)
        self.assertEqual(metrics["clarification_success"],
                         {"numerator": 0, "denominator": 1, "rate": 0.0})


class ChatClarificationContractTest(unittest.TestCase):
    """API 层最小接线：只新增键，既有键含义不变。"""

    def setUp(self):
        self.runs = []
        self.client = TestClient(app)
        auth_fixtures.login(self.client)

    def tearDown(self):
        try:
            if self.runs:
                with psycopg2.connect("host=127.0.0.1 port=5432 dbname=datamind_meta "
                                      "user=datamind_app password=datamind_app_dev") as conn, \
                        conn.cursor() as cur:
                    for run_id in self.runs:
                        for table in ("evidence_edges", "evidence_nodes", "claims", "runs"):
                            column = "run_id" if table != "runs" else "run_id"
                            cur.execute("DELETE FROM %s WHERE %s=%%s" % (table, column), (run_id,))
        finally:
            auth_fixtures.cleanup(self.client)
            self.client.close()

    @staticmethod
    def _rule_only_ask(question, source_id=None, **kwargs):
        """只走真实规则式 clarify()，绝不触发模型调用。"""
        message = sql_engine.clarify(question)
        if message:
            return {"status": "clarification_required", "clarification": message}
        return {"status": "failed", "error_code": "STUB_NO_MODEL"}

    def _post(self, question):
        response = self.client.post("/v1/chat", json={
            "question": question, "mode": "sql", "scope": {"database_sources": ["demo_retail"]}})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        if payload.get("run_id"):
            self.runs.append(payload["run_id"])
        return payload

    def test_needs_clarification_keeps_existing_keys_and_adds_structured_slots(self):
        with patch("datamind.agent.ask", side_effect=self._rule_only_ask):
            payload = self._post("销售额最高的品类是哪个？")
        self.assertEqual(payload["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(payload["error_code"], "AMBIGUOUS_QUERY")
        self.assertIsInstance(payload["answer"], str)
        self.assertEqual(payload["confidence_level"], "insufficient")
        self.assertEqual(payload["turns_used"], 0)
        self.assertEqual(payload["clarification_contract_version"], "clarification-slots-v1")
        self.assertEqual(payload["clarification_missing_slots"], ["time_range"])
        self.assertIn("2025年", payload["clarification_candidates"]["time_range"])

    def test_one_supplement_completes_without_a_second_clarification(self):
        seen = []

        def recording_ask(question, source_id=None, **kwargs):
            seen.append(question)
            return self._rule_only_ask(question, source_id, **kwargs)

        with patch("datamind.agent.ask", side_effect=recording_ask):
            first = self._post("销售额最高的品类是哪个？")
            self.assertEqual(first["status"], "NEEDS_CLARIFICATION")
            second = self._post("销售额最高的品类是哪个？ 2025年")
        self.assertNotEqual(second["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(seen[1], "销售额最高的品类是哪个？ 2025年")

    def test_unknown_rule_wording_yields_empty_slots_instead_of_a_guess(self):
        fields = clarification_contract_for_answer("随便问一句", "请补充更多信息。")
        self.assertEqual(fields["clarification_missing_slots"], [])
        self.assertEqual(fields["clarification_candidates"], {})

    def test_complete_questions_never_get_the_clarification_keys(self):
        with patch("datamind.agent.ask", side_effect=self._rule_only_ask):
            payload = self._post("2025年华东地区的销售额是多少？")
        self.assertNotEqual(payload["status"], "NEEDS_CLARIFICATION")
        self.assertNotIn("clarification_missing_slots", payload)


class FrontendSeamTest(unittest.TestCase):
    """前端那一半：没有 JS 测试运行器，所以只钉住「API 真的会发的键名」与接线是否还在。"""

    def setUp(self):
        self.source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_the_ui_reads_exactly_the_keys_the_api_contract_emits(self):
        for key in ("clarification_missing_slots", "clarification_candidates",
                    "clarification_slot_questions"):
            self.assertIn(key, self.source, key)
            # 键名必须与契约实现同源，避免前端读一个后端永远不发的名字。
            self.assertIn(key, clarification_contract("销售额最高的品类是哪个？"))

    def test_clarification_responses_are_routed_to_the_slot_panel(self):
        self.assertIn("out.status === 'NEEDS_CLARIFICATION'", self.source)
        self.assertIn("showClarification(out)", self.source)

    def test_one_click_supplement_reuses_the_question_box_and_reruns(self):
        self.assertIn("function completeWithSlot", self.source)
        self.assertIn("$('question').value = (current + ' ' + value).trim();", self.source)
        self.assertIn("run();", self.source)


if __name__ == "__main__":
    unittest.main()
