"""Guard the frozen CAD capability acceptance suite (`cad-capability-v1`).

These tests exist to keep the *evidence* honest, not to re-test the reader. They assert:

1. **冻结完整性**：manifest / labels / materials 齐全，且每份材料的 sha256 与冻结值一致
   （材料被悄悄改动而标签不变时，测试直接失败）；
2. **分层覆盖度**：六个能力层每层至少一例；"单位 unknown"层真的存在，且其材料里**真的
   没有** `$INSUNITS`（防止有人用"猜 mm"蒙过去）；positional 层的材料里**真的没有**
   `code 5` 句柄（防止用带句柄的文件冒充位置身份）；
3. **篡改拒绝**：改动材料字节、manifest 记的 sha256、或 labels.json 时，评测脚本必须
   **拒绝评测**（`SuiteIntegrityError` / 退出码 2），而不是给出一个看起来有效的成绩；
4. **失败项留分母**：向临时副本注入一个必然失败的期望后，汇总的分母包含它、退出码为 1，
   而仓库里的冻结标签**一字未动**；
5. **反证（不空转）**：把真实用例的期望改错，评测必须判它失败——证明比对真的同时读两侧，
   而不是恒真。

所有篡改/注入都作用在 `tempfile` 里的副本上；仓库内的冻结材料与标签不被写入。
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE_DIR = ROOT / 'data/evaluation/cad_capability_v1'
EVAL_SCRIPT = ROOT / 'scripts/eval_cad_capability.py'
REPORT_PATH = ROOT / 'reports/cad_capability_v1.json'

LAYERS = ('unit', 'version', 'handle', 'entity', 'layout', 'rejected')
REQUIRED_ENTITY_TYPES = ('LINE', 'CIRCLE', 'ARC', 'LWPOLYLINE', 'POLYLINE', 'TEXT', 'MTEXT',
                         'DIMENSION', 'INSERT')
REQUIRED_REJECTION_CODES = ('CAD_DUPLICATE_SOURCE_HANDLE', 'CAD_ENTITY_HANDLE_INVALID',
                            'CAD_DXF_AUDIT_ERRORS', 'CAD_ENTITY_TYPE_UNSUPPORTED:SPLINE',
                            'CAD_ENTITY_TYPE_UNSUPPORTED:HATCH',
                            'CAD_ENTITY_TYPE_UNSUPPORTED:3DFACE',
                            'CAD_PAPERSPACE_ENTITIES_SKIPPED',
                            'CAD_3D_OR_NONFINITE_UNSUPPORTED',
                            'CAD_OCS_OR_THICKNESS_UNSUPPORTED',
                            'CAD_HIDDEN_ENTITY_UNSUPPORTED')
REQUIRED_EXPECTED_KEYS = ('parse_status', 'unit', 'identity_mode', 'entity_counts',
                          'block_counts', 'unsupported_codes', 'error_codes_any', 'reader',
                          'absent_codes')
EXACT_STATUSES = ('parsed', 'partial', 'unsupported', 'rejected')


def _load_eval_module():
    spec = importlib.util.spec_from_file_location('cad_capability_evaluator', EVAL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _read_raw(path: Path) -> str:
    """Decode without universal-newline translation, so CRLF stays visible."""
    return path.read_bytes().decode('utf-8')


def _manifest():
    return json.loads(_read(SUITE_DIR / 'manifest.json'))


def _labels():
    return json.loads(_read(SUITE_DIR / 'labels.json'))


class SuiteIntegrityTests(unittest.TestCase):
    """冻结材料与标签必须齐全且按 sha256 对上。"""

    def test_manifest_labels_and_every_material_exist_and_hash_match(self):
        manifest = _manifest()
        labels = _labels()
        self.assertEqual(manifest['suite_id'], 'cad-capability-v1')
        self.assertEqual(manifest['cases_count'], len(labels['cases']))
        self.assertTrue((SUITE_DIR / manifest['labels_file']).is_file())
        self.assertEqual(_sha256(SUITE_DIR / manifest['labels_file']), manifest['labels_sha256'])
        self.assertEqual([record['case_id'] for record in manifest['materials']],
                         [case['case_id'] for case in labels['cases']])
        for record in manifest['materials']:
            path = SUITE_DIR / record['file']
            with self.subTest(material=record['file']):
                self.assertTrue(path.is_file(), f'missing material {record["file"]}')
                self.assertEqual(_sha256(path), record['sha256'])
                self.assertEqual(path.stat().st_size, record['bytes'])

    def test_no_material_is_empty_and_every_case_declares_required_keys(self):
        labels = _labels()
        for case in labels['cases']:
            with self.subTest(case=case['case_id']):
                material = SUITE_DIR / case['material']
                self.assertGreater(material.stat().st_size, 100)
                self.assertTrue(case['layers'])
                self.assertTrue(set(case['layers']) <= set(LAYERS))
                for key in REQUIRED_EXPECTED_KEYS:
                    self.assertIn(key, case['expected'], f'{case["case_id"]} missing {key}')
                self.assertIn(case['expected']['parse_status'], EXACT_STATUSES)
                if case['expected']['parse_status'] == 'rejected':
                    self.assertTrue(case['expected']['error_codes_any'],
                                    'a rejected case must name acceptable error codes')

    def test_code_hashes_are_recorded_as_64_hex_for_every_evaluated_file(self):
        manifest = _manifest()
        self.assertIn('ingestion/cad_adapter.py', manifest['evaluated_code'])
        self.assertIn('ingestion/cad_dxf_v2.py', manifest['evaluated_code'])
        for relative in manifest['evaluated_code']:
            digest = _sha256(ROOT / relative)
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
            self.assertNotEqual(digest, '')


class CoverageGuardTests(unittest.TestCase):
    """覆盖度守护：每个能力层至少一例，且关键层的材料真的具备该形态。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = _manifest()
        cls.labels = _labels()
        cls.cases = cls.labels['cases']

    def _cases_in_layer(self, layer):
        return [case for case in self.cases if layer in case['layers']]

    def test_every_capability_layer_has_at_least_one_case(self):
        for layer in LAYERS:
            with self.subTest(layer=layer):
                self.assertGreaterEqual(len(self._cases_in_layer(layer)), 1)
                self.assertEqual(self.manifest['layers'][layer], len(self._cases_in_layer(layer)))

    def test_unknown_unit_layer_exists_and_its_material_really_declares_no_insunits(self):
        candidates = [case for case in self._cases_in_layer('unit')
                      if case['expected']['unit'] == 'unknown'
                      and 'CAD_UNIT_CODE_NOT_MODELLED' in case['expected']['absent_codes']]
        self.assertTrue(candidates, 'no case pins "unit unknown with no not-modelled code"')
        for case in candidates:
            with self.subTest(case=case['case_id']):
                text = _read(SUITE_DIR / case['material'])
                # 材料里真的没有 $INSUNITS：unknown 只能来自"源未声明"，不能来自"猜 mm"。
                self.assertNotIn('$INSUNITS', text)
                self.assertNotIn('CAD_UNIT_CODE_NOT_MODELLED', case['expected']['unsupported_codes'])

    def test_positional_layer_is_really_handleless_and_source_layer_really_has_handles(self):
        positional = [case for case in self.cases
                      if case['expected']['identity_mode'] == 'positional']
        self.assertGreaterEqual(len(positional), 2, 'need both LF and CRLF positional materials')
        self.assertEqual({case['material'] for case in positional},
                         {'materials/handle_positional_lf.dxf',
                          'materials/handle_positional_crlf.dxf'})
        for case in positional:
            with self.subTest(case=case['case_id']):
                text = _read(SUITE_DIR / case['material'])
                self.assertEqual(re.findall(r'(?m)^[ \t]*5\r?\n', text), [],
                                 'positional material must carry zero code 5 handle tags')
                self.assertNotIn('$HANDLING', text)
                self.assertIn('CAD_SOURCE_HANDLE_UNAVAILABLE',
                              case['expected']['unsupported_codes'])
        source = [case for case in self.cases
                  if case['expected']['identity_mode'] == 'source'
                  and re.search(r'(?m)^[ \t]*5\r?\n', _read(SUITE_DIR / case['material']))]
        self.assertTrue(source, 'no source-handle material really carries a code 5 tag')

    def test_crlf_and_lf_materials_both_exist_and_differ_only_in_newlines(self):
        lf = _read_raw(SUITE_DIR / 'materials/handle_source_lf.dxf')
        crlf = _read_raw(SUITE_DIR / 'materials/handle_source_crlf.dxf')
        self.assertEqual(lf.count('\r\n'), 0)
        self.assertGreater(crlf.count('\r\n'), 0)
        self.assertEqual(crlf.replace('\r\n', '\n'), lf)
        positional_lf = _read_raw(SUITE_DIR / 'materials/handle_positional_lf.dxf')
        positional_crlf = _read_raw(SUITE_DIR / 'materials/handle_positional_crlf.dxf')
        self.assertEqual(positional_crlf.replace('\r\n', '\n'), positional_lf)

    def test_version_layer_covers_r12_and_at_least_four_further_versions(self):
        declared = {}
        for case in self.cases:
            text = _read(SUITE_DIR / case['material'])
            match = re.search(r'9\r?\n\$ACADVER\r?\n[ \t]*\d+\r?\n([^\r\n]*)', text)
            if match:
                declared.setdefault(match.group(1).strip(), []).append(case['case_id'])
        self.assertIn('AC1009', declared, 'R12 (AC1009) must be covered')
        self.assertGreaterEqual(len(declared), 5,
                                f'need >=5 DXF versions, got {sorted(declared)}')
        for version in ('AC1009', 'AC1015', 'AC1021', 'AC1027', 'AC1032'):
            self.assertIn(version, declared)

    def test_entity_layer_covers_every_declared_supported_entity(self):
        covered = set()
        for case in self.cases:
            covered.update(case['expected']['entity_counts'])
            reader = case['expected'].get('reader') or {}
            for spec in (reader.get('entities') or []) + (reader.get('any') or []):
                if spec.get('match', {}).get('entity_type'):
                    covered.add(spec['match']['entity_type'])
        missing = [name for name in REQUIRED_ENTITY_TYPES if name not in covered]
        self.assertEqual(missing, [], f'entity layer does not cover {missing}')

    def test_rejection_layer_declares_spline_hatch_3dface_paperspace_3d_ocs_hidden(self):
        declared = set()
        for case in self.cases:
            declared.update(case['expected']['unsupported_codes'])
            declared.update(case['expected']['error_codes_any'])
        missing = [code for code in REQUIRED_REJECTION_CODES
                   if not any(entry == code or entry.startswith(code + ':') or code.startswith(entry + ':')
                              for entry in declared)]
        self.assertEqual(missing, [], f'rejection layer does not declare {missing}')
        self.assertTrue(any(entry.startswith('CAD_POLYLINE_BULGE_UNSUPPORTED') for entry in declared),
                        'bulge must be declared, never silently accepted')

    def test_layout_layer_covers_model_and_paperspace(self):
        paperspace = [case for case in self.cases
                      if any(code.startswith('CAD_PAPERSPACE_ENTITIES_SKIPPED')
                             for code in case['expected']['unsupported_codes'])]
        model_only = [case for case in self.cases
                      if case['expected']['locator_layout'] == 'Model']
        self.assertTrue(paperspace, 'no paperspace declaration case')
        self.assertTrue(model_only, 'no Model-layout case')
        for case in model_only:
            with self.subTest(case=case['case_id']):
                self.assertEqual(case['expected']['locator_layout'], 'Model')

    def test_dimension_case_separates_geometry_measurement_from_declared_text(self):
        case = next(case for case in self.cases if case['case_id'] == 'entity_native_dimension')
        features = (case['expected']['reader'] or {}).get('features') or []
        authorities = sorted(spec['expect']['value_authority'] for spec in features)
        self.assertEqual(authorities, ['declared_text', 'dimension_measurement'])
        declared = next(spec for spec in features
                        if spec['expect']['value_authority'] == 'declared_text')
        measured = next(spec for spec in features
                        if spec['expect']['value_authority'] == 'dimension_measurement')
        # 几何测量值(10.0)与文字声明值(25.4)必须分开：不得互相代替。
        self.assertNotEqual(declared['expect']['geometry_measurement'],
                            declared['expect']['declared_value'])


