"""Synthetic contract/gate checks, not DXF extraction accuracy tests."""
from copy import deepcopy
from decimal import localcontext
import json
from pathlib import Path
import unittest

from pydantic import ValidationError
from ingestion.cad_contract import DrawingSnapshot, eligible_features

DATA = Path(__file__).resolve().parents[1] / 'data/cad_demo'


class CadContractTests(unittest.TestCase):
    def setUp(self):
        self.drawing = json.loads((DATA/'snapshot_v1.json').read_text())
        self.context = json.loads((DATA/'binding_context_v1.json').read_text())

    def reject(self, mutate, code):
        drawing = deepcopy(self.drawing)
        mutate(drawing)
        with self.assertRaises(ValidationError) as error:
            DrawingSnapshot.model_validate(drawing)
        self.assertIn(code, str(error.exception))

    def test_explicit_binding_and_annotation_bounds(self):
        result = eligible_features(self.drawing, self.context)
        self.assertEqual(result, json.loads((DATA/'expected_gate_v1.json').read_text()))
        self.assertEqual(result['features'][0]['lower'], '19.9')
        self.assertEqual(result['features'][0]['upper'], '20.1')

    def test_wrong_part_drawing_revision_or_source_never_binds(self):
        for key in self.context:
            with self.subTest(key=key):
                changed = dict(self.context)
                changed[key] = '0'*64 if key=='source_sha256' else 'WRONG'
                result = eligible_features(self.drawing, changed)
                self.assertEqual(result['features'], [])
                self.assertEqual(result['reason'], 'CAD_BUSINESS_BINDING_MISMATCH')

    def test_source_version_not_process_revision_and_types_are_strict(self):
        self.reject(lambda d:d['source'].update(document_version=True), 'int_type')
        self.reject(lambda d:d['source'].update(insunits=True), 'int_type')
        self.reject(lambda d:d['source'].update(process_revision='A102-V2'), 'extra_forbidden')
        self.reject(lambda d:d['source'].update(format='dwg'), 'literal_error')

    def test_title_evidence_and_annotation_evidence_are_exact(self):
        self.reject(lambda d:d['identity']['part_id'].update(value='A103'), 'CAD_IDENTITY_NOT_IN_SOURCE')
        self.reject(lambda d:d['features'][0].update(annotation_raw='%%c21 +/-0.1', nominal_value='21'),
                    'CAD_ANNOTATION_NOT_IN_SOURCE')
        self.reject(lambda d:d['features'][0].update(nominal_value='10'), 'CAD_DIMENSION_NOT_IN_ANNOTATION')
        self.reject(lambda d:d['features'][0].update(tolerance_upper='0.2'), 'CAD_DIMENSION_NOT_IN_ANNOTATION')

    def test_geometry_and_units_remain_separate(self):
        changed = deepcopy(self.drawing)
        changed['entities'][4]['radius'] = 9
        result = eligible_features(changed, self.context)
        self.assertEqual(result['features'], [])
        self.assertEqual(result['excluded'][0]['reason'], 'CAD_GEOMETRY_ANNOTATION_CONFLICT')
        self.assertEqual(changed['features'][0]['nominal_value'], '20')
        self.reject(lambda d:d['source'].update(insunits=1), 'CAD_UNIT_SETTING_MISMATCH')

    def test_unknown_units_and_unreadable_values_are_retained_but_excluded(self):
        changed = deepcopy(self.drawing)
        changed['source'].update(insunits=0, unit='unknown')
        result = eligible_features(changed, self.context)
        self.assertEqual(result['excluded'][0]['reason'], 'CAD_VALUE_OR_UNIT_UNKNOWN')
        changed = deepcopy(self.drawing)
        changed['features'][0].update(nominal_value=None, tolerance_lower=None, tolerance_upper=None)
        self.assertEqual(eligible_features(changed,self.context)['features'], [])
        self.reject(lambda d:d['features'][0].update(nominal_value=None), 'CAD_PARTIAL_DIMENSION')

    def test_ambiguous_and_unresolved_objects_are_not_selected(self):
        for state, targets in [('ambiguous',['20','21']), ('unresolved',[]), ('unresolved',['20'])]:
            with self.subTest(state=state, targets=targets):
                changed = deepcopy(self.drawing)
                changed['features'][0].update(association_status=state,target_candidates=targets,
                                              selected_target=None,association_evidence=None)
                result = eligible_features(changed,self.context)
                self.assertEqual(result['features'], [])
                self.assertEqual(result['excluded'][0]['reason'], 'CAD_ASSOCIATION_UNCERTAIN')
        self.reject(lambda d:d['features'][0].update(target_candidates=['20','21']),
                    'CAD_ASSOCIATION_EVIDENCE_REQUIRED')
        self.reject(lambda d:d['features'][0].update(association_status='ambiguous'),
                    'CAD_UNCERTAIN_ASSOCIATION_SELECTED')

    def test_reference_must_exist_in_same_view_and_be_geometry(self):
        for target in ['AA','13']:
            def mutate(d):
                d['features'][0].update(target_candidates=[target],selected_target=target)
                d['features'][0]['association_evidence']['target_handle']=target
            self.reject(mutate, 'CAD_TARGET_MISSING_WRONG_TYPE_OR_VIEW')
        def move(d):
            d['views'].append({'view_id':'rear','bbox':deepcopy(d['views'][0]['bbox'])})
            d['entities'][4]['view_id']='rear'
        self.reject(move, 'CAD_TARGET_MISSING_WRONG_TYPE_OR_VIEW')

    def test_duplicate_handles_features_views_or_candidates_are_rejected(self):
        self.reject(lambda d:d['entities'].append(deepcopy(d['entities'][0])), 'CAD_DUPLICATE_VIEW_OR_HANDLE')
        self.reject(lambda d:d['views'].append(deepcopy(d['views'][0])), 'CAD_DUPLICATE_VIEW_OR_HANDLE')
        self.reject(lambda d:d['features'].append(deepcopy(d['features'][0])), 'CAD_DUPLICATE_FEATURE')
        self.reject(lambda d:d['features'][0].update(target_candidates=['20','20']), 'CAD_DUPLICATE_TARGET')

    def test_stale_hash_handle_layer_and_view_locators(self):
        for field,value in [('source_sha256','0'*64),('handle','AA'),('layer','wrong'),('view_id','rear')]:
            self.reject(lambda d:d['features'][0]['locator'].update({field:value}), 'CAD_LOCATOR_STALE_OR_INVALID')
        self.reject(lambda d:d['features'][0]['association_evidence'].update(source_sha256='0'*64),
                    'CAD_ASSOCIATION_EVIDENCE_STALE')

    def test_nonfinite_geometry_3d_pixel_coordinates_and_invalid_extent(self):
        for value in [float('nan'),float('inf'),True,'10']:
            drawing = deepcopy(self.drawing)
            drawing['entities'][4]['center']['x']=value
            with self.assertRaises(ValidationError):
                DrawingSnapshot.model_validate(drawing)
        self.reject(lambda d:d['entities'][4]['center'].update(z=1), 'extra_forbidden')
        self.reject(lambda d:d['features'][0]['locator'].update(coordinate_system='pixels'), 'literal_error')
        self.reject(lambda d:d['views'][0]['bbox'].update(xmax=-5), 'CAD_BBOX_INVALID')
        self.reject(lambda d:d['entities'][4]['center'].update(x=100), 'CAD_ENTITY_OUTSIDE_VIEW')
        self.reject(lambda d:d['entities'][6].update(end={'x':0,'y':0}), 'CAD_ZERO_LENGTH_LINE')

    def test_malformed_or_nonfinite_decimal_and_caller_precision(self):
        for value in ['NaN','Infinity','1e2','-20','0','9'*65]:
            drawing = deepcopy(self.drawing)
            drawing['features'][0]['nominal_value']=value
            with self.assertRaises(ValidationError):
                DrawingSnapshot.model_validate(drawing)
        with localcontext() as ctx:
            ctx.prec=2
            result=eligible_features(self.drawing,self.context)
        self.assertEqual(result['features'][0]['upper'], '20.1')


if __name__=='__main__':
    unittest.main()
