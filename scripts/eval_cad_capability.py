"""Evaluate the frozen CAD capability acceptance suite against the production v2 path.

本脚本把 ``data/evaluation/cad_capability_v1/`` 的**冻结材料 + 冻结标签**逐例喂给真实的
生产路径（``ingestion.cad_adapter.parse``，内部即 ``ingestion/cad_dxf_v2``），把实际结果与
标签逐项比对，并写出 ``reports/cad_capability_v1.json``。

三条纪律：

1. **材料被改动就拒绝评测**：材料 sha256 与 ``manifest.json`` 冻结值不符时以
   ``SuiteIntegrityError`` 终止（退出码 2），**不产出成绩**；标签（``labels.json``）同样
   按 ``manifest.labels_sha256`` 校验，防止"只改标签不改材料"。
2. **失败项留在分母**：每一例无论通过与否都进入 ``cases`` 与它所属的每个能力层的分母；
   有失败项时退出码 1，但**报告仍然完整写出**。
3. **不把解析成功说成识别准确率**：报告只给"逐项断言通过比例"，并显式列出未覆盖能力。

这是**作者自建的合成验收集**，不是独立工厂数据，也不是盲测；两份真实图纸只作开发样本
（``data/cad_demo/DEVELOPMENT_SAMPLES.md``）。任何"泛化准确率"结论都超出本脚本的证据范围。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # The production modules live at the repository root; a direct `python scripts/...`
    # invocation must resolve them exactly as the application does.
    sys.path.insert(0, str(ROOT))
DEFAULT_SUITE = ROOT / 'data/evaluation/cad_capability_v1'
DEFAULT_OUTPUT = ROOT / 'reports/cad_capability_v1.json'
REPORT_VERSION = 'cad-capability-v1'
EXIT_OK = 0
EXIT_CAPABILITY_FAILURE = 1
EXIT_INTEGRITY_REFUSED = 2


class SuiteIntegrityError(RuntimeError):
    """The frozen suite no longer matches its recorded hashes; refuse to grade."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


# --------------------------------------------------------------------------------------
# 完整性
# --------------------------------------------------------------------------------------
def load_suite(suite_dir: Path):
    manifest_path = suite_dir / 'manifest.json'
    if not manifest_path.is_file():
        raise SuiteIntegrityError(f'missing suite manifest: {manifest_path}')
    manifest = _load_json(manifest_path)
    labels_path = suite_dir / manifest.get('labels_file', 'labels.json')
    if not labels_path.is_file():
        raise SuiteIntegrityError(f'missing suite labels: {labels_path}')
    labels = _load_json(labels_path)
    return manifest, labels, manifest_path, labels_path


def verify_integrity(suite_dir: Path, manifest: dict, labels: dict,
                     manifest_path: Path, labels_path: Path):
    """Every frozen hash must match before a single case is graded."""
    errors = []
    material_records = []
    for record in manifest.get('materials') or []:
        path = suite_dir / record['file']
        if not path.is_file():
            errors.append(f'missing material: {record["file"]} (case {record.get("case_id")})')
            material_records.append({'case_id': record.get('case_id'), 'file': record['file'],
                                     'sha256': None, 'expected_sha256': record.get('sha256'),
                                     'match': False})
            continue
        digest = _sha256(path)
        match = digest == record.get('sha256')
        material_records.append({'case_id': record.get('case_id'), 'file': record['file'],
                                 'sha256': digest, 'expected_sha256': record.get('sha256'),
                                 'match': match})
        if not match:
            errors.append(f'material sha256 mismatch: {record["file"]} '
                          f'(frozen {record.get("sha256")}, on disk {digest})')
    labels_digest = _sha256(labels_path)
    labels_match = labels_digest == manifest.get('labels_sha256')
    if not labels_match:
        errors.append(f'labels sha256 mismatch: {labels_path.name} '
                      f'(frozen {manifest.get("labels_sha256")}, on disk {labels_digest})')
    cases = labels.get('cases') or []
    manifest_ids = [record.get('case_id') for record in manifest.get('materials') or []]
    label_ids = [case.get('case_id') for case in cases]
    if manifest_ids != label_ids:
        errors.append('manifest material case_ids do not match labels case_ids')
    if errors:
        raise SuiteIntegrityError('; '.join(errors))
    return {'materials_verified': True,
            'materials': material_records,
            'labels_file': labels_path.name,
            'labels_sha256': labels_digest,
            'labels_match': True,
            'manifest_file': manifest_path.name,
            'manifest_sha256': _sha256(manifest_path)}


