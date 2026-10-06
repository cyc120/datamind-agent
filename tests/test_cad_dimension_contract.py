"""Bounded diameter contract checks; declarations do not authenticate people."""
import copy
from decimal import Decimal
import unittest
from pydantic import ValidationError
from ingestion.cad_dimension_contract import evaluate,digest,DimensionEvaluation
import cad_dimension_fixtures as fixtures


class CadDimensionContractTests(unittest.TestCase):
    def blocked(self,bundle,payload,reason):
        result=evaluate(bundle,payload);self.assertEqual(result['status'],'blocked');self.assertEqual(result['reason'],reason)
        self.assertIsNone(result['comparison']);return result

    def test_pending_use_cannot_compare_even_with_native_and_human_confirmation(self):
        bundle,p=fixtures.make_fixture();p['approvals']=[]
        r=self.blocked(bundle,p,'DIMENSION_USE_APPROVAL_REQUIRED');self.assertFalse(r['authentication_verified'])
        self.assertFalse(r['product_quality_conclusion']);self.assertFalse(r['formal_drawing_approval'])

    def test_approved_explicit_binding_and_inclusive_annotation_limits(self):
        for value,status in [('19.9','within_annotation_range'),('20.1','within_annotation_range'),('20.10000000000000000001','outside_annotation_range'),('19.89','outside_annotation_range')]:
            bundle,p=fixtures.make_fixture(value=value);r=evaluate(bundle,p)
            self.assertEqual(r['status'],'compared');self.assertEqual(r['comparison']['status'],status)
            self.assertEqual(r['comparison']['measurement_original']['value'],value)
        self.assertNotEqual(p['binding']['payload']['drawing_revision'],p['binding']['payload']['process_revision'])

    def test_units_are_exact_and_original_values_are_retained(self):
        bundle,p=fixtures.make_fixture(value='0.79',unit='in');r=evaluate(bundle,p)
        self.assertEqual(r['comparison']['normalized_mm']['measurement'],'20.066')
        self.assertEqual(r['comparison']['measurement_original'],{'value':'0.79','unit':'in'})
        bundle,p=fixtures.make_fixture(value='508',unit='mm',drawing_unit=1);r=evaluate(bundle,p)
        self.assertEqual(r['comparison']['normalized_mm']['lower'],'505.46')
        self.assertEqual(r['comparison']['status'],'within_annotation_range')

    def test_current_heads_and_drawing_version_cannot_be_inferred(self):
        bundle,p=fixtures.make_fixture()
        for key,value in [('current_document_version',2),('current_source_sha256','0'*64),('current_measurement_record_version',2),('current_review_id','new-review'),('current_binding_version',2)]:
            changed=copy.deepcopy(p);changed['context'][key]=value;self.blocked(bundle,changed,'DIMENSION_CURRENT_HEAD_MISMATCH')
        for key,value in [('document_version',2),('source_sha256','0'*64),('parser_version','other-parser'),('bundle_sha256','0'*64)]:
            changed=copy.deepcopy(p);changed['binding']['payload'][key]=value;fixtures.rehash_binding(changed)
            self.blocked(bundle,changed,'DIMENSION_DRAWING_VERSION_MISMATCH')

    def test_drawing_identity_and_production_scope_are_all_explicit(self):
        bundle,p=fixtures.make_fixture()
        for key in ['drawing_id','drawing_revision','part_id']:
            changed=copy.deepcopy(p);changed['binding']['payload'][key]='OTHER';fixtures.rehash_binding(changed)
            self.blocked(bundle,changed,'DIMENSION_DRAWING_IDENTITY_MISMATCH')
        for key in ['batch_id','operation_no','process_revision','feature_role','method']:
            changed=copy.deepcopy(p);changed['binding']['payload'][key]='OTHER';fixtures.rehash_binding(changed)
            self.blocked(bundle,changed,'DIMENSION_PRODUCTION_SCOPE_MISMATCH')

    def test_measurement_record_and_source_are_bound(self):
        bundle,p=fixtures.make_fixture()
        for key,value in [('measurement_id','other-measurement'),('measurement_sha256','0'*64),('measurement_source_sha256','0'*64),('measurement_record_version',2)]:
            changed=copy.deepcopy(p);changed['binding']['payload'][key]=value;fixtures.rehash_binding(changed)
            self.blocked(bundle,changed,'DIMENSION_MEASUREMENT_VERSION_MISMATCH')
        changed=copy.deepcopy(p);changed['measurement']['value']='20.2';changed['context']['current_measurement_sha256']=digest(changed['measurement'])
        self.blocked(bundle,changed,'DIMENSION_MEASUREMENT_VERSION_MISMATCH')

    def test_validity_is_half_open_at_measurement_time_with_timezone(self):
        bundle,p=fixtures.make_fixture();p['binding']['payload']['valid_to']=p['measurement']['measured_at'];fixtures.rehash_binding(p)
        self.blocked(bundle,p,'DIMENSION_OUTSIDE_VALIDITY')
        bundle,p=fixtures.make_fixture();p['binding']['payload']['valid_from']='2026-10-05T00:00:00+00:00';fixtures.rehash_binding(p)
        self.assertEqual(evaluate(bundle,p)['status'],'compared')
        p['context']['evaluated_at']='2026-10-05T07:00:00+08:00';self.blocked(bundle,p,'DIMENSION_MEASUREMENT_IN_FUTURE')
        for path in ['measured_at','evaluated_at']:
            bundle,p=fixtures.make_fixture();target=p['measurement'] if path=='measured_at' else p['context'];target[path]='2026-10-05T08:00:00'
            with self.assertRaises(ValidationError):DimensionEvaluation.model_validate(p)
        bundle,p=fixtures.make_fixture();p['binding']['payload']['valid_from']=p['binding']['payload']['valid_to']
        with self.assertRaises(ValidationError):DimensionEvaluation.model_validate(p)

    def test_changed_review_and_uncertain_association_block(self):
        bundle,p=fixtures.make_fixture();p['review']['payload']['reason']='new-review-reason';p['review']['sha256']=digest(p['review']['payload']);p['context']['current_review_sha256']=p['review']['sha256']
        self.blocked(bundle,p,'DIMENSION_REVIEW_VERSION_MISMATCH')
        for decision,target in [('needs_review',None),('rejected',None),('confirmed','FFFF')]:
            bundle,p=fixtures.make_fixture();r=p['review']['payload'];r.update(decision=decision,selected_target=target)
            p['review']['sha256']=digest(r);p['binding']['payload'].update(review_sha256=p['review']['sha256'],target_handle=target or p['binding']['payload']['target_handle'])
            p['context']['current_review_sha256']=p['review']['sha256'];fixtures.rehash_binding(p)
            self.blocked(bundle,p,'DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED')

    def test_human_selection_of_ambiguous_candidate_does_not_modify_original(self):
        bundle,p=fixtures.make_fixture(reference=False);before=copy.deepcopy(bundle)
        self.assertEqual(bundle['snapshot']['features'][0]['association_status'],'ambiguous')
        self.assertEqual(evaluate(bundle,p)['status'],'compared');self.assertEqual(bundle,before)
        self.assertIsNone(bundle['snapshot']['features'][0]['selected_target'])

    def test_geometry_conflict_and_unknown_units_cannot_be_approved_away(self):
        bundle,p=fixtures.make_fixture(radius=9.975);self.blocked(bundle,p,'CAD_GEOMETRY_ANNOTATION_CONFLICT')
        bundle,p=fixtures.make_fixture(drawing_unit=0);self.blocked(bundle,p,'CAD_VALUE_OR_UNIT_UNKNOWN')
        for value,unit in [(None,'mm'),('20','unknown')]:
            bundle,p=fixtures.make_fixture(value=value,unit=unit);self.blocked(bundle,p,'DIMENSION_MEASUREMENT_VALUE_OR_UNIT_UNKNOWN')

    def test_role_claims_and_independent_actors_are_required_but_not_authenticated(self):
        for target in ['binding','review','approvals']:
            bundle,p=fixtures.make_fixture();record=p['approvals'][0] if target=='approvals' else p[target]
            rolekey={'binding':'registrar_roles','review':'reviewer_roles','approvals':'approver_roles'}[target]
            record['payload'][rolekey]=['viewer'];record['sha256']=digest(record['payload'])
            if target=='binding':fixtures.rehash_binding(p)
            elif target=='review':p['binding']['payload']['review_sha256']=record['sha256'];p['context']['current_review_sha256']=record['sha256'];fixtures.rehash_binding(p)
            expected={'binding':'DIMENSION_REGISTRAR_PERMISSION_REQUIRED','review':'DIMENSION_REVIEW_PERMISSION_REQUIRED','approvals':'DIMENSION_APPROVAL_PERMISSION_REQUIRED'}[target]
            self.blocked(bundle,p,expected)
        for actor in ['synthetic-registrar-1','synthetic-reviewer-1']:
            bundle,p=fixtures.make_fixture();p['approvals'][0]['payload']['approver_user_id']=actor;p['approvals'][0]['sha256']=digest(p['approvals'][0]['payload'])
            self.blocked(bundle,p,'DIMENSION_INDEPENDENT_APPROVER_REQUIRED')
        bundle,p=fixtures.make_fixture();self.assertFalse(evaluate(bundle,p)['authentication_verified'])

    def test_rejection_revocation_future_approval_and_historical_evaluation(self):
        bundle,p=fixtures.make_fixture();original=evaluate(bundle,p);fixtures.event_after(p)
        self.blocked(bundle,p,'DIMENSION_USE_APPROVAL_REVOKED');p['context']['evaluated_at']='2026-10-05T10:00:00+08:00'
        self.assertEqual(evaluate(bundle,p),original)
        for decision,expected in [('rejected','DIMENSION_USE_APPROVAL_REJECTED'),('revoked','DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE')]:
            bundle,p=fixtures.make_fixture();p['approvals'][0]['payload']['decision']=decision;p['approvals'][0]['sha256']=digest(p['approvals'][0]['payload']);self.blocked(bundle,p,expected)
        bundle,p=fixtures.make_fixture();p['context']['evaluated_at']='2026-10-05T08:30:00+08:00';self.blocked(bundle,p,'DIMENSION_USE_APPROVAL_REQUIRED')

    def test_tampering_and_rehashed_stale_approval_are_blocked(self):
        for target,key in [('binding','rationale'),('review','reason'),('approvals','reason')]:
            bundle,p=fixtures.make_fixture();record=p['approvals'][0] if target=='approvals' else p[target];record['payload'][key]='tampered'
            self.blocked(bundle,p,'DIMENSION_RECORD_HASH_MISMATCH')
        for key in ['binding_sha256','review_sha256','measurement_sha256']:
            bundle,p=fixtures.make_fixture();event=p['approvals'][0];event['payload'][key]='0'*64;event['sha256']=digest(event['payload']);self.blocked(bundle,p,'DIMENSION_APPROVAL_VERSION_MISMATCH')

    def test_approval_chain_duplicate_missing_predecessor_order_and_limits(self):
        for mutation in ['duplicate','predecessor','time']:
            bundle,p=fixtures.make_fixture();fixtures.event_after(p);event=p['approvals'][1]
            if mutation=='duplicate':event['payload']['approval_id']=p['approvals'][0]['payload']['approval_id']
            elif mutation=='predecessor':event['payload']['previous_approval_id']=None
            else:event['payload']['created_at']='2026-10-05T08:30:00+08:00'
            event['sha256']=digest(event['payload']);self.blocked(bundle,p,'DIMENSION_APPROVAL_CHAIN_INVALID')
        bundle,p=fixtures.make_fixture();p['approvals']*=1001
        with self.assertRaises(ValidationError):DimensionEvaluation.model_validate(p)

    def test_strict_values_metric_and_extra_fields_cannot_be_silently_coerced(self):
        for value in [20.0,True,'NaN','Infinity','1e1','0','-20']:
            bundle,p=fixtures.make_fixture();p['measurement']['value']=value
            with self.assertRaises(ValidationError):DimensionEvaluation.model_validate(p)
        for changes in [{'metric':'temperature'},{'source_approval':'approved'},{'measured_at':'yesterday'}]:
            bundle,p=fixtures.make_fixture();p['measurement'].update(changes)
            with self.assertRaises(ValidationError):DimensionEvaluation.model_validate(p)

    def test_inputs_and_decimal_context_are_unchanged_after_gate(self):
        bundle,p=fixtures.make_fixture();before=copy.deepcopy((bundle,p));precision=__import__('decimal').getcontext().prec
        evaluate(bundle,p);self.assertEqual((bundle,p),before);self.assertEqual(__import__('decimal').getcontext().prec,precision)
        p['approvals']=[];before=copy.deepcopy((bundle,p));evaluate(bundle,p);self.assertEqual((bundle,p),before)
