"""WP2 cross-source tests: binding, evidence graph, pipeline, wiring and byte-stability.

The frozen prompt digests in ``NonCrossSourceByteStabilityTest`` were recorded on the baseline
commit ``e4e2ebf`` (before this work package) by capturing ``(system_prompt, user_prompt)`` at the
``provider_chat`` boundary; they are the assertion that the SQL and RAG routes did not change.
"""
import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg2

from datamind import binding as binding_mod
from datamind import cross_source as cross_source_mod
from datamind.agent import DataMindAgent, route_for
import datamind.rag_engine as rag_engine
import datamind.sql_engine as sql_engine
from datamind.evidence_graph import (EDGE_TYPES, EvidenceEdge, EvidenceGraph, GraphError,
                                     NODE_TYPES, canonical_json, detect_conflicts, stable_id,
                                     unresolved)
from datamind.industrial_rules import evaluate, temperature, timestamp

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'data/evaluation/cross_source_v1.json'
WRITE = 'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'
SHA_A = 'a' * 64
SHA_B = 'b' * 64
FIELDS = list(binding_mod.BINDING_FIELDS)

# Captured at the baseline commit e4e2ebf (system prompt + "\n" + user prompt).
SQL_PROMPT_SHA = 'eba77c041a7f0fbcf8caf528069a2ac3bcff68f7f7083cd5da19f95ca22de014'
RAG_PROMPT_SHA = 'a39c43a5898bb80735a7acb5281c8af082a33b6f7558ea57c2c103e6557ad9a5'
SQL_KEYS = {'answer', 'confidence_level', 'error_code', 'run_id', 'sql_result', 'status', 'turns_used'}
SQL_RESULT_KEYS = {'attempts', 'columns', 'elapsed_ms', 'executed', 'prompt_version', 'query_frame',
                   'row_count', 'rows', 'semantically_verified', 'source_id', 'source_snapshot_id',
                   'sql', 'sql_hash', 'validator_findings', 'verification_contract',
                   'verification_reasons'}
RAG_KEYS = {'answer', 'confidence_level', 'error_code', 'rag_result', 'run_id', 'status', 'turns_used'}
RAG_RESULT_KEYS = {'answer_coverage', 'attempts', 'citations', 'claims', 'elapsed_ms',
                   'prompt_version', 'retrieval', 'retrieved_evidence'}
INDUSTRIAL_QUESTION = '2026年9月A102工序30哪些批次温度超限？生产记录和质检报告是否一致？'
INDUSTRIAL_ANSWER_PREFIX = '工业合成演示：查到 9 个批次、10 条测量。'


def _scope(start, end):
    return {'start': start, 'end': end}


SCOPE_2025 = _scope('2025-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00')
SCOPE_2026_03 = _scope('2026-03-01T00:00:00+08:00', '2026-04-01T00:00:00+08:00')
SCOPE_2026_09 = _scope('2026-09-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')


def sql_evidence(rows, source_ref='demo_retail', snapshot='snap-1'):
    return {'source_ref': source_ref, 'snapshot_id': snapshot, 'columns': [],
            'sql_hash': hashlib.sha256(canonical_json(rows).encode()).hexdigest(), 'rows': rows}


def doc(document_id, version, text, **extra):
    block = {'document_id': document_id, 'document_version': version, 'source_sha256': SHA_A,
             'chunk_id': document_id + '-c1', 'title': document_id, 'text': text}
    block.update(extra)
    return block


def one_row(value='45', unit='day', entity='R-2', version='pol-2025', scope=SCOPE_2026_03,
            metric='refund_age'):
    return {'refund_id': entity, 'metric': metric, 'value': value, 'unit': unit,
            'entity_label': entity, 'applies_to_version': version, 'time_scope': scope}


def clause_block(**overrides):
    base = {'document_id': 'doc-refund-policy', 'document_version': 'pol-2025',
            'source_sha256': SHA_A, 'chunk_id': 'c1', 'title': '退款制度',
            'text': '退款时限：自申请之日起 30 天内未完成视为超时。',
            'valid_from': '2025-01-01T00:00:00+08:00', 'valid_to': '2027-01-01T00:00:00+08:00',
            'clause': {'clause_id': 'CL-REFUND-AGE', 'metric': 'refund_age', 'op': '>',
                       'limit': '30', 'unit': 'day',
                       'valid_from': '2025-01-01T00:00:00+08:00',
                       'valid_to': '2027-01-01T00:00:00+08:00'}}
    block = dict(base)
    block.update(overrides)
    return block