class TamperRefusalTests(unittest.TestCase):
    """材料/清单/标签被改动时必须拒绝评测，而不是给出成绩。"""

    @classmethod
    def setUpClass(cls):
        cls.ev = _load_eval_module()

    def _copy_suite(self, target: Path):
        shutil.copytree(SUITE_DIR, target)
        return target

    def test_modified_material_bytes_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = self._copy_suite(Path(tmp) / 'suite')
            material = suite / 'materials/unit_mm.dxf'
            material.write_bytes(material.read_bytes().replace(b'LINE', b'LINF', 1))
            with self.assertRaises(self.ev.SuiteIntegrityError) as caught:
                self.ev.evaluate(suite)
            self.assertIn('sha256 mismatch', str(caught.exception))
            self.assertIn('unit_mm.dxf', str(caught.exception))

    def test_modified_manifest_hash_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = self._copy_suite(Path(tmp) / 'suite')
            manifest_path = suite / 'manifest.json'
            manifest = json.loads(_read(manifest_path))
            manifest['materials'][0]['sha256'] = '0' * 64
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
            with self.assertRaises(self.ev.SuiteIntegrityError) as caught:
                self.ev.evaluate(suite)
            self.assertIn('sha256 mismatch', str(caught.exception))

    def test_modified_labels_are_refused_even_when_materials_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = self._copy_suite(Path(tmp) / 'suite')
            labels_path = suite / 'labels.json'
            labels = json.loads(_read(labels_path))
            target = next(case for case in labels['cases'] if case['case_id'] == 'unit_mm')
            target['expected']['unit'] = 'ft'  # 只改标签，不改材料
            labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + '\n',
                                   encoding='utf-8')
            with self.assertRaises(self.ev.SuiteIntegrityError) as caught:
                self.ev.evaluate(suite)
            self.assertIn('labels sha256 mismatch', str(caught.exception))

    def test_cli_refuses_with_nonzero_exit_and_writes_no_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = self._copy_suite(Path(tmp) / 'suite')
            (suite / 'materials/unit_in.dxf').write_bytes(b'not a dxf at all')
            output = Path(tmp) / 'report.json'
            completed = subprocess.run(
                [sys.executable, str(EVAL_SCRIPT), '--suite', str(suite), '--output', str(output),
                 '--quiet'],
                capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn('CAD_CAPABILITY_SUITE_INTEGRITY_REFUSED', completed.stderr)
            self.assertFalse(output.exists(), 'a refused evaluation must not write a report')


class FailureRetentionTests(unittest.TestCase):
    """注入一个必然失败的期望：它必须留在分母里，退出码为 1，报告仍写全。"""

    @classmethod
    def setUpClass(cls):
        cls.ev = _load_eval_module()

    def _inject_failing_case(self, suite: Path):
        manifest_path = suite / 'manifest.json'
        labels_path = suite / 'labels.json'
        manifest = json.loads(_read(manifest_path))
        labels = json.loads(_read(labels_path))
        donor = next(record for record in manifest['materials'] if record['case_id'] == 'unit_mm')
        injected = dict(donor)
        injected.update({'case_id': 'injected_fail', 'file': 'materials/injected_fail.dxf'})
        (suite / 'materials/injected_fail.dxf').write_bytes(
            (suite / donor['file']).read_bytes())
        injected['sha256'] = _sha256(suite / injected['file'])
        manifest['materials'].append(injected)
        manifest['cases_count'] = len(manifest['materials'])
        manifest['layers']['unit'] = manifest['layers']['unit'] + 1
        donor_label = next(case for case in labels['cases'] if case['case_id'] == 'unit_mm')
        wrong = copy.deepcopy(donor_label)
        wrong['case_id'] = 'injected_fail'
        wrong['material'] = injected['file']
        wrong['document_id'] = 'doc-capsuite-injected-fail'
        wrong['layers'] = ['unit']
        # 必然失败：期望一个不存在的缺口码，并把 LINE 计数写成 999。
        wrong['expected']['unsupported_codes'] = ['CAD_CODE_THAT_DOES_NOT_EXIST']
        wrong['expected']['entity_counts'] = {'LINE': 999}
        labels['cases'].append(wrong)
        labels_text = json.dumps(labels, ensure_ascii=False, indent=2) + '\n'
        labels_path.write_text(labels_text, encoding='utf-8')
        manifest['labels_sha256'] = hashlib.sha256(labels_text.encode('utf-8')).hexdigest()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
        return 'injected_fail'

    def test_injected_failure_stays_in_the_denominator_and_sets_exit_code_one(self):
        frozen_labels_before = _sha256(SUITE_DIR / 'labels.json')
        frozen_manifest_before = _sha256(SUITE_DIR / 'manifest.json')
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / 'suite'
            shutil.copytree(SUITE_DIR, suite)
            case_id = self._inject_failing_case(suite)
            report = self.ev.evaluate(suite)
            ids = [case['case_id'] for case in report['cases']]
            self.assertIn(case_id, ids)
            injected = next(case for case in report['cases'] if case['case_id'] == case_id)
            self.assertFalse(injected['passed'])
            self.assertTrue(injected['failure_reasons'])
            self.assertIn('injected_fail', [item['case_id']
                                            for item in report['summary']['failed_cases']])
            # 分母包含注入项：unit 层由 4 例变成 5 例，失败 1 例。
            self.assertEqual(report['summary']['layers']['unit']['cases'], 5)
            self.assertEqual(report['summary']['layers']['unit']['failed'], 1)
            self.assertEqual(report['summary']['layers']['unit']['passed'], 4)
            # 只有注入项失败：其余 31 例仍然通过，且都在 cases 里。
            self.assertEqual(report['summary']['cases'], 32)
            self.assertEqual(report['summary']['failed'], 1)
            self.assertEqual(report['summary']['exit_code'], 1)
            self.assertEqual(sum(layer['cases'] for layer in report['summary']['layers'].values()),
                             sum(len(case['layers']) for case in report['cases']))
        # 冻结标签/清单一字未动。
        self.assertEqual(_sha256(SUITE_DIR / 'labels.json'), frozen_labels_before)
        self.assertEqual(_sha256(SUITE_DIR / 'manifest.json'), frozen_manifest_before)

    def test_cli_exit_code_is_one_and_the_full_report_is_still_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / 'suite'
            shutil.copytree(SUITE_DIR, suite)
            case_id = self._inject_failing_case(suite)
            output = Path(tmp) / 'report.json'
            completed = subprocess.run(
                [sys.executable, str(EVAL_SCRIPT), '--suite', str(suite), '--output', str(output),
                 '--quiet'],
                capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertTrue(output.is_file(), 'the report must be written even with failures')
            report = json.loads(_read(output))
            self.assertEqual(report['summary']['exit_code'], 1)
            self.assertEqual(report['summary']['cases'], 32)
            self.assertIn(case_id, [case['case_id'] for case in report['cases']])
            self.assertTrue(report['uncovered'])
            self.assertTrue(report['not_measured'])


class NonVacuousTests(unittest.TestCase):
    """反证：这些测试不是恒真的——把期望改错，评测必须判失败。"""

    @classmethod
    def setUpClass(cls):
        cls.ev = _load_eval_module()
        cls.report = cls.ev.evaluate()
        cls.labels = _labels()

    def test_the_committed_report_matches_the_current_suite(self):
        self.assertTrue(REPORT_PATH.is_file(),
                        'reports/cad_capability_v1.json must exist (run the eval script)')
        report = json.loads(_read(REPORT_PATH))
        self.assertEqual(report['suite']['labels_sha256'], _manifest()['labels_sha256'])
        self.assertEqual([case['case_id'] for case in report['cases']],
                         [case['case_id'] for case in self.labels['cases']])

    def test_every_case_really_ran_checks(self):
        self.assertEqual(len(self.report['cases']), 31)
        self.assertGreater(sum(case['checks_total'] for case in self.report['cases']), 200)
        for case in self.report['cases']:
            with self.subTest(case=case['case_id']):
                self.assertGreaterEqual(case['checks_total'], 2)
                self.assertEqual(case['checks_passed'] + len(case['failure_reasons']),
                                 case['checks_total'])

    def test_a_deliberately_wrong_expectation_is_graded_as_a_failure(self):
        case = copy.deepcopy(next(item for item in self.labels['cases']
                                  if item['case_id'] == 'unit_mm'))
        case['expected']['unit'] = 'in'  # 材料声明的是 mm，改成 in 必须失败
        result = self.ev.evaluate_case(case, SUITE_DIR)
        self.assertFalse(result['passed'])
        metrics = [reason['metric'] for reason in result['failure_reasons']]
        self.assertIn('unit', metrics)
        wrong = copy.deepcopy(next(item for item in self.labels['cases']
                                   if item['case_id'] == 'handle_positional_lf'))
        wrong['expected']['identity_mode'] = 'source'  # positional 材料不可能读成 source
        result = self.ev.evaluate_case(wrong, SUITE_DIR)
        self.assertFalse(result['passed'])
        self.assertIn('identity_mode', [reason['metric'] for reason in result['failure_reasons']])

    def test_a_wrong_entity_count_is_graded_as_a_failure(self):
        case = copy.deepcopy(next(item for item in self.labels['cases']
                                  if item['case_id'] == 'entity_primitives'))
        case['expected']['entity_counts'] = {'LINE': 2}
        result = self.ev.evaluate_case(case, SUITE_DIR)
        self.assertFalse(result['passed'])
        self.assertIn('entity_count:LINE', [reason['metric'] for reason in result['failure_reasons']])

    def test_a_missing_expected_code_is_graded_as_a_failure(self):
        case = copy.deepcopy(next(item for item in self.labels['cases']
                                  if item['case_id'] == 'reject_mixed_unsupported'))
        case['expected']['unsupported_codes'] = ['CAD_ENTITY_TYPE_UNSUPPORTED:ELLIPSE']
        result = self.ev.evaluate_case(case, SUITE_DIR)
        self.assertFalse(result['passed'])
        self.assertIn('unsupported_code',
                      [reason['metric'] for reason in result['failure_reasons']])

    def test_the_report_keeps_uncovered_and_non_claims(self):
        provenance = self.report['provenance']
        self.assertFalse(provenance['is_independent_factory_data'])
        self.assertFalse(provenance['real_drawings_used_for_grading'])
        self.assertTrue(any('准确率' in claim for claim in provenance['non_claims']))
        self.assertTrue(self.report['uncovered'])
        for item in self.report['uncovered']:
            self.assertIn('capability', item)
            self.assertIn('reason', item)
        self.assertTrue(any('DWG' in item['capability'] for item in self.report['uncovered']))


class ReportShapeTests(unittest.TestCase):
    """报告 JSON 的字段与分层汇总结构必须存在且自洽。"""

    @classmethod
    def setUpClass(cls):
        cls.report = _load_eval_module().evaluate()

    def test_report_has_every_required_section(self):
        for key in ('report_version', 'generated_by', 'generated_at', 'duration_seconds', 'suite',
                    'provenance', 'integrity', 'summary', 'cases', 'observations', 'uncovered',
                    'not_measured'):
            self.assertIn(key, self.report)

    def test_integrity_section_records_materials_code_and_versions(self):
        integrity = self.report['integrity']
        self.assertTrue(integrity['materials_verified'])
        self.assertTrue(integrity['labels_match'])
        self.assertTrue(integrity['materials'])
        self.assertTrue(all(record['match'] for record in integrity['materials']))
        self.assertRegex(integrity['labels_sha256'], r'^[0-9a-f]{64}$')
        for relative in ('ingestion/cad_adapter.py', 'ingestion/cad_dxf_v2.py',
                         'ingestion/cad_contract_v2.py', 'datamind/parse_contract.py'):
            self.assertRegex(integrity['code_files'][relative], r'^[0-9a-f]{64}$')
        self.assertTrue(integrity['code_pin']['available'])
        self.assertEqual(integrity['code_pin']['drift'], [],
                         'evaluated code drifted from the pinned v2 manifest')
        self.assertRegex(integrity['ezdxf_version'], r'^\d+\.\d+')
        self.assertRegex(integrity['evaluator']['sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(integrity['suite_builder']['sha256'], r'^[0-9a-f]{64}$')

    def test_layer_summary_denominators_are_consistent(self):
        summary = self.report['summary']
        self.assertEqual(summary['cases'], len(self.report['cases']))
        self.assertEqual(summary['passed'] + summary['failed'], summary['cases'])
        self.assertEqual(summary['exit_code'], 0 if summary['failed'] == 0 else 1)
        self.assertEqual(set(summary['layers']), set(LAYERS))
        for layer, entry in summary['layers'].items():
            with self.subTest(layer=layer):
                self.assertEqual(set(entry), {'cases', 'passed', 'failed', 'pass_rate'})
                self.assertEqual(entry['passed'] + entry['failed'], entry['cases'])
                self.assertGreater(entry['cases'], 0)
                self.assertAlmostEqual(entry['pass_rate'], entry['passed'] / entry['cases'], places=6)
        self.assertEqual(sum(entry['cases'] for entry in summary['layers'].values()),
                         sum(len(case['layers']) for case in self.report['cases']))

    def test_every_case_reports_layers_and_check_levels(self):
        for case in self.report['cases']:
            with self.subTest(case=case['case_id']):
                self.assertTrue(case['layers'])
                self.assertTrue(set(case['layers']) <= set(LAYERS))
                self.assertIn('checks', case)
                self.assertTrue(case['checks'])
                self.assertTrue(all(check['level'] in ('adapter', 'reader')
                                    for check in case['checks']))
                self.assertTrue(any(check['level'] == 'adapter' for check in case['checks']))
                self.assertIn('material_sha256', case)
                self.assertRegex(case['material_sha256'], r'^[0-9a-f]{64}$')

    def test_reader_level_checks_cover_fields_the_adapter_projection_drops(self):
        readers = [case for case in self.report['cases']
                   if any(check['level'] == 'reader' for check in case['checks'])]
        metrics = {check['metric'] for case in readers for check in case['checks']}
        self.assertIn('entity:TEXT:rotation', metrics)
        self.assertIn('entity:LWPOLYLINE:closed', metrics)
        self.assertIn('feature[1]:value_authority', metrics)
        self.assertIn('max_block_path_depth', metrics)


if __name__ == '__main__':
    unittest.main()
