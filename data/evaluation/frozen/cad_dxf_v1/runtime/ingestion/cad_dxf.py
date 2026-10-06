"""Bounded native DXF profile; no gold, model, database or network input.

Titles use explicit DM_* layers, and object associations use source XDATA.
This is a controlled interchange profile, not arbitrary drawing understanding.
"""
from decimal import Decimal, localcontext
import hashlib
import io
import math
from pathlib import Path
import re

from pydantic import ValidationError
from ingestion.cad_contract import DrawingSnapshot

PARSER_VERSION = 'dxf-native-v1'
PROFILE = 'datamind-2d-labelled-dxf-v1'
MAX_BYTES = 2 * 1024 * 1024
MAX_ENTITIES = 10000
TITLE_LAYERS = {'DM_DRAWING_ID': 'drawing_id', 'DM_REVISION': 'drawing_revision', 'DM_PART_ID': 'part_id'}


class CadReadError(ValueError):
    def __init__(self, code, details=None):
        self.code, self.details = code, details or {}
        super().__init__(code)


def _library():
    try:
        import ezdxf
        from ezdxf import bbox
    except ImportError as error:
        raise CadReadError('CAD_DEPENDENCY_MISSING', {'requirements': 'requirements-cad.txt'}) from error
    return ezdxf, bbox


def _raw_handles(text):
    """Detect missing/duplicate entity handles before the library can repair them."""
    lines = text.splitlines()
    if len(lines) % 2:
        raise CadReadError('CAD_DXF_TAGS_INVALID')
    try:
        tags = [(int(lines[i].strip()), lines[i+1].strip()) for i in range(0, len(lines), 2)]
    except ValueError as error:
        raise CadReadError('CAD_DXF_TAGS_INVALID') from error
    inside, pending, handles = False, None, []

    def finish():
        if pending is not None:
            if len(pending) != 1 or not re.fullmatch(r'[1-9A-F][0-9A-F]*', pending[0]):
                raise CadReadError('CAD_ENTITY_HANDLE_INVALID')
            handles.append(pending[0])

    for i, (code, value) in enumerate(tags):
        if code == 0 and value == 'SECTION':
            inside = i+1 < len(tags) and tags[i+1] == (2, 'ENTITIES')
        elif inside and code == 0:
            finish()
            if value == 'ENDSEC':
                inside, pending = False, None
            else:
                pending = []
        elif inside and code == 5 and pending is not None:
            pending.append(value)
    if not handles or len(handles) > MAX_ENTITIES:
        raise CadReadError('CAD_ENTITY_COUNT_INVALID')
    if len(set(handles)) != len(handles):
        raise CadReadError('CAD_DUPLICATE_SOURCE_HANDLE')
    return handles


def _point(vector):
    if not all(math.isfinite(v) and abs(v) <= 1e9 for v in vector) or vector.z != 0:
        raise CadReadError('CAD_3D_OR_NONFINITE_UNSUPPORTED')
    return {'x': vector.x, 'y': vector.y}


