"""Native DXF into the unified contract, plus the explicit DWG refusal."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from datamind import parse_contract as contract
from ingestion import cad_adapter

ROOT = Path(__file__).resolve().parents[1]
DXF = ROOT / 'data/cad_demo/development/dev-mm.dxf'


def fake_bundle(*, target_handle='38', entities=3):
    """A CAD bundle whose dimension points at a handle that may not be included."""
    source = {'document_id': 'doc-x', 'document_version': 1, 'source_sha256': 'a' * 64,
              'format': 'dxf', 'parser_version': 'dxf-native-v1', 'provenance': 'synthetic_demo',
              'insunits': 4, 'unit': 'mm', 'coordinate_system': 'WCS_XY', 'layout': 'Model'}
    built = [{'handle': f'3{n}', 'layer': 'GEOMETRY', 'entity_type': 'CIRCLE',
              'text': None, 'insertion': None, 'bbox': {'xmin': 0, 'ymin': 0, 'xmax': 1, 'ymax': 1}}
             for n in range(entities)]
    feature = {'feature_id': 'diameter-3B', 'feature_type': 'diameter',
               'annotation_raw': '%%c20 +/-0.1',
               'locator': {'locator_kind': 'cad_entity', 'handle': '3B', 'layer': 'DM_DIAMETER',
                           'view_id': 'model'},
               'nominal_value': '20', 'tolerance_lower': '-0.1', 'tolerance_upper': '0.1',
               'unit': 'mm', 'association_status': 'confirmed', 'selected_target': target_handle,
               'association_evidence': {'method': 'native_reference', 'rationale': 'XDATA'}}
    return {'metadata': {'profile': 'datamind-2d-labelled-dxf-v1', 'parser_version': 'dxf-native-v1',
                         'ezdxf_version': '1.4.4', 'dxf_version': 'AC1024', 'warnings': []},
            'snapshot': {'contract_version': 'cad-contract-v1', 'source': source,
                         'entities': built, 'features': [feature], 'identity': {}, 'views': []}}


class CadDetectionTests(unittest.TestCase):
    def test_a_real_dxf_is_detected(self):
        self.assertEqual(contract.detect_media_type(DXF), contract.DXF_MEDIA_TYPE)

    def test_a_dwg_version_tag_is_not_mistaken_for_a_readable_dxf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drawing.dwg'
            path.write_bytes(b'AC1027' + bytes(200))
            self.assertEqual(contract.detect_media_type(path), contract.DWG_MEDIA_TYPE)

    def test_other_media_types_are_unaffected(self):
        self.assertEqual(contract.detect_media_type(ROOT / 'requirements.txt'),
                         contract.OCTET_STREAM)
        self.assertEqual(contract.detect_media_type(ROOT / 'data/knowledge_base/f_yoy.png'),
                         'image/png')


class DxfAdapterTests(unittest.TestCase):
    def setUp(self):
        contract._ADAPTERS.clear()
        cad_adapter.register_cad_adapters()

    def tearDown(self):
        contract._ADAPTERS.clear()

    def parse(self, **options):
        return cad_adapter.parse(DXF, document_id='doc-dev-mm', **options)

    def test_entities_and_dimensions_become_located_contract_blocks(self):
        result = self.parse()
        blocks = result.payload['blocks']
        self.assertEqual(result.payload['parse_status'], 'parsed')
        self.assertEqual(len(blocks), 8, [b['block_type'] for b in blocks])
        kinds = {b['block_type'] for b in blocks}
        self.assertEqual(kinds, {'title', 'paragraph', 'figure', 'dimension'})
        for block in blocks:
            locator = block['locator']
            self.assertEqual(locator['locator_kind'], 'cad_entity')
            self.assertEqual(locator['document_id'], 'doc-dev-mm')
            self.assertEqual(locator['layout'], 'Model')
            self.assertEqual(locator['unit'], 'mm')
            self.assertIsInstance(locator['model_xy'], list)
            self.assertTrue(locator['handle'])
            self.assertTrue(locator['layer'])

    def test_identity_layers_are_titles_and_geometry_has_no_text(self):
        blocks = self.parse().payload['blocks']
        titles = [b for b in blocks if b['block_type'] == 'title']
        self.assertEqual({b['locator']['layer'] for b in titles},
                         {'DM_DRAWING_ID', 'DM_REVISION', 'DM_PART_ID'})
        self.assertTrue(all(b['heading_level'] == 1 for b in titles))
        figures = [b for b in blocks if b['block_type'] == 'figure']
        self.assertTrue(figures)
        self.assertTrue(all(b['text'] == '' for b in figures))

    def test_dimension_carries_the_frozen_measurement_attributes(self):
        dimension = next(b for b in self.parse().payload['blocks'] if b['block_type'] == 'dimension')
        self.assertEqual(dimension['text'], '%%c20 +/-0.1')
        native = dimension['native_attributes']
        self.assertEqual(native['nominal_value'], '20')
        self.assertEqual(native['tolerance_lower'], '-0.1')
        self.assertEqual(native['tolerance_upper'], '0.1')
        self.assertEqual(native['unit'], 'mm')
        self.assertEqual(native['association_status'], 'confirmed')

    def test_relation_uses_the_native_reference_as_evidence(self):
        result = self.parse()
        relations = result.payload['relations']
        self.assertEqual(len(relations), 1)
        relation = relations[0]
        self.assertEqual(relation['relation_type'], 'references')
        self.assertEqual(relation['recovery_method'], 'native_reference')
        self.assertEqual(relation['state'], 'verified')
        self.assertTrue(relation['evidence'])
        ids = {b['block_id'] for b in result.payload['blocks']}
        self.assertIn(relation['source_block_id'], ids)
        self.assertIn(relation['target_block_id'], ids)
        # The registered result is contract-valid by construction.
        self.assertEqual(len(contract.validate_parse_result(result.payload).payload['blocks']), 8)

    def test_a_missing_reference_target_is_declared_not_invented(self):
        with patch.object(cad_adapter.cad_dxf, 'parse_file', return_value=fake_bundle(target_handle='FF')):
            result = cad_adapter.parse(DXF, document_id='doc-x')
        self.assertIn('dimension_target_not_included', result.payload['unsupported_features'])
        self.assertEqual(result.payload['relations'], [])
        self.assertEqual(result.payload['parse_status'], 'partial')

    def test_block_limit_is_declared(self):
        with patch.object(cad_adapter, 'MAX_BLOCKS', 3):
            result = self.parse()
        self.assertEqual(result.payload['parse_status'], 'partial')
        self.assertIn('block_limit_reached', result.payload['unsupported_features'])
        self.assertEqual(len(result.payload['blocks']), 3)

    def test_dwg_is_refused_with_the_converter_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drawing.dwg'
            path.write_bytes(b'AC1027' + bytes(200))
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(path, document_id='doc-dwg')
            self.assertEqual(caught.exception.detail['error_code'], 'CAD_DWG_CONVERTER_UNAVAILABLE')
            # It is not silently registered as a usable adapter either.
            with self.assertRaises(HTTPException) as caught:
                contract.dispatch(contract.DWG_MEDIA_TYPE, path=str(path))
            self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNKNOWN')

    def test_quality_warnings_are_carried_into_the_result(self):
        result = self.parse()
        self.assertIn('CAD_TEXT_BOUNDS_APPROXIMATE', result.payload['quality_findings'])


class BlockIdentityTests(unittest.TestCase):
    """F03: a block id is a fixed-length digest, never a truncated long string.

    The defect: `f'dxf-{document_id}-{index}'[:64]` collapsed a drawing's blocks onto one
    id once the document_id reached 59 characters, and the whole drawing then failed as
    `duplicate_block_id`.
    """

    def test_the_identifier_is_unique_and_bounded_for_any_document_id(self):
        for document_id in ('d', 'd' * 58, 'd' * 59, 'd' * 64, 'd' * 128, 'd' * 200):
            identifiers = {cad_adapter._block_identifier(document_id, index)
                           for index in range(500)}
            self.assertEqual(len(identifiers), 500, document_id[:16])
            for identifier in identifiers:
                self.assertLessEqual(len(identifier), 64, identifier)
        # The same index under a different document must not collide either.
        self.assertNotEqual(cad_adapter._block_identifier('d' * 59, 0),
                            cad_adapter._block_identifier('d' * 59 + 'x', 0))

    def test_a_drawing_parses_for_every_document_id_the_store_accepts(self):
        for length in (1, 59, 64):
            result = cad_adapter.parse(DXF, document_id='d' * length)
            identifiers = [block['block_id'] for block in result.payload['blocks']]
            self.assertEqual(len(set(identifiers)), len(identifiers))
            for identifier in identifiers:
                self.assertLessEqual(len(identifier), 64, identifier)
            self.assertEqual(result.payload['parse_status'], 'parsed')

    def test_an_id_the_contract_stores_cannot_hold_is_refused_up_front(self):
        # Every contract store declares document_id VARCHAR(64); accepting more here would
        # hand the caller a "parsed" result that only fails at INSERT time.
        for length in (65, 132, 200):
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(DXF, document_id='d' * length)
            self.assertEqual(caught.exception.detail['error_code'], 'PARSE_RESULT_INVALID')
            self.assertEqual(caught.exception.detail['details']['reason'], 'document_id_too_long')

    def test_the_frozen_source_profile_bound_admits_every_contract_legal_id(self):
        # The unified contract is the stricter of the two (64 vs the frozen profile's 128),
        # so the adapter never has to rewrite the caller's identity to satisfy the profile.
        self.assertLessEqual(contract.DOCUMENT_ID_MAX_LENGTH, cad_adapter.PROFILE_DOCUMENT_ID_MAX)


class SourceLocatorProjectionTests(unittest.TestCase):
    """F10: the `cad_entity` locator carries every field of the frozen source locator."""

    def setUp(self):
        contract._ADAPTERS.clear()
        cad_adapter.register_cad_adapters()

    def tearDown(self):
        contract._ADAPTERS.clear()

    def test_the_frozen_source_locator_projects_without_loss(self):
        from ingestion import cad_contract
        source_locator = {'locator_kind': 'cad_entity', 'source_sha256': 'a' * 64,
                          'handle': '2AF', 'layer': 'GEOMETRY', 'view_id': 'model',
                          'coordinate_system': 'WCS_XY', 'layout': 'Model'}
        # The frozen contract is the source of truth for what a source locator carries.
        cad_contract.Locator.model_validate(source_locator)
        projected = cad_adapter._locator('doc-1', 'v1',
                                         {'handle': '2AF', 'layer': 'GEOMETRY',
                                          'insertion': {'x': 1.0, 'y': 2.0}},
                                         unit='mm', layout='Model', coordinate_system='WCS_XY',
                                         view_id='model', source_sha256='a' * 64)
        for field in cad_contract.Locator.model_fields:
            self.assertIn(field, projected, field)
            self.assertEqual(projected[field], source_locator[field], field)
        # The two fields the source locator has no equivalent for are added, not invented:
        # the drawing supplies them (insertion point / INSUNITS).
        self.assertEqual(projected['model_xy'], [1.0, 2.0])
        self.assertEqual(projected['unit'], 'mm')
        self.assertEqual(contract.CAD_SOURCE_LOCATOR_DROPPED_FIELDS, {})

    def test_a_real_drawing_keeps_view_and_source_digest_on_every_block(self):
        result = cad_adapter.parse(DXF, document_id='doc-dev-mm')
        source_sha256 = result.payload['manifest']['source_sha256']
        self.assertEqual(len(source_sha256), 64)
        for block in result.payload['blocks']:
            locator = block['locator']
            self.assertEqual(locator['locator_kind'], 'cad_entity')
            self.assertEqual(locator['view_id'], 'model')
            self.assertEqual(locator['coordinate_system'], 'WCS_XY')
            self.assertEqual(locator['source_sha256'], source_sha256)
            self.assertEqual(locator['layout'], 'Model')
            for field in contract.CAD_SOURCE_LOCATOR_FIELDS:
                self.assertIn(field, locator, field)



class DrawingCliTests(unittest.TestCase):
    def test_cli_prints_the_summary_and_refuses_a_dwg(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/parse_drawing.py'), '--source', str(DXF),
             '--document-id', 'doc-cli-dxf'], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(summary['detected_media_type'], contract.DXF_MEDIA_TYPE)
        self.assertEqual(summary['blocks'], 8)
        self.assertEqual(summary['relations'], 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drawing.dwg'
            path.write_bytes(b'AC1027' + bytes(200))
            refused = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/parse_drawing.py'), '--source', str(path),
                 '--document-id', 'doc-cli-dwg'], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
            self.assertEqual(refused.returncode, 1)
            self.assertIn('CAD_DWG_CONVERTER_UNAVAILABLE', refused.stderr)


if __name__ == '__main__':
    unittest.main()
