"""B8 文档复杂度：维度边界、分数单调性、策略映射确定性、期望失败保留。

边界用真实 PDF（临时目录里用 pymupdf 现造空文档 / 全图页 / 无标题页 / 有标题页）验证，
不用假数据绕过观测器：``observe_pdf`` 是给摄取入口调用的那条真实路径。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datamind import complexity
from scripts.eval_complexity import expectation_mismatches

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "data/knowledge_base" / "f_net.png"


def _dimension(result: dict, key: str) -> dict:
    return next(dimension for dimension in result["dimensions"] if dimension["id"] == key)


def _base_observation(**overrides) -> dict:
    observation = {"page_count": 2, "char_count": 2000, "image_area_ratio": 0.0,
                   "table_count": 0, "continued_table_count": 0, "formula_chars": 0,
                   "formula_candidates": 0, "rotation_skew_ratio": 0.0,
                   "ocr_confidences": [0.95, 0.96], "text_blocks": 20,
                   "heading_level_count": 3, "font_switch_ratio": 0.0,
                   "distinct_font_count": 1, "invalid_unicode_ratio": 0.0,
                   "scanned_page_ratio": 0.0}
    observation.update(overrides)
    return observation


class DimensionBoundaryTest(unittest.TestCase):
    def test_empty_observation_reports_missing_ocr_and_low_score(self):
        result = complexity.score({})
        self.assertFalse(_dimension(result, "ocr_confidence")["available"])
        self.assertFalse(_dimension(result, "image_area_ratio")["available"])
        self.assertIn("ocr_confidence", result["missing_dimensions"])
        self.assertTrue(result["renormalized"])
        self.assertEqual(result["band"], "low")
        # 空文档没有任何文本块 → 标题层级按规则记完全缺失（=1.0），这条规则必须可见。
        heading = _dimension(result, "heading_hierarchy_missing")
        self.assertEqual(heading["score"], 1.0)
        self.assertEqual(result["complexity_score"],
                         round(heading["contribution_normalized"], 2))

    def test_all_image_page_scores_full_on_image_and_scanned_dimensions(self):
        result = complexity.score(_base_observation(
            char_count=0, text_blocks=0, heading_level_count=0, font_switch_ratio=None,
            distinct_font_count=None, image_area_ratio=1.0, scanned_page_ratio=1.0,
            ocr_confidences=None))
        self.assertEqual(_dimension(result, "image_area_ratio")["score"], 1.0)
        self.assertEqual(_dimension(result, "scanned_page_ratio")["score"], 1.0)
        self.assertNotEqual(result["band"], "low")
        policy = complexity.policy_for(result, _base_observation(
            image_area_ratio=1.0, scanned_page_ratio=1.0, char_count=0))
        self.assertTrue(policy["ocr_enabled"])
        self.assertTrue(policy["ocr_scanned_trigger"])

    def test_heading_rule_is_inverted_between_zero_and_three_levels(self):
        missing = complexity.score(_base_observation(heading_level_count=0))
        present = complexity.score(_base_observation(heading_level_count=3))
        self.assertEqual(_dimension(missing, "heading_hierarchy_missing")["score"], 1.0)
        self.assertEqual(_dimension(present, "heading_hierarchy_missing")["score"], 0.0)
        self.assertGreater(missing["complexity_score"], present["complexity_score"])

    def test_ocr_confidence_distribution_uses_mean_and_low_share(self):
        good = complexity.score(_base_observation(ocr_confidences=[0.99] * 10))
        bad = complexity.score(_base_observation(ocr_confidences=[0.4] * 10))
        self.assertLess(_dimension(good, "ocr_confidence")["score"],
                        _dimension(bad, "ocr_confidence")["score"])
        self.assertEqual(_dimension(bad, "ocr_confidence")["raw"]["low_confidence_share"], 1.0)

    def test_synthetic_boundary_documents_through_the_real_observer(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            empty_path = directory / "blank.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(empty_path)
            empty = complexity.observe_pdf(empty_path)
            self.assertEqual(empty["page_count"], 1)
            self.assertEqual(empty["char_count"], 0)
            self.assertEqual(empty["scanned_page_ratio"], 1.0)
            self.assertEqual(empty["heading_level_count"], 0)

            image_path = directory / "image_only.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_image(pymupdf.Rect(0, 0, 200, 200), filename=str(IMAGE))
                doc.save(image_path)
            image_only = complexity.observe_pdf(image_path)
            self.assertEqual(image_only["scanned_page_ratio"], 1.0)
            self.assertGreater(image_only["image_area_ratio"], 0.0)
            self.assertEqual(image_only["heading_level_count"], 0)

            plain_path = directory / "plain.pdf"
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 72), "body text without headings")
                doc.save(plain_path)
            plain = complexity.observe_pdf(plain_path)
            self.assertEqual(plain["heading_level_count"], 0)

            headed_path = directory / "headed.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 72), "Big Title", fontsize=20)
                page.insert_text((72, 120), "body text", fontsize=10)
                doc.save(headed_path)
            headed = complexity.observe_pdf(headed_path)
            self.assertEqual(headed["heading_level_count"], 1)


class ScoreMonotonicityTest(unittest.TestCase):
    def test_more_chars_never_lower_the_score(self):
        previous = -1.0
        for char_count in (0, 2000, 8000, 20000, 40000, 80000):
            result = complexity.score(_base_observation(char_count=char_count))
            self.assertGreaterEqual(result["complexity_score"], previous)
            previous = result["complexity_score"]

    def test_more_tables_and_continued_tables_raise_the_score(self):
        flat = complexity.score(_base_observation(table_count=0, continued_table_count=0))
        many = complexity.score(_base_observation(table_count=12, continued_table_count=3))
        self.assertGreater(many["complexity_score"], flat["complexity_score"])
        partial = complexity.score(_base_observation(table_count=6, continued_table_count=0))
        self.assertTrue(flat["complexity_score"] < partial["complexity_score"]
                        < many["complexity_score"])

    def test_more_scanned_pages_raise_the_score(self):
        scores = [complexity.score(_base_observation(
            scanned_page_ratio=ratio, image_area_ratio=ratio))["complexity_score"]
            for ratio in (0.0, 0.25, 0.5, 1.0)]
        self.assertEqual(scores, sorted(scores))
        self.assertLess(scores[0], scores[-1])

    def test_saturation_means_no_further_growth(self):
        saturated = complexity.score(_base_observation(char_count=40000, page_count=40))
        beyond = complexity.score(_base_observation(char_count=400000, page_count=400))
        self.assertEqual(saturated["complexity_score"], beyond["complexity_score"])

    def test_score_is_deterministic_for_the_same_observation(self):
        observation = _base_observation(table_count=3, ocr_confidences=[0.6, 0.9])
        self.assertEqual(json.dumps(complexity.score(observation), sort_keys=True),
                         json.dumps(complexity.score(observation), sort_keys=True))
        self.assertEqual(complexity.assess_ohr_record({"gt_text": "x" * 100})["policy"],
                         complexity.assess_ohr_record({"gt_text": "x" * 100})["policy"])


class PolicyMappingTest(unittest.TestCase):
    def _score(self, band: str) -> dict:
        return {"complexity_score": 0.0, "band": band}

    def test_band_boundaries_are_fixed_and_monotone(self):
        boundaries = [(0.0, "low"), (24.99, "low"), (25.0, "medium"), (49.99, "medium"),
                      (50.0, "high"), (74.99, "high"), (75.0, "very_high"), (100.0, "very_high")]
        for score_value, expected in boundaries:
            self.assertEqual(complexity.band_for(score_value), expected, score_value)
        self.assertEqual([label for _, label in complexity.BANDS],
                         ["low", "medium", "high", "very_high"])
        self.assertEqual(complexity.band_for(-1.0), "low")

    def test_policy_is_a_pure_function_of_band_and_scan_ratio(self):
        for band in ("low", "medium", "high", "very_high"):
            first = complexity.policy_for(self._score(band), {"scanned_page_ratio": 1.0})
            second = complexity.policy_for(self._score(band), {"scanned_page_ratio": 1.0})
            self.assertEqual(first, second)
            self.assertEqual(first["policy_version"], complexity.POLICY_VERSION)
            self.assertIn(band, first["reasons"][0])

    def test_ocr_switch_needs_a_scanned_page_even_when_the_band_wants_ocr(self):
        digital = complexity.policy_for(self._score("high"), {"scanned_page_ratio": 0.0})
        scanned = complexity.policy_for(self._score("high"), {"scanned_page_ratio": 1.0})
        self.assertFalse(digital["ocr_enabled"])
        self.assertTrue(scanned["ocr_enabled"])

    def test_scanned_pages_trigger_ocr_below_the_high_band(self):
        policy = complexity.policy_for(self._score("medium"), {"scanned_page_ratio": 0.31})
        self.assertTrue(policy["ocr_enabled"])
        self.assertTrue(policy["ocr_scanned_trigger"])
        just_below = complexity.policy_for(self._score("medium"), {"scanned_page_ratio": 0.29})
        self.assertFalse(just_below["ocr_enabled"])

    def test_budget_and_review_grow_with_the_band(self):
        budgets = [complexity.policy_for(self._score(band), {"scanned_page_ratio": 0.0})
                   for band in ("low", "medium", "high", "very_high")]
        self.assertEqual([policy["max_blocks"] for policy in budgets], sorted(
            policy["max_blocks"] for policy in budgets))
        self.assertEqual([policy["retrieval_candidates"] for policy in budgets], sorted(
            policy["retrieval_candidates"] for policy in budgets))
        self.assertFalse(budgets[0]["require_human_review"])
        self.assertTrue(budgets[-1]["require_human_review"])

    def test_weight_table_sums_to_one(self):
        self.assertAlmostEqual(sum(complexity.DIMENSION_WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(len(complexity.DIMENSIONS), 9)
        self.assertEqual(sorted(complexity.DIMENSION_WEIGHTS),
                         sorted(key for key, _, _ in complexity.DIMENSIONS))


class ExpectationFailureRetentionTest(unittest.TestCase):
    def test_band_and_policy_mismatches_are_both_retained(self):
        entry = {"doc_id": "demo", "expected_band": "high",
                 "expected_policy": {"ocr_enabled": True}}
        result = {"band": "low", "complexity_score": 12.5}
        mismatches = expectation_mismatches(entry, result, {"ocr_enabled": False})
        self.assertEqual([item["kind"] for item in mismatches],
                         ["BAND_MISMATCH", "POLICY_MISMATCH"])
        self.assertEqual(mismatches[0]["expected"], "high")
        self.assertEqual(mismatches[0]["observed"], "low")
        self.assertEqual(mismatches[0]["score"], 12.5)
        self.assertEqual(mismatches[1]["observed"], {"ocr_enabled": False})

    def test_a_matching_expectation_produces_no_mismatch(self):
        entry = {"doc_id": "demo", "expected_band": "low",
                 "expected_policy": {"ocr_enabled": False}}
        self.assertEqual(expectation_mismatches(entry, {"band": "low",
                                                        "complexity_score": 3.0},
                                                {"ocr_enabled": False}), [])

    def test_the_frozen_suite_still_carries_its_expectations(self):
        suite = json.loads((ROOT / "data/evaluation/complexity_v1.json").read_text(
            encoding="utf-8"))
        self.assertGreaterEqual(len(suite["documents"]), 20)
        for entry in suite["documents"]:
            self.assertIn(entry["expected_band"], ("low", "medium", "high", "very_high"))
            self.assertIn("ocr_enabled", entry["expected_policy"])
            self.assertTrue((ROOT / entry["path"]).is_file(), entry["path"])


if __name__ == "__main__":
    unittest.main()
