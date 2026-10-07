"""Mechanism ablation for V-CAR over one frozen synthetic set.

Four arms run on the same frozen cases so their repair and conflict behaviour can be
compared under the metric definitions in the design (section 6.4 and 9.2):

* ``ocr_text``  - plain OCR text plus lexical reading; no structured facts, no rules;
* ``structured`` - structured facts and rules replace/augment the OCR reading;
* ``local``     - the above plus a local re-read of every case that offers one;
* ``full``      - structured facts plus a *justified* local re-read, and conflicts
                  resolved only by a registered authority rule (otherwise left unresolved
                  with both sides returned).

This is a **mechanism** ablation over synthetic cases. No model is called, and the case
labels are frozen before any arm exists, so the numbers describe mechanism behaviour, not
model accuracy or retrieval quality. Every failure is preserved in the per-case detail.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / 'data/evaluation/vcar_ablation_v1.json'
ARMS = ('ocr_text', 'structured', 'local', 'full')
_NUMBER = re.compile(r'(-?\d+(?:\.\d+)?)\s*([A-Za-z%]{0,4})')


def dataset_sha256(path=None):
    return hashlib.sha256(Path(path or DATASET_PATH).read_bytes()).hexdigest()


def load_dataset(path=None, *, expected_sha256=None):
    """Load the frozen set; refuse a silently changed dataset when a digest is pinned."""
    raw = Path(path or DATASET_PATH).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('VCAR_ABLATION_DATASET_CHANGED')
    data = json.loads(raw.decode('utf-8'))
    cases = data.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('VCAR_ABLATION_DATASET_INVALID')
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('case_id'), str)
                or case.get('kind') not in {'field', 'conflict'} or not isinstance(case.get('truth'), dict)
                or not isinstance(case.get('sources'), dict)
                or not isinstance(case.get('pre_repair_correct'), bool)):
            raise ValueError('VCAR_ABLATION_DATASET_INVALID')
    return {'version': data.get('version'), 'sha256': digest, 'cases': cases}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else float(value)


def parse_ocr(text):
    """Read the first number and its immediate unit from raw OCR text."""
    match = _NUMBER.search(text or '')
    if not match:
        return {}
    unit = match.group(2).strip()
    reading = {'value': _number(float(match.group(1)))}
    if unit:
        reading['unit'] = unit
    return reading


def _reading(source):
    if not isinstance(source, dict):
        return None
    value = _number(source.get('value'))
    if value is None:
        return None
    reading = {'value': value}
    if source.get('unit'):
        reading['unit'] = source['unit']
    if source.get('entity'):
        reading['entity'] = source['entity']
    if source.get('basis'):
        reading['basis'] = source['basis']
    return reading


def _same(left, right):
    for key in ('value', 'unit', 'entity'):
        if left.get(key) != right.get(key):
            return False
    return True


def _outcome(reading, *, repair=None, conflict=None, unresolved=False, sides=None):
    return {'reading': copy.deepcopy(reading), 'unresolved': bool(unresolved),
            'repair': repair or {'attempted': False, 'changed': False},
            'conflict': conflict or {'detected': False, 'rule_based': False},
            'sides': copy.deepcopy(sides or [])}


def _sides(case):
    sides = []
    for name in ('ocr_text', 'structured_fact'):
        reading = parse_ocr(case['sources'].get('ocr_text')) if name == 'ocr_text' else _reading(case['sources'].get('structured_fact'))
        if reading:
            sides.append({'source': 'report' if name == 'ocr_text' else 'database', **reading})
    return sides


def _comparable(reading):
    """Normalize only the conversion the project actually implements (K -> C)."""
    if not reading or reading.get('unit') not in ('K', 'k'):
        return reading
    value = reading.get('value')
    if value is None:
        return reading
    return {**reading, 'value': round(float(value) - 273.15, 2), 'unit': 'C'}


def _reported(case):
    """Two independent sources for the same fact: the document reading and the store."""
    return (_comparable(parse_ocr(case['sources'].get('ocr_text'))),
            _comparable(_reading(case['sources'].get('structured_fact'))))


def _detected(case):
    """A conflict is two sources disagreeing on the *value*; never read from labels.

    Unit and entity differences are completeness/binding defects handled by the
    structured layer, not source conflicts, so they do not raise a conflict here.
    """
    report, database = _reported(case)
    if not report or not database:
        return False
    return report.get('value') != database.get('value')


def arm_ocr_text(case):
    return _outcome(parse_ocr(case['sources'].get('ocr_text')))


def _structured_reading(case):
    return _reading(case['sources'].get('structured_fact')) or parse_ocr(case['sources'].get('ocr_text'))


def arm_structured(case):
    base = parse_ocr(case['sources'].get('ocr_text'))
    reading = _structured_reading(case)
    changed = bool(base) and not _same(base, reading)
    # Blindly preferring one side still notices that the two disagree.
    conflict = {'detected': _detected(case), 'rule_based': False}
    return _outcome(reading, repair={'attempted': changed, 'changed': changed, 'from': base or None},
                    conflict=conflict)


def arm_local(case):
    base = parse_ocr(case['sources'].get('ocr_text'))
    reading = _structured_reading(case)
    reread = _reading(case['sources'].get('local_reread'))
    used_reread = reread is not None
    if used_reread:
        reading = reread
    changed = bool(base) and not _same(base, reading)
    conflict = {'detected': _detected(case), 'rule_based': False}
    return _outcome(reading, repair={'attempted': changed or used_reread, 'changed': changed,
                                     'from': base or None, 'reread': used_reread},
                    conflict=conflict)


def arm_full(case):
    base = parse_ocr(case['sources'].get('ocr_text'))
    structured = _reading(case['sources'].get('structured_fact'))
    reading = structured or base
    reread = _reading(case['sources'].get('local_reread'))
    used_reread = False
    # A local re-read is only justified when no structured evidence exists and the
    # quality layer flagged the reading; otherwise it is a speculative rewrite.
    if reread is not None and structured is None and case.get('defect') in {'low_confidence', 'unreadable'}:
        reading = reread
        used_reread = True
    sides = _sides(case)
    conflict = {'detected': False, 'rule_based': False}
    unresolved = False
    if _detected(case):
        rule = _reading(case['sources'].get('authority_rule'))
        conflict = {'detected': True, 'rule_based': rule is not None}
        if rule is not None:
            reading = rule
        else:
            # No registered authority rule: refuse to auto-resolve, keep both sides.
            unresolved = True
    changed = bool(base) and not _same(base, reading)
    return _outcome(reading, repair={'attempted': changed or used_reread, 'changed': changed,
                                     'from': base or None, 'reread': used_reread},
                    conflict=conflict, unresolved=unresolved, sides=sides)


ARM_FUNCTIONS = {'ocr_text': arm_ocr_text, 'structured': arm_structured,
                 'local': arm_local, 'full': arm_full}


def score_case(case, outcome):
    """Compare one arm's outcome with the frozen label, preserving every detail."""
    truth = case['truth']
    reading = outcome['reading'] or {}
    record = {'case_id': case['case_id'], 'kind': case['kind'], 'defect': case.get('defect'),
              'outcome': copy.deepcopy(outcome), 'truth': copy.deepcopy(truth),
              'pre_repair_correct': case['pre_repair_correct']}
    repair = outcome['repair']
    changed = bool(repair.get('changed'))
    if case['kind'] == 'field':
        correct = (not outcome['unresolved']) and _same(reading, truth)
    elif truth.get('unresolvable'):
        # A real conflict without a rule must stay unresolved.
        correct = outcome['unresolved'] and outcome['conflict']['detected']
    elif truth.get('no_conflict'):
        correct = (not outcome['conflict']['detected']) and _same(reading, truth)
    else:
        # A conflict that was never detected cannot count as resolved.
        correct = (outcome['conflict']['detected'] and not outcome['unresolved']
                   and _same(reading, truth))
    record['correct'] = bool(correct)
    record['wrong'] = bool(not correct and not outcome['unresolved'])
    record['unresolved_outcome'] = bool(outcome['unresolved'])
    record['repair_attempted'] = bool(repair.get('attempted'))
    record['repair_changed'] = changed
    record['repair_succeeded'] = bool(changed and correct and not case['pre_repair_correct'])
    record['repair_failed'] = bool(changed and not correct)
    # A mis-repair rewrites an already-correct value into a wrong one.
    record['misrepair'] = bool(changed and case['pre_repair_correct'] and not correct)
    return record


