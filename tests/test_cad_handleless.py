"""Handle-less old DXF: a declared positional identity, never a synthesised handle.

An old ASCII DXF written with `$HANDLING` off (R2.x/R12, and the real 453 KB `test.dxf`
drawing whose upload triggered this work) carries **no** `code 5` tag
on any entity record. `ezdxf` nevertheless answers `entity.dxf.handle` with a value it
synthesised (`'2'`, `'31E'`, ...), so "the library gave me a handle" is not evidence that
the *source* has one.

These tests pin the resulting contract:

1. a source whose records really carry a `code 5` tag keeps the existing
   `handle_source='source'` behaviour, byte for byte;
2. a source whose records carry none is read in `handle_source='positional'` mode: every
   entity gets a deterministic 1-based `entity_index` and `handle` is **absent**, the gap
   is declared as `CAD_SOURCE_HANDLE_UNAVAILABLE`, and the result is `partial`;
3. an invalid, duplicated or half-present handle set is still refused outright -- a
   positional fallback never rescues an untrustworthy handle set;
4. a positional locator in the unified contract must carry `entity_index` and must not
   carry a handle, while the old handle-only locator keeps validating unchanged.

The fixtures are hand-written ASCII DXF strings (LF and CRLF variants) because the
`ezdxf` writer always emits `code 5` handles, so it cannot produce this source shape.
"""
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import ezdxf
from fastapi import HTTPException

from datamind import parse_contract as contract
from ingestion import cad_adapter, cad_dxf_v2
from ingestion.cad_contract_v2 import (IDENTITY_MODES, POSITIONAL_BLOCK_UNSUPPORTED,
                                       SOURCE_HANDLE_UNAVAILABLE, DrawingSnapshot)
from ingestion.cad_dxf import CadReadError


def _pairs(items):
    """A DXF tag stream from `(code, value)` pairs, LF-terminated."""
    return ''.join(f'{code}\n{value}\n' for code, value in items)


def handleless_dxf(*, crlf=False, with_block=False):
    """A minimal AC1009 (R12) drawing whose entity records carry **no** `code 5` tag.

    Two `POLYLINE`s, each with its own `VERTEX`/`SEQEND` sub-records, so the fixture also
    pins the numbering rule: a sub-record never takes a primary `entity_index`. With
    `with_block=True` an unexpanded `INSERT` names a block that contains one `LINE`.
    """
    header = [('0', 'SECTION'), ('2', 'HEADER'), ('9', '$ACADVER'), ('1', 'AC1009'),
              ('9', '$EXTMIN'), ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
              ('9', '$EXTMAX'), ('10', '20.0'), ('20', '20.0'), ('30', '0.0'),
              ('0', 'ENDSEC')]
    tables = [('0', 'SECTION'), ('2', 'TABLES'),
              ('0', 'TABLE'), ('2', 'LAYER'), ('70', '1'),
              ('0', 'LAYER'), ('2', '0'), ('70', '0'), ('62', '7'), ('6', 'CONTINUOUS'),
              ('0', 'ENDTAB'), ('0', 'ENDSEC')]
    blocks = [('0', 'SECTION'), ('2', 'BLOCKS')]
    if with_block:
        blocks += [('0', 'BLOCK'), ('8', '0'), ('2', 'PART'), ('70', '0'),
                   ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
                   ('0', 'LINE'), ('8', '0'), ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
                   ('10', '1.0'), ('20', '0.0'), ('30', '0.0'),
                   ('0', 'ENDBLK'), ('8', '0')]
    blocks += [('0', 'ENDSEC')]
    entities = [('0', 'SECTION'), ('2', 'ENTITIES')]
    for vertices in (((0, 0), (5, 0), (5, 5)), ((10, 10), (15, 10))):
        entities += [('0', 'POLYLINE'), ('8', '0'), ('66', '1'),
                     ('10', '0.0'), ('20', '0.0'), ('30', '0.0')]
        for x, y in vertices:
            entities += [('0', 'VERTEX'), ('8', '0'), ('10', str(float(x))),
                         ('20', str(float(y))), ('30', '0.0')]
        entities += [('0', 'SEQEND'), ('8', '0')]
    if with_block:
        entities += [('0', 'INSERT'), ('8', '0'), ('2', 'PART'),
                     ('10', '2.0'), ('20', '2.0'), ('30', '0.0')]
    entities += [('0', 'ENDSEC'), ('0', 'EOF')]
    text = _pairs(header + tables + blocks + entities)
    return text.replace('\n', '\r\n') if crlf else text