class UnitBindingTest(unittest.TestCase):
    def test_normalize_unit_is_exact_and_scale_preserving(self):
        self.assertEqual(binding_mod.normalize_unit('1123.15', 'K', 'degC'), Decimal('850.00'))
        self.assertEqual(binding_mod.normalize_unit('5', 'mm', 'cm'), Decimal('0.5'))
        self.assertEqual(binding_mod.normalize_unit('1', 'min', 's'), Decimal('60'))
        self.assertEqual(binding_mod.normalize_unit('850', 'degC'), Decimal('850'))
        # 1 s -> min is 0.01666... and is refused instead of rounded
        self.assertIsNone(binding_mod.normalize_unit('1', 's', 'min'))
        self.assertIsNone(binding_mod.normalize_unit('1', 'kg', 'degC'))

    def test_unit_status_marks_unknown_and_units_equivalent_is_symmetric(self):
        self.assertTrue(binding_mod.unit_status('sensorX')['unit_unknown'])
        self.assertFalse(binding_mod.unit_status('min')['unit_unknown'])
        self.assertTrue(binding_mod.units_equivalent('K', 'degC'))
        self.assertTrue(binding_mod.units_equivalent('degC', 'K'))
        self.assertTrue(binding_mod.units_equivalent('min', 's'))
        self.assertFalse(binding_mod.units_equivalent('kg', 'm'))
        self.assertFalse(binding_mod.units_equivalent('sensorX', 'degC'))

    def test_source_agreement_never_picks_a_side(self):
        equal = binding_mod.source_agreement('1123.15', 'K', '850', 'degC')
        self.assertEqual(equal['status'], 'resolved_equivalent')
        self.assertEqual(equal['left_value'], '850.00')
        clash = binding_mod.source_agreement('1123.15', 'K', '845', 'degC')
        self.assertEqual(clash['status'], 'unresolved')
        self.assertEqual(clash['reason'], 'NO_REGISTERED_AUTHORITY_RULE')
        self.assertEqual((clash['left_value'], clash['right_value']), ('850.00', '845'))
        self.assertEqual(binding_mod.source_agreement('1', 'sensorX', '1', 'degC')['status'],
                         'needs_review')

    def test_version_applies_is_half_open_and_requires_timezone(self):
        self.assertTrue(binding_mod.version_applies('2026-09-01T00:00:00+08:00',
                                                    '2026-09-01T00:00:00+08:00',
                                                    '2026-10-01T00:00:00+08:00'))
        self.assertFalse(binding_mod.version_applies('2026-10-01T00:00:00+08:00',
                                                     '2026-09-01T00:00:00+08:00',
                                                     '2026-10-01T00:00:00+08:00'))
        self.assertTrue(binding_mod.version_applies('2030-01-01T00:00:00+08:00',
                                                    '2026-09-01T00:00:00+08:00', None))
        with self.assertRaises(ValueError):
            binding_mod.version_applies('2026-09-01', '2026-01-01T00:00:00+08:00', None)

    def test_time_scope_overlap_returns_unknown_not_true(self):
        self.assertTrue(binding_mod.time_scope_overlap(SCOPE_2026_09,
                                                       _scope('2026-09-15T00:00:00+08:00', None)))
        self.assertFalse(binding_mod.time_scope_overlap(SCOPE_2026_09, SCOPE_2026_03))
        self.assertFalse(binding_mod.time_scope_overlap(
            _scope('2026-01-01T00:00:00+08:00', '2026-09-01T00:00:00+08:00'), SCOPE_2026_09))
        self.assertIsNone(binding_mod.time_scope_overlap(None, SCOPE_2026_03))
        self.assertIsNone(binding_mod.time_scope_overlap({'start': None, 'end': None},
                                                         SCOPE_2026_03))

    def test_entity_bind_uses_approved_aliases_and_refuses_ambiguity(self):
        aliases = [{'alias': '华东地区', 'canonical_name': '华东', 'entity_type': 'region'},
                   {'alias': 'VIP', 'canonical_name': '钻石', 'entity_type': 'customer_level'},
                   {'alias': '黑卡', 'canonical_name': '钻石', 'entity_type': 'customer_level'},
                   {'alias': '歧义别名', 'canonical_name': '甲', 'entity_type': 'x'},
                   {'alias': '歧义别名', 'canonical_name': '乙', 'entity_type': 'x'}]
        bound = binding_mod.entity_bind('华东地区', aliases)
        self.assertEqual((bound['entity_id'], bound['match']), ('华东', 'approved_entity_alias'))
        literal = binding_mod.entity_bind('A102', aliases)
        self.assertEqual((literal['entity_id'], literal['match']), ('A102', 'canonical_literal'))
        ambiguous = binding_mod.entity_bind('歧义别名', aliases)
        self.assertEqual(ambiguous['match'], 'unbound')
        self.assertTrue(ambiguous['ambiguous'])
        self.assertIsNone(binding_mod.entity_bind('', aliases)['entity_id'])

    def test_binding_record_requires_all_seven_fields(self):
        good = binding_mod.build_binding(entity_id='华东', entity_label='华东', version='v1',
                                         unit='CNY', time_scope=SCOPE_2025, source_ref='demo_retail',
                                         source_sha256=SHA_A)
        self.assertTrue(binding_mod.binding_complete(good)[0])
        bad = binding_mod.build_binding(entity_id='华东', entity_label='华东', version=None,
                                        unit='sensorX', time_scope=None, source_ref='',
                                        source_sha256='nope')
        problems = binding_mod.binding_problems(bad)
        for expected in ('version', 'time_scope', 'source_ref', 'unit:UNIT_UNKNOWN',
                         'source_sha256:NOT_SHA256'):
            self.assertIn(expected, problems)

    def test_industrial_rules_still_call_binding_and_keep_their_contract(self):
        self.assertEqual(temperature('1123.15', 'K'), Decimal('850.00'))
        for value, unit in (('850', 'sensorX'), ('NaN', 'degC'), ('inf', 'K')):
            with self.assertRaises(ValueError):
                temperature(value, unit)
        with self.assertRaises(ValueError):
            timestamp('2026-09-01')
        import datamind.industrial_rules as rules
        source = Path(rules.__file__).read_text(encoding='utf-8')
        self.assertIn('from .binding import', source)
        self.assertLess(len(source.splitlines()), 120)


class EvidenceGraphTest(unittest.TestCase):
    def two_node_graph(self):
        graph = EvidenceGraph('run-test')
        left = graph.make_node('measurement', 'M-1', unit='K', version='A102-V2',
                               source_sha256=SHA_A,
                               payload={'source_kind': 'sql', 'source_ref': 'demo_industrial',
                                        'metric': 'holding_temperature', 'value': '1123.15',
                                        'entity_id': 'A102', 'entity_label': 'A102',
                                        'time_scope': SCOPE_2026_09})
        right = graph.make_node('doc_block', 'C-1', unit='degC', version='A102-V2',
                                source_sha256=SHA_B,
                                payload={'source_kind': 'doc', 'source_ref': 'doc-qc',
                                         'metric': 'holding_temperature', 'value': '845',
                                         'entity_id': 'A102', 'entity_label': 'A102',
                                         'time_scope': SCOPE_2026_09})
        return graph, left, right

    def test_node_and_edge_type_whitelists_are_enforced(self):
        graph = EvidenceGraph()
        with self.assertRaises(GraphError) as caught:
            graph.make_node('claim', 'c1')
        self.assertEqual(caught.exception.code, 'NODE_TYPE_NOT_ALLOWED')
        with self.assertRaises(GraphError) as caught:
            graph.make_node('sql_row', '')
        self.assertEqual(caught.exception.code, 'NODE_REF_EMPTY')
        node = graph.make_node('sql_row', 'r1')
        other = graph.make_node('doc_block', 'd1')
        with self.assertRaises(GraphError) as caught:
            graph.add_edge(node.node_id, other.node_id, 'derived_from', evidence='x')
        self.assertEqual(caught.exception.code, 'EDGE_TYPE_NOT_ALLOWED')
        with self.assertRaises(GraphError) as caught:
            graph.add_edge(node.node_id, other.node_id, 'supports', evidence='  ')
        self.assertEqual(caught.exception.code, 'EDGE_EVIDENCE_EMPTY')
        with self.assertRaises(GraphError) as caught:
            graph.add_edge(node.node_id, node.node_id, 'supports', evidence='x')
        self.assertEqual(caught.exception.code, 'EDGE_SELF_REFERENCE')
        self.assertEqual(set(NODE_TYPES), {'sql_row', 'doc_block', 'measurement', 'spec_clause'})
        self.assertEqual(set(EDGE_TYPES), {'entity_match', 'version_scope', 'unit_equivalent',
                                           'time_overlap', 'supports', 'conflicts'})

    def test_symmetric_edges_are_canonical_and_cycles_only_count_directional_edges(self):
        graph = EvidenceGraph()
        a = graph.make_node('sql_row', 'a')
        b = graph.make_node('doc_block', 'b')
        graph.add_edge(b.node_id, a.node_id, 'entity_match', evidence='same entity')
        graph.add_edge(a.node_id, b.node_id, 'entity_match', evidence='same entity')
        self.assertEqual(len(graph.edges()), 1)
        edge = graph.edges()[0]
        self.assertLess(edge.src, edge.dst)
        graph.add_edge(a.node_id, b.node_id, 'supports', evidence='doc supports row')
        graph.add_edge(b.node_id, a.node_id, 'version_scope', evidence='version covers row')
        problems = [item['code'] for item in graph.validate()]
        self.assertIn('EDGE_CYCLE', problems)

    def test_validate_reports_dangling_and_unknown_edges_on_an_imported_graph(self):
        graph = EvidenceGraph()
        node = graph.make_node('sql_row', 'a')
        graph._edges[('missing', node.node_id, 'supports')] = EvidenceEdge(
            'missing', node.node_id, 'supports', evidence='imported')
        problems = {item['code'] for item in graph.validate()}
        self.assertIn('EDGE_DANGLING_SRC', problems)
        graph._edges.clear()
        self.assertEqual(graph.validate(), [])

    def test_serialisation_is_deterministic_and_content_addressed(self):
        first, left, right = self.two_node_graph()
        second = EvidenceGraph('run-test')
        second.make_node('doc_block', 'C-1', unit='degC', version='A102-V2', source_sha256=SHA_B,
                         payload=right.payload)
        second.make_node('measurement', 'M-1', unit='K', version='A102-V2', source_sha256=SHA_A,
                         payload=left.payload)
        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(stable_id('n', {'b': 1, 'a': 2}), stable_id('n', {'a': 2, 'b': 1}))

    def test_conflict_keeps_both_values_and_needs_a_registered_authority(self):
        graph, left, right = self.two_node_graph()
        records = detect_conflicts(graph)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['resolution_status'], 'CONFLICT_UNRESOLVED')
        self.assertEqual(record['conflict_type'], 'value_conflict')
        self.assertEqual((record['left']['value'], record['right']['value']),
                         ('1123.15', '845'))
        self.assertEqual([edge.edge_type for edge in graph.edges()], ['conflicts'])
        self.assertEqual(len(unresolved(records)), 1)
        rule = {'rule_id': 'auth-1',
                'payload': {'version': 'source-authority-v1', 'selected_source': 'production',
                            'context': {'scope': {'part_id': 'A102',
                                                  'metric': 'holding_temperature'}}}}
        resolved_records = detect_conflicts(graph, [rule])
        self.assertEqual(resolved_records[0]['resolution_status'],
                         'resolved_by_registered_authority')
        self.assertEqual(resolved_records[0]['authority_rule_id'], 'auth-1')
        self.assertEqual(resolved_records[0]['selected_node_id'], left.node_id)
        self.assertEqual((resolved_records[0]['left']['value'],
                          resolved_records[0]['right']['value']), ('1123.15', '845'))

    def test_agreeing_values_and_different_versions_are_not_conflicts(self):
        graph, left, right = self.two_node_graph()
        right.payload['value'] = '1123.15'
        right.unit = 'K'
        self.assertEqual(detect_conflicts(graph), [])
        graph, left, right = self.two_node_graph()
        right.version = 'A102-V1'
        self.assertEqual(detect_conflicts(graph), [])

    def test_incomparable_units_are_reported_as_a_conflict(self):
        graph, left, right = self.two_node_graph()
        right.unit = 'kg'
        right.payload['unit'] = 'kg'
        record = detect_conflicts(graph)[0]
        self.assertEqual(record['conflict_type'], 'unit_incomparable')
        self.assertEqual(record['rule_id'], 'UNITS_NOT_COMPARABLE')


