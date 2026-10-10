"""Deterministic temperature/version rules. This module has no model or gold input.

The four core semantics — unit verification, version applicability, source agreement and
inclusive range compliance — live in :mod:`datamind.binding`; this module keeps the industrial
vocabulary (``compliance`` / ``agreement`` field names, issue kinds and reason codes) and calls
that module. Behaviour, error codes and the emitted structure are unchanged.
"""
from datetime import datetime  # noqa: F401  (re-exported for existing callers)
from decimal import Decimal, InvalidOperation

from .binding import (inclusive_range_status, normalize_unit, range_expression, source_agreement,
                      timestamp, version_applies)

RULE_VERSION = 'industrial-rules-v1'

__all__ = ['RULE_VERSION', 'timestamp', 'temperature', 'evaluate']


def temperature(value, unit):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('INVALID_TEMPERATURE') from None
    if not number.is_finite():
        raise ValueError('INVALID_TEMPERATURE')
    if unit not in ('K', 'degC'):
        raise ValueError('UNKNOWN_UNIT')
    converted = normalize_unit(number, unit, 'degC')
    if converted is None:
        raise ValueError('UNKNOWN_UNIT')
    return converted


def evaluate(row, requirements, reports):
    """One measurement stays one result; out-of-range and source conflict stay separate."""
    result = {**row, 'compliance': 'insufficient_evidence', 'agreement': 'insufficient_evidence',
              'applicable_revision': None, 'requirement': None, 'report': None,
              'normalized_value': None, 'normalized_unit': 'degC', 'issues': [], 'rules': []}
    problems = result['issues']
    try:
        production = temperature(row['value'], row['unit'])
        result['normalized_value'] = format(production, 'f')
    except ValueError as exc:
        production = None
        problems.append({'kind': 'unit_or_value', 'status': 'needs_review', 'reason': str(exc)})
    eligible = []
    for fact in requirements:
        if (fact['entity_id'],fact['operation_no'],fact['metric']) != (row['part_id'],row['operation_no'],row['metric']):
            continue
        if fact['approval_status'] != 'approved':
            continue
        try:
            valid = version_applies(row['produced_at'], fact['valid_from'], fact['valid_to'])
        except (ValueError, TypeError):
            problems.append({'kind':'version','status':'needs_review','reason':'VALIDITY_UNKNOWN','fact_ids':[fact['fact_id']]})
            continue
        if valid:
            eligible.append(fact)
    if len(eligible) > 1:
        result['compliance'] = 'needs_review'
        problems.append({'kind':'version_overlap','status':'needs_review','reason':'MULTIPLE_APPLICABLE_REVISIONS',
                         'fact_ids':[f['fact_id'] for f in eligible]})
    elif not eligible:
        problems.append({'kind':'requirement_gap','status':'insufficient_evidence','reason':'NO_APPLICABLE_REQUIREMENT'})
    else:
        req = eligible[0]; result['requirement'] = req; result['applicable_revision'] = req['source_revision']
        if row.get('recorded_revision') and row['recorded_revision'] != req['source_revision']:
            problems.append({'kind':'version_difference','status':'resolved_by_rule','reason':'SELECTED_BY_BATCH_TIME',
                             'recorded_revision':row['recorded_revision'],'selected_revision':req['source_revision']})
        result['rules'].append({'rule_id':'approved_validity_half_open','rule_version':RULE_VERSION})
        bounds = req.get('normalized_range')
        if production is None or not bounds or req.get('normalized_unit') != 'degC' or req.get('method') != row['method']:
            result['compliance'] = 'needs_review'
            problems.append({'kind':'requirement_context','status':'needs_review','reason':'UNIT_RANGE_OR_METHOD_UNRESOLVED'})
        else:
            low, high = Decimal(bounds['min']), Decimal(bounds['max'])
            result['compliance'] = inclusive_range_status(production, low, high)
            result['rules'].append({'rule_id':'inclusive_temperature_range','rule_version':RULE_VERSION,
                                    'expression':range_expression(low, production, high), 'passed': result['compliance']=='within_range'})
    matched = [f for f in reports if f.get('report_id') == row.get('report_id') and f.get('measurement_id') == row['measurement_id']]
    if len(matched) != 1:
        problems.append({'kind':'report_gap','status':'insufficient_evidence','reason':'REPORT_MISSING_OR_AMBIGUOUS'})
        return result
    report = matched[0]; result['report'] = report
    identity = ('entity_id','operation_no','batch_id','metric')
    expected = (row['part_id'],row['operation_no'],row['batch_id'],row['metric'])
    if tuple(report.get(k) for k in identity) != expected:
        result['agreement'] = 'needs_review'
        problems.append({'kind':'entity_alignment','status':'needs_review','reason':'REPORT_KEY_MISMATCH'})
        return result
    try:
        context_matches = timestamp(report['measured_at']) == timestamp(row['measured_at']) and report['method'] == row['method']
    except (ValueError, TypeError):
        context_matches = False
    if not context_matches or report['approval_status'] != 'approved' or row.get('quality_method') != row['method']:
        result['agreement'] = 'needs_review'
        problems.append({'kind':'measurement_context','status':'needs_review','reason':'METHOD_TIME_OR_APPROVAL_MISMATCH'})
        return result
    try:
        report_value = temperature(report['raw_value'], report['raw_unit'])
    except ValueError:
        report_value = None
    # The cross-source '来源一致' semantics (unit normalisation, exact decimal equality and the
    # rule that a differing value stays unresolved without a registered authority) come from
    # datamind.binding; the field names and issue kinds below are unchanged.
    verdict = source_agreement(row['value'], row['unit'], report['raw_value'], report['raw_unit'])
    if production is None or report_value is None or verdict['status'] == 'needs_review':
        result['agreement'] = 'needs_review'
        problems.append({'kind':'unit_or_value','status':'needs_review','reason':'VALUES_NOT_COMPARABLE'})
    elif verdict['status'] == 'unresolved':
        result['agreement'] = 'unresolved'
        problems.append({'kind':'value_conflict','status':'unresolved','reason':verdict['reason'],
                         'left_value':format(production,'f'),'right_value':format(report_value,'f')})
    else:
        result['agreement'] = verdict['status']
        result['rules'].append({'rule_id':'same_measurement_decimal_equality','rule_version':RULE_VERSION,
                                'left_value':format(production,'f'),'right_value':format(report_value,'f'),
                                'conversion':'degC = K - 273.15' if row['unit'] != report['raw_unit'] else 'identity'})
    return result
