"""Unified parse/locator/relation contract: validation, adapters and PDF block mapping.

These tests are the review of the contract: they pin the documented constants, the
per-kind locator requirements, relation integrity, the ingest state machine, the adapter
registry (including explicit refusal) and the mapping of real ingest block shapes.
"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from ingestion import ingest
from datamind import parse_contract as contract

ROOT = Path(__file__).resolve().parents[1]


def locator(kind='pdf_region', **overrides):
    base = {'locator_kind': kind, 'document_id': 'doc-1', 'source_version': 'v1'}
    if kind == 'pdf_region':
        base.update({'page_no': 1, 'page_width': 595.0, 'page_height': 842.0,
                     'bbox': [10.0, 20.0, 100.0, 120.0], 'unit': 'point',
                     'coordinate_system': 'top-left', 'rotation_degrees': 0})
    elif kind == 'image_region':
        base.update({'image_id': 'img-1', 'image_width': 800, 'image_height': 600,
                     'bbox': [0.0, 0.0, 100.0, 50.0], 'unit': 'px', 'coordinate_system': 'top-left'})
    elif kind == 'office_object':
        base.update({'part': 'word/document.xml', 'paragraph': 3})
    elif kind == 'sheet_range':
        base.update({'sheet': 'Sheet1', 'cell': 'B2'})
    elif kind == 'cad_entity':
        base.update({'layout': 'Model', 'handle': '2AF', 'layer': '0', 'model_xy': [0.0, 0.0], 'unit': 'mm'})
    elif kind == 'temporal_segment':
        base.update({'start': 0.0, 'end': 1.5, 'time_unit': 's', 'time_base': 'media'})
    elif kind == 'model_spatial_object':
        base.update({'object_id': 'obj-1', 'coordinate_reference': 'site', 'unit': 'm'})
    base.update(overrides)
    return base


def block(block_id='b1', locator_payload=None, **overrides):
    payload = {'block_id': block_id, 'document_id': 'doc-1', 'source_version': 'v1',
               'parser_version': 'pdf-ocr-0.4', 'block_type': 'paragraph', 'text': '原文',
               'native_attributes': {}, 'reading_order': 0,
               'locator': locator_payload or locator(), 'quality': {},
               'content_hash': 'a' * 64}
    payload.update(overrides)
    return payload


def relation(source='b1', target='b2', **overrides):
    payload = {'source_block_id': source, 'target_block_id': target,
               'relation_type': 'contains', 'evidence': '原文依据',
               'recovery_method': 'explicit_marker', 'state': 'candidate'}
    payload.update(overrides)
    return payload


def parse_result(**overrides):
    payload = {'classification': {'detected_media_type': 'application/pdf'},
               'adapter_capabilities': ['native_text'],
               'blocks': [block('b1'), block('b2')],
               'relations': [relation()],
               'parsed_scope': [{'kind': 'page', 'ref': 1, 'status': 'parsed'}],
               'failed_scope': [],
               'quality_findings': [],
               'unsupported_features': [],
               'artifact_refs': [],
               'manifest': {'parser_version': 'pdf-ocr-0.4'},
               'parse_status': 'parsed'}
    payload.update(overrides)
    return payload


class ContractConstantTests(unittest.TestCase):
    def test_documented_sets_are_pinned(self):
        self.assertEqual(contract.PARSE_STATUSES,
                         ('parsed', 'partial', 'needs_review', 'failed', 'unsupported'))
        self.assertEqual(contract.INGEST_STATES,
                         ('queued', 'classifying', 'parsing', 'validating', 'indexing', 'ready', 'failed'))
        self.assertEqual(contract.RELATION_TYPES,
                         ('precedes', 'contains', 'caption_of', 'header_of', 'continuation_of',
                          'annotates', 'connected_to', 'flows_to', 'references'))
        self.assertEqual(contract.RELATION_STATES, ('candidate', 'verified', 'needs_review'))
        self.assertEqual(contract.LOCATOR_KINDS,
                         ('pdf_region', 'image_region', 'office_object', 'sheet_range',
                          'cad_entity', 'temporal_segment', 'model_spatial_object'))

    def test_design_doc_path_is_relative_and_exists(self):
        path = contract.design_doc_path()
        self.assertTrue(path.is_file(), path)
        self.assertTrue(str(path).startswith(str(ROOT)))
        self.assertFalse(str(path).startswith('/Users/caicai/.codex'))


class IngestStateTests(unittest.TestCase):
    def test_every_documented_state_is_accepted(self):
        for state in contract.INGEST_STATES:
            self.assertEqual(contract.validate_ingest_state(state), state)

    def test_unknown_state_is_refused(self):
        for state in ('', 'READY', 'done', 3, None):
            with self.assertRaises(HTTPException) as caught:
                contract.validate_ingest_state(state)
            self.assertEqual(caught.exception.detail['error_code'], 'PARSE_STATUS_INVALID')

    def test_only_documented_transitions_are_allowed(self):
        self.assertEqual(contract.ingest_step('queued', 'classifying'), 'classifying')
        self.assertEqual(contract.ingest_step('indexing', 'ready'), 'ready')
        self.assertEqual(contract.ingest_step('parsing', 'failed'), 'failed')
        for state, target in (('queued', 'ready'), ('ready', 'parsing'), ('failed', 'parsing')):
            with self.assertRaises(HTTPException) as caught:
                contract.ingest_step(state, target)
            self.assertEqual(caught.exception.detail['error_code'], 'PARSE_STATUS_INVALID')


class LocatorTests(unittest.TestCase):
    def error(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def test_every_locator_kind_validates(self):
        for kind in contract.LOCATOR_KINDS:
            result = contract.validate_locator(locator(kind))
            self.assertEqual(result.locator_kind, kind)

    def test_pdf_region_bounds_and_page_number_are_enforced(self):
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator(page_no=0)))
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator(page_no=-1)))
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator(bbox=[100.0, 20.0, 10.0, 120.0])))
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator(bbox=[-5.0, 20.0, 100.0, 120.0])))
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator(bbox=[10.0, 20.0, 9999.0, 120.0])))

    def test_missing_required_fields_are_refused_per_kind(self):
        for kind in contract.LOCATOR_KINDS:
            for field in contract.LOCATOR_REQUIRED_FIELDS[kind]:
                payload = locator(kind)
                del payload[field]
                self.error('PARSE_LOCATOR_INVALID',
                           lambda payload=payload: contract.validate_locator(payload))

    def test_unknown_kind_is_refused(self):
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator(office_object=True, locator_kind='magic')))

    def test_cad_entity_handle_and_geometry_are_checked(self):
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.validate_locator(locator('cad_entity', handle='not-a-handle!')))
        self.assertIsNotNone(contract.validate_locator(locator('cad_entity', handle='2AF')))

    def test_pdf_region_helper_builds_a_valid_locator(self):
        built = contract.pdf_region_locator(document_id='doc-1', source_version='v1', page_no=2,
                                            bbox=[1.0, 2.0, 30.0, 40.0], page_width=595.0,
                                            page_height=842.0, rotation_degrees=90)
        self.assertEqual(built.locator_kind, 'pdf_region')
        self.assertEqual(built.payload['page_no'], 2)
        self.error('PARSE_LOCATOR_INVALID',
                   lambda: contract.pdf_region_locator(document_id='doc-1', source_version='v1',
                                                       page_no=0, bbox=[1.0, 2.0, 30.0, 40.0],
                                                       page_width=595.0, page_height=842.0,
                                                       rotation_degrees=0))


class RelationTests(unittest.TestCase):
    def error(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def test_relation_requires_known_endpoints_type_state_and_evidence(self):
        known = ['b1', 'b2']
        self.assertEqual(contract.validate_relation(relation(), known).relation_type, 'contains')
        for payload, code in ((relation(source='missing'), 'PARSE_RELATION_INVALID'),
                              (relation(target='missing'), 'PARSE_RELATION_INVALID'),
                              (relation(relation_type='magic'), 'PARSE_RELATION_INVALID'),
                              (relation(state='maybe'), 'PARSE_RELATION_INVALID')):
            self.error(code, lambda payload=payload: contract.validate_relation(payload, known))
        for field in contract.RELATION_REQUIRED_FIELDS:
            payload = relation()
            del payload[field]
            self.error('PARSE_RELATION_INVALID',
                       lambda payload=payload: contract.validate_relation(payload, known))

    def test_merge_by_similarity_is_not_an_acceptable_recovery_method(self):
        for method in contract.INSUFFICIENT_RECOVERY_METHODS:
            payload = relation(recovery_method=method)
            self.error('PARSE_RELATION_INVALID',
                       lambda payload=payload: contract.validate_relation(payload, ['b1', 'b2']))


class ParseResultTests(unittest.TestCase):
    def error(self, code, action):
        with self.assertRaises(HTTPException) as caught:
            action()
        self.assertEqual(caught.exception.detail['error_code'], code)

    def test_valid_result_round_trips(self):
        result = contract.validate_parse_result(parse_result())
        self.assertEqual(result.parse_status, 'parsed')
        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(len(result.relations), 1)

    def test_every_required_field_is_enforced(self):
        for field in contract.RESULT_REQUIRED_FIELDS:
            payload = parse_result()
            del payload[field]
            self.error('PARSE_RESULT_INVALID', lambda payload=payload: contract.validate_parse_result(payload))

    def test_parse_status_must_be_documented(self):
        self.error('PARSE_RESULT_INVALID',
                   lambda: contract.validate_parse_result(parse_result(parse_status='done')))

    def test_relation_endpoints_must_exist_in_the_same_result(self):
        payload = parse_result(relations=[relation(source='b1', target='ghost')])
        self.error('PARSE_RELATION_INVALID', lambda: contract.validate_parse_result(payload))

    def test_unknown_extras_are_refused_rather_than_invented(self):
        payload = parse_result()
        payload['invented_field'] = 1
        self.error('PARSE_RESULT_INVALID', lambda: contract.validate_parse_result(payload))


class AdapterRegistryTests(unittest.TestCase):
    def setUp(self):
        contract._ADAPTERS.clear()

    def tearDown(self):
        contract._ADAPTERS.clear()

    def test_registration_lookup_and_capabilities(self):
        record = contract.register_adapter('pdf-native', media_types=['application/pdf'],
                                           capabilities=['native_text', 'tables'])
        self.assertEqual(record['name'], 'pdf-native')
        self.assertEqual(contract.adapter_for('application/pdf')['name'], 'pdf-native')
        self.assertIn('native_text', contract.capabilities('pdf-native'))
        self.assertIn('application/pdf', contract.supported_media_types())
        self.assertTrue(contract.supports_capability('pdf-native', 'tables'))
        self.assertEqual(contract.require_capability('pdf-native', 'tables'), 'tables')

    def test_unregistered_media_type_is_explicitly_refused(self):
        for media_type in ('image/tiff', '', 'nonsense'):
            with self.assertRaises(HTTPException) as caught:
                contract.adapter_for(media_type)
            self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNKNOWN')

    def test_unsupported_capability_and_unknown_adapter_are_distinguished(self):
        contract.register_adapter('pdf-native', media_types=['application/pdf'],
                                  capabilities=['native_text'])
        with self.assertRaises(HTTPException) as caught:
            contract.require_capability('pdf-native', 'formula')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNSUPPORTED')
        with self.assertRaises(HTTPException) as caught:
            contract.require_capability('missing-adapter', 'native_text')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNKNOWN')

    def test_registration_inputs_are_validated(self):
        contract.register_adapter('pdf-native', media_types=['application/pdf'],
                                  capabilities=['native_text'])
        # The same media type cannot be claimed twice.
        with self.assertRaises(HTTPException) as caught:
            contract.register_adapter('other', media_types=['application/pdf'], capabilities=['x'])
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_RESULT_INVALID')
        for kwargs, code in (
                ({'media_types': [], 'capabilities': ['x']}, 'PARSE_RESULT_INVALID'),
                ({'media_types': ['application/pdf'], 'capabilities': []}, 'PARSE_RESULT_INVALID'),
                ({'media_types': ['application/pdf'], 'capabilities': ['']}, 'PARSE_RESULT_INVALID'),
                ({'media_types': ['nonsense'], 'capabilities': ['x']}, 'PARSE_ADAPTER_UNKNOWN')):
            with self.assertRaises(HTTPException) as caught:
                contract.register_adapter('bad-adapter', **kwargs)
            self.assertEqual(caught.exception.detail['error_code'], code)


class PdfMappingTests(unittest.TestCase):
    """Map the block dictionaries the ingest/OCR modules actually produce."""

    def native_block(self):
        return {'page_no': 1, 'bbox': [72.0, 100.0, 300.0, 140.0], 'block_type': 'paragraph',
                'heading_level': None, 'text': '正文内容', 'table_cells': None, 'ocr_confidence': None}

    def ocr_block(self):
        return {'page_no': 2, 'bbox': [10.0, 20.0, 200.0, 60.0], 'block_type': 'title',
                'heading_level': 2, 'text': '一、标题', 'table_cells': None, 'ocr_confidence': 0.92}

    def test_real_block_shapes_are_mapped_without_inventing_fields(self):
        blocks = [self.native_block(), self.ocr_block()]
        mapped = contract.from_pdf_blocks(blocks, page_sizes={1: (595.0, 842.0), 2: (595.0, 842.0)},
                                          parser_version='pdf-ocr-0.4', document_id='doc-1',
                                          source_version='v1')
        self.assertEqual(len(mapped), 2)
        first, second = mapped
        self.assertEqual(first['text'], '正文内容')
        self.assertEqual(first['page_no'], 1)
        self.assertIsNone(first['heading_level'])
        self.assertIsNone(first['ocr_confidence'])
        self.assertEqual(second['ocr_confidence'], 0.92)
        self.assertEqual(second['heading_level'], 2)
        for item in mapped:
            self.assertEqual(item['parser_version'], 'pdf-ocr-0.4')
            self.assertEqual(item['locator']['locator_kind'], 'pdf_region')
            self.assertEqual(item['locator']['bbox'], item['bbox'])
            self.assertEqual(item['locator']['page_width'], 595.0)
            self.assertIn('block_id', item)

    def test_missing_page_size_is_refused_instead_of_guessed(self):
        with self.assertRaises(HTTPException) as caught:
            contract.from_pdf_blocks([self.native_block()], page_sizes={}, parser_version='p',
                                     document_id='doc-1', source_version='v1')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_LOCATOR_INVALID')

    def test_mapped_blocks_are_accepted_by_the_result_contract(self):
        mapped = contract.from_pdf_blocks([self.native_block()], page_sizes={1: (595.0, 842.0)},
                                          parser_version='pdf-ocr-0.4', document_id='doc-1',
                                          source_version='v1')
        payload = parse_result(blocks=mapped, relations=[])
        self.assertEqual(len(contract.validate_parse_result(payload).blocks), 1)


if __name__ == '__main__':
    unittest.main()


class IngestIntegrationTests(unittest.TestCase):
    """The production parse path must actually emit a contract-validated result."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = patch.object(ingest, 'PAGE_IMG_DIR', Path(self.directory.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def native_pdf(self):
        return ROOT / 'data/knowledge_base/04_售后服务与退款管理制度.pdf'

    def scanned_pdf(self):
        return ROOT / 'data/knowledge_base/07_门店巡检记录_2025年12月_扫描版.pdf'

    def test_native_pdf_is_validated_against_the_contract(self):
        blocks, _, detail = ingest.parse_pdf(self.native_pdf(), 'contract-native', include_quality=True)
        summary = detail['parse_contract']
        self.assertEqual(summary['validation'], 'parsed', summary)
        self.assertEqual(summary['contract_version'], contract.CONTRACT_VERSION)
        self.assertEqual(summary['blocks'], len(blocks))
        self.assertEqual(summary['locator_kinds'], ['pdf_region'])
        self.assertEqual(summary['parse_status'], detail['parse_status'])
        # The result came through the registered adapter dispatch, not a direct call.
        self.assertEqual(summary['adapter'], 'pdf-native')

    def test_scanned_pdf_ocr_blocks_are_validated_too(self):
        blocks, _, detail = ingest.parse_pdf(self.scanned_pdf(), 'contract-scan', include_quality=True)
        summary = detail['parse_contract']
        self.assertEqual(summary['validation'], 'parsed', summary)
        self.assertEqual(summary['blocks'], len(blocks))
        self.assertTrue(blocks)

    def test_a_contract_violation_is_recorded_not_raised(self):
        import pymupdf
        path = self.native_pdf()
        with pymupdf.open(path) as document:
            page = document[0]
            oversized = {'page_no': 1, 'bbox': [0.0, 0.0, page.rect.width + 500.0, page.rect.height],
                         'block_type': 'paragraph', 'text': '越界块'}
            detail = {'source_sha256': 'a' * 64, 'parse_status': 'parsed', 'pages': []}
            summary = ingest._contract_summary(document, path, 'contract-bad', [oversized], detail)
        self.assertEqual(summary['validation'], 'failed', summary)
        self.assertEqual(summary['error_code'], 'PARSE_LOCATOR_INVALID')

    def test_no_blocks_skips_validation_instead_of_inventing_one(self):
        import pymupdf
        path = self.native_pdf()
        with pymupdf.open(path) as document:
            detail = {'source_sha256': 'b' * 64, 'parse_status': 'failed', 'pages': []}
            summary = ingest._contract_summary(document, path, 'contract-empty', [], detail)
        self.assertEqual(summary['validation'], 'skipped', summary)
        self.assertEqual(summary['reason'], 'no_blocks')