class PipelineFailClosedTest(unittest.TestCase):
    def run_plan(self, rows, blocks, **kwargs):
        return cross_source_mod.plan('依据制度找出超过时限的退款单，与报告是否一致？',
                                     ['demo_retail'], ['public_kb'],
                                     sql_evidence=sql_evidence(rows) if rows is not None else None,
                                     rag_evidence={'blocks': blocks} if blocks is not None else None,
                                     **kwargs)

    def test_missing_side_refuses_with_a_stable_code(self):
        result = self.run_plan([one_row()], None)
        self.assertEqual((result['status'], result['error_code']),
                         ('abstained', cross_source_mod.CROSS_SOURCE_EVIDENCE_INSUFFICIENT))
        self.assertEqual(result['failure_detail'], 'DOC_EVIDENCE_EMPTY')
        self.assertNotIn('查询结果', result['answer'])
        result = self.run_plan(None, [clause_block()])
        self.assertEqual(result['error_code'], cross_source_mod.CROSS_SOURCE_EVIDENCE_INSUFFICIENT)
        self.assertEqual(result['claims'], [])
        result = self.run_plan(None, None)
        self.assertEqual(result['error_code'], cross_source_mod.CROSS_SOURCE_EVIDENCE_INSUFFICIENT)
        self.assertEqual(result['claims'], [])

    def test_unbound_document_version_is_doc_binding_missing_not_a_guess(self):
        rows = [one_row(version='A102-V2')]
        blocks = [clause_block(document_version='A102-V1')]
        blocks[0]['clause']['valid_from'] = '2024-01-01T00:00:00+08:00'
        blocks[0]['clause']['valid_to'] = '2025-01-01T00:00:00+08:00'
        blocks[0]['valid_from'] = '2024-01-01T00:00:00+08:00'
        blocks[0]['valid_to'] = '2025-01-01T00:00:00+08:00'
        result = self.run_plan(rows, blocks)
        self.assertEqual((result['status'], result['error_code']),
                         ('failed', cross_source_mod.DOC_BINDING_MISSING))
        self.assertIn('VERSION_NOT_APPLICABLE', result['failure_detail'])
        self.assertEqual(len(result['repair_ledger']), 1)
        self.assertFalse(result['repair_ledger'][0]['applied'])
        self.assertNotIn('查询结果', result['answer'])

    def test_a_competing_document_value_on_another_revision_is_refused(self):
        rows = [one_row(version='pol-2025')]
        blocks = [clause_block(),
                  doc('doc-report', 'QC-2026', '退款单 R-2 的退款时长 50 天。', entity_label='R-2',
                      applies_to_version='QC-2026', metric='refund_age', value='50', unit='day',
                      time_scope=SCOPE_2026_03)]
        result = self.run_plan(rows, blocks)
        self.assertEqual(result['error_code'], cross_source_mod.DOC_BINDING_MISSING)
        self.assertIn('VERSION_MISMATCH', result['failure_detail'])
        self.assertEqual(result['conflicts'], [])

    def test_conflict_is_shown_with_both_sides_and_unresolved(self):
        rows = [one_row(version='pol-2025')]
        blocks = [clause_block(),
                  doc('doc-report', 'QC-2026', '退款单 R-2 的退款时长 50 天。', entity_label='R-2',
                      applies_to_version='pol-2025', metric='refund_age', value='50', unit='day',
                      time_scope=SCOPE_2026_03)]
        result = self.run_plan(rows, blocks)
        self.assertEqual((result['status'], result['error_code']),
                         ('abstained', cross_source_mod.CONFLICT_UNRESOLVED))
        self.assertEqual(len(result['unresolved_conflicts']), 1)
        record = result['conflicts'][0]
        self.assertEqual((record['left']['value'], record['right']['value']), ('45', '50'))
        self.assertTrue(record['left']['node_id'] and record['right']['node_id'])
        self.assertIn('CONFLICT_UNRESOLVED', result['answer'])

    def test_registered_authority_is_the_only_way_a_conflict_converges(self):
        rows = [one_row(version='pol-2025')]
        blocks = [clause_block(),
                  doc('doc-report', 'QC-2026', '退款单 R-2 的退款时长 50 天。', entity_label='R-2',
                      applies_to_version='pol-2025', metric='refund_age', value='50', unit='day',
                      time_scope=SCOPE_2026_03)]
        rule = {'rule_id': 'auth-refund',
                'payload': {'version': 'source-authority-v1', 'selected_source': 'production',
                            'context': {'scope': {'metric': 'refund_age', 'part_id': 'R-2'}}}}
        result = self.run_plan(rows, blocks, authority_rules=[rule])
        self.assertEqual((result['status'], result['error_code']), ('completed', None))
        self.assertEqual(result['conflicts'][0]['resolution_status'],
                         'resolved_by_registered_authority')
        self.assertEqual((result['conflicts'][0]['left']['value'],
                          result['conflicts'][0]['right']['value']), ('45', '50'))

    def test_one_bounded_repair_fills_a_missing_version_and_is_accounted(self):
        rows = [one_row()]
        rows[0].pop('applies_to_version')
        evidence = sql_evidence(rows, snapshot=None)
        result = cross_source_mod.plan('依据制度找出超过时限的退款单，与报告是否一致？',
                                       ['demo_retail'], ['public_kb'], sql_evidence=evidence,
                                       rag_evidence={'blocks': [clause_block()]})
        self.assertEqual(len(result['repair_ledger']), 1)
        entry = result['repair_ledger'][0]
        self.assertEqual((entry['kind'], entry['applied']), ('version_from_time_scope', True))
        self.assertEqual(entry['after'], 'pol-2025')
        self.assertEqual((result['status'], result['error_code']), ('completed', None))
        self.assertEqual(result['claims'][0]['version'], 'pol-2025')
        self.assertTrue(result['repair_applied'])

    def test_claims_carry_the_seven_binding_fields_and_resolving_evidence_ids(self):
        result = self.run_plan([one_row()], [clause_block()])
        node_ids = {node['node_id'] for node in result['evidence_graph']['nodes']}
        self.assertTrue(result['claims'])
        for claim in result['claims']:
            self.assertEqual(claim['binding_status'], 'bound')
            self.assertEqual(claim['binding_problems'], [])
            for field in FIELDS:
                self.assertIsNotNone(claim['binding'][field])
                self.assertIsNotNone(claim[field])
            self.assertTrue(claim['evidence_node_ids'])
            self.assertTrue(set(claim['evidence_node_ids']) <= node_ids)
        self.assertEqual(result['graph_problems'], [])
        self.assertEqual(len(result['graph_summary']['graph_digest']), 64)

    def test_graph_is_rebuildable_from_the_serialised_form_with_the_same_digest(self):
        result = self.run_plan([one_row()], [clause_block()])
        first = result['graph_summary']['graph_digest']
        again = self.run_plan([one_row()], [clause_block()])
        self.assertEqual(first, again['graph_summary']['graph_digest'])
        self.assertEqual(result['evidence_graph'], again['evidence_graph'])


class OfflineSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = json.loads(SUITE.read_text(encoding='utf-8'))
        spec = importlib.util.spec_from_file_location('wp2_eval_cross_source',
                                                      ROOT / 'scripts/eval_cross_source.py')
        cls.evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.evaluator)

    def test_suite_shape_four_structures_with_at_least_five_cases_each(self):
        structures = set(self.suite['structures'])
        self.assertEqual(structures, {'P1', 'P2', 'P3', 'P4'})
        counts = {}
        for case in self.suite['cases']:
            counts[case['structure']] = counts.get(case['structure'], 0) + 1
            self.assertIn(case['structure'], structures)
            self.assertTrue(case['question'].strip())
            self.assertTrue(case['expected_answer_checks'])
            self.assertTrue(case['expected_evidence_kinds'])
            self.assertEqual(case['expected_binding_fields'], FIELDS)
            self.assertIn('expected_failure_mode', case)
            for fixture in case['fixtures']:
                self.assertIn(fixture, self.suite['fixtures'])
            for check in case['expected_answer_checks']:
                self.assertTrue(callable(getattr(self.evaluator, 'check_one')))
        for name in structures:
            self.assertGreaterEqual(counts[name], 5, name)
        kinds = {json.dumps(case['expected_evidence_kinds']) for case in self.suite['cases']}
        self.assertGreater(len(kinds), 1)

    def test_every_frozen_case_passes_offline_without_a_model_call(self):
        self.evaluator.MODEL_CALLS['count'] = 0
        cases = self.evaluator.run_offline(self.suite)
        failed = [case for case in cases if not case['passed']]
        self.assertEqual(failed, [], msg=json.dumps(failed, ensure_ascii=False)[:2000])
        self.assertEqual(self.evaluator.MODEL_CALLS['count'], 0)
        self.assertEqual(len(cases), len(self.suite['cases']))
        modes = {case['error_code'] for case in cases}
        self.assertIn('CONFLICT_UNRESOLVED', modes)
        self.assertIn('DOC_BINDING_MISSING', modes)
        self.assertIn(None, modes)


class NonCrossSourceByteStabilityTest(unittest.TestCase):
    def test_sql_prompt_and_result_shape_are_byte_identical(self):
        captured = []

        def provider(profile, system, user, **kwargs):
            captured.append((system, user))
            return {'ok': True, 'text': json.dumps({
                'sql': "SELECT count(*) AS n FROM orders WHERE created_at >= '2024-01-01' "
                       "AND created_at < '2025-01-01'", 'interpretation': '自检'})}

        with patch.object(sql_engine, 'provider_chat', provider), \
                patch.object(sql_engine, 'model_profile',
                             lambda cfg=None: {'api_key': 'frozen-test', 'model': 'test'}):
            agent = DataMindAgent()
            try:
                result = agent.run('2024年一共有多少笔订单？', database_sources=['demo_retail'])
            finally:
                agent.close()
        digest = hashlib.sha256((captured[0][0] + '\n' + captured[0][1]).encode()).hexdigest()
        self.assertEqual(digest, SQL_PROMPT_SHA)
        self.assertEqual((result['status'], result['answer'], result['confidence_level']),
                         ('COMPLETED', '查询结果：997。', 'insufficient'))
        self.assertEqual(set(result), SQL_KEYS)
        self.assertNotIn('cross_source_result', result)
        self.assertEqual(set(result['sql_result']), SQL_RESULT_KEYS)

    def test_rag_prompt_and_result_shape_are_byte_identical(self):
        captured = []

        def provider(profile, system, user, **kwargs):
            captured.append((system, user))
            return {'ok': True, 'text': json.dumps({'abstain': True, 'claims': []})}

        with patch.object(rag_engine, 'provider_chat', provider):
            agent = DataMindAgent()
            try:
                result = agent.run('退款条件是什么？')
            finally:
                agent.close()
        digest = hashlib.sha256((captured[0][0] + '\n' + captured[0][1]).encode()).hexdigest()
        self.assertEqual(digest, RAG_PROMPT_SHA)
        self.assertEqual((result['status'], result['error_code']),
                         ('ABSTAINED', 'INSUFFICIENT_EVIDENCE'))
        self.assertEqual(set(result), RAG_KEYS)
        self.assertNotIn('cross_source_result', result)
        self.assertEqual(set(result['rag_result']), RAG_RESULT_KEYS)

    def test_industrial_result_is_unchanged(self):
        agent = DataMindAgent()
        try:
            result = agent.run(INDUSTRIAL_QUESTION, database_sources=['demo_industrial'],
                               document_collections=['industrial_demo'])
        finally:
            agent.close()
        self.assertEqual((result['status'], result['confidence_level']), ('COMPLETED', 'medium'))
        self.assertTrue(result['answer'].startswith(INDUSTRIAL_ANSWER_PREFIX), result['answer'][:80])
        self.assertEqual(len(result['industrial_result']['rows']), 10)
        self.assertNotIn('cross_source_result', result)
        self.assertEqual(set(result), {'answer', 'confidence_level', 'error_code',
                                       'industrial_result', 'run_id', 'status', 'turns_used'})

    def test_route_gate_is_unchanged(self):
        self.assertEqual(route_for('结合数据库，2025年销售额最高的品类是哪个？该品类的渠道经验手册怎么说？'),
                         'cross_source')
        self.assertEqual(route_for('退款条件是什么？'), 'rag')
        self.assertEqual(route_for('2024年一共有多少笔订单？'), 'sql')
        self.assertEqual(route_for(INDUSTRIAL_QUESTION), 'industrial')