def code_hashes(manifest: dict):
    """sha256 of every evaluated production file plus the pinned-v2 cross-check."""
    hashes = {}
    for relative in manifest.get('evaluated_code') or []:
        path = ROOT / relative
        hashes[relative] = _sha256(path) if path.is_file() else None
    pinned_path = ROOT / (manifest.get('pinned_v2_manifest') or '')
    pinned = {'manifest': manifest.get('pinned_v2_manifest'), 'available': False, 'match': {},
              'drift': []}
    if pinned_path.is_file():
        pinned_manifest = _load_json(pinned_path)
        pinned['available'] = True
        pinned['frozen_at'] = pinned_manifest.get('frozen_at')
        pinned['pinned_hashes'] = pinned_manifest.get('code_hashes') or {}
        for relative, digest in pinned['pinned_hashes'].items():
            on_disk = hashes.get(relative)
            if on_disk is None:
                path = ROOT / relative
                on_disk = _sha256(path) if path.is_file() else None
                hashes.setdefault(relative, on_disk)
            pinned['match'][relative] = (on_disk == digest)
            if on_disk != digest:
                pinned['drift'].append(relative)
    return hashes, pinned


# --------------------------------------------------------------------------------------
# 生产路径调用
# --------------------------------------------------------------------------------------
def run_adapter(path: Path, document_id: str):
    """Run the registered production adapter (`ingestion.cad_adapter.parse`)."""
    from fastapi import HTTPException

    from ingestion import cad_adapter
    try:
        result = cad_adapter.parse(path, document_id=document_id)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        return {'status': 'rejected', 'error_code': detail.get('error_code'),
                'detail': detail, 'payload': None, 'exception': None}
    except Exception as error:  # noqa: BLE001 - an unexpected crash must be reported, not hidden
        return {'status': 'error', 'error_code': type(error).__name__, 'detail': None,
                'payload': None, 'exception': f'{type(error).__name__}: {error}\n'
                                              + traceback.format_exc(limit=6)}
    payload = getattr(result, 'payload', result)
    return {'status': 'returned', 'error_code': None, 'detail': None, 'payload': payload,
            'exception': None}


def run_reader(path: Path, document_id: str):
    """Run the reader the adapter itself calls, for fields the adapter projection drops."""
    from ingestion import cad_dxf_v2
    from ingestion.cad_dxf import CadReadError
    try:
        bundle = cad_dxf_v2.parse_file(path, document_id=document_id)
    except CadReadError as error:
        return {'bundle': None, 'error_code': error.code}
    except Exception as error:  # noqa: BLE001
        return {'bundle': None, 'error_code': f'{type(error).__name__}: {error}'}
    return {'bundle': bundle, 'error_code': None}


# --------------------------------------------------------------------------------------
# 逐例比对
# --------------------------------------------------------------------------------------
def _matches(entry, code):
    return entry == code or entry.startswith(code + ':')


def _check(checks, level, metric, expected, actual, passed, detail=None):
    record = {'level': level, 'metric': metric, 'expected': expected, 'actual': actual,
              'passed': bool(passed)}
    if detail:
        record['detail'] = detail
    checks.append(record)


def _payload_codes(payload):
    return (list(payload.get('unsupported_features') or [])
            + list(payload.get('quality_findings') or []))