def handleless_bytes(**kwargs):
    return handleless_dxf(**kwargs).encode('utf-8')


def titled_doc(unit=4):
    """A small handle-bearing drawing: three DM_* titles plus a LINE and a CIRCLE."""
    doc = ezdxf.new('R2010', setup=True)
    doc.header['$INSUNITS'] = unit
    for layer in ('DM_DRAWING_ID', 'DM_REVISION', 'DM_PART_ID', 'GEOMETRY'):
        doc.layers.new(layer)
    model = doc.modelspace()
    for value, layer, y in (('DRW-1', 'DM_DRAWING_ID', 60), ('R1', 'DM_REVISION', 50),
                            ('P-1', 'DM_PART_ID', 40)):
        model.add_text(value, dxfattribs={'layer': layer, 'insert': (0, y), 'height': 2})
    line = model.add_line((0, 0), (5, 0), dxfattribs={'layer': 'GEOMETRY'})
    circle = model.add_circle((1, 1), 1, dxfattribs={'layer': 'GEOMETRY'})
    return doc, line, circle


def source_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode('utf-8')


# The `ezdxf` writer aligns group codes; match one entity handle tag however it is spaced.
_HANDLE_TAG = r'(?m)^ *5\r?\n{handle}\r?\n'


def replace_handle_tag(data, handle, replacement):
    pattern = _HANDLE_TAG.format(handle=re.escape(handle)).encode()
    return re.sub(pattern, replacement.encode(), data, count=1)


def drop_handle_tag(data, handle):
    return replace_handle_tag(data, handle, '')


def parsed(data, **options):
    return cad_dxf_v2.parse_bytes(data, document_id='doc-handleless', **options)


def codes(bundle):
    return [item['code'] for item in bundle['metadata']['unsupported']]


