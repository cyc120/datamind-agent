#!/usr/bin/env python
"""WP3: run the frozen binding-trap set through the ``plain`` / ``bind_only`` / ``full`` arms.

Modes
-----
``--freeze``  (re)write ``data/evaluation/binding_traps_v1.json`` from the deterministic generator
              in :mod:`datamind.binding_eval`.  Refuses to overwrite without ``--force``.
``--check``   regenerate the trap set in memory, compare it with the checked-in file *and* re-audit
              every label against the fixtures.  Any loosened label exits 1 with
              ``BINDING_TRAPS_LABELS_LOOSENED``; that is the "先冻结标签后评测" gate.
default       run the three arms over the frozen set and write the JSON report.

The answerer is a deterministic stub: no model is called (``model_calls == 0``), so the numbers
describe mechanism behaviour under the frozen definitions — never a real model's accuracy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import binding_eval  # noqa: E402

DEFAULT_OUTPUT = ROOT / 'reports/binding_ablation.json'


def _commit():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=str(ROOT), check=True,
                              capture_output=True, text=True).stdout.strip() or None
    except Exception:
        return None


def _api_version():
    try:
        from datamind.api import app
        return app.version
    except Exception:
        return None


def _file_digest(relative):
    """Provenance: the exact revision of the reused modules this run's numbers came from."""
    import hashlib
    path = ROOT / relative
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def freeze(path, *, force=False):
    target = Path(path)
    if target.exists() and not force:
        print(json.dumps({'status': 'failed', 'error_code': 'DATASET_EXISTS',
                          'path': str(target)}, ensure_ascii=False), file=sys.stderr)
        return 1
    payload = binding_eval.generate_dataset()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {'status': 'frozen', 'path': str(target), 'cases': payload['case_count'],
               'counts_by_trap_kind': payload['counts_by_trap_kind'],
               'sha256': binding_eval.dataset_digest(payload)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def check(path):
    result = binding_eval.check_frozen(path)
    stream = sys.stdout if result['ok'] else sys.stderr
    print(json.dumps(result, ensure_ascii=False), file=stream)
    return 0 if result['ok'] else 1


def evaluate(dataset_path, output, *, force=False, probe=True):
    output = Path(output)
    if output.exists() and not force:
        print(json.dumps({'status': 'failed', 'error_code': 'REPORT_EXISTS',
                          'path': str(output)}, ensure_ascii=False), file=sys.stderr)
        return 1
    try:
        dataset = binding_eval.load_dataset(dataset_path, expected_sha256=binding_eval.TRAPS_SHA256)
    except ValueError as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    audit = binding_eval.check_frozen(dataset_path)
    if not audit['ok']:
        print(json.dumps({'status': 'failed', 'error_code': audit['error_code'],
                          'problems': audit['problems']}, ensure_ascii=False), file=sys.stderr)
        return 1
    result = binding_eval.run(dataset)
    per_arm = {}
    for arm in result['arms']:
        metrics = arm['metrics']
        ledger = arm['repair_ledger']
        per_arm[arm['arm']] = {
            'cases': metrics['cases'],
            'binding_accuracy': metrics['binding_accuracy'],
            'binding_field_accuracy': metrics['binding_field_accuracy'],
            'binding_field_accuracy_all_cases': metrics['binding_field_accuracy_all_cases'],
            'misbinding_rate': metrics['misbinding_rate'],
            'misbinding_rate_all_cases': metrics['misbinding_rate_all_cases'],
            'misbinding': metrics['misbinding'],
            'asserted_high': metrics['asserted_high'],
            'abstained_or_low': metrics['abstained_or_low'],
            'conflict_precision': metrics['conflict']['precision'],
            'conflict_recall': metrics['conflict']['recall'],
            'false_positive_rate': metrics['conflict']['false_positive_rate'],
            'conflict_confusion': {key: metrics['conflict'][key] for key in
                                   ('expected_true', 'expected_false', 'true_positive',
                                    'false_positive', 'false_negative', 'true_negative')},
            'resolution_accuracy': metrics['resolution_accuracy'],
            'resolution_evaluated': metrics['resolution_evaluated'],
            'handled_correct_rate': metrics['handled_correct_rate'],
            'repair_ledger': {
                'attempts': ledger['attempts'], 'changed': ledger['changed'],
                'rejected': ledger['rejected'], 'succeeded': ledger['succeeded'],
                'redundant': ledger['redundant'], 'misrepair': ledger['misrepair'],
                'success_rate': ledger['success_rate'],
                'over_repair_rate': ledger['over_repair_rate'],
                'cost_ms': ledger['cost_ms'], 'by_kind': ledger['by_kind'],
                'model_calls': ledger['model_calls'], 'tokens': ledger['tokens'],
                'token_note': ledger['token_note'],
            },
            'elapsed_ms': arm['elapsed_ms'],
        }
    per_trap_kind = {}
    for kind, arms in result['per_trap_kind'].items():
        per_trap_kind[kind] = {}
        for name, metrics in arms.items():
            per_trap_kind[kind][name] = {
                'cases': metrics['cases'], 'binding_accuracy': metrics['binding_accuracy'],
                'misbinding_rate': metrics['misbinding_rate'],
                'asserted_high': metrics['asserted_high'],
                'conflict_precision': metrics['conflict']['precision'],
                'conflict_recall': metrics['conflict']['recall'],
                'false_positive_rate': metrics['conflict']['false_positive_rate'],
                'resolution_accuracy': metrics['resolution_accuracy'],
                'handled_correct_rate': metrics['handled_correct_rate'],
            }
    verdict = binding_eval.judge(per_arm)
    sensitivity = binding_eval.run_sensitivity(dataset) if probe else {}
    # A compact per-case index: which arm said what, with every failure preserved in /failures/.
    index = []
    for case in dataset['cases']:
        row = {'case_id': case['case_id'], 'trap_kind': case['trap_kind'],
               'expected_binding': case['expected_binding'],
               'expected_answerable': case['expected_answerable'],
               'expected_conflict': case['expected_conflict'],
               'expected_resolution': case['expected_resolution'], 'arms': {}}
        for arm in result['arms']:
            record = next(item for item in arm['cases'] if item['case_id'] == case['case_id'])
            row['arms'][arm['arm']] = {
                'binding': record['actual_binding'], 'confidence': record['confidence'],
                'asserted': record['asserted'], 'binding_correct': record['binding_correct'],
                'conflict_detected': record['conflict_detected'],
                'conflict_correct': record['conflict_correct'],
                'resolution': record['actual_resolution'],
                'resolution_correct': record['resolution_correct'],
                'misbinding': record['misbinding'],
                'handled_correct': record['handled_correct'],
                'status': record['status'], 'error_code': record['error_code'],
                'repair': record['repair'],
                'sides': record['sides'],
                'selected_source': record['selected_source'],
            }
        index.append(row)

    report = {
        'kind': 'binding-ablation',
        'suite': dataset.get('suite'),
        'binding_eval_version': binding_eval.BINDING_EVAL_VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'baseline_commit': _commit(),
        'api_version': _api_version(),
        'command': 'cd "%s" && .venv/bin/python scripts/eval_binding.py --force' % ROOT,
        'answerer': 'deterministic-stub',
        'model_calls': 0,
        'dataset': result['dataset'],
        'dataset_digest': dataset['sha256'],
        'dataset_file_sha256': dataset.get('file_sha256'),
        'dataset_pinned_sha256': binding_eval.TRAPS_SHA256,
        'dependency_digests': {name: _file_digest(name) for name in (
            'datamind/binding.py', 'datamind/evidence_graph.py', 'datamind/cross_source.py',
            'datamind/binding_eval.py', 'scripts/eval_binding.py')},
        'label_audit': audit,
        'metric_definitions': {
            'binding_accuracy': '正确绑定的答案声称 / 全部答案声称（每例一条答案声称，分母 = 冻结例数）',
            'misbinding_rate': '绑定错误却仍以 confidence=high 输出的声称 / 全部 high 置信声称'
                               '（零分母报 N/A，另给 misbinding_rate_all_cases 防止分母缩水掩盖错误）',
            'conflict_precision': 'TP/(TP+FP)，零分母 N/A',
            'conflict_recall': 'TP/(TP+FN)',
            'false_positive_rate': 'FP/(FP+TN)，零分母 N/A',
            'repair_ledger': 'attempts=记账的修复尝试数；success_rate=成功/发生改写；'
                             'over_repair_rate=误修复/发生改写（分母口径=发生改写的修复数）；'
                             'cost_ms=本臂在整集上的墙钟 CPU 时间（stub，无模型延迟）',
            'binding_correct_non_answerable': '不可作答例：绑定正确的唯一方式是「检出差异且不以 high 断言」',
        },
        'per_arm': per_arm,
        'per_trap_kind': per_trap_kind,
        'verdict': verdict,
        'sensitivity': sensitivity,
        'failures': result['failures'],
        'cases': index,
        'boundaries': [
            'stub 作答器：不调用任何上游模型（model_calls=0），因此本报告不是真实模型准确率，'
            '也不能外推为业务准确率；',
            '自建冻结陷阱集：40 例由本仓库自行编写并冻结，plain 臂的失败来自陷阱的设计，'
            '「相对下降」是机制对照结果，不是泛化能力的证明；',
            '零分母一律 N/A（None），不用 0 冒充；分母分子报告在同一条目内；',
            'failures[] 保留每一条失败样本（含 plain/bind_only/full 三臂），未做任何剔除；',
            'conflict 判定只统计 source_kind 不同的同 (entity, metric, version) 数值对；'
            '不可比（单位/时间口径/版本适用性）记为 needs_review 而非 unresolved；',
            '本报告不衡量真实模型、真实演示库查询或前端表现。',
        ],
        'claim_boundary': 'full 臂通过门槛只能支持「在冻结定义下机制有效」的实用创新举证；'
                          '若要主张技术突破领先性，还需要真实模型 + 独立外部数据的证据。',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    _print_summary(report)
    print(f'报告：{output}')
    return 0


def _print_summary(report):
    print(f"冻结集 {report['dataset']['suite']} v{report['dataset']['version']} "
          f"sha256={report['dataset']['sha256'][:16]}… 共 {report['dataset']['cases']} 例"
          f"（{report['dataset']['counts_by_trap_kind']}）")
    header = (f"{'臂':<10}{'绑定准确率':>11}{'错误绑定率':>12}{'断言数':>8}{'冲突P':>8}"
              f"{'冲突R':>8}{'误报率':>8}{'修复':>6}{'误修复率':>10}")
    print(header)
    for name, metrics in report['per_arm'].items():
        ledger = metrics['repair_ledger']
        print(f"{name:<10}{str(metrics['binding_accuracy']):>11}"
              f"{str(metrics['misbinding_rate']):>12}{metrics['asserted_high']:>8}"
              f"{str(metrics['conflict_precision']):>8}{str(metrics['conflict_recall']):>8}"
              f"{str(metrics['false_positive_rate']):>8}{str(ledger['attempts']):>6}"
              f"{str(ledger['over_repair_rate']):>10}")
    verdict = report['verdict']
    print(f"硬判定：{verdict['criterion']} → {'满足' if verdict['meets'] else '不满足'}"
          f"（plain={verdict['plain_misbinding_rate']} full={verdict['full_misbinding_rate']}"
          f" 相对下降={verdict['relative_drop']}）")
    print(f"失败样本 {len(report['failures'])} 条（全部保留在报告 failures[]）")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                        help='报告路径（默认 reports/binding_ablation.json）')
    parser.add_argument('--dataset', type=Path, default=binding_eval.TRAPS_PATH,
                        help='冻结陷阱集路径')
    parser.add_argument('--check', action='store_true', help='复现并校验冻结标签（不放宽）')
    parser.add_argument('--freeze', action='store_true', help='从确定性生成器重写冻结集')
    parser.add_argument('--force', action='store_true', help='允许覆盖已存在的报告/冻结集')
    parser.add_argument('--no-probe', action='store_true', help='不做 full 臂内部敏感性探针')
    args = parser.parse_args(argv)
    if args.check:
        return check(args.dataset)
    if args.freeze:
        return freeze(args.dataset, force=args.force)
    return evaluate(args.dataset, args.output, force=args.force, probe=not args.no_probe)


if __name__ == '__main__':
    raise SystemExit(main())