def _adapter_checks(case, actual, observations):
    expected = case['expected']
    checks = []
    payload = actual['payload']
    want_rejected = expected['parse_status'] == 'rejected'
    payload_status = (payload or {}).get('parse_status') if actual['status'] == 'returned' else None
    reported_status = 'rejected' if want_rejected and actual['status'] == 'rejected' else (
        actual['status'] if actual['status'] != 'returned' else payload_status)

    if want_rejected:
        _check(checks, 'adapter', 'parse_status', expected['parse_status'], actual['status'],
               actual['status'] == 'rejected',
               detail=json.dumps(actual['detail'], ensure_ascii=False) if actual.get('detail')
               else actual.get('exception'))
        allowed = expected.get('error_codes_any') or []
        _check(checks, 'adapter', 'rejection_code', allowed, actual['error_code'],
               actual['status'] == 'rejected' and actual['error_code'] in allowed)
        return checks

    _check(checks, 'adapter', 'parse_status', expected['parse_status'], reported_status,
           reported_status == expected['parse_status'],
           detail=actual.get('exception') or (json.dumps(actual['detail'], ensure_ascii=False)
                                              if actual.get('detail') else None))
    if actual['status'] != 'returned' or payload is None:
        # 生产路径没有返回可用结果：逐项记为失败，分母不缩小。
        for code in expected.get('unsupported_codes') or []:
            _check(checks, 'adapter', 'unsupported_code', code, None, False,
                   detail=actual.get('exception'))
        _check(checks, 'adapter', 'absent_codes', expected.get('absent_codes') or [],
               [actual['error_code']], False, detail='adapter did not return a payload')
        for entity_type in expected.get('entity_counts') or {}:
            _check(checks, 'adapter', f'entity_count:{entity_type}',
                   expected['entity_counts'][entity_type], None, False)
        return checks

    unsupported = list(payload.get('unsupported_features') or [])
    for code in expected.get('unsupported_codes') or []:
        _check(checks, 'adapter', 'unsupported_code', code,
               [entry for entry in unsupported if _matches(entry, code)],
               any(_matches(entry, code) for entry in unsupported))
    absent = expected.get('absent_codes') or []
    present = [entry for entry in _payload_codes(payload)
               if any(_matches(entry, code) for code in absent)]
    _check(checks, 'adapter', 'absent_codes', absent, present, not present)

    if expected.get('unit') is not None:
        units = sorted({block['locator'].get('unit') for block in payload.get('blocks') or []})
        _check(checks, 'adapter', 'unit', expected['unit'], units, units == [expected['unit']])

    if expected.get('identity_mode') is not None:
        mode = (payload.get('manifest') or {}).get('identity_mode')
        _check(checks, 'adapter', 'identity_mode', expected['identity_mode'], mode,
               mode == expected['identity_mode'])

    entity_counts = Counter((block.get('native_attributes') or {}).get('entity_type')
                            for block in payload.get('blocks') or [])
    entity_counts.pop(None, None)  # feature-only blocks (block_type='dimension') are not entities
    for entity_type, count in (expected.get('entity_counts') or {}).items():
        _check(checks, 'adapter', f'entity_count:{entity_type}', count,
               entity_counts.get(entity_type, 0), entity_counts.get(entity_type, 0) == count)
    total = sum(entity_counts.values())
    wanted_total = expected.get('entity_total')
    if wanted_total:
        _check(checks, 'adapter', 'entity_total', wanted_total, total,
               wanted_total['min'] <= total <= wanted_total['max'])
        if wanted_total.get('min') == wanted_total.get('max') and expected.get('entity_counts'):
            # 精确总数下同时按类型多重集比对：多出的实体类型也算失败，不能只查"至少"。
            wanted = Counter({key: value for key, value in (expected['entity_counts'] or {}).items()})
            actual_named = Counter({key: value for key, value in entity_counts.items() if key})
            _check(checks, 'adapter', 'entity_multiset', dict(sorted(wanted.items())),
                   dict(sorted(actual_named.items())), wanted == actual_named)
    feature_counts = Counter((block.get('native_attributes') or {}).get('feature_type')
                             for block in payload.get('blocks') or [])
    feature_counts.pop(None, None)
    for feature_type, count in (expected.get('feature_counts') or {}).items():
        _check(checks, 'adapter', f'feature_count:{feature_type}', count,
               feature_counts.get(feature_type, 0), feature_counts.get(feature_type, 0) == count)
    block_counts = Counter(block.get('block_type') for block in payload.get('blocks') or [])
    for block_type, count in (expected.get('block_counts') or {}).items():
        _check(checks, 'adapter', f'block_count:{block_type}', count,
               block_counts.get(block_type, 0), block_counts.get(block_type, 0) == count)

    if expected.get('locator_layout') is not None:
        layouts = sorted({block['locator'].get('layout') for block in payload.get('blocks') or []})
        _check(checks, 'adapter', 'locator_layout', expected['locator_layout'], layouts,
               layouts == [expected['locator_layout']])

    locators = [block['locator'] for block in payload.get('blocks') or []]
    if expected.get('identity_mode') == 'positional' and locators:
        has_handle = [locator for locator in locators if 'handle' in locator]
        indices = [locator.get('entity_index') for locator in locators]
        positional = [locator.get('handle_source') for locator in locators]
        _check(checks, 'adapter', 'positional_locator_no_handle', [], sorted(has_handle),
               not has_handle)
        _check(checks, 'adapter', 'positional_locator_index', 'unique int >= 1',
               sorted(indices), bool(indices)
               and all(isinstance(value, int) and value >= 1 for value in indices)
               and len(set(indices)) == len(indices))
        _check(checks, 'adapter', 'positional_locator_handle_source', 'positional',
               sorted(set(positional)), set(positional) == {'positional'})
    elif expected.get('identity_mode') == 'source' and locators:
        missing = [index for index, locator in enumerate(locators) if not locator.get('handle')]
        _check(checks, 'adapter', 'source_locator_has_handle', [], missing, not missing)

    limits = (payload.get('manifest') or {}).get('limits') or {}
    required = ('max_bytes', 'max_entities', 'max_expanded_entities', 'max_block_depth',
                'max_declared_items', 'max_blocks')
    if payload_status in ('parsed', 'partial') or expected.get('manifest_limits_required'):
        missing = [key for key in required
                   if not isinstance(limits.get(key), int) or limits[key] < 1]
        _check(checks, 'adapter', 'manifest_limits_reported', list(required), limits, not missing)
    elif not limits:
        # 显式记录这个结果形态的缺口：`parse_status='unsupported'` 的返回里没有 manifest.limits，
        # 消费方无法从该结果读出上限。它是产品行为，不是本套件的断言失败。
        observations.append(
            f'{case["case_id"]}: parse_status={payload_status!r} 的返回不含 manifest.limits，'
            '该结果形态下上限不可从结果读出')
    return checks


