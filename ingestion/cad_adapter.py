"""Native DXF adapters: the wide v2 engineering profile and the frozen v1 profile.

Two source profiles are read here and projected into the unified parse contract:

* ``v2`` (default) -- `ingestion/cad_dxf_v2.py`, profile `datamind-2d-engineering-dxf-v2`,
  contract ``cad-contract-v2``: LINE/CIRCLE/ARC/LWPOLYLINE/POLYLINE/TEXT/MTEXT, INSERT
  block references expanded recursively, and native DIMENSION entities. Every entity
  (and every block subtree) the reader cannot model is declared item by item, and
  `parse_status` becomes ``partial`` instead of the whole drawing being thrown away.
* ``v1`` -- `ingestion/cad_dxf.py`, the hash-frozen `datamind-2d-labelled-dxf-v1`
  profile and ``cad-contract-v1``. Kept selectable (`profile='v1'`) so every old
  evidence file still reproduces byte for byte.

Each contract block is located by `cad_entity` (layout, handle, layer, model coordinates,
unit). A v2 block also carries `block_path`, the INSERT handle chain that makes a
block-internal handle unique; the source locator's fields are all carried through
(`parse_contract.CAD_SOURCE_LOCATOR_FIELDS` / `CAD_SOURCE_LOCATOR_V2_FIELDS`) and
`parse_contract.CAD_SOURCE_LOCATOR_DROPPED_FIELDS` stays empty, which
`tests/test_cad_adapter.py` asserts field by field.

Dimension blocks keep the source distinction that matters for manufacturing use: a
labelled diameter carries the value its annotation declared, while a native dimension
carries `declared_text`/`declared_value` and the geometric `geometry_measurement`
separately with a `value_authority` label. Nothing promotes a measurement into a
declared size, and a native dimension is never tied to geometry it does not name.

DWG is proprietary and needs an external converter, so it is refused explicitly rather
than being guessed at.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from datamind import parse_contract

from . import cad_dxf, cad_dxf_v2
from .cad_dxf import CadReadError

ADAPTER = 'dxf-native'
DEFAULT_PROFILE = 'v2'
PROFILES = ('v1', 'v2')
PARSER_VERSIONS = {'v1': 'cad-contract-adapter-v1', 'v2': 'cad-contract-adapter-v2'}
CONTRACT_PARSER_VERSION = PARSER_VERSIONS[DEFAULT_PROFILE]
#: Contract blocks produced per document. Conservative by default: raising it stores more rows
#: and lengthens the parse. Overridable like the reader's other limits, and reported in
#: `manifest.limits` so a consumer can see the cap from the result instead of guessing why a
#: large drawing came back `partial` with `block_limit_reached`.
DEFAULT_MAX_BLOCKS = 2000
MAX_BLOCKS_CEILING = 200000


def _max_blocks() -> int:
    raw = os.environ.get('DATAMIND_CAD_MAX_BLOCKS')
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_BLOCKS
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_MAX_BLOCKS
    if not 1 <= value <= MAX_BLOCKS_CEILING:
        return DEFAULT_MAX_BLOCKS
    return value
# `ingestion/cad_contract.py` bounds its Identifier to 128 characters and that module is
# hash-pinned in the frozen profile's manifest. The unified contract is stricter (64, the
# width of every contract store column), so a contract-legal document id is always valid
# for the source profile too; this constant records that relation and is asserted in
# tests/test_cad_adapter.py instead of being handled by rewriting the caller's identity.
PROFILE_DOCUMENT_ID_MAX = 128
_GEOMETRY_TYPES = ('LINE', 'CIRCLE')
_GEOMETRY_TYPES_V2 = ('LINE', 'CIRCLE', 'ARC', 'LWPOLYLINE', 'POLYLINE', 'INSERT')
_TITLE_LAYERS = set(cad_dxf.TITLE_LAYERS)
_DWG_REFUSAL = 'CAD_DWG_CONVERTER_UNAVAILABLE'
_CAPABILITIES = {'v1': ['entities', 'text', 'layers', 'dimensions'],
                 'v2': ['entities', 'text', 'layers', 'dimensions', 'blocks', 'arcs',
                        'polylines', 'mtext', 'native_dimensions']}
_READER_OPTIONS = {'v1': (), 'v2': ('limits', 'max_bytes', 'max_entities',
                                    'max_expanded_entities', 'max_block_depth')}


def _block_identifier(document_id, index):
    """Return a stable block id that no other (document_id, index) can share.

    `dxf-{document_id}-{index}` truncated at 64 characters collapsed every block of a
    drawing whose document_id reached 59 characters onto one id, which the contract then
    refused as a duplicate. A fixed-length digest of the pair is collision-free for any
    document_id and always fits the 64-character column, exactly as
    `ingestion/office.py:_block_identifier` does for Office blocks.
    """
    material = f'{document_id}\x1f{index}'.encode('utf-8')
    return 'dxf-' + hashlib.sha256(material).hexdigest()[:32]


def _content_hash(document_id, source_version, parser_version, kind, identity, text):
    return hashlib.sha256(repr([document_id, source_version, parser_version, kind,
                                identity, text or '']).encode('utf-8')).hexdigest()


def _model_xy(entity):
    """The representative WCS point: insertion point, else the v2 `anchor`, else a center.

    The v2 reader supplies `anchor`, a real point of the entity (a dimension's first
    definition point, a polyline's first vertex); the bbox centre is only a fallback for
    a v1 entity, which has no explicit anchor.
    """
    for key in ('insertion', 'anchor'):
        point = entity.get(key) or {}
        if isinstance(point.get('x'), (int, float)) and isinstance(point.get('y'), (int, float)):
            return [float(point['x']), float(point['y'])]
    box = entity.get('bbox') or {}
    if all(isinstance(box.get(key), (int, float)) for key in ('xmin', 'ymin', 'xmax', 'ymax')):
        return [round((float(box['xmin']) + float(box['xmax'])) / 2, 6),
                round((float(box['ymin']) + float(box['ymax'])) / 2, 6)]
    return [0.0, 0.0]


def _locator(document_id, source_version, entity, *, unit, layout, coordinate_system, view_id,
             source_sha256=None):
    """Project a source locator into the unified `cad_entity` locator.

    Every field of the source locator is accounted for: `layer`/`layout` are the required
    ones, `handle`/`view_id`/`coordinate_system`/`source_sha256` are carried through,
    `block_path` is carried through when the source has one (v2), and `model_xy`/`unit`
    are added because the source locator has no equivalent for them (its coordinates live
    in the drawing's WCS and its unit in the source's INSUNITS). Nothing is discarded.

    A handle-less (positional) source entity has `handle=None`: the projection then sends
    `entity_index` + `handle_source='positional'` and **omits** `handle` entirely, so the
    unified locator cannot be read as if a source handle existed.
    """
    payload = {'locator_kind': 'cad_entity', 'document_id': document_id,
               'source_version': source_version, 'layout': layout or 'Model',
               'layer': entity['layer'], 'model_xy': _model_xy(entity), 'unit': unit}
    if entity.get('handle'):
        payload['handle'] = entity['handle']
        if entity.get('handle_source'):
            payload['handle_source'] = entity['handle_source']
    elif entity.get('entity_index') is not None:
        payload['handle_source'] = entity.get('handle_source') or 'positional'
        payload['entity_index'] = entity['entity_index']
    if view_id:
        payload['view_id'] = view_id
    if coordinate_system:
        payload['coordinate_system'] = coordinate_system
    if source_sha256:
        payload['source_sha256'] = source_sha256
    block_path = entity.get('block_path')
    if block_path is not None:
        payload['block_path'] = list(block_path)
    return parse_contract.validate_locator(payload).payload


def _snapshot_identity_mode(snapshot):
    """The identity mode a snapshot itself declares, for a bundle without `metadata.identity`.

    The v1 reader has no positional mode, so its snapshot answers `source`; the v2 reader
    states the mode in `metadata['identity']` and this fallback only covers a bundle that
    was assembled by hand (every test fixture does).
    """
    for entity in snapshot.get('entities') or ():
        if entity.get('handle'):
            return 'source'
        if entity.get('entity_index') is not None:
            return 'positional'
    return 'source'


def _locator_identity(locator):
    """The locator's own identity token, used as a content-hash input.

    A positional locator has no handle; its declared `entity_index` stands in so two
    different entities never hash to the same identity by accident.
    """
    if locator.get('handle'):
        return locator['handle']
    index = locator.get('entity_index')
    return None if index is None else 'i' + str(index)


def _block(document_id, source_version, parser_version, index, block_type, text, locator, *,
           native=None, heading_level=None):
    payload = {'block_id': _block_identifier(document_id, index),
               'document_id': document_id, 'source_version': source_version,
               'parser_version': parser_version, 'block_type': block_type,
               'text': text or '', 'native_attributes': native or {}, 'reading_order': index,
               'locator': locator, 'quality': {},
               'content_hash': _content_hash(document_id, source_version, parser_version,
                                             block_type, _locator_identity(locator), text)}
    if heading_level is not None:
        payload['heading_level'] = heading_level
    return payload


def _entity_block_type(entity, profile):
    if entity['layer'] in _TITLE_LAYERS:
        return 'title'
    geometry = _GEOMETRY_TYPES_V2 if profile == 'v2' else _GEOMETRY_TYPES
    if entity.get('entity_type') in geometry:
        return 'figure'
    return 'paragraph'


def _entity_native(entity, profile):
    """The source attributes a reader block carries on top of its locator."""
    native = {'entity_type': entity.get('entity_type'), 'bbox': entity.get('bbox')}
    if profile == 'v2':
        native['block_path'] = list(entity.get('block_path') or [])
        for key in ('block_name', 'expansion_status', 'expanded_entity_count', 'dimension_type',
                    'geometry_measurement', 'declared_text'):
            if entity.get(key) is not None:
                native[key] = entity[key]
    return native


def _feature_native(feature):
    native = {'feature_id': feature.get('feature_id'), 'feature_type': feature.get('feature_type'),
              'unit': feature.get('unit')}
    if feature.get('feature_type') == 'diameter':
        native.update({'nominal_value': feature.get('nominal_value'),
                       'tolerance_lower': feature.get('tolerance_lower'),
                       'tolerance_upper': feature.get('tolerance_upper'),
                       'association_status': feature.get('association_status'),
                       'value_authority': 'declared_text' if feature.get('nominal_value') else None})
    else:
        # A native dimension carries the two values and their authority separately, so a
        # consumer can never read the geometric measurement as a declared size.
        native.update({'dimension_type': feature.get('dimension_type'),
                       'declared_text': feature.get('declared_text'),
                       'declared_value': feature.get('declared_value'),
                       'geometry_measurement': feature.get('geometry_measurement'),
                       'value_authority': feature.get('value_authority')})
    return native


def _unsupported_result(*, source_name, profile, parser_version, declarations,
                        manifest_extra=None, block_cap=DEFAULT_MAX_BLOCKS):
    """A contract-valid result for a drawing with no supported entity at all.

    The reader declares every unread entity, so the refusal is not silent: each one is
    carried in `unsupported_features` and in `failed_scope`.
    """
    features = list(declarations.get('unsupported_features') or [])
    scopes = [{'kind': 'object', 'ref': item.get('handle') or 'drawing', 'status': 'unsupported',
               'detail': item.get('code')}
              for item in (declarations.get('unsupported') or [])[:100]]
    return {'classification': {'detected_media_type': parse_contract.DXF_MEDIA_TYPE,
                              'source_name': source_name},
            'adapter_capabilities': list(_CAPABILITIES[profile]),
            'blocks': [], 'relations': [], 'parsed_scope': [], 'failed_scope': scopes,
            'quality_findings': [], 'unsupported_features': features, 'artifact_refs': [],
            'manifest': {'parser_version': parser_version, 'profile': cad_dxf_v2.PROFILE,
                         'cad_contract_version': 'cad-contract-v2', 'source_sha256': None,
                         'identity_mode': declarations.get('identity') or 'source',
                         # `limits` is present in every shape, including this one: the docs
                         # promise the cap can be read from the result, and the capability
                         # suite flagged the gap (an `unsupported` result used to omit it).
                         'limits': {**((manifest_extra or {}).get('limits') or {}),
                                    'max_blocks': block_cap},
                         **(manifest_extra or {})},
            'parse_status': 'unsupported'}


def parse_dxf(path, *, document_id, source_version=None, provenance='synthetic_demo',
              profile=DEFAULT_PROFILE, **reader_options):
    """Map one native DXF into a contract payload (validated by `dispatch`).

    The id is checked against the contract store's column width before anything is
    parsed: an over-long id can never be stored, so refusing it here keeps the caller
    from getting a "parsed" result that only fails later at INSERT time. A contract-legal
    id is also within the frozen v1 source profile's bound (`PROFILE_DOCUMENT_ID_MAX`),
    so it is handed to the profile unchanged -- the caller's identity is never rewritten.
    """
    parse_contract.require_document_id(document_id, code='PARSE_RESULT_INVALID', subject='cad')
    # Resolved once per call: an explicit argument wins over the environment, and both are
    # validated (an out-of-range value falls back to the documented default rather than
    # silently truncating a drawing at an arbitrary number).
    block_cap = reader_options.pop('max_blocks', None)
    if block_cap is None:
        block_cap = _max_blocks()
    elif isinstance(block_cap, bool) or not isinstance(block_cap, int) \
            or not 1 <= block_cap <= MAX_BLOCKS_CEILING:
        block_cap = DEFAULT_MAX_BLOCKS
    if profile not in PROFILES:
        parse_contract.fail('CAD_PROFILE_UNSUPPORTED', subject='cad', profile=repr(profile),
                            supported=list(PROFILES))
    unknown = sorted(set(reader_options) - set(_READER_OPTIONS[profile]))
    if unknown:
        # A caller that asked for a limit the profile cannot honour must not receive a
        # result that looks bounded and is not.
        parse_contract.fail('CAD_PROFILE_OPTION_UNSUPPORTED', subject='cad', profile=profile,
                            options=unknown)
    parser_version = PARSER_VERSIONS[profile]
    reader = cad_dxf_v2 if profile == 'v2' else cad_dxf
    try:
        bundle = reader.parse_file(path, document_id=document_id, document_version=1,
                                  provenance=provenance, **reader_options)
    except CadReadError as error:
        if profile == 'v2' and error.code == 'CAD_NO_SUPPORTED_ENTITIES':
            return _unsupported_result(source_name=Path(path).name, profile=profile,
                                       parser_version=reader.PARSER_VERSION,
                                       declarations=error.details, block_cap=block_cap)
        # Surface the reader's own stable code instead of a generic handler failure.
        parse_contract.fail(error.code, subject='cad', **error.details)
    snapshot = bundle['snapshot']
    metadata = bundle['metadata']
    source = snapshot['source']
    source_sha256 = source.get('source_sha256')
    unit = source.get('unit') or 'unknown'
    layout = source.get('layout')
    coordinate_system = source.get('coordinate_system')
    version = source_version or source_sha256
    if not version:
        parse_contract.fail('PARSE_BLOCK_INVALID', subject='cad', reason='source_version')
    blocks, by_key, by_handle, index = [], {}, {}, 0
    # The reader declares its identity mode explicitly; it is carried into the manifest so
    # nothing downstream reads a positional drawing as an ordinary source-handle one.
    identity = metadata.get('identity') or {}
    identity_mode = identity.get('mode') or _snapshot_identity_mode(snapshot)
    # Everything the reader could not model is already declared with a stable code, an
    # entity type and a source handle; the adapter carries those declarations forward.
    unsupported = list(metadata.get('unsupported_features') or [])

    def declare(code):
        if code not in unsupported:
            unsupported.append(code)

    def add_block(block_type, text, locator, native, *, heading_level=None):
        nonlocal index
        block = _block(document_id, version, parser_version, index, block_type, text, locator,
                       native=native, heading_level=heading_level)
        blocks.append(block)
        index += 1
        return block

    for entity in snapshot['entities']:
        if index >= block_cap:
            declare('block_limit_reached')
            break
        locator = _locator(document_id, version, entity, unit=unit, layout=layout,
                           coordinate_system=coordinate_system, view_id=entity.get('view_id'),
                           source_sha256=source_sha256)
        block = add_block(_entity_block_type(entity, profile),
                          entity.get('text') or '', locator, _entity_native(entity, profile),
                          heading_level=1 if entity['layer'] in _TITLE_LAYERS else None)
        by_key.setdefault((entity.get('handle'), entity.get('entity_index'),
                           tuple(entity.get('block_path') or [])), block)
        if entity.get('handle'):
            by_handle.setdefault(entity['handle'], block)
    native_dimensions = 0
    relations = []
    for feature in snapshot['features']:
        if index >= block_cap:
            declare('block_limit_reached')
            break
        locator_payload = feature['locator']
        key = (locator_payload.get('handle'), locator_payload.get('entity_index'),
               tuple(locator_payload.get('block_path') or []))
        # The feature's own entity supplies the representative point (`insertion`/`anchor`),
        # so the projection adds `model_xy` from the source, never from the annotation text.
        entity = by_key.get(key) or {'layer': locator_payload['layer'],
                                     'block_path': locator_payload.get('block_path')}
        entity = {**entity, 'layer': locator_payload['layer'],
                  'block_path': locator_payload.get('block_path')}
        if locator_payload.get('handle'):
            entity['handle'] = locator_payload['handle']
        if locator_payload.get('entity_index') is not None:
            entity['entity_index'] = locator_payload['entity_index']
            entity['handle_source'] = (locator_payload.get('handle_source')
                                       or 'positional')
        locator = _locator(document_id, version, entity, unit=feature.get('unit') or unit,
                           layout=layout, coordinate_system=coordinate_system,
                           view_id=locator_payload.get('view_id'), source_sha256=source_sha256)
        native = _feature_native(feature)
        if feature.get('feature_type') == 'native_dimension':
            native_dimensions += 1
        text = feature.get('declared_text') or feature.get('annotation_raw') or ''
        block = add_block('dimension', text, locator, native)
        target = feature.get('selected_target')
        if not target:
            continue
        target_block = by_handle.get(target)
        if target_block is None:
            # The referenced entity is not in this bounded read; keep the gap visible
            # instead of reporting a relation whose target does not exist.
            declare('dimension_target_not_included')
            continue
        evidence = feature.get('association_evidence') or {}
        relations.append({'source_block_id': block['block_id'],
                          'target_block_id': target_block['block_id'],
                          'relation_type': 'references',
                          'recovery_method': evidence.get('method') or 'native_reference',
                          'state': 'verified' if feature.get('association_status') == 'confirmed'
                                   else 'candidate',
                          'evidence': evidence.get('rationale')
                                      or f'源 DXF 中显式 XDATA 引用句柄 {target}'})
    return _payload(path=path, profile=profile, reader=reader, metadata=metadata, snapshot=snapshot,
                    blocks=blocks, relations=relations, unsupported=unsupported,
                    native_dimensions=native_dimensions, source_sha256=source_sha256,
                    identity_mode=identity_mode, identity=identity, block_cap=block_cap)


def _payload(*, path, profile, reader, metadata, snapshot, blocks, relations, unsupported,
             native_dimensions, source_sha256, identity_mode='source', identity=None,
             block_cap=DEFAULT_MAX_BLOCKS):
    quality = list(metadata.get('warnings') or [])
    if identity_mode == 'positional':
        # The mode is stated in the *manifest* as well as in every locator, so a consumer
        # that only reads the manifest cannot mistake a positional drawing for an ordinary
        # source-handle parse. The reader already warns about the mode, so it is not repeated.
        if 'CAD_IDENTITY_MODE_POSITIONAL' not in quality:
            quality.append('CAD_IDENTITY_MODE_POSITIONAL')
        if identity.get('positional_records'):
            quality.append('CAD_SOURCE_HANDLE_UNAVAILABLE:'
                           + str(identity['positional_records']))
    if native_dimensions:
        # The reader never ties a native dimension to the geometry it measures; saying so
        # as a quality finding keeps it out of the "declared gap" status while staying visible.
        quality.append(f'CAD_NATIVE_DIMENSION_ASSOCIATION_NOT_RECOVERED:{native_dimensions}')
    manifest = {'parser_version': reader.PARSER_VERSION, 'profile': metadata.get('profile'),
                'cad_contract_version': snapshot.get('contract_version'),
                'source_sha256': source_sha256, 'identity_mode': identity_mode}
    if identity:
        # Deliberately *not* `manifest['identity']`: `identity` in this contract is the
        # drawing's business identity (`DM_DRAWING_ID` and friends), so the source's
        # handle/positional identity is named `source_identity` to keep the two apart.
        manifest['source_identity'] = identity
    for key in ('encoding', 'limits', 'unsupported_counts', 'counts'):
        if metadata.get(key) is not None:
            manifest[key] = metadata[key]
    # The block cap belongs next to the reader's other limits: without it a consumer sees
    # `partial` + `block_limit_reached` but cannot read *what* the cap was.
    manifest.setdefault('limits', {})['max_blocks'] = block_cap
    return {'classification': {'detected_media_type': parse_contract.DXF_MEDIA_TYPE,
                              'source_name': Path(path).name},
            'adapter_capabilities': list(_CAPABILITIES[profile]),
            'blocks': blocks, 'relations': relations,
            'parsed_scope': [{'kind': 'object', 'ref': block['block_id'], 'status': 'parsed'}
                             for block in blocks],
            'failed_scope': _failed_scope(metadata.get('unsupported')),
            'quality_findings': quality,
            'unsupported_features': unsupported,
            'artifact_refs': [],
            'manifest': manifest,
            'parse_status': 'partial' if unsupported else 'parsed'}


def _failed_scope(unsupported):
    """Declared unread entities, same cap as `unsupported_features`, as failed scope."""
    scopes = []
    for item in (unsupported or [])[:100]:
        if item.get('code'):
            scopes.append({'kind': 'object', 'ref': item.get('handle') or 'drawing',
                           'status': 'unsupported', 'detail': item['code']})
    return scopes


def register_cad_adapters():
    """Register the DXF adapter (idempotent).

    DWG is deliberately **not** registered: it has no capability here, and registering a
    handler-less adapter would only produce a generic "unsupported". `parse` reports the
    specific missing converter instead.
    """
    registered = set(parse_contract.registered_adapters())
    if ADAPTER not in registered:
        parse_contract.register_adapter(ADAPTER, media_types=[parse_contract.DXF_MEDIA_TYPE],
                                        capabilities=_CAPABILITIES[DEFAULT_PROFILE],
                                        handler=parse_dxf)
    return [ADAPTER]


def parse(path, *, document_id, source_version=None, profile=DEFAULT_PROFILE, **options):
    """Detect the drawing type and dispatch through the registered CAD adapter."""
    register_cad_adapters()
    detected = parse_contract.detect_media_type(path)
    if detected == parse_contract.DWG_MEDIA_TYPE:
        # A registered adapter without a handler would only say "unsupported"; the DWG
        # case deserves the specific reason.
        parse_contract.fail(_DWG_REFUSAL, subject='cad', reason='converter_required', format='dwg')
    return parse_contract.dispatch(detected, path=path, document_id=document_id,
                                   source_version=source_version, profile=profile, **options)
