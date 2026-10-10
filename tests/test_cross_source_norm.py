"""Normative cross-source corpus: determinism, parsing, frozen labels, offline P2/P3/P4.

The suite under test exists because round 2 proved the *public* knowledge base cannot support
P2/P3/P4 (169 chunks, one regex-extractable clause that names no queryable metric). These tests
hold every new claim to a reproducible check:

* the corpus is byte-deterministic and every clause parses into the fields the engine binds;
* the frozen labels in ``data/evaluation/cross_source_norm_v1.json`` are re-derived by
  ``scripts/eval_cross_source_norm.py --check`` and a *loosened* label fails that gate;
* P2/P3/P4 all complete on injected evidence with no database and no model
  (``model_calls`` stays 0), plus the unit trap refuses to round;
* no non-cross-source path changed: the SQL/RAG prompt digests are unchanged and the existing
  cross-source offline suite still reports 21/21 cases and 161/161 assertions.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import cross_source  # noqa: E402
from ingestion import norm_facts  # noqa: E402

SUITE_PATH = ROOT / 'data/evaluation/cross_source_norm_v1.json'
CORPUS_DIR = ROOT / 'data/knowledge_sources'
MANIFEST = CORPUS_DIR / 'manifest.json'
EVAL_SCRIPT = ROOT / 'scripts/eval_cross_source_norm.py'


def _load_builder():
    import importlib.util as loader
    spec = loader.spec_from_file_location('build_norm_corpus', CORPUS_DIR / 'build_norm_corpus.py')
    module = loader.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_eval():
    spec = importlib.util.spec_from_file_location('eval_cross_source_norm', EVAL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_suite():
    return json.loads(SUITE_PATH.read_text(encoding='utf-8'))


def corpus_paths():
    return sorted(CORPUS_DIR.glob('*.pdf')) + sorted(CORPUS_DIR.glob('*.docx'))


def db_available():
    try:
        import psycopg2
        with psycopg2.connect('host=127.0.0.1 port=5432 dbname=demo_retail '
                              'user=readonly_user password=readonly_dev') as conn:
            conn.set_session(readonly=True)
        return True
    except Exception:
        return False


DB = db_available()


# --- round-4 PDF file-id fixtures ---------------------------------------------------------------
# PyMuPDF writes each trailer ``/ID`` element either as a hex string (``<...>``) or as a literal
# string (``(...)``, octal escapes). The round-3 builder matched only the hex pair, so whenever
# PyMuPDF drew the literal form the bytes were returned untouched and ``--check`` reported
# ``deterministic: false`` — an independent reviewer measured 13 true / 4 false in 17 runs, this
# work package 11 true / 1 false in 12 runs. The fixtures are byte-equal apart from the ``/ID``
# form, and the literals are *shorter* than the hex element, which is why the failing corpus files
# also differed in length. They fail deterministically on the old regex instead of waiting for a
# roughly 3 % PyMuPDF draw.
PDF_ID_PREFIX = b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R '
PDF_ID_SUFFIX = b' >>\nstartxref\n9\n%%EOF\n'
PDF_ID_FIRST = b'<0123456789ABCDEF0123456789ABCDEF>'
PDF_ID_SECONDS = {
    'hex': b'<FEDCBA9876543210FEDCBA9876543210>',      # 34 bytes, the only form round 3 handled
    'literal': b'(abc\\333def\\244gh\\360\\306ijkl)',  # 30 bytes, the field shape
    'escaped': b'(a\\)b\\(c\\\\d)',                    # 13 bytes, escaped ``)`` and ``(``
}


def pdf_id_fixture(second):
    return PDF_ID_PREFIX + b'/ID[' + PDF_ID_FIRST + second + b']' + PDF_ID_SUFFIX


class NormCorpusDeterminismTest(unittest.TestCase):
    """The corpus must regenerate byte for byte and every clause must parse."""

    @classmethod
    def setUpClass(cls):
        cls.builder = _load_builder()
        cls.report = cls.builder.check()
        cls.extraction = cls.report['extraction']

    def test_regeneration_is_byte_identical(self):
        self.assertTrue(self.report['deterministic'], self.report['problems'])
        self.assertEqual([], self.report['problems'])
        self._assert_every_pdf_id_form_is_normalised()

    def _assert_every_pdf_id_form_is_normalised(self):
        """The round-4 regression guard for the falsified round-3 determinism claim.

        Round 3 asserted ``deterministic: true`` while a hex-only ``/ID`` regex let the literal
        form through; the corpus was therefore reproducible only by luck. These assertions fail on
        that regex every time, with no dependence on PyMuPDF's draw.
        """
        import hashlib
        hex_fixture = pdf_id_fixture(PDF_ID_SECONDS['hex'])
        expected = self.builder._normalize_pdf_id(hex_fixture)
        self.assertNotEqual(hex_fixture, expected, 'the random /ID must actually be replaced')
        for name in ('literal', 'escaped'):
            data = pdf_id_fixture(PDF_ID_SECONDS[name])
            self.assertNotEqual(len(data), len(hex_fixture),
                                f'the {name} fixture must differ in length, as the real files did')
            out = self.builder._normalize_pdf_id(data)
            self.assertEqual(expected, out, f'the {name} /ID form must normalise to the same bytes')
            # the written id is a pure function of the bytes *without* the /ID array ...
            start, end = self.builder._pdf_id_span(data)
            digest = hashlib.sha256(data[:start] + data[end:]).hexdigest().upper().encode()
            out_start, out_end = self.builder._pdf_id_span(out)
            self.assertEqual(b'/ID[<' + digest + b'><' + digest + b'>]', out[out_start:out_end])
            # ... and normalising an already-normalised file is a no-op (so --check is stable)
            self.assertEqual(out, self.builder._normalize_pdf_id(out))
        # a non-array ``/IDTree`` key must not be mistaken for the trailer /ID array
        tree = ((b'%PDF-1.7\n3 0 obj\n<< /IDTree 9 0 R >>\nendobj\ntrailer\n<< /Root 1 0 R /ID['
                 + PDF_ID_FIRST + PDF_ID_SECONDS['hex'] + b'] >>\nstartxref\n9\n'
                 b'%%EOF\n4 0 obj /IDTree\nendobj\n'))
        normalised_tree = self.builder._normalize_pdf_id(tree)
        self.assertEqual(2, normalised_tree.count(b'/IDTree'))
        self.assertIn(b'/IDTree 9 0 R', normalised_tree)
        # an unnormalisable file must be a hard error, not a silent random-bytes passthrough
        with self.assertRaises(RuntimeError):
            self.builder._normalize_pdf_id(b'%PDF-1.7\n1 0 obj\n<< >>\nendobj\n%%EOF\n')

    def test_manifest_matches_files_on_disk(self):
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.assertEqual('norm-corpus-v1', manifest['corpus_version'])
        for entry in manifest['files']:
            path = CORPUS_DIR / entry['name']
            self.assertTrue(path.is_file(), entry['name'])
            import hashlib
            self.assertEqual(entry['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertGreaterEqual(len(manifest['files']), 6)
        formats = {entry['format'] for entry in manifest['files']}
        self.assertEqual({'pdf', 'docx'}, formats)

    def test_threshold_clauses_all_parse_into_bindable_fields(self):
        clauses = self.extraction['threshold_clauses']
        self.assertGreaterEqual(clauses['total'], 6)
        self.assertEqual(clauses['total'], clauses['labelled_extracted'])
        self.assertFalse(self.extraction['clause_extraction_failures'])

    def test_prose_regex_is_not_enough_for_the_metric(self):
        """Why the corpus carries labels: raw prose loses the metric on most clauses."""
        clauses = self.extraction['threshold_clauses']
        self.assertEqual(clauses['total'], clauses['prose_regex_value_agrees'])
        self.assertLess(clauses['prose_regex_metric_agrees'], clauses['total'],
                        '若纯文本正则已能对齐全部指标，标签化解析就没有存在理由')

    def test_value_claims_and_binding_fields_are_complete(self):
        self.assertEqual(self.extraction['value_claims']['total'],
                         self.extraction['value_claims']['extracted'])
        self.assertGreaterEqual(self.extraction['value_claims']['total'], 6)
        fields = self.extraction['binding_fields']
        self.assertEqual(fields['required'], fields['complete'])
        self.assertEqual([], fields['incomplete'])

    def test_quality_transcript_comes_from_the_real_industrial_pdf(self):
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.assertTrue((ROOT / manifest['real_source_pdf']).is_file())
        self.assertEqual(12, len(manifest['transcript']))
        ids = {item['测量编号'] for item in manifest['transcript']}
        self.assertIn('M001-1', ids)
        # 原文与库内值本来就矛盾的那个样本必须保留
        q001 = [item for item in manifest['transcript'] if item['报告编号'] == 'Q001-1'][0]
        self.assertEqual('850', q001['实测值'])
        self.assertEqual('A102-V2', q001['库记录版次'])


class NormFactsParserTest(unittest.TestCase):
    """The labelled parser is the only place where a document becomes engine fields."""

    def test_first_separator_wins_and_iso_time_keeps_its_colons(self):
        self.assertEqual(('生效日期', '2026-09-01T00:00:00+08:00'),
                         norm_facts.labelled_value('生效日期：2026-09-01T00:00:00+08:00'))
        self.assertEqual(('阈值', '850'), norm_facts.labelled_value('阈值：850'))
        self.assertIsNone(norm_facts.labelled_value('这是一句没有标签的正文。'))

    def test_embedded_label_is_reported_as_ambiguous(self):
        block = {'text': '条款原文：条款编号：X 阈值 5 件', 'chunk_id': 'c1'}
        _metadata, clauses, _errors = norm_facts.extract_document(
            [{'text': '条款编号：X', 'block_id': 'b1', 'page_no': 1}, block],
            document_id='doc', source_sha256='a' * 64)
        self.assertEqual(1, len(clauses))
        self.assertIn('条款原文', clauses[0]['ambiguous'])
        self.assertEqual('条款编号：X 阈值 5 件', clauses[0]['clause_text'])

    def test_non_numeric_threshold_is_refused_not_guessed(self):
        _metadata, clauses, errors = norm_facts.extract_document(
            [{'text': '条款编号：X', 'block_id': 'b1', 'page_no': 1},
             {'text': '指标：温度', 'block_id': 'b2', 'page_no': 1},
             {'text': '阈值：八百五十', 'block_id': 'b3', 'page_no': 1}],
            document_id='doc', source_sha256='b' * 64)
        self.assertEqual(1, len(clauses))
        self.assertEqual('FIELD_NOT_NUMERIC', errors[0]['error'])
        self.assertIsNone(clauses[0]['limit'])

    def test_selection_is_deterministic_and_reports_drops(self):
        documents, clauses, _errors = norm_facts.corpus_records(corpus_paths())
        kept, dropped = norm_facts.select(clauses, documents,
                                          document_ids=['GY-A102-V2_工艺温度规范'],
                                          must_tokens=['GY-A102-V2-4.1'])
        self.assertEqual(['GY-A102-V2-4.1'], [block['chunk_id'] for block in kept])
        self.assertEqual(len(clauses) - 1, len(dropped))
        again, _ = norm_facts.select(clauses, documents,
                                     document_ids=['GY-A102-V2_工艺温度规范'],
                                     must_tokens=['GY-A102-V2-4.1'])
        self.assertEqual(kept, again)


class NormSuiteFrozenLabelTest(unittest.TestCase):
    """The frozen labels are re-derived from the real corpus/databases before any verdict."""

    @classmethod
    def setUpClass(cls):
        cls.eval = _load_eval()
        cls.suite = load_suite()

    def test_suite_shape(self):
        structures = {}
        for case in self.suite['cases']:
            structures.setdefault(case['structure'], []).append(case['case_id'])
            self.assertTrue(case['question'])
            self.assertIn(case['expected_status'], ('completed', 'abstained', 'failed'))
            self.assertTrue(case['expected_clause_ids'])
            self.assertEqual(set(self.suite['binding_fields']),
                             set(case['expected_binding_fields']))
            self.assertTrue(case['expected_answer_checks'])
        for structure in self.suite['structures']:
            self.assertGreaterEqual(len(structures[structure]), 5, structure)

    @unittest.skipUnless(DB, 'demo_retail/datamind_meta unavailable')
    def test_frozen_labels_recompute_green(self):
        result = self.eval.check_labels(self.suite)
        self.assertTrue(result['ok'], result['problems'])

    @unittest.skipUnless(DB, 'demo_retail/datamind_meta unavailable')
    def test_loosened_label_breaks_the_gate(self):
        """A relaxed frozen label must fail ``--check`` (the gate is not decorative)."""
        loosened = json.loads(json.dumps(self.suite))
        for case in loosened['cases']:
            if case['case_id'] == 'norm-p2-01':
                for check in case['expected_answer_checks']:
                    if check['check'] == 'threshold_violations':
                        check['count'] = 0
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'loosened.json'
            path.write_text(json.dumps(loosened, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, str(EVAL_SCRIPT), '--check', '--suite', str(path)],
                cwd=str(ROOT), capture_output=True, text=True)
        self.assertNotEqual(0, completed.returncode, completed.stdout[-2000:])
        self.assertIn('threshold_violations_recomputed', completed.stdout)

    @unittest.skipUnless(DB, 'demo_retail/datamind_meta unavailable')
    def test_declared_divergence_is_verified_in_both_directions(self):
        """引擎缺陷已修：冻结期望更正为 9/9，修订前后两组数字都不许漂移。

        第 3 轮把 ``edge_between`` 单向键查找缺陷下的引擎实测值（P4 2/9）与独立重算值（9/9）
        双向冻结。第 4 轮修好引擎后，期望值更正为 9/9；本测试证明修订**不是放宽**：标签门接受
        修复后的 9/9，拒绝旧的缺陷值 2/9，也拒绝把超限数放宽为 0。
        """
        cases = {case['case_id']: case for case in self.suite['cases']}
        case = cases['norm-p4-05']
        revision = case['expected_recompute_divergence']
        self.assertEqual('resolved', revision['status'])
        self.assertEqual(2, revision['before']['engine_threshold_checks'])
        self.assertEqual(9, revision['before']['independent_threshold_checks'])
        self.assertEqual(9, revision['after']['engine_threshold_checks'])
        self.assertEqual(9, revision['after']['independent_threshold_checks'])
        # 修复后 engine 实测 == 独立重算；冻结标签必须两者都满足。
        result = self.eval.check_labels({'cases': [case], 'structures': ['P4']})
        self.assertTrue(result['ok'], result['problems'])
        # 把引擎实测值退回缺陷期的 2/9 必须失败（防止把引擎缺陷重新冻结成期望）。
        refrozen = json.loads(json.dumps(case))
        for check in refrozen['expected_answer_checks']:
            if check['check'] == 'threshold_checks':
                check['count'] = revision['before']['engine_threshold_checks']
        result = self.eval.check_labels({'cases': [refrozen], 'structures': ['P4']})
        self.assertFalse(result['ok'])
        self.assertIn('threshold_checks_recomputed',
                      [item['check'] for item in result['problems']])
        # 放宽超限条数同样必须失败。
        loosened = json.loads(json.dumps(case))
        for check in loosened['expected_answer_checks']:
            if check['check'] == 'threshold_violations':
                check['count'] = 0
        result = self.eval.check_labels({'cases': [loosened], 'structures': ['P4']})
        self.assertFalse(result['ok'])
        self.assertIn('threshold_violations_recomputed',
                      [item['check'] for item in result['problems']])
        # 顶层修订记录必须同时保留修订前后两组数字与「引擎缺陷已修」的说明。
        top = self.suite['labels_revision']
        self.assertEqual(['norm-p4-04', 'norm-p4-05'], top['revisions'][0]['case_ids'])
        self.assertEqual(2, top['revisions'][0]['before']['norm-p4-05']['engine_threshold_checks'])
        self.assertEqual(9, top['revisions'][0]['after']['norm-p4-05']['engine_threshold_checks'])
        self.assertTrue(top['policy'])
        # 冲突键粒度（缺陷 B）已在主协调者授权后闭合：deferred 必须为空，且修订记录里必须留下
        # 「引擎已修 + 独立复算同步 + 期望值由缺陷期行为更正」的完整痕迹（不是放宽标签）。
        self.assertEqual([], top.get('deferred', []))
        p3_row = [row for row in top['revisions'] if row['case_ids'] == ['norm-p3-09']]
        self.assertEqual(1, len(p3_row), 'norm-p3-09 的修订记录必须存在')
        p3_revision = p3_row[0]
        self.assertEqual('abstained', p3_revision['before']['expected_status'])
        self.assertTrue(p3_revision['before']['expected_conflict']['present'])
        self.assertEqual('completed', p3_revision['after']['expected_status'])
        self.assertFalse(p3_revision['after']['expected_conflict']['present'])
        # 缺陷期标签（abstained + 冲突存在）必须被标签门拒绝：证明这次是更正而不是放宽。
        cases = {case['case_id']: case for case in self.suite['cases']}
        regressed = json.loads(json.dumps(cases['norm-p3-09']))
        regressed['expected_status'] = 'abstained'
        for check in regressed['expected_answer_checks']:
            if check['check'] == 'status':
                check['equals'] = 'abstained'
            if check['check'] == 'conflict':
                check['present'] = True
        result = self.eval.check_labels({'cases': [regressed], 'structures': ['P3']})
        self.assertFalse(result['ok'])
        # 缺陷期标签在冲突复算上必然失败（status 由引擎实测判定，冲突由独立复算判定）。
        self.assertIn('conflict_recomputed', [item['check'] for item in result['problems']])


class CrossSourceNormInjectedEvidenceTest(unittest.TestCase):
    """P2/P3/P4 on injected evidence: no database, no model, deterministic verdicts."""

    def setUp(self):
        self._guard = None
        import datamind.providers as providers
        self._original = providers.chat

        def forbidden(*args, **kwargs):
            raise AssertionError('MODEL_CALL_IN_INJECTED_EVIDENCE_TEST')

        providers.chat = forbidden
        self._guard = providers

    def tearDown(self):
        if self._guard is not None:
            self._guard.chat = self._original

    @staticmethod
    def _sql(rows, source_ref='demo_retail'):
        return {'source_ref': source_ref, 'snapshot_id': f'{source_ref}@test',
                'sql': 'SELECT 1', 'sql_hash': 'f' * 64, 'columns': [], 'rows': rows}

    @staticmethod
    def _row(entity, value, unit, metric, *, revision=None, instant=None, scope=None, row_id=None):
        row = {'row_id': row_id or f'{entity}-{value}', 'entity_label': entity, 'metric': metric,
               'value': value, 'unit': unit, 'recorded_at': instant,
               'time_scope': scope or {'start': '2025-01-01T00:00:00+08:00', 'end': None}}
        if revision:
            row['recorded_revision'] = revision
        return row

    def test_p2_document_rule_filters_the_injected_rows(self):
        docs = cross_source.norm_corpus_evidence(
            document_ids=['DM-KF-2026-001_售后时效规范'], must_tokens=['DM-KF-2026-001-6.1'])
        scope = {'start': '2025-01-01T00:00:00+08:00', 'end': '2026-01-01T00:00:00+08:00'}
        rows = [self._row('online', 3, '天', '时长', instant='2025-06-01T00:00:00+08:00', scope=scope),
                self._row('app', 12, '天', '时长', instant='2025-06-02T00:00:00+08:00', scope=scope)]
        result = cross_source.plan('找出 2025 年处理时长超过规范上限的退款记录。', ['demo_retail'],
                                   ['DM-KF-2026-001_售后时效规范'],
                                   sql_evidence=self._sql(rows), rag_evidence=docs)
        self.assertEqual('P2', result['intent']['structure'])
        self.assertEqual('completed', result['status'], result['failure_detail'])
        violations = [check for check in result['threshold_checks'] if check['violation'] is True]
        self.assertEqual(1, len(violations))
        self.assertEqual('7', violations[0]['limit'])
        self.assertIn('筛出 1 条', result['answer'])

    def test_p3_conflict_is_presented_with_both_original_values(self):
        docs = cross_source.norm_corpus_evidence(document_ids=['ZJ-A102_质检核验记录'],
                                                 must_tokens=['ZJ-Q001-1'])
        rows = [self._row('A102', 870, 'degC', '温度', revision='A102-V2',
                          instant='2026-09-05T10:00:00+08:00',
                          scope={'start': '2026-09-01T00:00:00+08:00', 'end': None},
                          row_id='M001-1')]
        result = cross_source.plan('核验 A102 保温温度质检记录与库内测量是否一致。', ['demo_industrial'],
                                   ['ZJ-A102_质检核验记录'], sql_evidence=self._sql(rows),
                                   rag_evidence=docs)
        self.assertEqual('P3', result['intent']['structure'])
        self.assertEqual('abstained', result['status'])
        self.assertEqual('CONFLICT_UNRESOLVED', result['error_code'])
        conflict = result['unresolved_conflicts'][0]
        self.assertEqual({'850', '870'}, {str(conflict['left']['value']),
                                          str(conflict['right']['value'])})
        self.assertIn('850', result['answer'])
        self.assertIn('870', result['answer'])
        # 缺陷 B（第 4 轮）：真冲突必须仍然成立，且两侧都指向同一条被命名的记录 M001-1。
        self.assertEqual('M001-1', conflict['left']['record_scope'])
        self.assertEqual('M001-1', conflict['right']['record_scope'])

        # 缺陷 B：两个 *不同* 测量各自的文档值与库值一致时，不得互相配对成假冲突。
        # （样本形状取自 norm-p3-09：M006-2=870 与 M011-1=860 各自与文档一致。）
        scope = {'start': '2026-09-01T00:00:00+08:00', 'end': None}
        docs = cross_source.norm_corpus_evidence(document_ids=['ZJ-A102_质检核验记录'],
                                                 any_tokens=['ZJ-Q006-2', 'ZJ-Q011-1'])
        rows = [self._row('A102', 870, 'degC', '温度', revision='A102-V2',
                          instant='2026-09-08T11:00:00+08:00', scope=scope, row_id='M006-2'),
                self._row('A102', 860, 'degC', '温度', revision='A102-V2',
                          instant='2026-09-12T10:00:00+08:00', scope=scope, row_id='M011-1')]
        result = cross_source.plan(
            '核验 A102 保温温度质检报告 Q006-2 与 Q011-1 的实测值与库内测量是否一致。',
            ['demo_industrial'], ['ZJ-A102_质检核验记录'],
            sql_evidence=self._sql(rows, 'demo_industrial'), rag_evidence=docs)
        self.assertEqual('completed', result['status'], result['failure_detail'])
        self.assertEqual([], result['unresolved_conflicts'])
        self.assertEqual([], result['conflicts'])

    def test_p3_agreement_completes_without_a_conflict(self):
        docs = cross_source.norm_corpus_evidence(document_ids=['ZJ-A102_质检核验记录'],
                                                 must_tokens=['ZJ-Q002-1'])
        rows = [self._row('A102', '1123.15', 'K', '温度', revision='A102-V2',
                          instant='2026-09-06T10:00:00+08:00',
                          scope={'start': '2026-09-01T00:00:00+08:00', 'end': None},
                          row_id='M002-1')]
        result = cross_source.plan('核验 A102 保温温度质检记录与库内测量是否一致。', ['demo_industrial'],
                                   ['ZJ-A102_质检核验记录'], sql_evidence=self._sql(rows),
                                   rag_evidence=docs)
        self.assertEqual('completed', result['status'], result['failure_detail'])
        self.assertEqual([], result['conflicts'])

    def test_p4_multihop_chain_and_unit_trap(self):
        docs = cross_source.norm_corpus_evidence(
            document_ids=['GY-A102-V2_工艺温度规范', 'SM-GY-2026-001_工艺温度判定说明材料'],
            any_tokens=['GY-A102-V2-4.1', 'SM-GY-2026-001-1.1'])
        rows = [self._row('A102', 870, 'degC', '温度', revision='A102-V2',
                          instant='2026-09-05T10:00:00+08:00',
                          scope={'start': '2026-09-01T00:00:00+08:00', 'end': None})]
        result = cross_source.plan('A102 保温温度异常的条款与说明材料逐跳证据。', ['demo_industrial'],
                                   ['GY-A102-V2_工艺温度规范'], sql_evidence=self._sql(rows),
                                   rag_evidence=docs)
        self.assertEqual('completed', result['status'], result['failure_detail'])
        self.assertIn('多跳跨源链路', result['answer'])
        hop_types = {edge['edge_type'] for edge in result['evidence_graph']['edges']}
        self.assertTrue({'version_scope', 'supports'}.issubset(hop_types), hop_types)

        trap = cross_source.norm_corpus_evidence(
            document_ids=['GY-A103_工艺温度规范_华氏版'], must_tokens=['GY-A103-F-4.2'])
        rows = [self._row('A103', 850, 'degC', '温度', instant='2026-09-09T10:00:00+08:00',
                          scope={'start': '2026-08-01T00:00:00+08:00', 'end': None})]
        result = cross_source.plan('A103 保温温度预警线的条款与说明材料逐跳证据。',
                                   ['demo_industrial'], ['GY-A103_工艺温度规范_华氏版'],
                                   sql_evidence=self._sql(rows, 'demo_industrial'),
                                   rag_evidence=trap)
        self.assertEqual('completed', result['status'], result['failure_detail'])
        reviews = [check for check in result['threshold_checks']
                   if check.get('reason') == 'UNIT_NOT_COMPARABLE']
        self.assertEqual(1, len(reviews))
        self.assertIsNone(reviews[0]['violation'])

        # 缺陷 A（第 4 轮）：add_edge 把对称边规范成 src<dst，edge_between 因此必须双向查找；
        # 非对称边（supports / version_scope）保持精确方向语义。
        from datamind.evidence_graph import (DIRECTIONAL_EDGE_TYPES, EvidenceGraph,
                                             SYMMETRIC_EDGE_TYPES)
        graph = EvidenceGraph('defect-a-edge-lookup')
        left = graph.make_node('sql_row', 'row-a')
        right = graph.make_node('doc_block', 'block-b')
        graph.add_edge(left.node_id, right.node_id, 'entity_match', evidence='same entity')
        self.assertIsNotNone(graph.edge_between(left.node_id, right.node_id, 'entity_match'))
        self.assertIsNotNone(graph.edge_between(right.node_id, left.node_id, 'entity_match'))
        graph.add_edge(left.node_id, right.node_id, 'version_scope', evidence='version covers')
        self.assertIsNotNone(graph.edge_between(left.node_id, right.node_id, 'version_scope'))
        self.assertIsNone(graph.edge_between(right.node_id, left.node_id, 'version_scope'))
        self.assertIn('entity_match', SYMMETRIC_EDGE_TYPES)
        self.assertIn('unit_equivalent', SYMMETRIC_EDGE_TYPES)
        self.assertEqual(('version_scope', 'supports'), DIRECTIONAL_EDGE_TYPES)


class NonCrossSourceInvarianceTest(unittest.TestCase):
    """Additive only: no other route, prompt or fixture changed."""

    SQL_PROMPT_SHA = 'eba77c041a7f0fbcf8caf528069a2ac3bcff68f7f7083cd5da19f95ca22de014'
    RAG_PROMPT_SHA = 'a39c43a5898bb80735a7acb5281c8af082a33b6f7558ea57c2c103e6557ad9a5'

    @unittest.skipUnless(DB, 'demo_retail/datamind_meta unavailable')
    def test_sql_and_rag_prompts_are_still_byte_identical(self):
        """Same capture technique as ``test_cross_source.py``: system + "\\n" + user digest."""
        import hashlib
        import json as jsonlib
        from unittest.mock import patch
        from datamind import rag_engine, sql_engine
        from datamind.agent import DataMindAgent

        captured = []

        def sql_provider(profile, system, user, **kwargs):
            captured.append(('sql', system, user))
            return {'ok': True, 'text': jsonlib.dumps({
                'sql': "SELECT count(*) AS n FROM orders WHERE created_at >= '2024-01-01' "
                       "AND created_at < '2025-01-01'", 'interpretation': '自检'})}

        with patch.object(sql_engine, 'provider_chat', sql_provider), \
                patch.object(sql_engine, 'model_profile',
                             lambda cfg=None: {'api_key': 'frozen-test', 'model': 'test'}):
            agent = DataMindAgent()
            try:
                result = agent.run('2024年一共有多少笔订单？', database_sources=['demo_retail'])
            finally:
                agent.close()
        self.assertNotIn('cross_source_result', result)
        digest = hashlib.sha256((captured[0][1] + '\n' + captured[0][2]).encode()).hexdigest()
        self.assertEqual(self.SQL_PROMPT_SHA, digest)

        captured = []

        def rag_provider(profile, system, user, **kwargs):
            captured.append((system, user))
            return {'ok': True, 'text': jsonlib.dumps({'abstain': True, 'claims': []})}

        with patch.object(rag_engine, 'provider_chat', rag_provider):
            agent = DataMindAgent()
            try:
                result = agent.run('退款条件是什么？')
            finally:
                agent.close()
        self.assertNotIn('cross_source_result', result)
        digest = hashlib.sha256((captured[0][0] + '\n' + captured[0][1]).encode()).hexdigest()
        self.assertEqual(self.RAG_PROMPT_SHA, digest)

    def test_norm_adapter_is_a_pure_addition(self):
        self.assertTrue(callable(cross_source.norm_corpus_evidence))
        evidence = cross_source.norm_corpus_evidence(document_ids=['GY-A102-V2_工艺温度规范'])
        self.assertEqual({'source_ref', 'blocks', 'present', 'reason', 'documents',
                          'selected_document_ids', 'dropped_count', 'clause_count',
                          'parse_errors', 'parser_version'}, set(evidence))
        self.assertTrue(evidence['present'])
        self.assertEqual(1, len(evidence['blocks']))
        # 语料解析不写知识库：解析前后 chunks 行数一致（只读）
        if DB:
            import psycopg2
            with psycopg2.connect('host=127.0.0.1 port=5432 dbname=datamind_meta '
                                  'user=readonly_user password=readonly_dev') as conn:
                conn.set_session(readonly=True)
                with conn.cursor() as cur:
                    cur.execute('SELECT count(*) FROM chunks')
                    before = cur.fetchone()[0]
        cross_source.norm_corpus_evidence()
        if DB:
            with psycopg2.connect('host=127.0.0.1 port=5432 dbname=datamind_meta '
                                  'user=readonly_user password=readonly_dev') as conn:
                conn.set_session(readonly=True)
                with conn.cursor() as cur:
                    cur.execute('SELECT count(*) FROM chunks')
                    after = cur.fetchone()[0]
            self.assertEqual(before, after)

    def test_offline_cross_source_suite_is_unchanged(self):
        frozen = json.loads((ROOT / 'reports/cross_source_offline.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / 'offline.json'
            completed = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/eval_cross_source.py'), '--offline',
                 '--out', str(out)],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(0, completed.returncode, completed.stdout[-2000:])
            fresh = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(21, fresh['totals']['cases'])
        self.assertEqual(fresh['totals']['passed'], fresh['totals']['cases'])
        self.assertEqual(161, fresh['checks']['total'])
        self.assertEqual(0, fresh['checks']['failed'])
        self.assertEqual(0, fresh['model_calls'])
        self.assertEqual(frozen['totals'], fresh['totals'])
        self.assertEqual(frozen['checks'], fresh['checks'])
        for wanted, found in zip(frozen['cases'], fresh['cases']):
            self.assertEqual(wanted['case_id'], found['case_id'])
            self.assertEqual(wanted['status'], found['status'])
            self.assertEqual(wanted.get('failure_detail'), found.get('failure_detail'))


if __name__ == '__main__':
    unittest.main()