def _reader_select(entities, match):
    if 'entity_index' in match:
        for entity in entities:
            if entity.get('entity_index') == match['entity_index']:
                return entity
        return None
    if 'entity_type' not in match:
        return None
    candidates = [entity for entity in entities if entity.get('entity_type') == match['entity_type']]
    if 'block_path_len' in match:
        candidates = [entity for entity in candidates
                      if len(entity.get('block_path') or []) == match['block_path_len']]
    occurrence = match.get('occurrence')
    if occurrence is None:
        return candidates[0] if candidates else None
    return candidates[occurrence] if occurrence < len(candidates) else None


def _reader_checks(case, reader):
    expected = case['expected'].get('reader') or {}
    checks = []
    if not expected:
        return checks
    if reader['bundle'] is None:
        _check(checks, 'reader', 'reader_available', 'parsed', reader['error_code'], False)
        return checks
    snapshot = reader['bundle']['snapshot']
    entities = snapshot['entities']
    features = snapshot.get('features') or []

    for spec in expected.get('entities') or []:
        match, want = spec['match'], spec['expect']
        entity = _reader_select(entities, match)
        for key, value in want.items():
            got = entity.get(key) if entity else None
            _check(checks, 'reader', f'entity:{match.get("entity_type")}:{key}', value, got,
                   entity is not None and got == value, detail=json.dumps(match, ensure_ascii=False))
    for spec in expected.get('any') or []:
        match = spec['match']
        candidates = [entity for entity in entities
                      if all(entity.get(key) == value or
                             (key == 'block_path_len'
                              and len(entity.get('block_path') or []) == value)
                             for key, value in match.items())]
        _check(checks, 'reader', 'any_entity', json.dumps(match, ensure_ascii=False),
               len(candidates), bool(candidates))
    for spec in expected.get('features') or []:
        index = spec['index']
        feature = features[index] if index < len(features) else {}
        for key, value in spec['expect'].items():
            _check(checks, 'reader', f'feature[{index}]:{key}', value, feature.get(key),
                   feature.get(key) == value)
    if 'feature_count' in expected:
        _check(checks, 'reader', 'feature_count', expected['feature_count'], len(features),
               len(features) == expected['feature_count'])
    if 'max_block_path_depth' in expected:
        depth = max((len(entity.get('block_path') or []) for entity in entities), default=0)
        _check(checks, 'reader', 'max_block_path_depth', expected['max_block_path_depth'], depth,
               depth == expected['max_block_path_depth'])
    if 'entity_indices' in expected:
        indices = [entity.get('entity_index') for entity in entities]
        _check(checks, 'reader', 'entity_indices', expected['entity_indices'], indices,
               indices == expected['entity_indices'])
    if expected.get('require_no_handle'):
        leaked = sorted({entity.get('handle') for entity in entities if entity.get('handle')})
        _check(checks, 'reader', 'require_no_handle', [], leaked, not leaked)
    if expected.get('require_handle_present'):
        handles = [entity.get('handle') for entity in entities]
        _check(checks, 'reader', 'require_handle_present', 'every entity has a source handle',
               handles, bool(handles) and all(handles))
    return checks