def summarize(records):
    def ratio(numerator, denominator):
        return None if denominator == 0 else round(numerator / denominator, 4)

    conflicts = [item for item in records if item['kind'] == 'conflict']
    true_conflicts = [item for item in records
                      if item['kind'] == 'conflict' and not item['truth'].get('no_conflict')]
    tp = sum(1 for item in true_conflicts if item['outcome']['conflict']['detected'])
    fp = sum(1 for item in conflicts if item['outcome']['conflict']['detected']
             and item['truth'].get('no_conflict'))
    fn = len(true_conflicts) - tp
    attempted = sum(1 for item in records if item['repair_attempted'])
    changed = [item for item in records if item['repair_changed']]
    succeeded = sum(1 for item in records if item['repair_succeeded'])
    misrepair = sum(1 for item in records if item['misrepair'])
    correct = sum(1 for item in records if item['correct'])
    return {
        'cases': len(records), 'correct': correct, 'wrong': sum(1 for item in records if item['wrong']),
        'unresolved': sum(1 for item in records if item['unresolved_outcome']),
        'accuracy': ratio(correct, len(records)),
        'fields': {'cases': sum(1 for item in records if item['kind'] == 'field'),
                   'correct': sum(1 for item in records if item['kind'] == 'field' and item['correct'])},
        'conflict': {
            'cases': len(conflicts), 'true_conflicts': len(true_conflicts),
            'detected': tp, 'true_positive': tp, 'false_positive': fp, 'false_negative': fn,
            'precision': ratio(tp, tp + fp), 'recall': ratio(tp, tp + fn),
            'correct_resolution': sum(1 for item in true_conflicts if item['correct']
                                      and not item['unresolved_outcome']),
            'wrong_resolution': sum(1 for item in true_conflicts if item['wrong']),
            'left_unresolved': sum(1 for item in true_conflicts if item['unresolved_outcome']),
        },
        'repair': {
            'attempted': attempted, 'changed': len(changed),
            'succeeded': succeeded, 'failed': sum(1 for item in records if item['repair_failed']),
            'success_rate': ratio(succeeded, len(changed)),
            'trigger_ratio': ratio(attempted, len(records)),
            'misrepair': misrepair,
            'misrepair_rate': ratio(misrepair, len(changed)),
            'misrepair_share_of_correct_inputs': ratio(
                misrepair, sum(1 for item in records if item['pre_repair_correct'])),
        },
    }


def run(dataset, *, arms=None, clock=time.monotonic):
    """Run every arm over the same frozen cases and return metrics plus per-case detail."""
    names = list(arms or ARM_FUNCTIONS)
    for name in names:
        if name not in ARM_FUNCTIONS:
            raise ValueError('VCAR_ABLATION_ARM_INVALID')
    results = []
    for name in names:
        started = clock()
        records = [score_case(case, ARM_FUNCTIONS[name](case)) for case in dataset['cases']]
        results.append({'arm': name, 'metrics': summarize(records), 'cases': records,
                        'elapsed_ms': round((clock() - started) * 1000, 2)})
    return {'dataset': {'version': dataset['version'], 'sha256': dataset['sha256'],
                        'cases': len(dataset['cases'])},
            'arms': results,
            'boundaries': ['合成机制消融，不调用任何模型；不是 OCR/问答准确率',
                           '标签在任何机制实现前冻结，未参与调参',
                           '误修复率分母为「发生改写的案例数」，另给出占原正确输入的份额',
                           '未实现真实视觉载荷，局部重识别臂为机制替身']}
