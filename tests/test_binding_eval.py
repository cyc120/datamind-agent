"""WP3 binding-trap set + three-arm ablation: frozen labels, metric arithmetic, arm behaviour.

Every case here is deterministic and needs no database: the trap set, the three arms and the
report writer are pure Python.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from datamind import binding_eval as be

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
import eval_binding  # noqa: E402


def tiny(cases):
    return {'suite': 'tiny', 'version': 'tiny', 'sha256': 'a' * 64, 'payload': {'cases': cases},
            'cases': cases}


SCOPE = {'start': '2025-01-01T00:00:00+08:00', 'end': '2025-02-01T00:00:00+08:00'}


def synthetic_case(case_id='syn-1', *, conflict_label=True, answerable=False,
                   resolution='needs_review', sql=('A1', 'm', '10', 'mm'),
                   doc=('A1', 'm', '20', 'mm')):
    return {'case_id': case_id, 'trap_kind': 'unit_mismatch', 'question': 'q',
            'sql': {'source_ref': 's', 'snapshot_id': 'snap', 'rows': [
                {'entity_label': sql[0], 'metric': sql[1], 'value': sql[2], 'unit': sql[3],
                 'time_scope': dict(SCOPE)}]},
            'docs': {'source_ref': 'd', 'blocks': [
                {'document_id': 'd1', 'document_version': 'snap', 'chunk_id': 'd1-c1',
                 'entity_label': doc[0], 'metric': doc[1], 'value': doc[2], 'unit': doc[3],
                 'time_scope': dict(SCOPE), 'text': 'doc', 'source_sha256': 'b' * 64}]},
            'authority_rules': [], 'aliases': [],
            'primary': {'side': 'sql', 'metric': sql[1]},
            'expected_binding': {'entity_id': sql[0], 'version': 'snap', 'unit': sql[3],
                                 'time_scope': dict(SCOPE)},
            'expected_answerable': answerable, 'expected_conflict': conflict_label,
            'expected_resolution': resolution, 'expected_value': sql[2]}


class FrozenDatasetTests(unittest.TestCase):
    def test_frozen_set_matches_the_pinned_digest(self):
        dataset = be.load_dataset(expected_sha256=be.TRAPS_SHA256)
        self.assertEqual(dataset['suite'], 'binding_traps_v1')
        self.assertEqual(len(dataset['cases']), 40)
        self.assertEqual(be.dataset_digest(dataset['payload']), be.TRAPS_SHA256)

    def test_four_trap_kinds_with_at_least_eight_cases_each(self):
        dataset = be.load_dataset()
        counts = {}
        for case in dataset['cases']:
            counts[case['trap_kind']] = counts.get(case['trap_kind'], 0) + 1
        self.assertEqual(set(counts), set(be.TRAP_KINDS))
        for kind, count in counts.items():
            self.assertGreaterEqual(count, 8, kind)

    def test_every_case_freezes_binding_conflict_and_resolution_labels(self):
        dataset = be.load_dataset()
        for case in dataset['cases']:
            self.assertIn(case['expected_resolution'], be.RESOLUTIONS, case['case_id'])
            for field in ('entity_id', 'version', 'unit', 'time_scope'):
                self.assertIn(field, case['expected_binding'], case['case_id'])
            self.assertIsInstance(case['expected_answerable'], bool)
            self.assertIsInstance(case['expected_conflict'], bool)
            self.assertTrue(case['provenance'], case['case_id'])
            self.assertEqual(
                bool(case['expected_answerable']),
                case['expected_resolution'] == 'resolved_by_authority', case['case_id'])

    def test_changed_dataset_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            be.load_dataset(expected_sha256='0' * 64)
        self.assertEqual(str(caught.exception), 'BINDING_TRAPS_DATASET_CHANGED')

    def test_generator_is_deterministic(self):
        self.assertEqual(be.dataset_digest(be.generate_dataset()),
                         be.dataset_digest(be.generate_dataset()))

    def test_check_passes_on_the_checked_in_file(self):
        result = be.check_frozen()
        self.assertTrue(result['ok'], result['problems'])
        self.assertEqual(result['problems'], [])
        self.assertEqual(result['pinned_sha256'], be.TRAPS_SHA256)
        self.assertTrue(result['pin_matches'])


class LoosenedLabelTests(unittest.TestCase):
    def _loosen(self, mutate):
        payload = json.loads(Path(be.TRAPS_PATH).read_text(encoding='utf-8'))
        mutate(payload)
        directory = tempfile.mkdtemp()
        path = Path(directory) / 'loosened.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return path

    def test_loosened_conflict_label_fails_the_check(self):
        # bt-d-01 is a real value contradiction: claiming "no conflict" is a loosened label.
        path = self._loosen(lambda payload: [
            case.update({'expected_conflict': False})
            for case in payload['cases'] if case['case_id'] == 'bt-d-01'])
        result = be.check_frozen(path)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'BINDING_TRAPS_LABELS_LOOSENED')
        self.assertTrue(any('CONFLICT_LABEL_LOOSENED:bt-d-01' in problem
                            for problem in result['problems']), result['problems'])

    def test_loosened_answerable_label_fails_the_check(self):
        path = self._loosen(lambda payload: [
            case.update({'expected_answerable': True})
            for case in payload['cases'] if case['case_id'] == 'bt-c-01'])
        result = be.check_frozen(path)
        self.assertFalse(result['ok'])
        self.assertTrue(any('ANSWERABLE_LABEL_LOOSENED:bt-c-01' in problem
                            for problem in result['problems']), result['problems'])

    def test_edited_fixture_fails_the_regeneration_gate(self):
        path = self._loosen(lambda payload: payload['cases'][0]['sql']['rows'][0].update(
            {'value': '999999'}))
        result = be.check_frozen(path)
        self.assertFalse(result['ok'])
        self.assertTrue(any(problem.startswith('GENERATED_DATASET_DIFFERS')
                            for problem in result['problems']), result['problems'])

    def test_removed_case_fails_the_check(self):
        path = self._loosen(lambda payload: payload.update(
            {'cases': payload['cases'][:-1], 'case_count': len(payload['cases']) - 1}))
        result = be.check_frozen(path)
        self.assertFalse(result['ok'])
        self.assertTrue(any('CASE_COUNT_TOO_SMALL' in problem or
                            problem.startswith('GENERATED_DATASET_DIFFERS')
                            for problem in result['problems']), result['problems'])

    def test_check_command_exits_one_on_a_loosened_file(self):
        path = self._loosen(lambda payload: [
            case.update({'expected_conflict': False})
            for case in payload['cases'] if case['trap_kind'] == 'doc_db_opposite'])
        self.assertEqual(eval_binding.main(['--check', '--dataset', str(path)]), 1)


class MetricArithmeticTests(unittest.TestCase):
    def test_zero_denominators_are_na_not_zero(self):
        # An unregistered unit leaves bind_only's binding incomplete, so it emits no
        # high-confidence claim at all: 错误绑定率 has a zero denominator and must be N/A.
        case = synthetic_case(conflict_label=True, sql=('A1', 'm', '10', 'AU'),
                              doc=('A1', 'm', '20', 'mm'))
        result = be.run(tiny([case]), arms=['bind_only', 'plain'])
        bind_only = result['arms'][0]['metrics']
        bind_only_ledger = result['arms'][0]['repair_ledger']
        self.assertEqual(bind_only['asserted_high'], 0)
        self.assertIsNone(bind_only['misbinding_rate'], '零分母必须报 N/A 而不是 0')
        self.assertIsNone(bind_only['conflict']['precision'], '零分母必须报 N/A 而不是 0')
        self.assertEqual(bind_only['conflict']['recall'], 0.0,
                         '冻结集里有真冲突，召回率是真实的 0，不是 N/A')
        self.assertIsNone(bind_only['resolution_accuracy'], '没有 resolution 概念时必须 N/A')
        self.assertIsNone(bind_only_ledger['success_rate'], '零修复必须报 N/A')
        self.assertIsNone(bind_only_ledger['over_repair_rate'])
        # plain asserts everything, so its denominator is not zero and the rate is a real 1.0.
        plain = result['arms'][1]['metrics']
        self.assertEqual(plain['asserted_high'], 1)
        self.assertEqual(plain['misbinding_rate'], 1.0)

    def test_misbinding_rate_denominator_is_high_confidence_claims_only(self):
        case = synthetic_case(answerable=True, conflict_label=False,
                              resolution='resolved_by_authority', sql=('A1', 'm', '10', 'mm'),
                              doc=('A1', 'm', '10', 'mm'))
        result = be.run(tiny([case]), arms=['plain', 'bind_only'])
        for arm in result['arms']:
            metrics = arm['metrics']
            self.assertEqual(metrics['asserted_high'], 1)
            self.assertEqual(metrics['misbinding_rate'],
                             metrics['misbinding'] / metrics['asserted_high'])
        self.assertEqual(result['arms'][0]['metrics']['misbinding_rate'], 0.0)
        self.assertEqual(result['arms'][1]['metrics']['misbinding_rate'], 0.0)

    def test_over_repair_rate_denominator_is_changed_repairs(self):
        ledger = be.summarize_ledger([{'kind': 'x', 'applied': True}, {'kind': 'x', 'applied': True},
                                      {'kind': 'y', 'applied': False}],
                                     [False, True], [True, False])
        self.assertEqual(ledger['attempts'], 3)
        self.assertEqual(ledger['changed'], 2)
        self.assertEqual(ledger['rejected'], 1)
        self.assertEqual(ledger['succeeded'], 1)
        self.assertEqual(ledger['misrepair'], 1)
        self.assertEqual(ledger['success_rate'], 0.5)
        self.assertEqual(ledger['over_repair_rate'], 0.5)
        empty = be.summarize_ledger([], [], [])
        self.assertIsNone(empty['over_repair_rate'], '零分母必须报 N/A')
        self.assertIsNone(empty['success_rate'])

    def test_binding_equality_is_field_level(self):
        expected = {'entity_id': 'A102', 'version': 'V2', 'unit': 'mm',
                    'time_scope': {'start': '2025-01-01T00:00:00+08:00', 'end': None}}
        self.assertTrue(be.binding_equal({'entity_id': 'A102', 'version': 'V2', 'unit': '毫米',
                                          'time_scope': {'start': '2025-01-01T00:00:00+08:00',
                                                         'end': None}}, expected))
        self.assertFalse(be.binding_equal({**expected, 'version': 'V1'}, expected))
        self.assertFalse(be.binding_equal({**expected, 'entity_id': '华东地区'}, expected))
        # A naive timestamp is not the same binding as a timezone-aware one.
        self.assertFalse(be.binding_equal({'entity_id': 'A102', 'version': 'V2', 'unit': 'mm',
                                           'time_scope': {'start': '2025-01-01', 'end': None}},
                                          expected))

    def test_non_answerable_case_requires_detection_and_refusal(self):
        case = synthetic_case(conflict_label=True, answerable=False)
        outcome = {'arm': 'x', 'binding': case['expected_binding'], 'confidence': 'high',
                   'conflict_detected': False, 'resolution': 'not_evaluated',
                   'asserted': True, 'repair': None, 'sides': [], 'selected_source': None,
                   'status': 'completed', 'error_code': None, 'model_calls': 0}
        self.assertFalse(be.score_case(case, outcome)['binding_correct'])
        outcome.update({'confidence': 'low', 'asserted': False, 'conflict_detected': True})
        self.assertTrue(be.score_case(case, outcome)['binding_correct'])
        outcome.update({'confidence': 'low', 'conflict_detected': False})
        self.assertFalse(be.score_case(case, outcome)['binding_correct'],
                         '既不检出也不断言不能算绑定正确')


class ArmBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = be.load_dataset()
        cls.result = be.run(cls.dataset)
        cls.by_arm = {arm['arm']: arm for arm in cls.result['arms']}

    def test_three_arms_run_on_the_same_frozen_cases(self):
        for arm in self.result['arms']:
            self.assertEqual(len(arm['cases']), len(self.dataset['cases']))
            self.assertEqual([item['case_id'] for item in arm['cases']],
                             [case['case_id'] for case in self.dataset['cases']])

    def test_arms_are_deterministic(self):
        second = be.run(self.dataset)
        for left, right in zip(self.result['arms'], second['arms']):
            self.assertEqual(left['metrics']['misbinding'], right['metrics']['misbinding'])
            self.assertEqual(left['metrics']['binding_correct'],
                             right['metrics']['binding_correct'])

    def test_no_arm_calls_a_model(self):
        for arm in self.result['arms']:
            self.assertEqual(arm['repair_ledger']['model_calls'], 0)
            self.assertTrue(all(item['model_calls'] == 0 for item in arm['cases']))

    def test_no_arm_reaches_the_provider_while_it_is_armed_to_explode(self):
        import datamind.providers as providers
        engines = [sys.modules.get(name) for name in ('datamind.sql_engine', 'datamind.rag_engine')]
        originals = [(providers, getattr(providers, 'chat', None))]
        originals += [(module, getattr(module, 'provider_chat', None)) for module in engines
                      if module is not None]

        def forbidden(*args, **kwargs):
            raise RuntimeError('MODEL_CALL_FORBIDDEN_IN_BINDING_EVAL')

        try:
            providers.chat = forbidden
            for module in engines:
                if module is not None and hasattr(module, 'provider_chat'):
                    module.provider_chat = forbidden
            result = be.run(be.load_dataset())
        finally:
            for holder, original in originals:
                if original is not None:
                    setattr(holder, 'chat' if holder is providers else 'provider_chat', original)
        self.assertEqual([arm['arm'] for arm in result['arms']], list(be.ARMS))

    def test_plain_arm_always_asserts_and_never_detects_conflicts(self):
        metrics = self.by_arm['plain']['metrics']
        self.assertEqual(metrics['asserted_high'], 40)
        self.assertEqual(metrics['conflict']['true_positive'], 0)
        self.assertEqual(metrics['conflict']['false_positive'], 0)
        self.assertIsNone(metrics['conflict']['precision'])

    def test_bind_only_improves_field_binding_but_not_verification(self):
        plain = self.by_arm['plain']['metrics']['binding_field_accuracy']
        bind_only = self.by_arm['bind_only']['metrics']['binding_field_accuracy']
        self.assertGreater(bind_only['entity_id']['numerator'],
                           plain['entity_id']['numerator'],
                           'approved 别名规范化必须体现在字段级绑定上')
        self.assertEqual(bind_only['version']['numerator'], plain['version']['numerator'],
                         '只绑定不核验：版本适用性不会自己变好')
        self.assertEqual(bind_only['version']['numerator'], 9)

    def test_full_arm_is_the_only_arm_that_verifies_and_repairs(self):
        ledger = self.by_arm['full']['repair_ledger']
        self.assertGreater(ledger['attempts'], 0)
        self.assertGreater(ledger['changed'], 0)
        self.assertEqual(ledger['misrepair'], 0)
        self.assertEqual(ledger['over_repair_rate'], 0.0)
        self.assertEqual(self.by_arm['plain']['repair_ledger']['attempts'], 0)
        self.assertIsNone(self.by_arm['plain']['repair_ledger']['over_repair_rate'])
        self.assertIsNone(self.by_arm['bind_only']['repair_ledger']['over_repair_rate'])

    def test_full_arm_never_settles_on_one_side_without_a_registered_authority(self):
        checked = 0
        for case in self.dataset['cases']:
            if case['authority_rules'] or not case['expected_conflict']:
                continue
            checked += 1
            record = next(item for item in self.by_arm['full']['cases']
                          if item['case_id'] == case['case_id'])
            self.assertIn(record['actual_resolution'], ('unresolved', 'needs_review'),
                          case['case_id'])
            self.assertIsNone(record['selected_source'], case['case_id'])
            self.assertNotEqual(record['confidence'], 'high', case['case_id'])
            self.assertGreaterEqual(len(record['sides']), 2, case['case_id'])
            values = sorted(str(side['value']) for side in record['sides'])
            self.assertEqual(values, sorted([str(case['expected_value']),
                                             str(_doc_value(case))]), case['case_id'])
        self.assertGreaterEqual(checked, 15, '无权威登记的冲突例必须覆盖到')

    def test_full_arm_meets_the_wp3_acceptance_criterion(self):
        per_arm = {arm['arm']: arm['metrics'] for arm in self.result['arms']}
        verdict = be.judge({name: {'misbinding_rate': metrics['misbinding_rate']}
                            for name, metrics in per_arm.items()})
        self.assertGreater(per_arm['plain']['misbinding_rate'], 0.0,
                           'plain 若本来就不犯错，门槛无法体现增益')
        self.assertTrue(verdict['meets'], verdict)
        self.assertGreaterEqual(verdict['relative_drop'], 0.5)
        self.assertLessEqual(per_arm['full']['misbinding_rate'], 0.05)

    def test_a_clean_plain_arm_blocks_the_leadership_claim(self):
        verdict = be.judge({'plain': {'misbinding_rate': 0.0}, 'full': {'misbinding_rate': 0.0}})
        self.assertFalse(verdict['meets'])
        self.assertIn('PLAIN_HAS_NO_MISBINDING', verdict['reason'])

    def test_sensitivity_probe_shows_every_sub_step_carries_gain(self):
        probe = be.run_sensitivity(self.dataset)
        baseline = self.by_arm['full']['metrics']['misbinding_rate']
        for variant in ('full_no_version_check', 'full_no_conflict_gate'):
            self.assertGreater(probe[variant]['misbinding_rate'], baseline, variant)
        self.assertLess(probe['full_no_repair']['binding_accuracy'],
                        self.by_arm['full']['metrics']['binding_accuracy'])

    def test_every_failure_is_preserved_with_expected_actual_and_why(self):
        self.assertTrue(self.result['failures'])
        for entry in self.result['failures']:
            self.assertTrue(entry['case_id'])
            self.assertIn(entry['arm'], be.ARMS)
            self.assertIn(entry['trap_kind'], be.TRAP_KINDS)
            self.assertTrue(entry['why'])
            self.assertIn('binding', entry['expected'])
            self.assertIn('binding', entry['actual'])
        arms = {entry['arm'] for entry in self.result['failures']}
        self.assertIn('plain', arms)
        self.assertIn('bind_only', arms)


def _doc_value(case):
    for block in case['docs']['blocks']:
        if block.get('value') is not None:
            return block['value']
    return None


class ReportWriterTests(unittest.TestCase):
    def test_script_writes_the_report_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'binding_ablation.json'
            self.assertEqual(eval_binding.main(['--output', str(output)]), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['kind'], 'binding-ablation')
            self.assertEqual(report['dataset_digest'], be.TRAPS_SHA256)
            self.assertEqual(report['model_calls'], 0)
            for arm in be.ARMS:
                metrics = report['per_arm'][arm]
                for field in ('binding_accuracy', 'misbinding_rate', 'conflict_precision',
                              'conflict_recall', 'false_positive_rate'):
                    self.assertIn(field, metrics)
                ledger = metrics['repair_ledger']
                for field in ('attempts', 'success_rate', 'over_repair_rate', 'cost_ms'):
                    self.assertIn(field, ledger)
            self.assertEqual(set(report['per_trap_kind']), set(be.TRAP_KINDS))
            self.assertTrue(report['failures'])
            self.assertIn('meets', report['verdict'])
            self.assertTrue(report['boundaries'])
            # The report refuses to be silently overwritten...
            self.assertEqual(eval_binding.main(['--output', str(output)]), 1)
            # ...unless --force is explicit.
            self.assertEqual(eval_binding.main(['--output', str(output), '--force']), 0)

    def test_script_reports_the_hard_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            self.assertEqual(eval_binding.main(['--output', str(output), '--no-probe']), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            verdict = report['verdict']
            self.assertEqual(verdict['criterion'],
                             'full 臂错误绑定率相对 plain 臂下降 ≥50% 且绝对值 ≤5%')
            self.assertIsInstance(verdict['meets'], bool)
            self.assertFalse(report['sensitivity'])

    def test_script_refuses_a_changed_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / 'changed.json'
            payload = json.loads(Path(be.TRAPS_PATH).read_text(encoding='utf-8'))
            payload['cases'][0]['expected_answerable'] = not payload['cases'][0]['expected_answerable']
            changed.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            output = Path(directory) / 'report.json'
            self.assertEqual(eval_binding.main(['--output', str(output), '--dataset', str(changed)]), 1)
            self.assertFalse(output.exists())

    def test_freeze_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'traps.json'
            self.assertEqual(eval_binding.main(['--freeze', '--dataset', str(path)]), 0)
            self.assertTrue(path.exists())
            self.assertEqual(eval_binding.main(['--freeze', '--dataset', str(path)]), 1)
            self.assertEqual(eval_binding.main(['--freeze', '--dataset', str(path), '--force']), 0)

    def test_frozen_report_is_valid_json_on_disk(self):
        report = json.loads(Path(be.TRAPS_PATH).read_text(encoding='utf-8'))
        self.assertEqual(report['case_count'], 40)
        self.assertEqual(report['digest'], be.TRAPS_SHA256)


if __name__ == '__main__':
    unittest.main()