def evaluate_case(case, suite_dir: Path):
    path = suite_dir / case['material']
    actual = run_adapter(path, case['document_id'])
    observations = []
    checks = _adapter_checks(case, actual, observations)
    needs_reader = bool(case['expected'].get('reader'))
    reader = run_reader(path, case['document_id']) if needs_reader else {'bundle': None,
                                                                        'error_code': None}
    checks.extend(_reader_checks(case, reader))
    failures = [check for check in checks if not check['passed']]
    return {
        'case_id': case['case_id'],
        'material': case['material'],
        'material_sha256': _sha256(path),
        'layers': list(case['layers']),
        'expected_parse_status': case['expected']['parse_status'],
        'adapter_status': actual['status'],
        'adapter_error_code': actual['error_code'],
        'identity_mode': ((actual['payload'] or {}).get('manifest') or {}).get('identity_mode'),
        'unit': sorted({block['locator'].get('unit')
                        for block in ((actual['payload'] or {}).get('blocks') or [])}),
        'unsupported_features': list(((actual['payload'] or {}).get('unsupported_features')) or []),
        'observations': observations,
        'checks_passed': len(checks) - len(failures),
        'checks_total': len(checks),
        'passed': not failures,
        'failure_reasons': [{'level': check['level'], 'metric': check['metric'],
                             'expected': check['expected'], 'actual': check['actual'],
                             'detail': check.get('detail')}
                            for check in failures],
        'checks': checks,
    }


