# -*- coding: utf-8 -*-
"""WP1-a 冻结评测登记：一致性 / append-only / diff / 统一 schema 的单项测试。

不读上游模型、不改库：除 `LiveRegistryConsistencyTests` 需要只读元数据库外，
其余用例全部用内存中的合成题面 + 临时目录，模拟「题面被改动」而不触碰演示库。
"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_registry():
    path = ROOT / "data/evaluation/registry/registry.py"
    spec = importlib.util.spec_from_file_location("wp1a_registry_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load_registry()


def fake_case(case_id: str, question: str, *, sql: str | None = "SELECT 1",
              claims=None, label: str = "none") -> dict:
    """构造一个与 normalize_case() 同构的冻结体。"""
    return {
        "case_id": case_id,
        "question": question,
        "category": "测试",
        "difficulty": "easy",
        "expected_route": "sql",
        "gold": {"sql": sql, "claims": claims},
        "clarification_label": label,
        "failure_category": None,
        "data_version": "test-v1",
        "required_evidence": None,
    }


def good_report() -> dict:
    return {
        "schema_version": registry.REPORT_SCHEMA_VERSION,
        "suite": "nl2sql",
        "cases": [
            {"case_id": "c-1", "status": "completed", "sql": "SELECT 1", "rows": [[1]],
             "citations": None, "verdict": "pass", "failure_reason": None},
            {"case_id": "c-2", "status": "failed", "sql": None, "rows": None,
             "citations": None, "verdict": "fail", "failure_reason": "GOLD_MISMATCH"},
        ],
        "summary": {"total": 2, "passed": 1, "failed": 1, "refused": 0, "errors": 0,
                    "accuracy": 0.5, "refusal_rate": 0.0,
                    "failure_counts": {"GOLD_MISMATCH": 1}},
    }


class RegistryCheckTests(unittest.TestCase):
    """冻结一致性 + diff 明细（全部合成数据，不连库）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wp1a_registry_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cases = [fake_case("t-001", "有多少客户？", sql="SELECT count(*) FROM customers"),
                      fake_case("t-002", "退款多久到账？", sql=None, claims=["3个工作日"]),
                      fake_case("t-003", "销售额最高产品？", sql=None, label="needed")]

    def _write(self, payload: dict, name: str = "nl2sql_v1.json") -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_build_payload_and_check_agree(self):
        payload = registry.build_payload("nl2sql", self.cases, version="v1")
        self.assertEqual(payload["case_count"], 3)
        self.assertEqual(payload["data_version"], "test-v1")
        self.assertEqual(payload["suite_version"], "nl2sql-v1")
        self.assertEqual(registry.registry_digest(payload), payload["digest"])
        self.assertTrue(all(case["case_digest"] for case in payload["cases"]))
        path = self._write(payload)
        summary = registry.check_registry(path, cases=self.cases)
        self.assertEqual(summary["cases"], 3)
        self.assertFalse(summary["diff"]["divergent"])

    def test_check_fails_when_question_edited_and_reports_field_diff(self):
        payload = registry.build_payload("nl2sql", self.cases, version="v1")
        payload["cases"][0]["question"] = "被篡改的题面（模拟库内题面被改动）"
        path = self._write(payload)
        with self.assertRaises(registry.RegistryMismatch) as caught:
            registry.check_registry(path, cases=self.cases)
        diff = caught.exception.diff
        changed = {(item["case_id"], item["field"]): item for item in diff["changed"]}
        self.assertIn(("t-001", "question"), changed)
        self.assertEqual(changed[("t-001", "question")]["registry"], "被篡改的题面（模拟库内题面被改动）")
        self.assertEqual(changed[("t-001", "question")]["database"], "有多少客户？")
        text = str(caught.exception)
        self.assertIn("t-001.question", text)
        self.assertIn("字段不一致", text)

    def test_check_reports_missing_and_extra_cases(self):
        payload = registry.build_payload("nl2sql", self.cases[:2], version="v1")
        path = self._write(payload)
        with self.assertRaises(registry.RegistryMismatch) as caught:
            registry.check_registry(path, cases=self.cases)
        diff = caught.exception.diff
        self.assertEqual(diff["extra_in_db"], ["t-003"])
        self.assertEqual(diff["missing_in_db"], [])
        self.assertEqual(diff["registry_cases"], 2)
        self.assertEqual(diff["database_cases"], 3)

    def test_check_detects_tampered_file_digest(self):
        payload = registry.build_payload("nl2sql", self.cases, version="v1")
        payload["cases"][0]["gold"]["sql"] = "SELECT 2"
        path = self._write(payload)
        with self.assertRaises(registry.RegistryMismatch) as caught:
            registry.check_registry(path, cases=self.cases)
        self.assertFalse(caught.exception.diff["digest_ok"])
        self.assertIn("digest", str(caught.exception))

    def test_digest_ignores_created_at_and_commit(self):
        first = registry.build_payload("nl2sql", self.cases, created_at="2026-01-01T00:00:00Z",
                                       commit="aaaaaaa")
        second = registry.build_payload("nl2sql", self.cases, created_at="2026-10-10T00:00:00Z",
                                        commit="bbbbbbb")
        self.assertEqual(first["digest"], second["digest"])