class RoutingTests(unittest.TestCase):
    """Media sniffing and the adapter dispatch seam."""

    def setUp(self):
        contract._ADAPTERS.clear()

    def tearDown(self):
        contract._ADAPTERS.clear()

    def test_pdf_and_png_are_sniffed_from_signatures(self):
        base = ROOT / 'data/knowledge_base'
        self.assertEqual(contract.detect_media_type(base / '04_售后服务与退款管理制度.pdf'),
                         'application/pdf')
        self.assertEqual(contract.detect_media_type(base / 'f_yoy.png'), 'image/png')
        self.assertEqual(contract.detect_media_type(ROOT / 'requirements.txt'), contract.OCTET_STREAM)

    def test_unreadable_path_is_reported_not_guessed(self):
        with self.assertRaises(HTTPException) as caught:
            contract.detect_media_type(ROOT / 'data/does-not-exist.pdf')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_MEDIA_UNREADABLE')

    def test_dispatch_routes_to_the_registered_handler_and_validates(self):
        seen = {}

        def handler(**kwargs):
            seen.update(kwargs)
            return parse_result()

        contract.register_adapter('demo', media_types=['application/pdf'],
                                  capabilities=['native_text'], handler=handler)
        result = contract.dispatch('application/pdf', marker='x')
        self.assertEqual(seen, {'marker': 'x'})
        self.assertEqual(result.parse_status, 'parsed')
        self.assertEqual(len(result.blocks), 2)

    def test_dispatch_refuses_unknown_media_and_handlerless_adapters(self):
        with self.assertRaises(HTTPException) as caught:
            contract.dispatch('image/tiff')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNKNOWN')
        contract.register_adapter('declared-only', media_types=['application/pdf'],
                                  capabilities=['native_text'])
        with self.assertRaises(HTTPException) as caught:
            contract.dispatch('application/pdf')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNSUPPORTED')

    def test_dispatch_validates_the_handler_output(self):
        contract.register_adapter('bad', media_types=['application/pdf'], capabilities=['x'],
                                  handler=lambda **_: {'classification': {}, 'blocks': []})
        with self.assertRaises(HTTPException) as caught:
            contract.dispatch('application/pdf')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_RESULT_INVALID')

    def test_handler_errors_are_mapped_to_stable_codes(self):
        def rejected(**_):
            raise HTTPException(422, detail={'error_code': 'PARSE_LOCATOR_INVALID'})

        contract.register_adapter('rejecting', media_types=['application/pdf'], capabilities=['x'],
                                  handler=rejected)
        with self.assertRaises(HTTPException) as caught:
            contract.dispatch('application/pdf')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_LOCATOR_INVALID')

        contract._ADAPTERS.clear()
        contract.register_adapter('wrong-signature', media_types=['application/pdf'], capabilities=['x'],
                                  handler=lambda required_positional: parse_result())
        with self.assertRaises(HTTPException) as caught:
            contract.dispatch('application/pdf', unexpected=True)
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_RESULT_INVALID')

        contract._ADAPTERS.clear()
        contract.register_adapter('exploding', media_types=['application/pdf'], capabilities=['x'],
                                  handler=lambda **_: (_ for _ in ()).throw(RuntimeError('boom')))
        with self.assertRaises(HTTPException) as caught:
            contract.dispatch('application/pdf')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_ADAPTER_UNSUPPORTED')

    def test_handler_must_be_callable(self):
        with self.assertRaises(HTTPException) as caught:
            contract.register_adapter('bad-handler', media_types=['application/pdf'],
                                      capabilities=['x'], handler='not-callable')
        self.assertEqual(caught.exception.detail['error_code'], 'PARSE_RESULT_INVALID')