class AgentWiringTest(unittest.TestCase):
    QUESTION = '结合数据库，2025年销售额最高的品类是哪个？该品类的渠道经验手册怎么说？'

    def setUp(self):
        self.run_ids = []

    def tearDown(self):
        if not self.run_ids:
            return
        with psycopg2.connect(WRITE) as conn, conn.cursor() as cur:
            for run_id in self.run_ids:
                for table in ('trace_events', 'evidence_edges', 'evidence_nodes', 'claims', 'runs'):
                    cur.execute(f'DELETE FROM {table} WHERE run_id=%s', (run_id,))

    def evidence(self, docs=True, conflicting=False):
        rows = [{'category': '家用电器', 'metric': 'sales_amount', 'value': '128000.50',
                 'unit': 'CNY', 'time_scope': SCOPE_2025}]
        blocks = []
        if docs:
            blocks.append(doc('doc-channel', '2025-04',
                              '家用电器品类 2025 年渠道经验：线下门店转化率最高。',
                              entity_label='家用电器'))
            if conflicting:
                blocks.append(doc('doc-report', '2025-04', '家用电器销售额为 99000 元。',
                                  entity_label='家用电器', applies_to_version='retail-snapshot-v3',
                                  metric='sales_amount', value='99000', unit='CNY',
                                  time_scope=SCOPE_2025))
        return {'sql': sql_evidence(rows, snapshot='retail-snapshot-v3'), 'docs': blocks}

    def run_agent(self, evidence):
        agent = DataMindAgent()
        self.run_ids.append(agent.run_id)
        try:
            return agent.run(self.QUESTION, database_sources=['demo_retail'],
                             # M2 (round 1) corrected the collection vocabulary: `public_kb`
                             # never existed, so the real collection is used here.
                             document_collections=['demo_kb'],
                             cross_source_evidence=evidence)
        finally:
            agent.close()

    def test_completed_cross_source_adds_only_the_new_key_and_persists_the_graph(self):
        result = self.run_agent(self.evidence())
        self.assertEqual((result['status'], result['confidence_level']),
                         ('COMPLETED', 'medium'))
        self.assertIn('cross_source_result', result)
        payload = result['cross_source_result']
        self.assertEqual(payload['kind'], 'cross_source')
        self.assertTrue(payload['claims'])
        self.assertEqual(payload['graph_problems'], [])
        self.assertTrue(all(claim['binding_status'] == 'bound' for claim in payload['claims']))
        self.assertEqual(set(result), {'answer', 'confidence_level', 'error_code',
                                       'cross_source_result', 'run_id', 'status', 'turns_used'})
        with psycopg2.connect(WRITE) as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM evidence_nodes WHERE run_id=%s', (result['run_id'],))
            nodes = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM evidence_edges WHERE run_id=%s', (result['run_id'],))
            edges = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM claims WHERE run_id=%s', (result['run_id'],))
            claims = cur.fetchone()[0]
        self.assertEqual(nodes, payload['graph_summary']['node_count'])
        self.assertEqual(edges, payload['graph_summary']['edge_count'])
        self.assertEqual(claims, len(payload['claims']))
        self.assertGreater(nodes, 0)

    def test_conflict_reaches_the_api_shape_as_abstained_with_both_values(self):
        result = self.run_agent(self.evidence(conflicting=True))
        self.assertEqual((result['status'], result['error_code']),
                         ('ABSTAINED', 'CONFLICT_UNRESOLVED'))
        conflicts = result['cross_source_result']['conflicts']
        self.assertEqual(len(conflicts), 1)
        self.assertNotIn(None, (conflicts[0]['left']['value'], conflicts[0]['right']['value']))

    def test_missing_document_side_stays_fail_closed_through_the_agent(self):
        result = self.run_agent(self.evidence(docs=False))
        self.assertEqual((result['status'], result['error_code']),
                         ('ABSTAINED', 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT'))
        payload = result['cross_source_result']
        self.assertEqual(payload['failure_detail'], 'DOC_EVIDENCE_EMPTY')
        self.assertNotIn('查询结果', payload['answer'])


class LiveProjectionContractTest(unittest.TestCase):
    """Round 2: the live (real SQL / real retrieval) contract, with adapters injected.

    No upstream model is called: ``sql_engine.ask`` and ``KnowledgeRetriever`` are replaced by
    fakes that return the *real* shapes (Decimal / datetime cells, a ``query_frame`` whose timezone
    is a sibling key of bare dates). That is what the frozen fixtures never exercised.
    """

    LIVE_QUESTION = '结合数据库，2025年销售额最高的品类是哪个？该品类的渠道经验手册怎么说？'

    @staticmethod
    def ask_result(columns=('category', 'sales_amount'), rows=None, *, status='completed',
                   frame=None, error_code=None):
        return {'status': status, 'error_code': error_code, 'source_id': 'demo_retail',
                'source_snapshot_id': 'snap-demo_retail-e9495c660cd1', 'columns': list(columns),
                'rows': rows if rows is not None else [['家电', Decimal('1397497.39550')]],
                'row_count': len(rows if rows is not None else [[]]), 'sql': 'SELECT 1',
                'sql_hash': 'a' * 64,
                'query_frame': frame if frame is not None else {
                    'intent': 'rank_entities', 'metrics': ['demo_retail.sales_amount'],
                    'time_filters': [{'field_id': 'orders.created_at', 'from': '2025-01-01',
                                      'to': '2026-01-01', 'timezone': 'Asia/Shanghai'}]}}

    @staticmethod
    def retriever(hits):
        class FakeRetriever:
            def __init__(self, *args, **kwargs):
                pass

            def retrieve(self, question, *, collections=None, limit=8, **kwargs):
                self.question = question
                return {'hits': [dict(hit) for hit in hits], 'mode': 'lexical', 'warnings': [],
                        'snapshot_id': 'kb-fake'}

        return FakeRetriever

    @staticmethod
    def hit(chunk_id, text, *, title='渠道经验手册', document_id='doc-channel', version=1):
        return {'chunk_id': chunk_id, 'document_id': document_id, 'title': title,
                'collection': 'demo_kb', 'document_version': version,
                'document_sha256': SHA_B, 'page_start': 1, 'exact_text': text}

    def test_sql_scope_drops_only_a_trailing_document_request(self):
        self.assertEqual(cross_source_mod.sql_scope(self.LIVE_QUESTION),
                         '结合数据库，2025年销售额最高的品类是哪个')
        # A single rule-embedded sentence is passed through untouched — no intent guessing.
        rule = ('结合数据库的退款单记录，根据《售后服务与退款管理制度》的退款时限，'
                '找出超过该时限仍未完成的退款单。')
        self.assertEqual(cross_source_mod.sql_scope(rule), rule)
        # A document question is not stripped when its data intent is absent.
        self.assertEqual(cross_source_mod.sql_scope('该品类的渠道经验手册怎么说？'),
                         '该品类的渠道经验手册怎么说？')

    def test_live_projection_binds_decimal_rows_and_a_timezone_aware_scope(self):
        with patch.object(sql_engine, 'ask', lambda *args, **kwargs: self.ask_result()):
            evidence = cross_source_mod.live_sql_evidence(self.LIVE_QUESTION, ['demo_retail'])
        projection = evidence['projection']
        self.assertEqual(projection['value_column'], 'sales_amount')
        self.assertEqual(projection['metric'], '销售额')
        self.assertEqual(projection['unit'], 'CNY')
        self.assertEqual(projection['sql_question'], '结合数据库，2025年销售额最高的品类是哪个')
        self.assertEqual(projection['time_scope'],
                         {'start': '2025-01-01T00:00:00+08:00', 'end': '2026-01-01T00:00:00+08:00'})
        self.assertEqual(evidence['rows'][0]['value'], Decimal('1397497.39550'))
        self.assertEqual(evidence['rows'][0]['unit'], 'CNY')
        self.assertEqual(cross_source_mod.sql_entity_anchors(evidence), ['家电'])

    def test_live_projection_names_the_entity_from_a_chinese_projection_alias(self):
        answer = self.ask_result(columns=('渠道', '销售额'),
                                 rows=[['online', Decimal('1065789.12500')]])
        with patch.object(sql_engine, 'ask', lambda *args, **kwargs: answer):
            evidence = cross_source_mod.live_sql_evidence(
                '结合数据库，2025年销售额最高的渠道是哪个？该渠道的运营经验手册怎么说？',
                ['demo_retail'])
        self.assertEqual(evidence['projection']['entity_column'], '渠道')
        self.assertEqual(evidence['rows'][0]['entity_label'], 'online')
        self.assertEqual(cross_source_mod.sql_entity_anchors(evidence), ['online'])

    def test_live_projection_refuses_to_guess_between_two_dimensions(self):
        answer = self.ask_result(columns=('region_name', 'channel', 'sales_amount'),
                                 rows=[['华东', 'online', Decimal('10')]])
        with patch.object(sql_engine, 'ask', lambda *args, **kwargs: answer):
            evidence = cross_source_mod.live_sql_evidence(
                '结合数据库，2025年销售额最高的对象是哪个？该对象的说明材料怎么说？',
                ['demo_retail'])
        self.assertIsNone(evidence['projection']['entity_column'])
        self.assertNotIn('entity_label', evidence['rows'][0])

    def test_live_sql_evidence_refuses_an_empty_allow_list(self):
        with self.assertRaises(cross_source_mod.CrossSourceError) as caught:
            cross_source_mod.live_sql_evidence(self.LIVE_QUESTION, [])
        self.assertEqual(caught.exception.code, cross_source_mod.SOURCE_NOT_ALLOWED)

    def test_live_run_answers_from_a_real_row_and_the_entity_anchored_retrieval(self):
        hits = [self.hit('c1', '家电品类 2025 年渠道经验：线下门店转化率最高。'),
                self.hit('c2', '与本次查询无关的运输时效说明。', title='物流说明',
                         document_id='doc-logistics')]
        with patch.object(sql_engine, 'ask', lambda *args, **kwargs: self.ask_result()), \
                patch('datamind.knowledge.KnowledgeRetriever', self.retriever(hits)):
            result = cross_source_mod.run(self.LIVE_QUESTION, database_sources=['demo_retail'],
                                          aliases=[], authority_rules=[])
        self.assertEqual((result['status'], result['error_code']), ('completed', None))
        self.assertEqual(result['graph_problems'], [])
        self.assertTrue(result['claims'])
        for claim in result['claims']:
            self.assertEqual(claim['binding_problems'], [])
            for field in FIELDS:
                self.assertIsNotNone(claim['binding'][field])
        self.assertEqual(result['doc_linkage']['linked'], 1)
        self.assertEqual(result['doc_linkage']['ignored'], 1)
        self.assertIn('家电', result['answer'])
        self.assertIn('1397497.39550', result['answer'])
        self.assertIn('渠道经验手册', result['answer'])
        self.assertNotIn('物流说明', result['answer'])

    def test_live_run_with_only_unlinked_retrieval_still_refuses(self):
        hits = [self.hit('c1', '与本次查询无关的运输时效说明。', title='物流说明',
                         document_id='doc-logistics')]
        with patch.object(sql_engine, 'ask', lambda *args, **kwargs: self.ask_result()), \
                patch('datamind.knowledge.KnowledgeRetriever', self.retriever(hits)):
            result = cross_source_mod.run(self.LIVE_QUESTION, database_sources=['demo_retail'],
                                          aliases=[], authority_rules=[])
        self.assertEqual((result['status'], result['error_code']),
                         ('failed', cross_source_mod.DOC_BINDING_MISSING))
        self.assertEqual(result['failure_detail'], 'NO_DOC_EVIDENCE_LINKED')
        self.assertEqual(result['doc_linkage']['linked'], 0)
        self.assertNotIn('查询结果', result['answer'])

    def test_live_run_reports_an_upstream_clarification_as_no_sql_evidence(self):
        answer = self.ask_result(status='clarification_required', rows=[])
        with patch.object(sql_engine, 'ask', lambda *args, **kwargs: answer), \
                patch('datamind.knowledge.KnowledgeRetriever', self.retriever([])):
            result = cross_source_mod.run(self.LIVE_QUESTION, database_sources=['demo_retail'],
                                          aliases=[], authority_rules=[])
        self.assertEqual(result['failure_detail'], 'SQL_EVIDENCE_EMPTY')
        self.assertEqual(result['sql_evidence']['projection']['status'], 'clarification_required')
        self.assertEqual(result['sql_evidence']['row_count'], 0)


class LiveLinkageTest(unittest.TestCase):
    """Plan-level rules that make the real retrieval usable without weakening fail-closed."""

    QUESTION = '结合数据库，2025年销售额最高的品类是哪个？该品类的渠道经验手册怎么说？'

    def plan(self, rows, blocks, aliases=None):
        return cross_source_mod.plan(
            self.QUESTION, ['demo_retail'], ['demo_kb'],
            sql_evidence=sql_evidence(rows, snapshot='snap-demo_retail-e9495c660cd1'),
            rag_evidence={'blocks': blocks}, aliases=aliases, authority_rules=[])

    def test_a_noise_clause_whose_metric_no_row_carries_cannot_veto_the_answer(self):
        rows = [{'category': '家电', 'metric': 'sales_amount', 'value': '128000.50', 'unit': 'CNY',
                 'time_scope': SCOPE_2025}]
        blocks = [doc('doc-channel', '2025-04', '家电品类 2025 年渠道经验：线下门店转化率最高。'),
                  # Retrieved noise that the clause regex happily turns into a spec_clause with an
                  # unknown validity window: it carries a metric no data row has, so it is context.
                  doc('doc-faq', '2025-01', '物流信息可能延迟，若超过 48 小时未更新请联系客服。')]
        result = self.plan(rows, blocks)
        self.assertEqual((result['status'], result['error_code']), ('completed', None))
        clause_nodes = [node for node in result['evidence_graph']['nodes']
                        if node['node_type'] == 'spec_clause']
        self.assertTrue(clause_nodes)
        self.assertTrue(all(node['node_id'] in result['doc_linkage']['ignored_node_ids']
                            for node in clause_nodes))

    def test_a_relevant_clause_that_does_not_apply_still_fails_closed(self):
        rows = [{'measurement_id': 'M-1', 'batch_id': 'B031', 'metric': 'holding_temperature',
                 'value': '1145', 'unit': 'K', 'entity_label': 'B031',
                 'applies_to_version': 'A102-V2', 'time_scope': SCOPE_2026_09}]
        blocks = [doc('doc-batch', 'BN-031', 'B031 批次温度超限后的说明材料：等待工艺确认。',
                      entity_label='B031', applies_to_version='A102-V2'),
                  # The clause's own document never names the batch entity (same shape as the
                  # frozen cs-p4-05 fixture): it cannot link through entity identity, only through
                  # version applicability — which does not cover the claim.
                  clause_block(document_id='doc-spec-old', document_version='A102-V1',
                               clause={'clause_id': 'CL-A102-30-V1', 'metric': 'holding_temperature',
                                       'op': '>', 'limit': '870', 'unit': 'degC',
                                       'valid_from': '2024-01-01T00:00:00+08:00',
                                       'valid_to': '2025-01-01T00:00:00+08:00'})]
        result = self.plan(rows, blocks)
        self.assertEqual((result['status'], result['error_code']),
                         ('failed', cross_source_mod.DOC_BINDING_MISSING))
        self.assertIn('VERSION_NOT_APPLICABLE', result['failure_detail'])

    def test_an_approved_alias_in_the_document_links_the_entity(self):
        rows = [{'channel': 'online', 'metric': 'sales_amount', 'value': '3120126.71',
                 'unit': 'CNY', 'time_scope': SCOPE_2025}]
        aliases = [{'alias': '线上', 'canonical_name': 'online', 'entity_type': 'channel'}]
        blocks = [doc('doc-channel', '2025-01', '线上商城承接品牌搜索流量，是销售额基本盘。')]
        result = self.plan(rows, blocks, aliases=aliases)
        self.assertEqual((result['status'], result['error_code']), ('completed', None))
        edges = [edge for edge in result['evidence_graph']['edges']
                 if edge['edge_type'] == 'entity_match']
        self.assertTrue(any(edge['binding_type'] == 'approved_alias' for edge in edges))

    def test_answer_cites_the_linked_document_not_the_first_retrieved_block(self):
        rows = [{'category': '家电', 'metric': 'sales_amount', 'value': '128000.50', 'unit': 'CNY',
                 'time_scope': SCOPE_2025}]
        blocks = [doc('doc-logistics', '2025-01', '运输时效说明：大件 3 个工作日送达。',
                      title='物流说明'),
                  doc('doc-channel', '2025-04', '家电品类 2025 年渠道经验：线下门店转化率最高。',
                      title='渠道经验手册')]
        result = self.plan(rows, blocks)
        self.assertEqual(result['status'], 'completed')
        self.assertIn('渠道经验手册', result['answer'])
        self.assertNotIn('物流说明', result['answer'])
        self.assertIn('128000.50', result['answer'])


class LiveEvalBranchTest(unittest.TestCase):
    """``scripts/eval_cross_source.py --live`` counts real answers only — refusals never pass."""

    @classmethod
    def setUpClass(cls):
        cls.suite = json.loads(SUITE.read_text(encoding='utf-8'))
        spec = importlib.util.spec_from_file_location('wp2_eval_cross_source_live',
                                                      ROOT / 'scripts/eval_cross_source.py')
        cls.evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.evaluator)

    @staticmethod
    def completed_result():
        claim = {'claim_id': 'cl-1', 'metric': '销售额', 'value': '1397497.39550',
                 'normalized_value': '1397497.39550', 'unit': 'CNY', 'binding_status': 'bound',
                 'binding_problems': [], 'evidence_node_ids': ['n-1', 'n-2'],
                 'binding': {field: 'x' for field in FIELDS}}
        node = {'node_id': 'n-2', 'node_type': 'doc_block',
                'payload': {'title': '渠道经验手册', 'text': '家电品类渠道经验'}}
        return {'status': 'completed', 'error_code': None, 'failure_detail': None,
                'answer': '查询结果：家电 的 销售额 = 1397497.39550 CNY（来源 demo_retail，版本 snap-1）；'
                          '文档《渠道经验手册》说明：家电品类渠道经验',
                'claims': [claim], 'conflicts': [], 'unresolved_conflicts': [],
                'doc_linkage': {'doc_nodes': 1, 'linked': 1, 'ignored': 0,
                                'linked_node_ids': ['n-2'], 'ignored_node_ids': []},
                'evidence_graph': {'nodes': [node],
                                   'edges': [{'edge_type': 'entity_match'}]},
                'graph_summary': {'node_count': 2, 'edge_count': 1},
                'sql_evidence': {'present': True, 'row_count': 1,
                                 'projection': {'status': 'completed'}},
                'document_evidence': {'block_count': 1}}

    def test_every_frozen_case_declares_a_live_contract_and_answer_cases_are_p1_only(self):
        contracts = {}
        for case in self.suite['cases']:
            expected = case['live_expected']
            self.assertIn(expected['contract'], ('answer', 'fail_closed'))
            self.assertTrue(expected['contract'] == 'answer' or expected.get('blocker'))
            contracts[case['structure']] = contracts.get(case['structure'], set())
            contracts[case['structure']].add(expected['contract'])
            # The live contract is an *addition*; the frozen offline assertions are untouched.
            self.assertTrue(case['expected_answer_checks'])
            self.assertIn('expected_failure_mode', case)
        self.assertEqual(contracts['P1'], {'answer'})
        for structure in ('P2', 'P3', 'P4'):
            self.assertEqual(contracts[structure], {'fail_closed'}, structure)

    def test_live_contract_flags_a_completed_answer_without_a_linked_document(self):
        case = {'case_id': 'x', 'live_expected': {'contract': 'answer'}}
        result = self.completed_result()
        self.assertEqual(self.evaluator.live_failures(case, result), [])
        unlinked = self.completed_result()
        unlinked['doc_linkage'] = {'doc_nodes': 1, 'linked': 0, 'ignored': 1,
                                   'linked_node_ids': [], 'ignored_node_ids': ['n-2']}
        checks = [item['check'] for item in self.evaluator.live_failures(case, unlinked)]
        self.assertIn('live_doc_linked', checks)

    def test_fail_closed_refusal_is_never_counted_as_an_answer(self):
        refusal = {'case_id': 'r', 'structure': 'P2', 'live_expected': {'contract': 'fail_closed'},
                   'status': 'abstained', 'error_code': 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT',
                   'failure_reason_class': 'sql_evidence_empty', 'passed': True, 'failures': []}
        answered = dict(refusal, case_id='a', structure='P1',
                        live_expected={'contract': 'answer'}, status='completed',
                        error_code=None, passed=True, failures=[])
        summary = self.evaluator.live_summary([refusal, answered])
        self.assertEqual(summary['answerable_cases'], 1)
        self.assertEqual(summary['answered_and_verified'], 1)
        self.assertEqual(summary['answered_rate'], '1/1')
        self.assertEqual(summary['declared_refusal_cases'], 1)
        self.assertEqual(summary['declared_refusal_fail_closed_ok'], 1)
        # A declared refusal that actually answered is a contract violation, not a pass.
        sneaky = dict(refusal, status='completed', passed=False)
        self.assertEqual(self.evaluator.live_summary([sneaky])['answered_and_verified'], 0)

    def test_failure_classes_are_specific_and_never_a_generic_bucket(self):
        classify = self.evaluator.classify_failure
        self.assertEqual(classify({'status': 'completed', 'error_code': None}), 'completed')
        self.assertEqual(classify({'status': 'abstained', 'failure_detail': 'SQL_EVIDENCE_EMPTY',
                                   'sql_evidence': {'projection': {'status': 'failed',
                                                                   'error_code': 'MODEL_EMPTY_RESPONSE'}}}),
                         'upstream_model_error')
        self.assertEqual(classify({'status': 'abstained', 'failure_detail': 'SQL_EVIDENCE_EMPTY',
                                   'sql_evidence': {'projection': {
                                       'status': 'clarification_required'}}}),
                         'upstream_clarification_required')
        self.assertEqual(classify({'status': 'failed',
                                   'failure_detail': 'DOC_NODE_BINDING_INCOMPLETE:time_scope'}),
                         'doc_node_binding_incomplete')
        self.assertEqual(classify({'status': 'failed',
                                   'failure_detail': 'VERSION_MISMATCH:a:v1!=v2'}),
                         'version_mismatch')
        self.assertEqual(classify({'status': 'failed',
                                   'failure_detail': 'VERSION_NOT_APPLICABLE:VERSION_NOT_APPLICABLE'}),
                         'doc_version_not_applicable')
        self.assertEqual(classify({'status': 'failed', 'error_code': 'CONFLICT_UNRESOLVED',
                                   'failure_detail': 'value_conflict'}), 'conflict_unresolved')

    def test_only_model_errors_are_retried_and_every_attempt_is_recorded(self):
        case = {'case_id': 'p1-x', 'structure': 'P1', 'question': 'q',
                'fixtures': ['p1_category'], 'live_expected': {'contract': 'answer'}}
        suite = {'structures': {'P1': 'x'}, 'cases': [case], 'fixtures': {'p1_category': {}}}
        attempts = []

        def flaky(question, **kwargs):
            attempts.append(question)
            if len(attempts) < 3:
                return {'status': 'abstained', 'error_code': 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT',
                        'failure_detail': 'SQL_EVIDENCE_EMPTY', 'claims': [], 'answer': '',
                        'sql_evidence': {'present': False, 'row_count': 0,
                                         'projection': {'status': 'failed',
                                                        'error_code': 'MODEL_EMPTY_RESPONSE'}}}
            return self.completed_result()

        with patch.object(self.evaluator.cross_source, 'run', flaky), \
                patch.object(self.evaluator, '_binding_contract',
                             lambda result: {'hit': True, 'claims': 1, 'incomplete_claims': [],
                                             'claims_missing_fields': []}), \
                patch.object(self.evaluator, '_answer_evidence',
                             lambda result: {'carries_evidence_and_binding': True}), \
                patch.object(self.evaluator, '_edge_type_counts', lambda result: {'entity_match': 1}):
            cases = self.evaluator.run_live(suite)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(cases[0]['attempts']), 3)
        self.assertTrue(cases[0]['passed'])
        self.assertEqual([item['upstream_error_code'] for item in cases[0]['attempts']],
                         ['MODEL_EMPTY_RESPONSE'] * 2 + [None])

        attempts.clear()

        def stubborn(question, **kwargs):
            attempts.append(question)
            return {'status': 'abstained', 'error_code': 'CROSS_SOURCE_EVIDENCE_INSUFFICIENT',
                    'failure_detail': 'SQL_EVIDENCE_EMPTY', 'claims': [], 'answer': '',
                    'sql_evidence': {'present': False, 'row_count': 0,
                                     'projection': {'status': 'failed',
                                                    'error_code': 'REPAIR_BUDGET_EXHAUSTED'}}}

        with patch.object(self.evaluator.cross_source, 'run', stubborn):
            cases = self.evaluator.run_live(suite)
        self.assertEqual(len(attempts), 1)  # a non-MODEL_* failure is reported, not retried
        self.assertEqual(len(cases[0]['attempts']), 1)
        self.assertFalse(cases[0]['passed'])


class IndustrialRulesParityTest(unittest.TestCase):
    """The refactor must not change ``evaluate`` for the industrial demo rows."""

    @classmethod
    def setUpClass(cls):
        from datamind.industrial import run
        cls.result = run(INDUSTRIAL_QUESTION,
                         dsn='host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user '
                             'password=readonly_dev')
        cls.requirements = [fact for fact in cls.result['facts'] if fact['fact_kind'] == 'requirement']
        cls.reports = [fact for fact in cls.result['facts'] if fact['fact_kind'] == 'measurement']

    def test_evaluate_keeps_the_documented_outcomes(self):
        import copy
        agreements = {}
        for row in self.result['sql_rows']:
            verdict = evaluate(copy.deepcopy(row), copy.deepcopy(self.requirements),
                               copy.deepcopy(self.reports))
            agreements[verdict['agreement']] = agreements.get(verdict['agreement'], 0) + 1
            self.assertEqual(verdict['normalized_unit'], 'degC')
        self.assertEqual(len(self.result['sql_rows']), 10)
        self.assertEqual(agreements.get('unresolved'), 1)
        self.assertTrue(all(value >= 0 for value in agreements.values()))


if __name__ == '__main__':
    unittest.main()