def evaluate(suite_dir=DEFAULT_SUITE):
    """Grade the frozen suite; raises `SuiteIntegrityError` rather than grading a tampered one."""
    suite_dir = Path(suite_dir)
    started = time.monotonic()
    manifest, labels, manifest_path, labels_path = load_suite(suite_dir)
    integrity = verify_integrity(suite_dir, manifest, labels, manifest_path, labels_path)
    hashes, pinned = code_hashes(manifest)
    import ezdxf
    integrity['code_files'] = hashes
    integrity['code_pin'] = pinned
    integrity['ezdxf_version'] = ezdxf.__version__
    integrity['python_version'] = sys.version
    # 记录测量工具自身与生成器，便于复核"这次成绩是谁算出来的、材料是谁生成的"。
    integrity['evaluator'] = {'path': 'scripts/eval_cad_capability.py',
                              'sha256': _sha256(Path(__file__).resolve())}
    integrity['suite_builder'] = {'path': manifest.get('generator'),
                                  'sha256': manifest.get('generator_sha256')}

    cases = [evaluate_case(case, suite_dir) for case in labels['cases']]
    layer_names = list(manifest.get('layers') or {})
    for case in cases:
        for layer in case['layers']:
            if layer not in layer_names:
                layer_names.append(layer)
    layers = {}
    for layer in layer_names:
        members = [case for case in cases if layer in case['layers']]
        passed = [case for case in members if case['passed']]
        layers[layer] = {'cases': len(members), 'passed': len(passed),
                         'failed': len(members) - len(passed),
                         'pass_rate': round(len(passed) / len(members), 6) if members else None}
    failed_cases = [case for case in cases if not case['passed']]
    exit_code = EXIT_OK if not failed_cases else EXIT_CAPABILITY_FAILURE
    duration = round(time.monotonic() - started, 3)
    return {
        'report_version': REPORT_VERSION,
        'generated_by': 'scripts/eval_cad_capability.py',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'duration_seconds': duration,
        'suite': {'suite_id': manifest.get('suite_id'),
                  'suite_dir': str(suite_dir.relative_to(ROOT)) if suite_dir.is_relative_to(ROOT)
                              else str(suite_dir),
                  'cases_count': len(cases),
                  'ezdxf_version': manifest.get('ezdxf_version'),
                  'profile': manifest.get('profile'),
                  'parser_version': manifest.get('parser_version'),
                  'labels_sha256': manifest.get('labels_sha256')},
        'provenance': {
            'kind': 'author-built synthetic acceptance suite',
            'is_independent_factory_data': False,
            'is_blind_test': False,
            'measurement_path': 'ingestion.cad_adapter.parse (production v2 path)',
            'real_drawings_used_for_grading': False,
            'development_samples': manifest.get('provenance', {}).get('development_samples') or [],
            'claims': [
                '逐项断言通过比例，按能力分层汇总；失败项留在分母。',
                '材料与标签一起冻结；材料或标签被改动时拒绝评测。',
                '几何测量值与文字声明尺寸分开断言，不做等价替换。',
            ],
            'non_claims': [
                '不是识别准确率，也不是泛化准确率；不给出 accuracy 指标。',
                '不是独立工厂数据，也不是盲测；材料是作者生成的合成样本。',
                '两份真实图纸只作开发样本，其缺陷已被用于修改读取器，不能作为独立测试集。',
                '不含模型/数据库/网络调用，也不写真实业务库。',
            ],
        },
        'integrity': integrity,
        'summary': {
            'cases': len(cases),
            'passed': len(cases) - len(failed_cases),
            'failed': len(failed_cases),
            'pass_rate': round((len(cases) - len(failed_cases)) / len(cases), 6) if cases else None,
            'layers': layers,
            'failed_cases': [{'case_id': case['case_id'], 'layers': case['layers'],
                              'reasons': [reason['metric'] for reason in case['failure_reasons']]}
                             for case in failed_cases],
            'exit_code': exit_code,
        },
        'cases': cases,
        'observations': [observation for case in cases for observation in case['observations']],
        'uncovered': manifest.get('uncovered') or [],
        'not_measured': manifest.get('not_measured') or [],
    }


def write_report(report, output=DEFAULT_OUTPUT):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--no-write', action='store_true',
                        help='评测但不写报告文件（供测试使用）')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.suite)
    except SuiteIntegrityError as error:
        # 材料/标签被改动：明确报错并拒绝评测，绝不产出看起来有效的成绩。
        print(f'CAD_CAPABILITY_SUITE_INTEGRITY_REFUSED: {error}', file=sys.stderr)
        return EXIT_INTEGRITY_REFUSED
    if not args.no_write:
        write_report(report, args.output)
    if not args.quiet:
        print(json.dumps({'suite_id': report['suite']['suite_id'],
                          'cases': report['summary']['cases'],
                          'passed': report['summary']['passed'],
                          'failed': report['summary']['failed'],
                          'layers': report['summary']['layers'],
                          'failed_cases': report['summary']['failed_cases'],
                          'exit_code': report['summary']['exit_code']},
                         ensure_ascii=False, indent=1))
    return report['summary']['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
