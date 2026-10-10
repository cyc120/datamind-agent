# -*- coding: utf-8 -*-
"""WP8「效率与伸缩性证明」的单项测试。

覆盖五件事，全部离线（不连数据库、不调上游模型、不写仓内报告）：

1. **生成器确定性**：`scripts/build_scale_data.py` 同一档位重复生成逐字节相同、`--check`
   能抓到篡改/缺文件、小档是大档前缀（取值域稳定的表）、一档之内外键自洽。
2. **指标算术**：最近秩分位、零分母 `N/A`、空样本全为 `None`（不是 0）。
3. **并发统计口径**：成功率的分子分母、失败留在分母里、峰值内存 max/sum。
4. **模型段必须留空**：模型/首字/完整响应的 p50/p95 必须是 `None` 而不是 `0`，且带原因。
5. **既有 eval_scale 行为不变**：默认 `--kind v1`、报告路径 `reports/scale_v1.json`、
   子进程 `cad`/`retrieval` 分派文本、既有报告字段集合（用桩 measure 复跑真实 `run_v1`），
   以及真实 `cad` 子进程返回的字段。
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load("wp8_build_scale_data_under_test", ROOT / "scripts/build_scale_data.py")
scale = _load("wp8_eval_scale_under_test", ROOT / "scripts/eval_scale.py")

SMALL_TIERS = (200, 400)


class GeneratorDeterminismTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.first = self.root / "first"
        self.second = self.root / "second"

    def tearDown(self):
        self._tmp.cleanup()

    def test_regeneration_is_byte_identical(self):
        left = generator.generate(self.first, SMALL_TIERS)
        right = generator.generate(self.second, SMALL_TIERS)
        self.assertEqual(left["files"], right["files"])
        for relative, digest in left["files"].items():
            self.assertEqual(hashlib.sha256((self.first / relative).read_bytes()).hexdigest(),
                             digest)
            self.assertEqual((self.first / relative).read_bytes(),
                             (self.second / relative).read_bytes())

    def test_manifest_counts_match_the_tier(self):
        manifest = generator.generate(self.first, SMALL_TIERS)
        for tier in SMALL_TIERS:
            counts = generator.counts_for(tier)
            for table in generator.TABLE_ORDER:
                self.assertEqual(manifest["tiers"][str(tier)]["tables"][table]["rows"],
                                 counts[table])

    def test_tier_row_counts_are_what_the_audit_asks_for(self):
        self.assertEqual(generator.counts_for(1000),
                         {"products": 20, "customers": 50, "orders": 200, "order_items": 1000})
        self.assertEqual(generator.counts_for(10000)["order_items"], 10000)
        self.assertEqual(generator.counts_for(100000)["order_items"], 100000)
        self.assertEqual(generator.TIERS, (1000, 10000, 100000))

    def test_check_passes_on_a_fresh_directory(self):
        generator.generate(self.first, SMALL_TIERS)
        self.assertEqual(generator.verify(self.first, SMALL_TIERS), [])

    def test_check_detects_a_tampered_file(self):
        generator.generate(self.first, SMALL_TIERS)
        target = self.first / "200" / "order_items.tsv"
        target.write_text(target.read_text(encoding="utf-8") + "999\t1\t1\t1\t1.00\t0.000\n",
                          encoding="utf-8")
        problems = generator.verify(self.first, SMALL_TIERS)
        self.assertTrue(problems, "改了内容却报通过")
        self.assertTrue(any("order_items" in problem for problem in problems))

    def test_check_detects_a_missing_manifest(self):
        self.first.mkdir(parents=True, exist_ok=True)
        problems = generator.verify(self.first, SMALL_TIERS)
        self.assertTrue(problems)
        self.assertTrue(any("manifest" in problem for problem in problems))

    def test_small_tier_is_a_prefix_of_the_larger_tier(self):
        generator.generate(self.first, SMALL_TIERS)
        for table in generator.PREFIX_STABLE_TABLES:
            small = (self.first / "200" / f"{table}.tsv").read_text(encoding="utf-8").splitlines()
            large = (self.first / "400" / f"{table}.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(large[:len(small)], small, f"{table} 不再是前缀")
        self.assertNotIn("orders", generator.PREFIX_STABLE_TABLES,
                         "orders.customer_id 取值域随客户数变化，不该被当成前缀稳定的表")

    def test_foreign_keys_hold_inside_one_tier(self):
        """回归：曾经从最大档切片，1 千档的订单会引用 1 万档才存在的 customer_id。"""
        generator.generate(self.first, SMALL_TIERS)
        for tier in SMALL_TIERS:
            directory = self.first / str(tier)
            customers = {int(line.split("\t")[0])
                         for line in (directory / "customers.tsv").read_text().splitlines()}
            products = {int(line.split("\t")[0])
                        for line in (directory / "products.tsv").read_text().splitlines()}
            orders = {}
            for line in (directory / "orders.tsv").read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                orders[int(parts[0])] = int(parts[1])
                self.assertIn(int(parts[1]), customers, f"{tier} 档订单引用了不存在的客户")
            for line in (directory / "order_items.tsv").read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                self.assertIn(int(parts[1]), orders, f"{tier} 档明细引用了不存在的订单")
                self.assertIn(int(parts[2]), products, f"{tier} 档明细引用了不存在的产品")

    def test_clean_removes_only_its_own_generated_directory(self):
        scratch = ROOT / "tmp" / f"wp8_test_clean_{uuid.uuid4().hex[:8]}"
        try:
            generator.generate(scratch, (200,))
            self.assertTrue((scratch / "manifest.json").is_file())
            generator.clean(scratch)
            self.assertFalse(scratch.exists())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_clean_refuses_a_directory_without_our_manifest(self):
        scratch = ROOT / "tmp" / f"wp8_test_clean_{uuid.uuid4().hex[:8]}"
        try:
            scratch.mkdir(parents=True, exist_ok=True)
            (scratch / "unrelated.txt").write_text("not ours", encoding="utf-8")
            with self.assertRaises(SystemExit):
                generator.clean(scratch)
            self.assertTrue(scratch.exists())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_clean_refuses_a_path_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "manifest.json").write_text('{"marker": "datamind-scale-probe"}',
                                                           encoding="utf-8")
            with self.assertRaises(SystemExit):
                generator.clean(Path(directory), force=True)


class MetricArithmeticTests(unittest.TestCase):
    def test_percentile_of_an_empty_sample_is_none(self):
        self.assertIsNone(scale.percentile([], 0.5))
        self.assertIsNone(scale.percentile([], 0.95))

    def test_percentile_is_nearest_rank(self):
        self.assertEqual(scale.percentile([10.0, 20.0, 30.0], 0.5), 20.0)
        self.assertEqual(scale.percentile([10.0, 20.0, 30.0], 0.95), 30.0)
        self.assertEqual(scale.percentile([7.5], 0.95), 7.5)

    def test_summarize_of_an_empty_sample_keeps_none_not_zero(self):
        summary = scale.summarize([])
        self.assertEqual(summary["n"], 0)
        for key in ("p50_ms", "p95_ms", "min_ms", "max_ms"):
            self.assertIsNone(summary[key])
            self.assertNotEqual(summary[key], 0)
        self.assertEqual(summary["samples_ms"], [])

    def test_ratio_reports_na_for_a_zero_denominator(self):
        self.assertEqual(scale.ratio(0, 0), "N/A")
        self.assertEqual(scale.ratio(5, 0), "N/A")
        self.assertEqual(scale.ratio(0, 10), 0.0)
        self.assertEqual(scale.ratio(3, 4), 0.75)

    def test_null_segment_has_no_numeric_zero(self):
        segment = scale.null_segment("no model")
        self.assertEqual(segment["n"], 0)
        self.assertIsNone(segment["p50_ms"])
        self.assertIsNone(segment["p95_ms"])
        self.assertEqual(segment["status"], "not_measured")
        self.assertEqual(segment["reason"], "no model")


class ModelSegmentTests(unittest.TestCase):
    def test_model_segments_are_null_not_zero(self):
        sections = scale.profile_sections({"route": [1.0, 2.0]})
        for name in ("model", "first_token", "complete_response"):
            self.assertIn(name, sections)
            self.assertIsNone(sections[name]["p50_ms"], f"{name} 不能拿 0 冒充")
            self.assertIsNone(sections[name]["p95_ms"])
            self.assertEqual(sections[name]["n"], 0)
            self.assertNotEqual(sections[name]["p50_ms"], 0)
            self.assertIsNone(sections[name]["cold_ms"], f"{name} 的冷启动也必须是 null")
            self.assertTrue(sections[name]["reason"])

    def test_the_reason_records_the_real_observation(self):
        self.assertIn("CSRF_REQUIRED", scale.MODEL_SEGMENT_REASON)
        self.assertIn("null", scale.MODEL_SEGMENT_REASON)

    def test_cold_is_the_first_call_and_warm_excludes_it(self):
        sections = scale.profile_sections({"route": [9.0, 1.0, 3.0]})
        self.assertEqual(sections["route"]["cold_ms"], 9.0)
        self.assertEqual(sections["route"]["n"], 2)
        # 最近秩法：n=2 时 p50 取第 ceil(0.5*2)=1 个（即排序后的最小值），p95 取最大值
        self.assertEqual(sections["route"]["p50_ms"], 1.0)
        self.assertEqual(sections["route"]["p95_ms"], 3.0)
        self.assertNotIn(9.0, sections["route"]["samples_ms"])

    def test_a_cold_only_segment_has_no_warm_statistics(self):
        sections = scale.profile_sections({"catalog_build": [20.0]})
        self.assertEqual(sections["catalog_build"]["cold_ms"], 20.0)
        self.assertEqual(sections["catalog_build"]["n"], 0)
        self.assertIsNone(sections["catalog_build"]["p50_ms"])


class ConcurrencyStatisticsTests(unittest.TestCase):
    def test_all_workers_succeed(self):
        results = [{"ok": True, "queries": 8, "elapsed_ms": 100, "peak_rss_bytes": 1000}
                   for _ in range(4)]
        point = scale.concurrency_point(4, 8, results, 2.0)
        self.assertEqual(point["requests_planned"], 32)
        self.assertEqual(point["requests_completed"], 32)
        self.assertEqual(point["request_success_rate"], 1.0)
        self.assertEqual(point["worker_success_rate"], 1.0)
        self.assertEqual(point["completed_per_second"], 16.0)
        self.assertEqual(point["failed"], 0)
        self.assertEqual(point["peak_rss_bytes_max"], 1000)
        self.assertEqual(point["peak_rss_bytes_sum_over_workers"], 4000)
        self.assertEqual(point["errors"], [])

    def test_a_failed_worker_stays_in_the_denominator(self):
        results = [{"ok": True, "queries": 8, "elapsed_ms": 90, "peak_rss_bytes": 1000},
                   {"ok": False, "queries": 0, "error": "OperationalError: gone"}]
        point = scale.concurrency_point(2, 8, results, 1.0)
        self.assertEqual(point["requests_planned"], 16)
        self.assertEqual(point["requests_completed"], 8)
        self.assertEqual(point["request_success_rate"], 0.5)
        self.assertEqual(point["worker_success_rate"], 0.5)
        self.assertEqual(point["failed"], 1)
        self.assertEqual(point["errors"], ["OperationalError: gone"])
        self.assertEqual(point["completed_per_second"], 8.0)

    def test_every_worker_failing_is_zero_not_na(self):
        results = [{"ok": False, "queries": 0, "error": "boom"} for _ in range(2)]
        point = scale.concurrency_point(2, 8, results, 1.0)
        self.assertEqual(point["request_success_rate"], 0.0)
        self.assertEqual(point["worker_success_rate"], 0.0)
        self.assertEqual(point["requests_completed"], 0)
        self.assertEqual(point["peak_rss_bytes_max"], 0)   # 没读到内存是 0，不是 None
        self.assertEqual(point["errors"], ["boom", "boom"])

    def test_zero_workers_is_na_not_zero(self):
        point = scale.concurrency_point(0, 8, [], 1.0)
        self.assertEqual(point["requests_planned"], 0)
        self.assertEqual(point["request_success_rate"], "N/A")
        self.assertEqual(point["worker_success_rate"], "N/A")


class LegacyBehaviorGuardTests(unittest.TestCase):
    """`--kind v1`（既有 CAD+检索 扫描）的行为与输出字段必须保持不变。"""

    def test_default_kind_is_still_v1(self):
        args = scale.build_parser().parse_args([])
        self.assertEqual(args.kind, "v1")
        self.assertEqual(args.entity_scales, "100,1000,5000,20000")
        self.assertFalse(args.json_only)

    def test_legacy_report_path_and_repeats_are_unchanged(self):
        self.assertEqual(scale.REPORT, ROOT / "reports" / "scale_v1.json")
        self.assertEqual(scale.REPEATS, 3)

    def test_legacy_child_still_dispatches_cad_and_retrieval(self):
        self.assertIn('kind = sys.argv[1]', scale.CHILD)
        self.assertIn('if kind == "cad":', scale.CHILD)
        self.assertIn('elif kind == "retrieval":', scale.CHILD)
        self.assertIn('retriever._selected_rows(payload["collections"], refs)', scale.CHILD)
        self.assertIn('raise SystemExit("unknown kind: " + kind)', scale.CHILD)
        self.assertIn('"elapsed_ms"', scale.CHILD)

    def test_legacy_report_shape_via_a_stubbed_sweep(self):
        """用桩 measure 复跑真实 run_v1：报告字段集合与既有版本一致。"""
        fake = {"elapsed_ms_median": 1.0, "elapsed_ms_runs": [1.0, 1.0, 1.0],
                "peak_rss_bytes": 1024, "detail": {"parse_status": "parsed", "blocks": 1,
                                                   "max_blocks": 2000, "rows": 0, "documents": 0},
                "repeats": 3}
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "scale_v1.json"
            original_report, original_measure = scale.REPORT, scale.measure
            original_build = scale.build_dxf
            try:
                scale.REPORT = report_path
                scale.measure = lambda kind, payload: dict(fake)
                scale.build_dxf = lambda path, entities: path.write_text("0\n", encoding="utf-8")
                code = scale.run_v1(SimpleNamespace(entity_scales="100,1000", json_only=True))
            finally:
                scale.REPORT, scale.measure = original_report, original_measure
                scale.build_dxf = original_build
            self.assertEqual(code, 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(set(report), {"generated_at", "machine", "caveats",
                                           "repeats_per_scale", "cad_parse", "retrieval"})
            self.assertEqual(report["repeats_per_scale"], 3)
            self.assertEqual(len(report["cad_parse"]), 2)
            self.assertEqual([entry["selected_documents"] for entry in report["retrieval"]],
                             [1, 2, 5, 8])
            self.assertEqual(set(report["cad_parse"][0]),
                             {"entities_requested", "bytes", "elapsed_ms_median",
                              "elapsed_ms_runs", "peak_rss_bytes", "detail", "repeats"})

    def test_legacy_cad_child_still_returns_the_same_detail_fields(self):
        try:
            import ezdxf  # noqa: F401
        except Exception:                                     # pragma: no cover
            self.skipTest("ezdxf 不可用，跳过真实 cad 子进程")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scale-100.dxf"
            scale.build_dxf(path, 100)
            original_repeats = scale.REPEATS
            try:
                scale.REPEATS = 1                              # 只跑一次，保持测试快
                result = scale.measure("cad", {"path": str(path)})
            finally:
                scale.REPEATS = original_repeats
            self.assertEqual(set(result), {"elapsed_ms_median", "elapsed_ms_runs",
                                           "peak_rss_bytes", "detail", "repeats"})
            self.assertEqual(set(result["detail"]), {"parse_status", "blocks", "max_blocks"})
            self.assertGreater(result["detail"]["blocks"], 0)

    def test_v2_sections_merge_without_touching_the_v1_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "scale_v2.json"
            original = scale.V2_REPORT
            try:
                scale.V2_REPORT = report_path
                scale.save_v2("sql_scale", {"status": "measured"}, command="pytest sql")
                scale.save_v2("profile", {"status": "measured_toolchain_only"},
                              command="pytest profile")
                report = json.loads(report_path.read_text(encoding="utf-8"))
            finally:
                scale.V2_REPORT = original
            self.assertEqual(set(report["sections"]), {"sql_scale", "profile"})
            self.assertEqual(report["sql_scale"], {"status": "measured"})
            self.assertEqual(report["model_segment_policy"]["status"], "empty")
            self.assertIn("CSRF_REQUIRED", report["model_segment_policy"]["reason"])
            self.assertEqual(scale.REPORT, ROOT / "reports" / "scale_v1.json")

    def test_v2_kinds_keep_v1_and_add_only_measurement_modes(self):
        self.assertEqual(scale.V2_KINDS[0], "v1")
        for kind in ("sql", "documents", "concurrency", "profile", "retrieval_scan",
                     "locator_ab", "all"):
            self.assertIn(kind, scale.V2_KINDS)


if __name__ == "__main__":
    unittest.main()
