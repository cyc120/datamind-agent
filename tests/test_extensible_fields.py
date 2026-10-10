# -*- coding: utf-8 -*-
"""WP5a / B2：设备维保工单领域的生成器确定性、标签冻结、断言算术与原领域回归。

运行方式（仓库纪律：只跑本文件）
    cd "/Users/caicai/DataMind Agent" && .venv/bin/python scripts/test_isolated.py --pattern test_extensible_fields.py

覆盖点
------
1. 生成器逐字节确定性（含跨秒重跑，抓 zip/core.xml 时间戳这类隐性非确定性）；
2. 标签冻结校验：材料或标签被篡改 → `build_suite.py --check` 退出码非 0；
3. 字段断言的算术自洽（分子分母、分维度求和、失败原因分类）；
4. 三档对照的分母不移动、声明式臂极差 ≤5pp；
5. 非法取值不得变成字段值（拒绝而非补值）；
6. 原领域回归不受影响（只读计数 + 相关引擎文件零改动）。
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUITE_DIR = ROOT / "data/evaluation/extensible_field_v1"
BUILD_SUITE = SUITE_DIR / "build_suite.py"


from scripts.eval_extensible_fields import (  # noqa: E402  (需要 ROOT 已在 sys.path)
    ORIGINAL_DOMAIN_BASELINE, evaluate, extract, label_index,
    normalize_value, original_domain_guard, score, stub_predictions,
)


def run_check(suite_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILD_SUITE), "--check", "--suite-dir", str(suite_dir)],
                          capture_output=True, text=True)


def run_write(suite_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILD_SUITE), "--write", "--suite-dir", str(suite_dir)],
                          capture_output=True, text=True)


class GeneratorDeterminismTest(unittest.TestCase):
    def test_two_writes_separated_in_time_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.assertEqual(run_write(Path(first)).returncode, 0)
            time.sleep(1.2)  # 跨秒：zip 条目时间戳与 openpyxl 的 core.modified 都会变
            self.assertEqual(run_write(Path(second)).returncode, 0)
            manifest = json.loads((SUITE_DIR / "manifest.json").read_bytes())
            for material in manifest["materials"]:
                left = (Path(first) / material["file"]).read_bytes()
                right = (Path(second) / material["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(left).hexdigest(), hashlib.sha256(right).hexdigest(),
                                 f"{material['file']} 两次生成不一致（非确定性）")
                self.assertEqual(hashlib.sha256(left).hexdigest(), material["sha256"],
                                 f"{material['file']} 与 manifest 冻结摘要不一致")
            self.assertEqual((Path(first) / "labels.json").read_bytes(),
                             (Path(second) / "labels.json").read_bytes())
            self.assertEqual((Path(first) / "manifest.json").read_bytes(),
                             (Path(second) / "manifest.json").read_bytes())


class FrozenLabelTest(unittest.TestCase):
    def test_pristine_suite_passes_check(self):
        completed = run_check(SUITE_DIR)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("check ok", completed.stdout)

    def _copied_suite(self, destination: Path) -> Path:
        shutil.copytree(SUITE_DIR, destination / "suite")
        return destination / "suite"

    def test_tampered_material_fails_check(self):
        with tempfile.TemporaryDirectory() as temp:
            suite = self._copied_suite(Path(temp))
            target = suite / "materials/m01_txt_labeled.txt"
            target.write_bytes(target.read_bytes().replace("EQ-001".encode(), "EQ-901".encode()))
            completed = run_check(suite)
            self.assertNotEqual(completed.returncode, 0, "篡改材料后 --check 必须失败")
            self.assertIn("MATERIAL_TAMPERED", completed.stdout)

    def test_tampered_labels_fail_check(self):
        with tempfile.TemporaryDirectory() as temp:
            suite = self._copied_suite(Path(temp))
            labels_path = suite / "labels.json"
            payload = json.loads(labels_path.read_bytes())
            payload["assertions"][0]["expected"] = "EQ-999"
            labels_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            completed = run_check(suite)
            self.assertNotEqual(completed.returncode, 0, "篡改标签后 --check 必须失败")
            self.assertIn("LABELS_DRIFT", completed.stdout)

    def test_tampered_manifest_digest_fails_check(self):
        with tempfile.TemporaryDirectory() as temp:
            suite = self._copied_suite(Path(temp))
            manifest_path = suite / "manifest.json"
            payload = json.loads(manifest_path.read_bytes())
            payload["materials"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            completed = run_check(suite)
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue("MANIFEST_DRIFT" in completed.stdout or "MATERIAL_DIGEST_DRIFT" in completed.stdout)


class AssertionArithmeticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluate(SUITE_DIR, "host=127.0.0.1 port=5432 dbname=datamind_meta "
                                         "user=readonly_user password=readonly_dev")
        cls.labels = json.loads((SUITE_DIR / "labels.json").read_bytes())

    def test_numerator_denominator_and_breakdown_sums(self):
        total = len(self.labels["assertions"])
        declarative = self.report["results"]["declarative"]
        self.assertEqual(declarative["total"], total)
        self.assertEqual(declarative["passed"] + declarative["failed"], total)
        for dimension in ("per_field", "per_material", "per_format"):
            passed = sum(item["passed"] for item in self.report[dimension].values())
            counted = sum(item["total"] for item in self.report[dimension].values())
            self.assertEqual(counted, total, f"{dimension} 分母与断言总数不一致")
            self.assertEqual(passed, declarative["passed"], f"{dimension} 分子与总分子不一致")

    def test_failure_list_is_complete_and_classified(self):
        failures = self.report["failures"]
        self.assertEqual(len(failures), self.report["results"]["declarative"]["failed"])
        self.assertEqual(len({f["assertion_id"] for f in failures}), len(failures))
        self.assertTrue(all(f["reason"] in {"missed_value", "value_mismatch", "fabricated_value"}
                            for f in failures))
        self.assertEqual(self.report["null_expected"]["count"], 3,
                         "冻结集应恰好有 3 条“材料里没有该字段”的期望")
        self.assertEqual(self.report["null_expected"]["passed"], 3, "缺省字段不得被补值")

    def test_evidence_and_rejection_counters_are_sane(self):
        evidence = self.report["evidence_coverage"]
        self.assertLessEqual(evidence["with_locator"], evidence["with_value"])
        self.assertEqual(evidence["assertion_total"], len(self.labels["assertions"]))
        rejections = self.report["invalid_value_rejections"]
        self.assertEqual(rejections["count"], len(rejections["samples"]) if rejections["count"] <= 10
                         else 10)

    def test_assertion_denominator_does_not_move_across_tiers(self):
        totals = {tier["tier"]: tier["declarative"]["total"] for tier in self.report["results"]["tiers"]}
        self.assertEqual(set(totals.values()), {len(self.labels["assertions"])},
                         "三档对照不得改变分母")

    def test_declarative_arm_flat_and_stub_depends_on_examples(self):
        tiers = self.report["results"]["tiers"]
        declarative = [tier["declarative"]["rate"] for tier in tiers]
        stub = [tier["stub_fewshot"]["rate"] for tier in tiers]
        self.assertLessEqual(max(declarative) - min(declarative), 0.05,
                             "声明式臂三档极差应 ≤5 个百分点（不靠示例堆）")
        self.assertEqual(self.report["results"]["tier_spread_pp"]["declarative"], 0.0)
        self.assertGreater(stub[-1], stub[0], "对照组：示例应当只影响 stub 臂")
        self.assertLessEqual(stub[0], 0.05)

    def test_score_is_exact_match_only(self):
        assertions = [{"assertion_id": "a", "material_id": "m", "record_key": "k",
                       "field": "f", "expected": "1"}]
        exact = {"m\u0000k\u0000f": {"value": "1", "locator": "l"}}
        near = {"m\u0000k\u0000f": {"value": "1.0", "locator": "l"}}
        self.assertEqual(score(assertions, exact)["passed"], 1)
        self.assertEqual(score(assertions, near)["passed"], 0)


class ExtractionBoundaryTest(unittest.TestCase):
    def test_invalid_value_is_rejected_not_fabricated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.txt"
            path.write_text("设备编号：EQ-777\n备件号：SP-99X\n", encoding="utf-8")
            material = {"material_id": "tmp", "mode": "text_blocks", "format": "txt",
                        "kv_separator": "：", "record_separator": ""}
            fields = [{"name": "equipment_id", "label": "设备编号", "aliases": [],
                       "pattern": r"^EQ-\d{3}$"},
                      {"name": "spare_part_no", "label": "备件号", "aliases": [],
                       "pattern": r"^SP-\d{3}$"}]
            values, rejected = extract(material, path, label_index(fields), fields, "equipment_id")
            self.assertIn("tmp\u0000EQ-777\u0000equipment_id", values)
            self.assertTrue(all("spare_part_no" not in key for key in values))
            self.assertEqual([item["reason"] for item in rejected], ["value_failed_declared_pattern"])

    def test_missing_field_stays_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.txt"
            path.write_text("设备编号：EQ-778\n", encoding="utf-8")
            material = {"material_id": "tmp", "mode": "text_blocks", "format": "txt",
                        "kv_separator": "：", "record_separator": ""}
            fields = [{"name": "equipment_id", "label": "设备编号", "aliases": [],
                       "pattern": r"^EQ-\d{3}$"},
                      {"name": "labor_hours", "label": "工时", "aliases": [], "pattern": r"^\d+(\.\d+)?$"}]
            values, _ = extract(material, path, label_index(fields), fields, "equipment_id")
            self.assertEqual(sorted(key.split("\u0000")[-1] for key in values), ["equipment_id"])

    def test_zero_example_stub_abstains(self):
        manifest = json.loads((SUITE_DIR / "manifest.json").read_bytes())
        labels = json.loads((SUITE_DIR / "labels.json").read_bytes())
        stub = stub_predictions(manifest, SUITE_DIR, [])
        self.assertEqual(stub["predictions"], {})
        expected_nulls = [a for a in labels["assertions"] if a["expected"] is None]
        self.assertEqual(score(labels["assertions"], stub["predictions"])["passed"], len(expected_nulls))

    def test_normalize_value_is_generic_only(self):
        self.assertEqual(normalize_value(" 2.5小时 "), "2.5")
        self.assertEqual(normalize_value("EQ-001"), "EQ-001")
        self.assertIsNone(normalize_value("   "))


class OriginalDomainRegressionTest(unittest.TestCase):
    def test_registry_attribute_contract_is_reused_without_ddl(self):
        manifest = json.loads((SUITE_DIR / "manifest.json").read_bytes())
        registry_attributes = {"label", "aliases", "enum_map", "semantic_role", "sensitivity",
                               "unit", "pattern", "value_type", "name", "required"}
        for field in manifest["fields"]:
            self.assertTrue(set(field) <= registry_attributes,
                            f"字段声明出现未登记的属性：{set(field) - registry_attributes}")
            self.assertIn(field["semantic_role"], {"id", "dimension", "time", "metric", "text"})
        contract = manifest["registry_contract"]
        self.assertEqual((contract["ddl_changes"], contract["migrations_added"],
                          contract["registry_writes"]), (0, 0, 0))
        # 真意图：**本功能（可扩展要素提取）自身不引入 DDL/迁移**。
        # 原断言写成「`git status -- db/migrations` 必须为空」，等于要求整个工作区在本功能
        # 之外也不得出现任何未提交迁移；同轮其它**已批准**的工作包合法新增迁移
        # （WP2 的 019_evidence_graph.sql）就会把它染红，而那与本功能无关。
        # 因此改为**显式白名单**表达，且不是永真：
        #   ① 白名单外出现任何迁移条目 → 失败（未经批准的 DDL 一律拦截）；
        #   ② 白名单内也只允许「新增」（??），改动/删除既有迁移 → 失败；
        #   ③ 白名单只能由协调者按轮次显式批准后扩充，本功能不得自行添加。
        approved_new_migrations = {"019_evidence_graph.sql"}  # WP2 证据图，本轮已批准
        status = subprocess.run(["git", "status", "--porcelain", "--", "db/migrations"],
                                cwd=str(ROOT), capture_output=True, text=True)
        for line in (line for line in status.stdout.splitlines() if line.strip()):
            code, path = line[:2], line[3:].strip()
            self.assertEqual(code, "??",
                             f"本 WP 不得改动/删除既有迁移，只允许已批准的新增：{line}")
            self.assertIn(Path(path).name, approved_new_migrations,
                          f"出现未经批准的迁移文件（本功能自身不得引入 DDL）：{line}")

    def test_engine_file_untouched_and_domain_counts_unchanged(self):
        numstat = subprocess.run(["git", "diff", "--numstat", "HEAD", "--", "datamind/catalog.py"],
                                 cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(numstat.stdout.strip(), "", "datamind/catalog.py 不应有改动")
        guard = original_domain_guard("host=127.0.0.1 port=5432 dbname=datamind_meta "
                                      "user=readonly_user password=readonly_dev")
        self.assertEqual(guard["status"], "ok", f"只读守卫不可用：{guard.get('error')}")
        self.assertEqual(guard["actual"], ORIGINAL_DOMAIN_BASELINE,
                         "原领域元数据计数发生变化，新增领域必须是非破坏性的")


if __name__ == "__main__":
    unittest.main()