def parse_bytes(data, *, document_id, document_version=1, provenance='synthetic_demo'):
    if not data or len(data) > MAX_BYTES:
        raise CadReadError('CAD_SOURCE_SIZE_INVALID')
    if data.startswith(b'AutoCAD Binary DXF') or b'\x00' in data:
        raise CadReadError('CAD_BINARY_DXF_UNSUPPORTED')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise CadReadError('CAD_ENCODING_UNSUPPORTED') from error
    raw_handles = _raw_handles(text)
    ezdxf, bbox = _library()
    try:
        doc = ezdxf.read(io.StringIO(text))
        audit = doc.audit()
    except (ezdxf.DXFError, ValueError, TypeError) as error:
        raise CadReadError('CAD_DXF_INVALID') from error
    if audit.errors or audit.fixes:
        raise CadReadError('CAD_DXF_REPAIR_REQUIRED', {'errors': len(audit.errors), 'fixes': len(audit.fixes)})
    if any(len(layout) for layout in doc.layouts if layout.name != 'Model'):
        raise CadReadError('CAD_PAPERSPACE_UNSUPPORTED')
    native = list(doc.modelspace())
    if set(raw_handles) != {e.dxf.handle for e in native} or len(raw_handles) != len(native):
        raise CadReadError('CAD_ENTITY_LOSS_OR_LAYOUT_UNSUPPORTED')
    unit_code = doc.header.get('$INSUNITS', 0)
    if unit_code not in (0, 1, 4):
        raise CadReadError('CAD_UNIT_UNSUPPORTED', {'insunits': unit_code})
    unit = {0: 'unknown', 1: 'in', 4: 'mm'}[unit_code]
    sha = hashlib.sha256(data).hexdigest()
    entities, identity, render_metadata = [], {}, {}
    warnings = ['CAD_TEXT_BOUNDS_APPROXIMATE', 'CAD_SINGLE_MODEL_VIEW_ONLY']
    if unit == 'unknown':
        warnings.append('CAD_UNIT_UNKNOWN')

    def locator(entity):
        return dict(locator_kind='cad_entity', source_sha256=sha, handle=entity.dxf.handle,
                    layer=entity.dxf.layer, view_id='model', coordinate_system='WCS_XY', layout='Model')

    for entity in native:
        kind = entity.dxftype()
        if kind not in {'LINE', 'CIRCLE', 'TEXT'}:
            raise CadReadError('CAD_ENTITY_TYPE_UNSUPPORTED', {'handle': entity.dxf.handle, 'type': kind})
        if tuple(entity.dxf.get('extrusion', (0, 0, 1))) != (0, 0, 1) or entity.dxf.get('thickness', 0) != 0:
            raise CadReadError('CAD_OCS_OR_THICKNESS_UNSUPPORTED', {'handle': entity.dxf.handle})
        if (entity.dxf.get('invisible', 0) or doc.layers.get(entity.dxf.layer).is_off()
                or doc.layers.get(entity.dxf.layer).is_frozen()):
            raise CadReadError('CAD_HIDDEN_ENTITY_UNSUPPORTED', {'handle': entity.dxf.handle})
        item = dict(entity_type=kind, handle=entity.dxf.handle, layer=entity.dxf.layer, view_id='model')
        if kind == 'LINE':
            item.update(start=_point(entity.dxf.start), end=_point(entity.dxf.end))
        elif kind == 'CIRCLE':
            if not math.isfinite(entity.dxf.radius) or not 0 < entity.dxf.radius <= 1e9:
                raise CadReadError('CAD_RADIUS_INVALID')
            item.update(center=_point(entity.dxf.center), radius=entity.dxf.radius)
        else:
            if (entity.dxf.get('rotation', 0) != 0 or entity.dxf.get('oblique', 0) != 0
                    or entity.dxf.get('width', 1) != 1 or entity.dxf.get('halign', 0) != 0
                    or entity.dxf.get('valign', 0) != 0 or entity.dxf.get('text_generation_flag', 0) != 0
                    or entity.dxf.get('style', 'Standard').lower() != 'standard'):
                raise CadReadError('CAD_TEXT_TRANSFORM_UNSUPPORTED', {'handle': entity.dxf.handle})
            style = doc.styles.get(entity.dxf.get('style', 'Standard'))
            if (any(style.dxf.get(key, default) != default for key, default in
                    [('height', 0), ('width', 1), ('oblique', 0), ('flags', 0), ('generation_flags', 0), ('bigfont', '')])
                    or style.dxf.get('font','').lower() not in ('', 'txt')):
                raise CadReadError('CAD_TEXT_STYLE_UNSUPPORTED', {'handle': entity.dxf.handle})
            insert = _point(entity.dxf.insert)
            height = entity.dxf.height
            if not math.isfinite(height) or not 0 < height <= 1e6 or not entity.dxf.text.strip():
                raise CadReadError('CAD_TEXT_INVALID', {'handle': entity.dxf.handle})
            if any(ord(c) < 32 for c in entity.dxf.text):
                raise CadReadError('CAD_TEXT_CONTROL_CHARACTER_UNSUPPORTED')
            extent = bbox.extents([entity])
            if not extent.has_data:
                raise CadReadError('CAD_TEXT_BOUNDS_UNAVAILABLE')
            # Include the insertion point; ezdxf text bounds are approximate.
            bounds = dict(xmin=min(insert['x'], extent.extmin.x), ymin=min(insert['y'], extent.extmin.y),
                          xmax=max(insert['x'], extent.extmax.x), ymax=max(insert['y'], extent.extmax.y))
            item.update(text=entity.dxf.text, insertion=insert, bbox=bounds)
            render_metadata[entity.dxf.handle] = {'text_height': height}
            if entity.dxf.layer in TITLE_LAYERS:
                key = TITLE_LAYERS[entity.dxf.layer]
                if key in identity:
                    raise CadReadError('CAD_TITLE_DUPLICATE', {'field': key})
                identity[key] = {'value': entity.dxf.text, 'locator': locator(entity)}
        entities.append(item)
    if set(identity) != set(TITLE_LAYERS.values()):
        raise CadReadError('CAD_TITLE_MISSING', {'missing': sorted(set(TITLE_LAYERS.values())-set(identity))})
    circles = {e.dxf.handle: e for e in native if e.dxftype() == 'CIRCLE'}
    features = []
    for annotation in native:
        if annotation.dxftype() != 'TEXT' or annotation.dxf.layer != 'DM_DIAMETER':
            continue
        candidates, evidence, selected = sorted(circles), None, None
        if annotation.has_xdata('DATAMIND'):
            tags = list(annotation.get_xdata('DATAMIND'))
            if not tags or tags[0] != (1000, 'diameter_target') or any(t.code != 1005 for t in tags[1:]):
                raise CadReadError('CAD_REFERENCE_FORMAT_UNSUPPORTED')
            candidates = [t.value for t in tags[1:]]
            if (not candidates or len(set(candidates)) != len(candidates)
                    or any(c not in circles for c in candidates)):
                raise CadReadError('CAD_REFERENCE_TARGET_INVALID')
            if len(candidates) == 1:
                selected = candidates[0]
                evidence = dict(method='native_reference', reference_id='xdata-'+annotation.dxf.handle,
                                source_sha256=sha, annotation_handle=annotation.dxf.handle,
                                target_handle=selected, rationale='Explicit DATAMIND XDATA diameter_target handle in source DXF; no business approval inferred.')
        state = 'confirmed' if evidence else ('ambiguous' if len(candidates)>1 else 'unresolved')
        if len(candidates) > 100:
            raise CadReadError('CAD_TOO_MANY_ASSOCIATION_CANDIDATES')
        nominal = lower = upper = None
        match = re.fullmatch(r'%%c([0-9]+(?:\.[0-9]+)?) \+/-([0-9]+(?:\.[0-9]+)?)', annotation.dxf.text)
        if unit != 'unknown' and match and len(match[1]) <= 64 and len(match[2]) <= 64:
            with localcontext() as ctx:
                ctx.prec = 160
                n, t = Decimal(match[1]), Decimal(match[2])
                if n > 0 and n-t > 0:
                    nominal, lower, upper = format(n,'f'), format(-t,'f'), format(t,'f')
        if nominal is None:
            warnings.append('CAD_DIMENSION_VALUE_UNKNOWN:'+annotation.dxf.handle)
        features.append(dict(feature_id='diameter-'+annotation.dxf.handle, feature_type='diameter',
                             annotation_raw=annotation.dxf.text, locator=locator(annotation), nominal_value=nominal,
                             tolerance_lower=lower, tolerance_upper=upper, unit=unit, association_status=state,
                             target_candidates=candidates, selected_target=selected, association_evidence=evidence))
    bounds = []
    for item in entities:
        if item['entity_type'] == 'LINE':
            a, b = item['start'], item['end']
            bounds.append((min(a['x'],b['x']),min(a['y'],b['y']),max(a['x'],b['x']),max(a['y'],b['y'])))
        elif item['entity_type'] == 'CIRCLE':
            c, r = item['center'], item['radius']
            bounds.append((c['x']-r,c['y']-r,c['x']+r,c['y']+r))
        else:
            b = item['bbox']; bounds.append((b['xmin'],b['ymin'],b['xmax'],b['ymax']))
    view = dict(xmin=min(b[0] for b in bounds)-1, ymin=min(b[1] for b in bounds)-1,
                xmax=max(b[2] for b in bounds)+1, ymax=max(b[3] for b in bounds)+1)
    payload = dict(contract_version='cad-contract-v1', source=dict(document_id=document_id,document_version=document_version,
                   source_sha256=sha, format='dxf',parser_version=PARSER_VERSION,provenance=provenance,insunits=unit_code,
                   unit=unit,coordinate_system='WCS_XY',layout='Model'),identity=identity,
                   views=[dict(view_id='model',bbox=view)],entities=entities,features=features)
    try:
        snapshot = DrawingSnapshot.model_validate(payload).model_dump()
    except ValidationError as error:
        raise CadReadError('CAD_OUTPUT_CONTRACT_INVALID', {'validation': str(error)}) from error
    return dict(snapshot=snapshot, metadata=dict(profile=PROFILE,parser_version=PARSER_VERSION,ezdxf_version=ezdxf.__version__,
                dxf_version=doc.dxfversion,warnings=warnings,text_bounds='approximate_default_font',render_entities=render_metadata))


def parse_file(path, **context):
    path = Path(path)
    if path.suffix.lower() != '.dxf':
        raise CadReadError('CAD_FORMAT_UNSUPPORTED')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES+1)
    return parse_bytes(data, **context)
