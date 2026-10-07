"""V-CAR mechanism ablation: frozen labels, metric arithmetic and arm behaviour."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from datamind import ablation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import eval_vcar_ablation  # noqa: E402


def tiny(cases):
    return {'version': 'tiny', 'sha256': 'a' * 64, 'cases': cases}


def case(case_id, kind, truth, sources, *, pre=False, defect=None):
    return {'case_id': case_id, 'kind': kind, 'pre_repair_correct': pre, 'defect': defect,
            'sources': sources, 'truth': truth}


ARITHMETIC_CASES = [
    # Values agree; only the unit is missing, so the structured layer completes the
    # reading without raising a source conflict.
    case('c1', 'field', {'value': 2, 'unit': 'mm'},
         {'ocr_text': '外径 2', 'structured_fact': {'value': 2, 'unit': 'mm'}, 'local_reread': None},
         defect='missing_unit'),
    case('c2', 'field', {'value': 5, 'unit': 'mm'},
         {'ocr_text': '外径 5 mm', 'structured_fact': {'value': 5, 'unit': 'mm'},
          'local_reread': {'value': 9, 'unit': 'mm'}}, pre=True),
    case('c3', 'conflict', {'value': 7, 'unit': 'C', 'no_conflict': True},
         {'ocr_text': '报告 7 C', 'structured_fact': {'value': 7, 'unit': 'C'}, 'local_reread': None,
          'authority_rule': None}, defect='source_conflict'),
]


class AblationDatasetTests(unittest.TestCase):
    def test_frozen_dataset_matches_the_pinned_digest(self):
        dataset = ablation.load_dataset(expected_sha256=eval_vcar_ablation.DATASET_SHA256)
        self.assertEqual(dataset['version'], 'vcar-ablation-v1')
        self.assertEqual(len(dataset['cases']), 16)
        self.assertEqual(dataset['sha256'], eval_vcar_ablation.DATASET_SHA256)

    def test_changed_dataset_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            ablation.load_dataset(expected_sha256='0' * 64)
        self.assertEqual(str(caught.exception), 'VCAR_ABLATION_DATASET_CHANGED')

    def test_malformed_datasets_are_refused(self):
        for payload in ({'cases': []}, {'cases': [{'case_id': 1}]}, {'cases': 'x'}, {}):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'bad.json'
                path.write_text(json.dumps(payload), encoding='utf-8')
                with self.assertRaises(ValueError) as caught:
                    ablation.load_dataset(path)
                self.assertEqual(str(caught.exception), 'VCAR_ABLATION_DATASET_INVALID')

    def test_case_count_and_failure_detail_are_preserved(self):
        dataset = ablation.load_dataset()
        result = ablation.run(dataset)
        for arm in result['arms']:
            self.assertEqual(len(arm['cases']), len(dataset['cases']))
            for record in arm['cases']:
                self.assertIn('correct', record)
                self.assertIsInstance(record['correct'], bool)
        self.assertTrue(result['boundaries'])

    def test_unknown_arm_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            ablation.run(ablation.load_dataset(), arms=['nope'])
        self.assertEqual(str(caught.exception), 'VCAR_ABLATION_ARM_INVALID')


class AblationMetricTests(unittest.TestCase):
    def metrics(self, arm):
        result = ablation.run(tiny(ARITHMETIC_CASES), arms=[arm])
        return result['arms'][0]['metrics']

    def test_ocr_text_arm_never_repairs_or_detects_conflicts(self):
        metrics = self.metrics('ocr_text')
        self.assertEqual(metrics['repair']['changed'], 0)
        self.assertIsNone(metrics['repair']['success_rate'], '零分母必须报告 N/A 而不是 0')
        self.assertIsNone(metrics['conflict']['precision'], '零分母必须报告 N/A')
        self.assertIsNone(metrics['conflict']['recall'], '零分母必须报告 N/A')

    def test_ocr_text_arm_misses_every_real_conflict_on_the_frozen_set(self):
        metrics = ablation.run(ablation.load_dataset(), arms=['ocr_text'])['arms'][0]['metrics']
        self.assertEqual(metrics['conflict']['true_conflicts'], 4)
        self.assertEqual(metrics['conflict']['true_positive'], 0)
        self.assertEqual(metrics['conflict']['false_negative'], 4)
        self.assertEqual(metrics['conflict']['recall'], 0.0)

    def test_structured_arm_repairs_without_misrepairing(self):
        metrics = self.metrics('structured')
        repair = metrics['repair']
        self.assertEqual(repair['changed'], 1)
        self.assertEqual(repair['succeeded'], 1)
        self.assertEqual(repair['success_rate'], 1.0)
        self.assertEqual(repair['misrepair'], 0)
        self.assertEqual(repair['misrepair_rate'], 0.0)

    def test_local_arm_misrepairs_an_already_correct_value(self):
        metrics = self.metrics('local')
        repair = metrics['repair']
        self.assertEqual(repair['changed'], 2)
        self.assertEqual(repair['succeeded'], 1)
        self.assertEqual(repair['success_rate'], 0.5)
        self.assertEqual(repair['misrepair'], 1)
        self.assertEqual(repair['misrepair_rate'], 0.5)
        self.assertEqual(repair['misrepair_share_of_correct_inputs'], 1.0)

    def test_full_arm_does_not_rewrite_structured_evidence(self):
        metrics = self.metrics('full')
        repair = metrics['repair']
        self.assertEqual(repair['misrepair'], 0)
        self.assertEqual(repair['changed'], 1)
        self.assertEqual(metrics['correct'], 3)
        self.assertEqual(metrics['accuracy'], 1.0)

    def test_conflict_precision_counts_a_false_positive(self):
        # The two sources disagree on the value, but the label records that they are
        # different quantities rather than a real conflict, so flagging one is a FP.
        false_conflict = case('c4', 'conflict', {'value': 3, 'unit': 'C', 'no_conflict': True},
                              {'ocr_text': '报告 3 C', 'structured_fact': {'value': 9, 'unit': 'C'},
                               'local_reread': None, 'authority_rule': None}, defect='source_conflict')
        real = case('c5', 'conflict', {'value': 1, 'unit': 'C', 'unresolvable': True},
                    {'ocr_text': '报告 2 C', 'structured_fact': {'value': 1, 'unit': 'C'},
                     'local_reread': None, 'authority_rule': None}, defect='source_conflict')
        conflict = ablation.run(tiny([real, false_conflict]), arms=['structured'])['arms'][0]['metrics']['conflict']
        self.assertEqual(conflict['true_positive'], 1)
        self.assertEqual(conflict['false_positive'], 1)
        self.assertEqual(conflict['precision'], 0.5)
        self.assertEqual(conflict['recall'], 1.0)
        self.assertEqual(conflict['wrong_resolution'], 1)

    def test_conflict_detection_is_computed_from_sources_not_labels(self):
        # Same sources with opposite labels must produce the same detection decision.
        sources = {'ocr_text': '报告 2 C', 'structured_fact': {'value': 1, 'unit': 'C'},
                   'local_reread': None, 'authority_rule': None}
        labelled = case('x1', 'conflict', {'value': 1, 'unit': 'C', 'no_conflict': True}, sources,
                        defect='source_conflict')
        unlabelled = case('x2', 'field', {'value': 1, 'unit': 'C'}, sources)
        detected = [ablation.arm_structured(item)['conflict']['detected'] for item in (labelled, unlabelled)]
        self.assertEqual(detected, [True, True])


class AblationArmTests(unittest.TestCase):
    def test_arms_are_deterministic(self):
        dataset = ablation.load_dataset()
        first = ablation.run(dataset)
        second = ablation.run(dataset)
        for left, right in zip(first['arms'], second['arms']):
            self.assertEqual(left['metrics'], right['metrics'])

    def test_full_arm_leaves_ruleless_conflicts_unresolved_with_both_sides(self):
        dataset = ablation.load_dataset()
        result = ablation.run(dataset, arms=['full'])
        metrics = result['arms'][0]['metrics']
        self.assertEqual(metrics['conflict']['left_unresolved'], 2)
        self.assertEqual(metrics['conflict']['wrong_resolution'], 0)
        unresolved = [record for record in result['arms'][0]['cases'] if record['unresolved_outcome']]
        self.assertEqual(len(unresolved), 2)
        for record in unresolved:
            self.assertGreaterEqual(len(record['outcome']['sides']), 2, record)

    def test_full_arm_is_the_only_arm_without_misrepairs_on_the_frozen_set(self):
        result = ablation.run(ablation.load_dataset())
        misrepairs = {arm['arm']: arm['metrics']['repair']['misrepair'] for arm in result['arms']}
        self.assertGreater(misrepairs['local'], 0)
        self.assertEqual(misrepairs['full'], 0)

    def test_parse_ocr_reads_a_number_and_an_immediate_unit(self):
        self.assertEqual(ablation.parse_ocr('温度上限 850'), {'value': 850})
        self.assertEqual(ablation.parse_ocr('温度上限 850 C'), {'value': 850, 'unit': 'C'})
        self.assertEqual(ablation.parse_ocr('外径 20.04 mm'), {'value': 20.04, 'unit': 'mm'})
        self.assertEqual(ablation.parse_ocr('没有数字'), {})


class AblationScriptTests(unittest.TestCase):
    def test_script_writes_a_new_report_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'ablation.json'
            self.assertEqual(eval_vcar_ablation.main(['--output', str(output)]), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['kind'], 'mechanism-ablation')
            self.assertEqual(report['dataset']['sha256'], eval_vcar_ablation.DATASET_SHA256)
            self.assertEqual([arm['arm'] for arm in report['arms']], list(ablation.ARMS))
            self.assertEqual(eval_vcar_ablation.main(['--output', str(output)]), 1)

    def test_script_refuses_a_changed_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / 'changed.json'
            payload = json.loads(Path(ablation.DATASET_PATH).read_text(encoding='utf-8'))
            payload['cases'][0]['truth'] = {'value': 12345}
            changed.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            output = Path(directory) / 'report.json'
            self.assertEqual(eval_vcar_ablation.main(['--output', str(output), '--dataset', str(changed)]), 1)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