class AppendOnlyTests(unittest.TestCase):
    """已发布版本只增不改：重复冻结必须被拒绝，并提示用新版本号。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wp1a_appendonly_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cases = [fake_case("t-001", "有多少客户？")]

    def test_freeze_refuses_to_overwrite_existing_version(self):
        path, payload = registry.freeze("nl2sql", version="v1", out_dir=self.tmp, cases=self.cases)
        self.assertTrue(path.exists())
        before = path.read_text(encoding="utf-8")
        with self.assertRaises(registry.RegistryExistsError) as caught:
            registry.freeze("nl2sql", version="v1", out_dir=self.tmp,
                            cases=[fake_case("t-001", "改过的题面")])
        self.assertIn("--version v2", str(caught.exception))
        self.assertEqual(path.read_text(encoding="utf-8"), before, "旧版本文件不得被改写")

    def test_new_version_is_allowed_and_keeps_old_one(self):
        old_path, old = registry.freeze("nl2sql", version="v1", out_dir=self.tmp, cases=self.cases)
        new_path, new = registry.freeze("nl2sql", version="v2", out_dir=self.tmp,
                                        cases=[fake_case("t-001", "改过的题面")])
        self.assertNotEqual(old_path, new_path)
        self.assertEqual(old["suite_version"], "nl2sql-v1")
        self.assertEqual(new["suite_version"], "nl2sql-v2")
        self.assertTrue(old_path.exists() and new_path.exists())


class ReportSchemaTests(unittest.TestCase):
    """统一报告 schema：缺字段要报错，零分母必须是 N/A。"""

    def test_wellformed_report_passes(self):
        self.assertEqual(registry.validate_report(good_report()), [])

    def test_missing_case_field_is_reported(self):
        payload = good_report()
        del payload["cases"][0]["verdict"]
        problems = registry.validate_report(payload)
        self.assertTrue(any("缺少字段 verdict" in item for item in problems), problems)

    def test_missing_summary_is_reported(self):
        payload = good_report()
        del payload["summary"]
        problems = registry.validate_report(payload)
        self.assertTrue(any("summary" in item for item in problems), problems)

    def test_fail_without_failure_reason_is_reported(self):
        payload = good_report()
        payload["cases"][1]["failure_reason"] = None
        problems = registry.validate_report(payload)
        self.assertTrue(any("failure_reason" in item for item in problems), problems)

    def test_inconsistent_counts_are_reported(self):
        payload = good_report()
        payload["summary"]["passed"] = 2
        problems = registry.validate_report(payload)
        self.assertTrue(any("不自洽" in item for item in problems), problems)

    def test_zero_denominator_reports_na_not_zero(self):
        # 纯函数层面：零分母返回 "N/A"
        self.assertEqual(registry.ratio(0, 0), "N/A")
        self.assertEqual(registry.ratio(3, 0), "N/A")
        self.assertEqual(registry.ratio(1, 4), 0.25)
        # schema 层面：total=0 却报 accuracy=0 必须被拒
        payload = good_report()
        payload["cases"] = []
        payload["summary"] = {"total": 0, "passed": 0, "failed": 0, "refused": 0, "errors": 0,
                              "accuracy": 0, "refusal_rate": 0, "failure_counts": {}}
        problems = registry.validate_report(payload)
        self.assertTrue(any("零分母必须报 'N/A'" in item for item in problems), problems)
        # 正确写法：零分母报 N/A，即通过
        payload["summary"]["accuracy"] = "N/A"
        payload["summary"]["refusal_rate"] = "N/A"
        self.assertEqual(registry.validate_report(payload), [])

    def test_na_with_nonzero_denominator_is_reported(self):
        payload = good_report()
        payload["summary"]["accuracy"] = "N/A"
        problems = registry.validate_report(payload)
        self.assertTrue(any("非零，不能报 'N/A'" in item for item in problems), problems)


class LiveRegistryConsistencyTests(unittest.TestCase):
    """仓内已发布的登记文件必须与只读元数据库里的题面逐字段一致。"""

    EXPECTED = {"nl2sql": 14, "rag": 12, "robustness": 4}

    def test_published_registry_files_match_database(self):
        try:
            registry.fetch_cases("nl2sql")
        except Exception as error:  # 无法连库时如实跳过，不伪装成通过
            self.skipTest(f"元数据库不可用：{error}")
        checked = 0
        for suite in registry.SUITES:
            paths = sorted(registry.REGISTRY_DIR.glob(f"{suite}_v*.json"))
            self.assertTrue(paths, f"{suite} 缺少登记文件：{registry.REGISTRY_DIR}")
            path = paths[-1]
            summary = registry.check_registry(path)
            self.assertEqual(summary["cases"], self.EXPECTED[suite],
                             f"{suite} 冻结题数应为 {self.EXPECTED[suite]}")
            checked += 1
        self.assertEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()
