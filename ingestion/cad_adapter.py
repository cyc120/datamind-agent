"""Native DXF adapter: frozen 2D entities, dimensions and references into the contract.

Reads through `ingestion/cad_dxf.py` (the frozen `datamind-2d-labelled-dxf-v1` profile) and
emits one contract block per entity plus one per extracted dimension, located by
`cad_entity` (layout, handle, layer, model coordinates, unit). Dimensions reference the
entity handle their source XDATA names, so the relation carries real evidence instead of
an inferred association.

Scope is deliberately narrow: LINE/CIRCLE/TEXT and explicit XDATA references only. DWG is
proprietary and needs an external converter, so it is refused explicitly rather than
being guessed at.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from datamind import parse_contract

from . import cad_dxf

ADAPTER = 'dxf-native'
CONTRACT_PARSER_VERSION = 'cad-contract-adapter-v1'
MAX_BLOCKS = 2000
_GEOMETRY_TYPES = ('LINE', 'CIRCLE')
_TITLE_LAYERS = set(cad_dxf.TITLE_LAYERS)
_DWG_REFUSAL = 'CAD_DWG_CONVERTER_UNAVAILABLE'


def _content_hash(document_id, source_version, kind, identity, text):
    return hashlib.sha256(repr([document_id, source_version, CONTRACT_PARSER_VERSION, kind,
                                identity, text or '']).encode('utf-8')).hexdigest()


def _model_xy(entity):
    insertion = entity.get('insertion') or {}
    if isinstance(insertion.get('x'), (int, float)) and isinstance(insertion.get('y'), (int, float)):
        return [float(insertion['x']), float(insertion['y'])]
    box = entity.get('bbox') or {}
    if all(isinstance(box.get(key), (int, float)) for key in ('xmin', 'ymin', 'xmax', 'ymax')):
        return [round((float(box['xmin']) + float(box['xmax'])) / 2, 6),
                round((float(box['ymin']) + float(box['ymax'])) / 2, 6)]
    return [0.0, 0.0]


def _locator(document_id, source_version, entity, *, unit, layout, coordinate_system, view_id):
    payload = {'locator_kind': 'cad_entity', 'document_id': document_id,
               'source_version': source_version, 'layout': layout or 'Model',
               'handle': entity['handle'], 'layer': entity['layer'],
               'model_xy': _model_xy(entity), 'unit': unit}
    if view_id:
        payload['view_id'] = view_id
    if coordinate_system:
        payload['coordinate_system'] = coordinate_system
    return parse_contract.validate_locator(payload).payload


def _block(document_id, source_version, index, block_type, text, locator, *, native=None,
           heading_level=None):
    payload = {'block_id': f'dxf-{document_id}-{index}'.replace(' ', '_')[:64],
               'document_id': document_id, 'source_version': source_version,
               'parser_version': CONTRACT_PARSER_VERSION, 'block_type': block_type,
               'text': text or '', 'native_attributes': native or {}, 'reading_order': index,
               'locator': locator, 'quality': {},
               'content_hash': _content_hash(document_id, source_version, block_type,
                                             locator.get('handle'), text)}
    if heading_level is not None:
        payload['heading_level'] = heading_level
    return payload


def _entity_block_type(entity):
    if entity['layer'] in _TITLE_LAYERS:
        return 'title'
    if entity.get('entity_type') in _GEOMETRY_TYPES:
        return 'figure'
    return 'paragraph'


def parse_dxf(path, *, document_id, source_version=None, provenance='synthetic_demo'):
    """Map one bounded native DXF into a contract payload (validated by `dispatch`)."""
    bundle = cad_dxf.parse_file(path, document_id=document_id, document_version=1,
                                provenance=provenance)
    snapshot = bundle['snapshot']
    source = snapshot['source']
    unit = source.get('unit') or 'mm'
    layout = source.get('layout')
    coordinate_system = source.get('coordinate_system')
    version = source_version or source.get('source_sha256')
    if not version:
        parse_contract.fail('PARSE_BLOCK_INVALID', subject='cad', reason='source_version')
    blocks, by_handle, unsupported, index = [], {}, [], 0
    for entity in snapshot['entities']:
        if index >= MAX_BLOCKS:
            if 'block_limit_reached' not in unsupported:
                unsupported.append('block_limit_reached')
            break
        locator = _locator(document_id, version, entity, unit=unit, layout=layout,
                           coordinate_system=coordinate_system, view_id=entity.get('view_id'))
        block = _block(document_id, version, index, _entity_block_type(entity),
                       entity.get('text') or '', locator,
                       native={'entity_type': entity.get('entity_type'),
                               'bbox': entity.get('bbox')},
                       heading_level=1 if entity['layer'] in _TITLE_LAYERS else None)
        blocks.append(block)
        by_handle.setdefault(entity['handle'], block)
        index += 1
    relations = []
    for feature in snapshot['features']:
        if index >= MAX_BLOCKS:
            if 'block_limit_reached' not in unsupported:
                unsupported.append('block_limit_reached')
            break
        locator = _locator(document_id, version, {'handle': feature['locator']['handle'],
                                                 'layer': feature['locator']['layer'],
                                                 'insertion': feature.get('insertion') or {}},
                           unit=feature.get('unit') or unit, layout=layout,
                           coordinate_system=coordinate_system,
                           view_id=feature['locator'].get('view_id'))
        block = _block(document_id, version, index, 'dimension',
                       feature.get('annotation_raw') or '', locator,
                       native={'feature_id': feature.get('feature_id'),
                               'feature_type': feature.get('feature_type'),
                               'nominal_value': feature.get('nominal_value'),
                               'tolerance_lower': feature.get('tolerance_lower'),
                               'tolerance_upper': feature.get('tolerance_upper'),
                               'unit': feature.get('unit'),
                               'association_status': feature.get('association_status')})
        blocks.append(block)
        index += 1
        target = feature.get('selected_target')
        target_block = by_handle.get(target)
        if target_block is None:
            # The referenced entity is not in this bounded read; keep the gap visible
            # instead of reporting a relation whose target does not exist.
            unsupported.append('dimension_target_not_included')
            continue
        evidence = (feature.get('association_evidence') or {}).get('rationale')
        method = (feature.get('association_evidence') or {}).get('method') or 'native_reference'
        relations.append({'source_block_id': block['block_id'],
                          'target_block_id': target_block['block_id'],
                          'relation_type': 'references', 'recovery_method': method,
                          'state': 'verified' if feature.get('association_status') == 'confirmed'
                                   else 'candidate',
                          'evidence': evidence or f'源 DXF 中显式 XDATA 引用句柄 {target}'})
    payload = {'classification': {'detected_media_type': parse_contract.DXF_MEDIA_TYPE,
                                  'source_name': Path(path).name},
               'adapter_capabilities': ['entities', 'text', 'layers', 'dimensions'],
               'blocks': blocks, 'relations': relations,
               'parsed_scope': [{'kind': 'object', 'ref': block['block_id'], 'status': 'parsed'}
                                for block in blocks],
               'failed_scope': [],
               'quality_findings': list(bundle['metadata'].get('warnings') or []),
               'unsupported_features': unsupported,
               'artifact_refs': [],
               'manifest': {'parser_version': cad_dxf.PARSER_VERSION,
                            'profile': bundle['metadata'].get('profile'),
                            'cad_contract_version': snapshot.get('contract_version'),
                            'source_sha256': source.get('source_sha256')},
               'parse_status': 'partial' if unsupported else 'parsed'}
    return payload


def register_cad_adapters():
    """Register the DXF adapter (idempotent).

    DWG is deliberately **not** registered: it has no capability here, and registering a
    handler-less adapter would only produce a generic "unsupported". `parse` reports the
    specific missing converter instead.
    """
    registered = set(parse_contract.registered_adapters())
    if ADAPTER not in registered:
        parse_contract.register_adapter(ADAPTER, media_types=[parse_contract.DXF_MEDIA_TYPE],
                                        capabilities=['entities', 'text', 'layers', 'dimensions'],
                                        handler=parse_dxf)
    return [ADAPTER]


def parse(path, *, document_id, source_version=None, **options):
    """Detect the drawing type and dispatch through the registered CAD adapter."""
    register_cad_adapters()
    detected = parse_contract.detect_media_type(path)
    if detected == parse_contract.DWG_MEDIA_TYPE:
        # A registered adapter without a handler would only say "unsupported"; the DWG
        # case deserves the specific reason.
        parse_contract.fail(_DWG_REFUSAL, subject='cad', reason='converter_required', format='dwg')
    return parse_contract.dispatch(detected, path=path, document_id=document_id,
                                  source_version=source_version, **options)
