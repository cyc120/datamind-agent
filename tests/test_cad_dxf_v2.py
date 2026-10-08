"""Wide engineering-DXF reader (`dxf-native-v2`): new entities, bounds, and the v1 guard.

Fixtures are generated with `ezdxf` because the real drawings live outside this
repository; the coordinator's integration step is what exercises them. Every fixture here
covers one entity family, one declared refusal or one limit.
"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import ezdxf
from fastapi import HTTPException
from pydantic import ValidationError

from datamind import parse_contract as contract
from ingestion import cad_adapter, cad_dxf, cad_dxf_v2
from ingestion.cad_contract_v2 import CONTRACT_VERSION, DrawingSnapshot, declared_sizes
from ingestion.cad_dxf import CadReadError

ROOT = Path(__file__).resolve().parents[1]
FROZEN_V1 = ROOT / 'data/evaluation/frozen/cad_dxf_v1/manifest.json'
FROZEN_V2 = ROOT / 'data/evaluation/frozen/cad_dxf_v2/manifest.json'


def new_doc(unit=4):
    doc = ezdxf.new('R2010', setup=True)
    doc.header['$INSUNITS'] = unit
    for layer in ('DM_DRAWING_ID', 'DM_REVISION', 'DM_PART_ID', 'DM_DIAMETER', 'GEOMETRY'):
        doc.layers.new(layer)
    doc.appids.new('DATAMIND')
    return doc


def add_titles(model, drawing='DRW-V2', revision='R1', part='A200'):
    for value, layer, y in ((drawing, 'DM_DRAWING_ID', 60), (revision, 'DM_REVISION', 50),
                            (part, 'DM_PART_ID', 40)):
        model.add_text(value, dxfattribs={'layer': layer, 'insert': (0, y), 'height': 2})


def engineering_doc(unit=4):
    """Every newly supported entity family plus one explicitly unsupported one."""
    doc = new_doc(unit)
    model = doc.modelspace()
    add_titles(model)
    circle = model.add_circle((10, 10), 10, dxfattribs={'layer': 'GEOMETRY'})
    model.add_lwpolyline([(0, 0), (10, 0), (10, 5)], close=True,
                         dxfattribs={'layer': 'GEOMETRY'})
    model.add_polyline2d([(20, 0), (30, 0), (30, 5)], dxfattribs={'layer': 'GEOMETRY'})
    model.add_arc((40, 0), 5, 30, 120, dxfattribs={'layer': 'GEOMETRY'})
    model.add_mtext('第一行\\P第二行 {\\fArial|b1;粗体}', dxfattribs={'insert': (0, -10),
                                                                 'char_height': 2.5})
    plain = model.add_linear_dim(base=(0, -20), p1=(0, 0), p2=(10, 0))
    plain.render()
    overridden = model.add_linear_dim(base=(0, -30), p1=(0, 0), p2=(10, 0), text='10 +/-0.1')
    overridden.render()
    model.add_ellipse((0, 0), (5, 5), ratio=0.5, dxfattribs={'layer': 'GEOMETRY'})
    annotation = model.add_text('%%c20 +/-0.1', dxfattribs={'layer': 'DM_DIAMETER',
                                                            'insert': (25, 25), 'height': 2})
    annotation.set_xdata('DATAMIND', [(1000, 'diameter_target'), (1005, circle.dxf.handle)])
    return doc


def blocks_doc(*, rotation=0.0, scale=1.0, xscale=None, depth=8):
    """A nested block reference: PART -> SUB, plus block content of each family."""
    doc = new_doc()
    model = doc.modelspace()
    add_titles(model)
    part = doc.blocks.new('PART')
    part.add_line((0, 0), (2, 0), dxfattribs={'layer': 'GEOMETRY'})
    part.add_circle((1, 0), 0.5, dxfattribs={'layer': 'GEOMETRY'})
    part.add_text('BLK', dxfattribs={'layer': 'GEOMETRY', 'insert': (0, 1), 'height': 1})
    sub = doc.blocks.new('SUB')
    sub.add_line((0, 0), (1, 0), dxfattribs={'layer': 'GEOMETRY'})
    part.add_blockref('SUB', (2, 0), dxfattribs={'rotation': 90})
    for index in range(depth):
        name = f'L{index}'
        if name not in doc.blocks:
            block = doc.blocks.new(name)
            block.add_line((0, 0), (index + 1, 0), dxfattribs={'layer': 'GEOMETRY'})
    reference = model.add_blockref('PART', (10, 20), dxfattribs={
        'rotation': rotation, 'xscale': xscale if xscale is not None else scale,
        'yscale': scale})
    return doc, reference


def cycle_doc():
    doc = new_doc()
    model = doc.modelspace()
    add_titles(model)
    first, second = doc.blocks.new('CYCLE_A'), doc.blocks.new('CYCLE_B')
    first.add_line((0, 0), (1, 0), dxfattribs={'layer': 'GEOMETRY'})
    first.add_blockref('CYCLE_B', (0, 0))
    second.add_line((0, 0), (1, 1), dxfattribs={'layer': 'GEOMETRY'})
    second.add_blockref('CYCLE_A', (0, 0))
    model.add_blockref('CYCLE_A', (0, 0))
    return doc


def source(doc, encoding='utf-8'):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode(encoding)


def parsed(doc, **options):
    return cad_dxf_v2.parse_bytes(source(doc), document_id='test-v2', **options)


def codes(bundle):
    return [item['code'] for item in bundle['metadata']['unsupported']]


class NewEntityTests(unittest.TestCase):
    def setUp(self):
        self.bundle = parsed(engineering_doc())
        self.snapshot = self.bundle['snapshot']
        self.by_type = {}
        for entity in self.snapshot['entities']:
            self.by_type.setdefault(entity['entity_type'], []).append(entity)

    def test_the_profile_contract_and_encoding_are_declared(self):
        self.assertEqual(self.bundle['metadata']['profile'], cad_dxf_v2.PROFILE)
        self.assertEqual(self.bundle['metadata']['parser_version'], 'dxf-native-v2')
        self.assertEqual(self.snapshot['contract_version'], 'cad-contract-v2')
        self.assertEqual(self.snapshot['source']['encoding'], 'utf-8')
        self.assertEqual(self.snapshot['source']['insunits'], 4)
        self.assertEqual(self.snapshot['source']['unit'], 'mm')

    def test_polylines_keep_vertices_and_the_closed_flag(self):
        for kind, closed in (('LWPOLYLINE', True), ('POLYLINE', False)):
            entity = self.by_type[kind][0]
            self.assertEqual([(v['x'], v['y']) for v in entity['vertices']],
                             [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)] if closed
                             else [(20.0, 0.0), (30.0, 0.0), (30.0, 5.0)])
            self.assertEqual(entity['closed'], closed)
            self.assertEqual(entity['anchor'], entity['vertices'][0])
            self.assertEqual(entity['block_path'], [])

    def test_an_arc_keeps_centre_radius_and_sweep(self):
        arc = self.by_type['ARC'][0]
        self.assertEqual((arc['center']['x'], arc['center']['y']), (40.0, 0.0))
        self.assertEqual(arc['radius'], 5.0)
        self.assertEqual((arc['start_angle'], arc['end_angle']), (30.0, 120.0))
        # The exact arc bounds include the 90-degree quadrant point, not the full circle.
        view = self.snapshot['views'][0]['bbox']
        self.assertLessEqual(view['ymin'], -1)
        self.assertEqual(arc['anchor'], arc['center'])

    def test_mtext_keeps_the_plain_text_and_the_raw_format_codes(self):
        mtext = self.by_type['MTEXT'][0]
        self.assertEqual(mtext['text'], '第一行\n第二行 粗体')
        self.assertEqual(mtext['raw_text'], '第一行\\P第二行 {\\fArial|b1;粗体}')
        self.assertEqual(mtext['char_height'], 2.5)
        self.assertEqual(mtext['attachment_point'], 1)

    def test_native_dimensions_separate_measurement_from_declared_text(self):
        dimensions = sorted((f for f in self.snapshot['features']
                             if f['feature_type'] == 'native_dimension'),
                            key=lambda f: f['feature_id'])
        self.assertEqual(len(dimensions), 2)
        plain, overridden = dimensions
        # `<>` means the display value is generated from the geometry: measurement only.
        self.assertIsNone(plain['declared_text'])
        self.assertEqual(plain['value_authority'], 'dimension_measurement')
        self.assertEqual(plain['geometry_measurement'], '10.0')
        # An explicit override is the drafter's declaration; the measurement stays separate.
        self.assertEqual(overridden['declared_text'], '10 +/-0.1')
        self.assertIsNone(overridden['declared_value'])
        self.assertEqual(overridden['geometry_measurement'], '10.0')
        self.assertEqual(overridden['value_authority'], 'declared_text')

    def test_a_plain_numeric_override_becomes_a_declared_value(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        dimension = doc.modelspace().add_linear_dim(base=(0, -5), p1=(0, 0), p2=(12.5, 0),
                                                    text='12.5')
        dimension.render()
        feature = parsed(doc)['snapshot']['features'][0]
        self.assertEqual(feature['declared_value'], '12.5')
        self.assertEqual(feature['value_authority'], 'declared_text')
        self.assertEqual(feature['geometry_measurement'], '12.5')

    def test_the_labelled_diameter_keeps_v1_semantics(self):
        feature = next(f for f in self.snapshot['features'] if f['feature_type'] == 'diameter')
        self.assertEqual(feature['nominal_value'], '20')
        self.assertEqual(feature['tolerance_lower'], '-0.1')
        self.assertEqual(feature['association_status'], 'confirmed')
        self.assertTrue(feature['selected_target'])

    def test_text_rotation_and_a_custom_style_are_now_supported(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.styles.new('CUSTOM', dxfattribs={'font': 'arial.ttf'})
        doc.modelspace().add_text('rotated', dxfattribs={'insert': (1, 2), 'height': 3,
                                                        'rotation': 30, 'style': 'CUSTOM'})
        entity = next(e for e in parsed(doc)['snapshot']['entities']
                      if e['entity_type'] == 'TEXT' and e['text'] == 'rotated')
        self.assertEqual(entity['rotation'], 30.0)
        self.assertEqual(entity['height'], 3.0)

    def test_identity_is_optional_and_captured_when_declared(self):
        self.assertEqual({key: field['value'] for key, field in self.snapshot['identity'].items()},
                         {'drawing_id': 'DRW-V2', 'drawing_revision': 'R1', 'part_id': 'A200'})
        doc = new_doc()
        doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={'layer': 'GEOMETRY'})
        bundle = parsed(doc)
        # A real drawing need not declare the DM_* layers; an absent title is absent.
        self.assertEqual({key: field for key, field in bundle['snapshot']['identity'].items()
                          if field}, {})
        self.assertNotIn('CAD_TITLE_MISSING', codes(bundle))

    def test_a_duplicate_title_field_is_declared_and_dropped(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_text('R-OTHER', dxfattribs={'layer': 'DM_REVISION', 'insert': (0, 70),
                                                        'height': 2})
        snapshot = parsed(doc)['snapshot']
        self.assertIsNone(snapshot['identity']['drawing_revision'])
        self.assertEqual(snapshot['identity']['part_id']['value'], 'A200')


class BlockExpansionTests(unittest.TestCase):
    def test_an_insert_expands_with_the_block_name_and_original_handles(self):
        doc, reference = blocks_doc()
        bundle = parsed(doc)
        snapshot = bundle['snapshot']
        inserts = [e for e in snapshot['entities'] if e['entity_type'] == 'INSERT']
        self.assertEqual(len(inserts), 2)
        outer = next(e for e in inserts if e['block_path'] == [])
        self.assertEqual(outer['handle'], reference.dxf.handle)
        self.assertEqual(outer['block_name'], 'PART')
        self.assertEqual(outer['expansion_status'], 'expanded')
        self.assertEqual(outer['expanded_entity_count'], 5)
        nested = next(e for e in inserts if e['block_path'])
        self.assertEqual(nested['block_name'], 'SUB')
        self.assertEqual(nested['block_path'], [reference.dxf.handle])
        # The reference record comes before the content it reaches (document order).
        order = [(e['entity_type'], tuple(e['block_path'])) for e in snapshot['entities']]
        self.assertLess(order.index(('INSERT', ())), order.index(('LINE', (reference.dxf.handle,))))
        # A block-internal handle is unique together with its chain.
        part_line = next(e for e in snapshot['entities'] if e['entity_type'] == 'LINE'
                         and e['block_path'] == [reference.dxf.handle])
        block_line = next(line for line in doc.blocks.get('PART') if line.dxftype() == 'LINE')
        self.assertEqual(part_line['handle'], block_line.dxf.handle)
        self.assertEqual((part_line['start']['x'], part_line['start']['y']), (10.0, 20.0))
        self.assertEqual((part_line['end']['x'], part_line['end']['y']), (12.0, 20.0))

    def test_a_rotated_scaled_reference_moves_geometry_text_and_radius(self):
        doc, _reference = blocks_doc(rotation=90, scale=2)
        snapshot = parsed(doc)['snapshot']
        part_path = [e['handle'] for e in snapshot['entities']
                     if e['entity_type'] == 'INSERT' and e['block_path'] == []]
        entities = [e for e in snapshot['entities'] if e['block_path'] == part_path]
        line = next(e for e in entities if e['entity_type'] == 'LINE')
        self.assertAlmostEqual(line['start']['x'], 10.0)
        self.assertAlmostEqual(line['start']['y'], 20.0)
        self.assertAlmostEqual(line['end']['x'], 10.0)
        self.assertAlmostEqual(line['end']['y'], 24.0)
        circle = next(e for e in entities if e['entity_type'] == 'CIRCLE')
        self.assertAlmostEqual(circle['center']['x'], 10.0)
        self.assertAlmostEqual(circle['center']['y'], 22.0)
        self.assertAlmostEqual(circle['radius'], 1.0)
        text = next(e for e in entities if e['entity_type'] == 'TEXT')
        self.assertEqual(text['text'], 'BLK')
        self.assertAlmostEqual(text['rotation'], 90.0)
        self.assertAlmostEqual(text['height'], 2.0)

    def test_a_block_internal_dimension_is_declared_not_rescaled(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        block = doc.blocks.new('MEASURED')
        dimension = block.add_linear_dim(base=(0, -5), p1=(0, 0), p2=(4, 0))
        dimension.render()
        doc.modelspace().add_blockref('MEASURED', (0, 0), dxfattribs={'xscale': 2, 'yscale': 2})
        bundle = parsed(doc)
        self.assertIn('CAD_DIMENSION_IN_BLOCK_UNSUPPORTED', codes(bundle))
        self.assertEqual([f for f in bundle['snapshot']['features']
                          if f['feature_type'] == 'native_dimension'], [])

    def test_a_cyclic_block_definition_is_refused_by_the_audit(self):
        # ezdxf's audit rejects a circular block reference outright; the stable code is
        # reported instead of a partial read of a document the library cannot resolve.
        with self.assertRaises(CadReadError) as error:
            parsed(cycle_doc())
        self.assertEqual(error.exception.code, 'CAD_DXF_AUDIT_ERRORS')

    def test_the_recursion_guard_is_defence_in_depth(self):
        # The guard stays reachable for a library build whose audit accepts a cycle.
        doc, reference = blocks_doc()
        extractor = cad_dxf_v2._Extractor(doc, limits=cad_dxf_v2.resolve_limits(),
                                          sha='a' * 64, unit='mm', bbox_module=ezdxf.bbox)
        extractor._insert(reference, [], [], ('PART',))
        self.assertEqual(extractor.counts['CAD_BLOCK_RECURSION_CYCLE'], 1)
        self.assertEqual(extractor.entities[0]['expansion_code'], 'CAD_BLOCK_RECURSION_CYCLE')

    def test_the_depth_limit_is_declared_and_parameterised(self):
        doc, _reference = blocks_doc()
        bundle = parsed(doc, max_block_depth=1)
        self.assertIn('CAD_BLOCK_DEPTH_LIMIT_REACHED', codes(bundle))
        skipped = next(e for e in bundle['snapshot']['entities']
                       if e['entity_type'] == 'INSERT' and e['expansion_status'] == 'skipped')
        self.assertEqual(skipped['expansion_code'], 'CAD_BLOCK_DEPTH_LIMIT_REACHED')

    def test_a_mirrored_block_keeps_geometry_but_refuses_text(self):
        doc, _reference = blocks_doc(scale=1, xscale=-1)
        bundle = parsed(doc)
        self.assertIn('CAD_TEXT_IN_BLOCK_TRANSFORM_UNSUPPORTED', codes(bundle))
        self.assertTrue([e for e in bundle['snapshot']['entities'] if e['entity_type'] == 'LINE'])

    def test_a_non_uniform_reference_is_declared_not_approximated(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        block = doc.blocks.new('STRETCH')
        block.add_circle((0, 0), 1, dxfattribs={'layer': 'GEOMETRY'})
        doc.modelspace().add_blockref('STRETCH', (0, 0), dxfattribs={'xscale': 2, 'yscale': 1})
        bundle = parsed(doc)
        self.assertIn('CAD_INSERT_NON_UNIFORM_SCALE_UNSUPPORTED', codes(bundle))
        self.assertEqual(bundle['snapshot']['entities'][-1]['expansion_status'], 'skipped')

    def test_a_missing_block_is_declared(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_blockref('NOT_DEFINED', (0, 0))
        bundle = parsed(doc)
        self.assertIn('CAD_BLOCK_MISSING', codes(bundle))


class DeclaredRefusalTests(unittest.TestCase):
    def test_an_unsupported_entity_type_is_declared_with_type_and_handle(self):
        doc = engineering_doc()
        bundle = parsed(doc)
        item = next(item for item in bundle['metadata']['unsupported']
                    if item['code'] == 'CAD_ENTITY_TYPE_UNSUPPORTED')
        self.assertEqual(item['entity_type'], 'ELLIPSE')
        self.assertTrue(item['handle'])
        self.assertEqual(bundle['metadata']['first_unsupported'], item)
        self.assertTrue(any(text.startswith('CAD_ENTITY_TYPE_UNSUPPORTED:ELLIPSE:')
                            for text in bundle['metadata']['unsupported_features']))
        # The supported entities are still all present.
        self.assertEqual(len(bundle['snapshot']['entities']), 11)

    def test_spline_hatch_and_3dface_are_declared_individually(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        model = doc.modelspace()
        model.add_spline([(0, 0), (1, 1), (2, 0)])
        model.add_3dface([(0, 0), (1, 0), (1, 1), (0, 1)])
        model.add_point((0, 0))
        bundle = parsed(doc)
        declared = {(item['entity_type'], item['code']) for item in bundle['metadata']['unsupported']}
        for kind in ('SPLINE', '3DFACE', 'POINT'):
            self.assertIn((kind, 'CAD_ENTITY_TYPE_UNSUPPORTED'), declared)

    def test_ocs_3d_and_hidden_entities_are_declared(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_circle((0, 0), 1, dxfattribs={'extrusion': (0, 1, 0)})
        bundle = parsed(doc)
        self.assertIn('CAD_OCS_OR_THICKNESS_UNSUPPORTED', codes(bundle))

        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_line((0, 0, 0), (1, 0, 5))
        self.assertIn('CAD_3D_OR_NONFINITE_UNSUPPORTED', codes(parsed(doc)))

        doc = new_doc()
        add_titles(doc.modelspace())
        doc.layers.new('HIDDEN').off()
        doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={'layer': 'HIDDEN'})
        self.assertIn('CAD_HIDDEN_ENTITY_UNSUPPORTED', codes(parsed(doc)))

    def test_a_bulged_polyline_is_declared_instead_of_flattened(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        polyline = doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1)],
                                                   dxfattribs={'layer': 'GEOMETRY'})
        polyline.set_points([(0, 0, 0, 0, 0), (1, 0, 0, 0, 0.5), (1, 1, 0, 0, 0)], format='xyseb')
        self.assertIn('CAD_POLYLINE_BULGE_UNSUPPORTED', codes(parsed(doc)))

    def test_text_alignments_we_do_not_model_are_declared(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_text('placed', dxfattribs={'insert': (0, 0), 'height': 2,
                                                       'halign': 1})
        self.assertIn('CAD_TEXT_TRANSFORM_UNSUPPORTED', codes(parsed(doc)))

    def test_paper_space_content_is_declared_and_not_read(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.layouts.get('Layout1').add_circle((0, 0), 5)
        bundle = parsed(doc)
        item = next(item for item in bundle['metadata']['unsupported']
                    if item['code'] == 'CAD_PAPERSPACE_ENTITIES_SKIPPED')
        self.assertIn('Layout1', item['detail'])
        self.assertEqual([e for e in bundle['snapshot']['entities']
                          if e['entity_type'] == 'CIRCLE'], [])

    def test_a_drawing_with_no_supported_entity_is_refused_with_its_declarations(self):
        doc = new_doc()
        doc.modelspace().add_ellipse((0, 0), (5, 5), ratio=0.5)
        with self.assertRaises(CadReadError) as error:
            parsed(doc)
        self.assertEqual(error.exception.code, 'CAD_NO_SUPPORTED_ENTITIES')
        self.assertTrue(error.exception.details['unsupported'])

    def test_several_unsupported_entities_are_declared_item_by_item(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        for index in range(12):
            doc.modelspace().add_point((index, index))
        bundle = parsed(doc)
        self.assertEqual(bundle['metadata']['unsupported_counts']['CAD_ENTITY_TYPE_UNSUPPORTED'], 12)
        self.assertEqual(len([t for t in bundle['metadata']['unsupported_features']
                              if t.startswith('CAD_ENTITY_TYPE_UNSUPPORTED:POINT:')]), 12)

    def test_the_declaration_list_is_bounded_with_an_explicit_total(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        for index in range(20):
            doc.modelspace().add_point((index, index))
        bundle = parsed(doc, limits=cad_dxf_v2.CadLimits(max_declared_items=5))
        self.assertEqual(len(bundle['metadata']['unsupported']), 5)
        self.assertIn('CAD_UNSUPPORTED_ITEMS_TRUNCATED:20', bundle['metadata']['unsupported_features'])


class LimitTests(unittest.TestCase):
    def test_the_documented_defaults_and_the_environment_names(self):
        limits = cad_dxf_v2.resolve_limits(env={})
        self.assertEqual(limits.max_bytes, 32 * 1024 * 1024)
        self.assertEqual(limits.max_entities, 200_000)
        self.assertEqual(limits.max_expanded_entities, 200_000)
        self.assertEqual(limits.max_block_depth, 8)
        self.assertEqual(cad_dxf_v2.LIMIT_ENV['max_bytes'], 'DATAMIND_CAD_MAX_BYTES')
        self.assertEqual(cad_dxf_v2.LIMIT_ENV['max_entities'], 'DATAMIND_CAD_MAX_ENTITIES')
        self.assertEqual(cad_dxf_v2.LIMIT_ENV['max_expanded_entities'],
                         'DATAMIND_CAD_MAX_EXPANDED_ENTITIES')
        self.assertEqual(cad_dxf_v2.LIMIT_ENV['max_block_depth'], 'DATAMIND_CAD_MAX_BLOCK_DEPTH')

    def test_the_environment_overrides_and_an_override_wins(self):
        limits = cad_dxf_v2.resolve_limits({'max_bytes': 99},
                                          env={'DATAMIND_CAD_MAX_BYTES': '2048',
                                               'DATAMIND_CAD_MAX_BLOCK_DEPTH': '3'})
        self.assertEqual(limits.max_bytes, 99)
        self.assertEqual(limits.max_block_depth, 3)

    def test_an_unusable_limit_is_refused_rather_than_clamped(self):
        for value in ('0', '-5', 'abc', '200001'):
            with self.subTest(value=value), self.assertRaises(CadReadError) as error:
                cad_dxf_v2.resolve_limits(env={'DATAMIND_CAD_MAX_ENTITIES': value})
            self.assertEqual(error.exception.code, 'CAD_LIMIT_INVALID')

    def test_the_byte_limit_is_enforced_from_the_parameters(self):
        document = engineering_doc()
        with self.assertRaises(CadReadError) as error:
            parsed(document, max_bytes=64)
        self.assertEqual(error.exception.code, 'CAD_SOURCE_SIZE_INVALID')

    def test_the_source_entity_bound_is_enforced(self):
        with self.assertRaises(CadReadError) as error:
            parsed(engineering_doc(), max_entities=3)
        self.assertEqual(error.exception.code, 'CAD_ENTITY_COUNT_INVALID')

    def test_the_expanded_entity_bound_truncates_with_a_declaration(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        block = doc.blocks.new('MANY')
        for index in range(5):
            block.add_line((0, index), (1, index), dxfattribs={'layer': 'GEOMETRY'})
        doc.modelspace().add_blockref('MANY', (0, 0))
        bundle = parsed(doc, max_expanded_entities=6)
        self.assertIn('CAD_ENTITY_LIMIT_REACHED', codes(bundle))
        self.assertEqual(len(bundle['snapshot']['entities']), 6)
        insert = next(e for e in bundle['snapshot']['entities'] if e['entity_type'] == 'INSERT')
        self.assertEqual(insert['expansion_status'], 'expanded_with_gaps')
        self.assertEqual(insert['expanded_entity_count'], 2)

    def test_the_declared_limits_travel_in_the_metadata(self):
        metadata = parsed(engineering_doc(), max_bytes=1 << 20, max_block_depth=4)['metadata']
        self.assertEqual(metadata['limits']['max_bytes'], 1 << 20)
        self.assertEqual(metadata['limits']['max_block_depth'], 4)
        self.assertEqual(metadata['limits']['max_declared_items'], 500)


class UnitTests(unittest.TestCase):
    def test_units_come_from_insunits_and_never_guess_mm(self):
        for code, unit, modelled in ((4, 'mm', True), (1, 'in', True), (0, 'unknown', True),
                                     (5, 'unknown', False)):
            with self.subTest(insunits=code):
                doc = new_doc(unit=code)
                add_titles(doc.modelspace())
                bundle = parsed(doc)
                source = bundle['snapshot']['source']
                self.assertEqual(source['unit'], unit)
                self.assertEqual(source['insunits'], code)
                self.assertEqual(source['insunits_modelled'], modelled)
                if not modelled:
                    self.assertIn('CAD_UNIT_CODE_NOT_MODELLED', codes(bundle))

    def test_a_missing_unit_header_stays_unknown_and_keeps_values_unset(self):
        doc = engineering_doc(unit=0)
        bundle = parsed(doc)
        self.assertEqual(bundle['snapshot']['source']['unit'], 'unknown')
        feature = next(f for f in bundle['snapshot']['features'] if f['feature_type'] == 'diameter')
        self.assertIsNone(feature['nominal_value'])
        self.assertIn('CAD_UNIT_UNKNOWN', bundle['metadata']['warnings'])

    def test_a_non_utf8_source_is_decoded_by_its_declared_code_page(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_mtext('原件', dxfattribs={'insert': (0, -10), 'char_height': 2})
        # ezdxf always writes ANSI_1252; the declaration is what a Chinese CAD export
        # really carries, so the fixture writes it explicitly before encoding to GBK.
        text = source(doc).decode('utf-8').replace('ANSI_1252', 'ANSI_936')
        data = text.encode('gbk')
        bundle = cad_dxf_v2.parse_bytes(data, document_id='test-gbk')
        self.assertEqual(bundle['snapshot']['source']['encoding'], 'gbk')
        mtext = next(e for e in bundle['snapshot']['entities'] if e['entity_type'] == 'MTEXT')
        self.assertEqual(mtext['text'], '原件')
        self.assertIn('CAD_ENCODING_FALLBACK:gbk', bundle['metadata']['warnings'])


class SourceIntegrityTests(unittest.TestCase):
    def test_binary_oversize_empty_and_malformed_sources_are_refused(self):
        for data, code in ((b'AutoCAD Binary DXF\x00', 'CAD_BINARY_DXF_UNSUPPORTED'),
                           (b'invalid\nsource\n', 'CAD_DXF_TAGS_INVALID'),
                           (b'', 'CAD_SOURCE_SIZE_INVALID'),
                           (b'x' * (cad_dxf_v2.DEFAULT_MAX_BYTES + 1), 'CAD_SOURCE_SIZE_INVALID')):
            with self.subTest(code=code), self.assertRaises(CadReadError) as error:
                cad_dxf_v2.parse_bytes(data, document_id='test-v2', max_bytes=None)
            self.assertEqual(error.exception.code, code)

    def test_a_duplicate_source_handle_is_detected_before_the_library_repairs_it(self):
        doc = engineering_doc()
        data = source(doc)
        arc = next(e for e in doc.modelspace() if e.dxftype() == 'ARC')
        annotation = next(e for e in doc.modelspace() if e.dxftype() == 'TEXT'
                          and e.dxf.layer == 'DM_DIAMETER')
        modified = data.replace(('\n' + annotation.dxf.handle + '\n').encode(),
                                ('\n' + arc.dxf.handle + '\n').encode(), 1)
        with self.assertRaises(CadReadError) as error:
            cad_dxf_v2.parse_bytes(modified, document_id='test-v2')
        self.assertEqual(error.exception.code, 'CAD_DUPLICATE_SOURCE_HANDLE')

    def test_the_source_digest_is_of_the_bytes_that_were_read(self):
        data = source(engineering_doc())
        bundle = cad_dxf_v2.parse_bytes(data, document_id='test-v2')
        self.assertEqual(bundle['snapshot']['source']['source_sha256'],
                         hashlib.sha256(data).hexdigest())

    def test_the_view_box_contains_every_entity(self):
        snapshot = parsed(engineering_doc())['snapshot']
        drawn = DrawingSnapshot.model_validate(snapshot)
        box = drawn.views[0].bbox
        for entity in drawn.entities:
            self.assertTrue(box.contains(entity.bounds()), entity.handle)


class DeclaredSizeGateTests(unittest.TestCase):
    def test_only_declared_values_are_usable(self):
        snapshot = parsed(engineering_doc())['snapshot']
        gate = declared_sizes(snapshot)
        usable = {item['feature_id']: item for item in gate['features']}
        excluded = {item['feature_id']: item['reason'] for item in gate['excluded']}
        diameter = next(f for f in snapshot['features'] if f['feature_type'] == 'diameter')
        self.assertEqual(usable[diameter['feature_id']]['value'], '20')
        self.assertEqual(usable[diameter['feature_id']]['unit'], 'mm')
        self.assertEqual(len(usable), 1)
        measurements = [f for f in snapshot['features']
                        if f['feature_type'] == 'native_dimension'
                        and f['value_authority'] == 'dimension_measurement']
        self.assertTrue(measurements)
        for feature in measurements:
            self.assertEqual(excluded[feature['feature_id']], 'CAD_VALUE_IS_GEOMETRY_MEASUREMENT')

    def test_a_numeric_override_is_usable_and_no_geometry_is_substituted(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        dimension = doc.modelspace().add_linear_dim(base=(0, -5), p1=(0, 0), p2=(12.5, 0),
                                                    text='12.5')
        dimension.render()
        gate = declared_sizes(parsed(doc)['snapshot'])
        self.assertEqual([item['value'] for item in gate['features']], ['12.5'])
        self.assertEqual(gate['features'][0]['value_authority'], 'declared_text')


class ContractTests(unittest.TestCase):
    def _snapshot(self):
        return parsed(engineering_doc())['snapshot']

    def test_a_duplicate_handle_and_block_path_is_refused(self):
        snapshot = self._snapshot()
        first = snapshot['entities'][0]
        circle = next(e for e in snapshot['entities'] if e['entity_type'] == 'CIRCLE')
        duplicate = dict(circle)
        duplicate['handle'] = first['handle']
        duplicate['block_path'] = list(first['block_path'])
        snapshot['identity'] = {}
        snapshot['features'] = []
        snapshot['entities'] = [first, duplicate]
        with self.assertRaises(ValidationError) as error:
            DrawingSnapshot.model_validate(snapshot)
        self.assertIn('CAD_DUPLICATE_HANDLE_OR_BLOCK_PATH', str(error.exception))

    def test_a_native_dimension_feature_must_match_its_entity(self):
        snapshot = self._snapshot()
        feature = next(f for f in snapshot['features'] if f['feature_type'] == 'native_dimension')
        feature['declared_text'] = '9'
        feature['declared_value'] = '9'
        feature['value_authority'] = 'declared_text'
        with self.assertRaises(ValidationError):
            DrawingSnapshot.model_validate(snapshot)

    def test_the_authority_label_cannot_contradict_the_values(self):
        snapshot = self._snapshot()
        feature = next(f for f in snapshot['features'] if f['feature_type'] == 'native_dimension')
        feature['value_authority'] = 'declared_text'
        with self.assertRaises(ValidationError) as error:
            DrawingSnapshot.model_validate(snapshot)
        self.assertIn('CAD_VALUE_AUTHORITY_MISMATCH', str(error.exception))

    def test_the_block_path_is_bounded_and_well_formed(self):
        snapshot = self._snapshot()
        snapshot['entities'][0]['block_path'] = ['not-a-handle']
        with self.assertRaises(ValidationError):
            DrawingSnapshot.model_validate(snapshot)


class AdapterProfileTests(unittest.TestCase):
    """The default profile is v2 and v1 stays reachable for the old evidence."""

    def setUp(self):
        contract._ADAPTERS.clear()
        cad_adapter.register_cad_adapters()

    def tearDown(self):
        contract._ADAPTERS.clear()

    def test_the_default_profile_is_v2_and_v1_is_selectable(self):
        self.assertEqual(cad_adapter.DEFAULT_PROFILE, 'v2')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'engineering.dxf'
            path.write_bytes(source(engineering_doc()))
            modern = cad_adapter.parse(path, document_id='doc-v2')
            # v1 refuses the whole drawing: its raw-handle check also sees the inline
            # POLYLINE vertices, so the entity set no longer matches. That discipline is
            # preserved, not weakened -- only the default profile changed.
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(path, document_id='doc-v1', profile='v1')
            self.assertEqual(caught.exception.detail['error_code'],
                             'CAD_ENTITY_LOSS_OR_LAYOUT_UNSUPPORTED')
        self.assertEqual(modern.payload['manifest']['profile'], cad_dxf_v2.PROFILE)
        self.assertEqual(modern.payload['parse_status'], 'partial')

    def test_v1_still_refuses_an_arc_drawing_that_v2_reads(self):
        doc = new_doc()
        add_titles(doc.modelspace())
        doc.modelspace().add_arc((0, 0), 5, 0, 90, dxfattribs={'layer': 'GEOMETRY'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'arc.dxf'
            path.write_bytes(source(doc))
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(path, document_id='doc-arc', profile='v1')
            self.assertEqual(caught.exception.detail['error_code'], 'CAD_ENTITY_TYPE_UNSUPPORTED')
            self.assertEqual(cad_adapter.parse(path, document_id='doc-arc').payload['parse_status'],
                             'parsed')

    def test_an_unknown_profile_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'engineering.dxf'
            path.write_bytes(source(engineering_doc()))
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(path, document_id='doc-v3', profile='v3')
            self.assertEqual(caught.exception.detail['error_code'], 'CAD_PROFILE_UNSUPPORTED')

    def test_a_reader_option_the_profile_cannot_honour_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'engineering.dxf'
            path.write_bytes(source(engineering_doc()))
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(path, document_id='doc-v1', profile='v1', max_bytes=10)
            self.assertEqual(caught.exception.detail['error_code'], 'CAD_PROFILE_OPTION_UNSUPPORTED')

    def test_the_reader_limit_travels_through_the_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'engineering.dxf'
            path.write_bytes(source(engineering_doc()))
            with self.assertRaises(HTTPException) as caught:
                cad_adapter.parse(path, document_id='doc-limit', max_entities=3)
            self.assertEqual(caught.exception.detail['error_code'], 'CAD_ENTITY_COUNT_INVALID')
            payload = cad_adapter.parse(path, document_id='doc-limit',
                                        max_expanded_entities=2).payload
        self.assertEqual(payload['parse_status'], 'partial')


class FrozenV1GuardTests(unittest.TestCase):
    """The v2 work must not move a single byte of the frozen v1 implementation."""

    def test_the_eight_frozen_v1_files_are_unchanged(self):
        manifest = json.loads(FROZEN_V1.read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['code_hashes']), 8)
        for relative, digest in sorted(manifest['code_hashes'].items()):
            completed = subprocess.run(['shasum', '-a', '256', str(ROOT / relative)],
                                       capture_output=True, text=True, check=True)
            self.assertEqual(completed.stdout.split()[0], digest, relative)

    def test_the_frozen_v1_identity_still_matches_the_live_modules(self):
        manifest = json.loads(FROZEN_V1.read_text(encoding='utf-8'))
        self.assertEqual(cad_dxf.PARSER_VERSION, manifest['parser_version'])
        self.assertEqual(cad_dxf.PROFILE, manifest['profile'])
        self.assertEqual(cad_dxf.MAX_BYTES, 2 * 1024 * 1024)
        self.assertEqual(cad_dxf.MAX_ENTITIES, 10000)


class FrozenV2ManifestTests(unittest.TestCase):
    """The v2 freeze is only a freeze while the pinned files still match it."""

    def setUp(self):
        self.manifest = json.loads(FROZEN_V2.read_text(encoding='utf-8'))

    def test_every_pinned_file_still_hashes_to_the_manifest(self):
        self.assertTrue(self.manifest['code_hashes'])
        for relative, digest in sorted(self.manifest['code_hashes'].items()):
            completed = subprocess.run(['shasum', '-a', '256', str(ROOT / relative)],
                                       capture_output=True, text=True, check=True)
            self.assertEqual(completed.stdout.split()[0], digest, relative)

    def test_the_manifest_identity_matches_the_live_v2_modules(self):
        self.assertEqual(self.manifest['parser_version'], cad_dxf_v2.PARSER_VERSION)
        self.assertEqual(self.manifest['profile'], cad_dxf_v2.PROFILE)
        self.assertEqual(self.manifest['contract_version'], CONTRACT_VERSION)
        self.assertEqual(self.manifest['ezdxf_version'], ezdxf.__version__)
        self.assertTrue(self.manifest['v1_code_hashes_unchanged'])

    def test_the_documented_default_limits_are_the_code_defaults(self):
        limits = cad_dxf_v2.resolve_limits(env={})
        self.assertEqual(self.manifest['tunable_limits']['DATAMIND_CAD_MAX_BYTES'],
                         limits.max_bytes)
        self.assertEqual(self.manifest['tunable_limits']['DATAMIND_CAD_MAX_ENTITIES'],
                         limits.max_entities)
        self.assertEqual(self.manifest['tunable_limits']['DATAMIND_CAD_MAX_EXPANDED_ENTITIES'],
                         limits.max_expanded_entities)
        self.assertEqual(self.manifest['tunable_limits']['DATAMIND_CAD_MAX_BLOCK_DEPTH'],
                         limits.max_block_depth)


if __name__ == '__main__':
    unittest.main()