def handle_values(node):
    """Every value sitting under a `handle` key anywhere in a nested payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'handle':
                yield value
            yield from handle_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from handle_values(item)


class HandlelessFixtureTests(unittest.TestCase):
    """The fixture is really handle-less, and the reader really does not invent one."""

    def test_the_fixture_carries_no_code_5_record_at_all(self):
        text = handleless_dxf()
        self.assertNotIn('$HANDLING', text)
        pairs = [(int(text.splitlines()[i].strip()), text.splitlines()[i + 1])
                 for i in range(0, len(text.splitlines()), 2)]
        self.assertEqual([value for code, value in pairs if code == 5], [])

    def test_the_library_really_does_synthesise_handles_for_that_fixture(self):
        # This is the trap the positional mode exists for: the raw tags carry no handle,
        # yet `ezdxf` answers with one. The fixture would be worthless otherwise.
        doc = ezdxf.read(io.StringIO(handleless_dxf()))
        fabricated = set()
        for entity in doc.modelspace():
            fabricated.add(entity.dxf.get('handle'))
            for vertex in getattr(entity, 'vertices', ()):  # POLYLINE owns its VERTEX + SEQEND
                fabricated.add(vertex.dxf.get('handle'))
            seqend = getattr(entity, 'seqend', None)
            if seqend is not None:
                fabricated.add(seqend.dxf.get('handle'))
        fabricated.discard(None)
        self.assertTrue(fabricated, 'ezdxf stopped synthesising handles; revisit this test')
        self.bundle = parsed(handleless_bytes())
        surviving = {value for value in handle_values(self.bundle['snapshot']) if value is not None}
        self.assertEqual(surviving & fabricated, set(),
                         'a synthesised handle leaked into the snapshot')

    def test_positional_mode_parses_and_declares_the_missing_handle_evidence(self):
        bundle = parsed(handleless_bytes())
        self.assertEqual(bundle['metadata']['identity']['mode'], 'positional')
        self.assertEqual(bundle['metadata']['identity']['handling_declared'], None)
        self.assertEqual(bundle['metadata']['identity']['source_handle_records'], 0)
        self.assertEqual(bundle['snapshot']['source']['source_sha256'],
                         hashlib.sha256(handleless_bytes()).hexdigest())
        feature = SOURCE_HANDLE_UNAVAILABLE
        self.assertIn(feature, [item.split(':')[0] for item in
                                bundle['metadata']['unsupported_features']])
        self.assertIn(f'{feature}:2', bundle['metadata']['unsupported_features'])
        self.assertIn(feature, codes(bundle))
        self.assertIn('CAD_IDENTITY_MODE_POSITIONAL', bundle['metadata']['warnings'])

    def test_every_entity_has_unique_contiguous_1_based_indices_and_no_handle(self):
        snapshot = parsed(handleless_bytes())['snapshot']
        self.assertEqual(len(snapshot['entities']), 2)
        indices = [entity['entity_index'] for entity in snapshot['entities']]
        self.assertEqual(indices, [1, 2])
        self.assertEqual(indices, sorted(set(indices)))
        for entity in snapshot['entities']:
            self.assertNotIn('handle', entity)
            self.assertEqual(entity['handle_source'], 'positional')
            self.assertEqual(entity['entity_type'], 'POLYLINE')
            self.assertEqual(entity['layer'], '0')
        self.assertIsInstance(snapshot['entities'][0]['entity_index'], int)

    def test_a_vertex_record_never_takes_a_primary_index(self):
        # 5 VERTEX + 2 SEQEND sub-records exist in the fixture; a POLYLINE still owns
        # exactly one number, so two polylines produce exactly indices 1 and 2.
        bundle = parsed(handleless_bytes())
        self.assertEqual(bundle['metadata']['counts']['source_entities'], 9)
        self.assertEqual([e['entity_index'] for e in bundle['snapshot']['entities']], [1, 2])

    def test_the_index_is_stable_across_parses_and_the_digest_changes_with_the_bytes(self):
        first = parsed(handleless_bytes())
        second = parsed(handleless_bytes())
        self.assertEqual(json.dumps(first['snapshot'], sort_keys=True),
                         json.dumps(second['snapshot'], sort_keys=True))
        other = parsed(handleless_bytes(with_block=True))
        self.assertNotEqual(first['snapshot']['source']['source_sha256'],
                            other['snapshot']['source']['source_sha256'])

    def test_the_positional_snapshot_is_contract_valid(self):
        snapshot = parsed(handleless_bytes())['snapshot']
        drawing = DrawingSnapshot.model_validate(snapshot)
        self.assertEqual(len(drawing.entities), 2)
        for entity in drawing.entities:
            self.assertIsNone(entity.handle)
            self.assertEqual(entity.handle_source, 'positional')
            self.assertGreaterEqual(entity.entity_index, 1)

    def test_the_unit_stays_unknown_and_is_never_guessed_into_mm(self):
        source = parsed(handleless_bytes())['snapshot']['source']
        self.assertEqual(source['unit'], 'unknown')
        self.assertEqual(source['insunits'], 0)
        self.assertIn('CAD_UNIT_UNKNOWN', parsed(handleless_bytes())['metadata']['warnings'])

    def test_a_block_reference_is_declared_instead_of_guessed(self):
        bundle = parsed(handleless_bytes(with_block=True))
        self.assertEqual(bundle['metadata']['identity']['mode'], 'positional')
        self.assertIn(POSITIONAL_BLOCK_UNSUPPORTED, codes(bundle))
        inserts = [e for e in bundle['snapshot']['entities'] if e['entity_type'] == 'INSERT']
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]['expansion_status'], 'skipped')
        self.assertEqual(inserts[0]['expansion_code'], POSITIONAL_BLOCK_UNSUPPORTED)
        self.assertEqual(inserts[0]['expanded_entity_count'], 0)
        # The INSERT itself is a primary ENTITIES record, so it owns index 3, and the
        # block-internal LINE (a BLOCKS record) is not expanded into a positional entity.
        self.assertEqual(inserts[0]['entity_index'], 3)
        self.assertEqual([e['entity_type'] for e in bundle['snapshot']['entities']],
                         ['POLYLINE', 'POLYLINE', 'INSERT'])


class CrlfSourceTests(unittest.TestCase):
    """Windows CAD exports are CRLF; the reader must not lose the ENTITIES section."""

    def test_the_crlf_fixture_is_really_crlf(self):
        self.assertNotIn(b'\r\n', handleless_bytes())
        self.assertIn(b'\r\n', handleless_bytes(crlf=True))

    def test_crlf_and_lf_sources_parse_to_the_same_snapshot(self):
        lf = parsed(handleless_bytes())
        crlf = parsed(handleless_bytes(crlf=True))
        self.assertEqual(crlf['metadata']['identity']['mode'], 'positional')
        self.assertEqual(len(crlf['snapshot']['entities']), len(lf['snapshot']['entities']))
        self.assertEqual([e['entity_index'] for e in crlf['snapshot']['entities']],
                         [e['entity_index'] for e in lf['snapshot']['entities']])
        # The digest is of the source bytes, and CRLF is a different byte sequence, so it
        # is normalised out; everything else must be identical.
        def without_digest(bundle):
            snapshot = dict(bundle['snapshot'])
            snapshot['source'] = dict(snapshot['source'], source_sha256='<digest>')
            return json.dumps(snapshot, sort_keys=True)

        self.assertEqual(without_digest(crlf), without_digest(lf))
        self.assertNotEqual(crlf['snapshot']['source']['source_sha256'],
                            lf['snapshot']['source']['source_sha256'])

    def test_the_entire_entities_section_was_read(self):
        # Before the newline fix `ezdxf.read(io.StringIO(...))` saw the section name as
        # 'ENTITIES\r' and read no entity at all, which the integrity check then refused.
        bundle = parsed(handleless_bytes(crlf=True))
        self.assertEqual(bundle['metadata']['counts']['entities'], 2)


class SourceHandleModeTests(unittest.TestCase):
    """A handle-bearing source is untouched by the positional feature."""

    def test_a_handle_bearing_source_stays_in_source_mode(self):
        doc, _line, _circle = titled_doc()
        # Every ENTITIES record plus every block-definition record carries a real handle.
        expected = len(doc.modelspace()) + sum(
            len(block) for block in doc.blocks
            if not block.name.startswith(('*Model_Space', '*Paper_Space')))
        bundle = parsed(source_bytes(doc))
        self.assertEqual(bundle['metadata']['identity']['mode'], 'source')
        self.assertEqual(bundle['metadata']['identity']['source_handle_records'], expected)
        self.assertEqual(bundle['metadata']['identity']['positional_records'], 0)
        self.assertNotIn(SOURCE_HANDLE_UNAVAILABLE, codes(bundle))
        for entity in bundle['snapshot']['entities']:
            self.assertTrue(entity['handle'])
            self.assertNotIn('entity_index', entity)
            self.assertNotIn('handle_source', entity)

    def test_the_source_mode_snapshot_keeps_its_previous_entity_shape(self):
        doc, line, circle = titled_doc()
        bundle = parsed(source_bytes(doc))
        line_record = next(e for e in bundle['snapshot']['entities']
                           if e['entity_type'] == 'LINE')
        self.assertEqual(set(line_record),
                         {'handle', 'layer', 'view_id', 'block_path', 'anchor',
                          'entity_type', 'start', 'end'})
        self.assertEqual(line_record['handle'], line.dxf.handle)
        # The handle-only projection still validates in the unified contract.
        projected = contract.validate_locator({
            'locator_kind': 'cad_entity', 'document_id': 'doc-x', 'source_version': 'v1',
            'layout': 'Model', 'handle': line.dxf.handle, 'layer': 'GEOMETRY',
            'model_xy': [0.0, 0.0], 'unit': 'mm'})
        self.assertEqual(projected.payload['handle'], line.dxf.handle)

    def test_a_declared_handling_flag_matching_the_records_is_not_a_warning(self):
        doc, _line, _circle = titled_doc()
        data = source_bytes(doc)
        declared = data.replace(b'$INSUNITS', b'$HANDLING\n 70\n1\n9\n$INSUNITS', 1)
        bundle = parsed(declared)
        self.assertEqual(bundle['metadata']['identity']['mode'], 'source')
        self.assertTrue(bundle['metadata']['identity']['handling_declared'])
        self.assertNotIn('CAD_HANDLING_FLAG_INCONSISTENT', bundle['metadata']['warnings'])


class UntrustworthyHandleSetTests(unittest.TestCase):
    """A positional fallback must never paper over a bad handle set."""

    def error(self, code, data):
        with self.assertRaises(CadReadError) as caught:
            parsed(data)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_a_non_hexadecimal_handle_is_refused(self):
        _doc, line, _circle = titled_doc()
        data = source_bytes(_doc)
        broken = replace_handle_tag(data, line.dxf.handle, ' 5\nZZ\n')
        self.assertNotEqual(broken, data)
        error = self.error('CAD_ENTITY_HANDLE_INVALID', broken)
        self.assertEqual(error.details['handle'], 'ZZ')

    def test_a_duplicate_handle_is_refused(self):
        _doc, line, circle = titled_doc()
        data = source_bytes(_doc)
        duplicate = replace_handle_tag(data, line.dxf.handle, f' 5\n{circle.dxf.handle}\n')
        self.assertNotEqual(duplicate, data)
        error = self.error('CAD_DUPLICATE_SOURCE_HANDLE', duplicate)
        self.assertEqual(error.details['handles'], [circle.dxf.handle])

    def test_a_half_present_handle_set_is_refused_not_made_positional(self):
        # One record with a handle and one without: the source is internally inconsistent,
        # so pretending it is handle-less would reinterpret the handle-bearing part.
        _doc, line, _circle = titled_doc()
        data = source_bytes(_doc)
        mixed = drop_handle_tag(data, line.dxf.handle)
        self.assertNotEqual(mixed, data)
        error = self.error('CAD_ENTITY_HANDLE_INVALID', mixed)
        self.assertIsNone(error.details['handle'])


class PositionalLocatorContractTests(unittest.TestCase):
    """The unified `cad_entity` locator under both identity modes."""

    def locator(self, **overrides):
        payload = {'locator_kind': 'cad_entity', 'document_id': 'doc-1', 'source_version': 'v1',
                   'layout': 'Model', 'layer': '0', 'model_xy': [0.0, 0.0], 'unit': 'unknown'}
        payload.update(overrides)
        return payload

    def error(self, reason, payload):
        with self.assertRaises(HTTPException) as caught:
            contract.validate_locator(payload)
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_LOCATOR_INVALID')
        self.assertEqual(caught.exception.detail['details']['subject'], 'locator.cad_entity')
        self.assertEqual(caught.exception.detail['details']['reason'], reason)

    def test_a_positional_locator_validates_and_keeps_its_declared_identity(self):
        carried = contract.validate_locator(
            self.locator(handle_source='positional', entity_index=7))
        self.assertEqual(carried.payload['entity_index'], 7)
        self.assertEqual(carried.payload['handle_source'], 'positional')
        self.assertNotIn('handle', carried.payload)

    def test_a_positional_locator_that_smuggles_a_handle_is_refused(self):
        self.error('positional_handle_forbidden',
                   self.locator(handle_source='positional', entity_index=7, handle='2AF'))

    def test_a_positional_locator_needs_an_entity_index(self):
        self.error('entity_index', self.locator(handle_source='positional'))

    def test_an_entity_index_must_be_a_positive_integer(self):
        for value in (0, -1, '7', 1.0, True):
            with self.subTest(value=value):
                self.error('entity_index', self.locator(handle_source='positional',
                                                        entity_index=value))

    def test_an_unknown_handle_source_is_refused(self):
        for value in ('library', '', 1):
            with self.subTest(value=value):
                self.error('handle_source',
                           self.locator(handle_source=value, entity_index=1))

    def test_a_locator_with_neither_identity_is_refused(self):
        self.error('handle_or_entity_index', self.locator())

    def test_a_positional_locator_may_not_carry_a_block_path(self):
        self.error('positional_block_path_forbidden',
                   self.locator(handle_source='positional', entity_index=1,
                                block_path=['2AF']))
        carried = contract.validate_locator(
            self.locator(handle_source='positional', entity_index=1, block_path=[]))
        self.assertEqual(carried.payload['block_path'], [])

    def test_the_old_handle_only_locator_still_validates_unchanged(self):
        for overrides in ({'handle': '2AF'}, {'handle': '2AF', 'handle_source': 'source'}):
            with self.subTest(overrides=overrides):
                carried = contract.validate_locator(self.locator(**overrides))
                self.assertEqual(carried.payload['handle'], '2AF')
                self.assertNotIn('entity_index', carried.payload)
        self.error('handle', self.locator(handle='not-a-handle!'))
        self.error('handle', self.locator(handle_source='source', entity_index=3))

    def test_identity_modes_are_pinned(self):
        self.assertEqual(IDENTITY_MODES, ('source', 'positional'))
        self.assertEqual(set(contract.CAD_HANDLE_SOURCES), set(IDENTITY_MODES))
        self.assertEqual(set(contract.CAD_POSITIONAL_LOCATOR_FIELDS),
                         {'entity_index', 'handle_source'})


class PositionalAdapterTests(unittest.TestCase):
    """The projection into the unified parse contract keeps the mode honest."""

    def write(self, data, suffix='.dxf'):
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.write(data)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_a_positional_drawing_reports_partial_and_names_every_entity_by_index(self):
        path = self.write(handleless_bytes())
        result = cad_adapter.parse_dxf(path, document_id='doc-adapter')
        self.assertEqual(result['parse_status'], 'partial')
        self.assertEqual(result['manifest']['identity_mode'], 'positional')
        self.assertEqual(result['manifest']['source_identity']['positional_records'], 2)
        self.assertTrue(any(item.startswith(SOURCE_HANDLE_UNAVAILABLE)
                            for item in result['unsupported_features']))
        self.assertIn('CAD_IDENTITY_MODE_POSITIONAL', result['quality_findings'])
        self.assertIn(f'{SOURCE_HANDLE_UNAVAILABLE}:2', result['quality_findings'])
        self.assertEqual(len(result['blocks']), 2)
        for block in result['blocks']:
            locator = block['locator']
            self.assertNotIn('handle', locator)
            self.assertEqual(locator['handle_source'], 'positional')
            self.assertGreaterEqual(locator['entity_index'], 1)
        self.assertEqual([block['locator']['entity_index'] for block in result['blocks']], [1, 2])

    def test_a_source_handle_drawing_keeps_its_locator_handle(self):
        doc, line, _circle = titled_doc()
        path = self.write(source_bytes(doc))
        result = cad_adapter.parse_dxf(path, document_id='doc-adapter')
        self.assertEqual(result['parse_status'], 'parsed')
        self.assertEqual(result['manifest']['identity_mode'], 'source')
        locators = [block['locator'] for block in result['blocks']]
        self.assertTrue(any(locator.get('handle') == line.dxf.handle for locator in locators))
        for locator in locators:
            self.assertNotIn('entity_index', locator)
            self.assertNotIn('handle_source', locator)

    def test_a_handleless_drawing_with_no_supported_entity_still_declares_its_mode(self):
        # The only entity is a SOLID, which this profile does not model. The reader
        # declares it and refuses the whole drawing, and the result must still say the
        # source had no handle at all rather than leaving the identity implicit.
        solid = _pairs([('0', 'SECTION'), ('2', 'HEADER'), ('9', '$ACADVER'), ('1', 'AC1009'),
                        ('0', 'ENDSEC'),
                        ('0', 'SECTION'), ('2', 'TABLES'),
                        ('0', 'TABLE'), ('2', 'LAYER'), ('70', '1'),
                        ('0', 'LAYER'), ('2', '0'), ('70', '0'), ('62', '7'),
                        ('6', 'CONTINUOUS'), ('0', 'ENDTAB'), ('0', 'ENDSEC'),
                        ('0', 'SECTION'), ('2', 'BLOCKS'), ('0', 'ENDSEC'),
                        ('0', 'SECTION'), ('2', 'ENTITIES'),
                        ('0', 'SOLID'), ('8', '0'),
                        ('10', '0.0'), ('20', '0.0'), ('30', '0.0'),
                        ('11', '1.0'), ('21', '0.0'), ('31', '0.0'),
                        ('12', '0.0'), ('22', '1.0'), ('32', '0.0'),
                        ('13', '1.0'), ('23', '1.0'), ('33', '0.0'),
                        ('0', 'ENDSEC'), ('0', 'EOF')]).encode()
        result = cad_adapter.parse_dxf(self.write(solid), document_id='doc-adapter')
        self.assertEqual(result['parse_status'], 'unsupported')
        self.assertEqual(result['manifest']['identity_mode'], 'positional')
        self.assertEqual(result['blocks'], [])
        self.assertIn(SOURCE_HANDLE_UNAVAILABLE,
                      [item.split(':')[0] for item in result['unsupported_features']])


if __name__ == '__main__':
    unittest.main()
