"""Closed-scope selection capture against actual in-memory Qdrant.

The embedding fixture is synthetic. These tests exercise the vector SDK and
multi-publication readback contract, without a database or query-model call.
"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from qdrant_client import QdrantClient,models

from datamind import pdf_vectors as vectors
import test_pdf_vectors as fixtures


class PdfSelectionVectorTests(unittest.TestCase):
    fixture=fixtures.PdfVectorTests.fixture
    error=fixtures.PdfVectorTests.error
    rehash=fixtures.PdfVectorTests.rehash

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='pdf-selection-vectors-')
        self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name).resolve()
        self.client=QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        self.plans=[]
        for position in range(2):
            row,bundle=self.fixture(document_id='selected-'+str(position),block_count=70)
            plan=vectors.build_plan(row,bundle,'pdf-pub-'+uuid.uuid4().hex,fixtures.synthetic_embed)
            self.assertEqual(len(plan['points']),70)
            vectors.write_and_verify(plan,self.client)
            self.plans.append(plan)
        self.collection=self.plans[0]['collection']
        self.selected_ids={point['id'] for plan in self.plans for point in plan['points']}

    def test_140_points_are_captured_in_one_read_with_complete_ids_and_vectors(self):
        with patch.object(self.client,'retrieve',wraps=self.client.retrieve) as retrieve, \
             patch.object(self.client,'upsert',wraps=self.client.upsert) as upsert:
            vectors.verify_selection(self.plans,self.client)
        retrieve.assert_called_once();call=retrieve.call_args
        self.assertEqual(call.args[0],self.collection)
        self.assertEqual(set(call.kwargs['ids']),self.selected_ids)
        self.assertEqual(len(call.kwargs['ids']),140)
        self.assertIs(call.kwargs['with_payload'],True)
        self.assertIs(call.kwargs['with_vectors'],True)
        self.assertEqual(call.kwargs['timeout'],5);upsert.assert_not_called()

    def test_missing_point_from_either_selected_publication_rejects_whole_capture(self):
        missing=self.plans[1]['points'][-1]['id']
        self.client.delete(self.collection,models.PointIdsList(points=[missing]),wait=True)
        with patch.object(self.client,'retrieve',wraps=self.client.retrieve) as retrieve, \
             patch.object(self.client,'upsert',wraps=self.client.upsert) as upsert:
            self.error('PDF_INDEX_VECTOR_MISSING',lambda:vectors.verify_selection(self.plans,self.client))
        retrieve.assert_called_once();upsert.assert_not_called()
        self.assertEqual(self.client.retrieve(self.collection,[missing],with_vectors=True),[])

    def test_selected_payload_tampering_is_rejected_and_never_overwritten(self):
        altered=self.plans[0]['points'][0]['id']
        self.client.overwrite_payload(self.collection,{'acl':['public'],'publication_id':'forged'},[altered])
        with patch.object(self.client,'upsert',wraps=self.client.upsert) as upsert:
            self.error('PDF_INDEX_VECTOR_MISMATCH',lambda:vectors.verify_selection(self.plans,self.client))
        upsert.assert_not_called()
        actual=self.client.retrieve(self.collection,[altered],with_vectors=True)[0]
        self.assertEqual(actual.payload,{'acl':['public'],'publication_id':'forged'})

    def test_selected_vector_tampering_is_rejected_and_never_overwritten(self):
        point=copy.deepcopy(self.plans[1]['points'][0])
        point['vector']=[1.0]+[0.0]*(vectors.VECTOR_DIM-1)
        self.client.upsert(self.collection,[models.PointStruct(**point)],wait=True)
        with patch.object(self.client,'upsert',wraps=self.client.upsert) as upsert:
            self.error('PDF_INDEX_VECTOR_MISMATCH',lambda:vectors.verify_selection(self.plans,self.client))
        upsert.assert_not_called()
        actual=self.client.retrieve(self.collection,[point['id']],with_vectors=True)[0]
        self.assertEqual(actual.vector,point['vector'])

    def test_unselected_and_orphan_points_in_shared_collection_are_never_retrieved(self):
        row,bundle=self.fixture(document_id='unselected-source')
        unselected=vectors.build_plan(row,bundle,'pdf-pub-'+uuid.uuid4().hex,fixtures.synthetic_embed)
        vectors.write_and_verify(unselected,self.client)
        orphan=str(uuid.uuid4())
        self.client.upsert(self.collection,[models.PointStruct(id=orphan,vector=[1.0]+[0.0]*511,
                           payload={'acl':['admin_private'],'publication_id':'unpublished-orphan'})],wait=True)
        with patch.object(self.client,'retrieve',wraps=self.client.retrieve) as retrieve, \
             patch.object(self.client,'query_points',wraps=self.client.query_points) as query:
            vectors.verify_selection(self.plans,self.client)
        retrieve.assert_called_once();query.assert_not_called()
        ids=set(retrieve.call_args.kwargs['ids'])
        self.assertEqual(ids,self.selected_ids)
        self.assertNotIn(orphan,ids);self.assertNotIn(unselected['points'][0]['id'],ids)
        self.assertEqual(self.client.count(self.collection,exact=True).count,142)

    def test_duplicate_publications_and_points_are_rejected_before_sdk_capture(self):
        with patch.object(self.client,'retrieve',wraps=self.client.retrieve) as retrieve:
            caught=self.error('PDF_SEARCH_SELECTION_INVALID',lambda:vectors.verify_selection([self.plans[0],self.plans[0]],self.client))
            self.assertEqual(caught.status_code,422)
            duplicate=copy.deepcopy(self.plans[0])
            duplicate['chunks'].append(copy.deepcopy(duplicate['chunks'][0]))
            duplicate['points'].append(copy.deepcopy(duplicate['points'][0]));self.rehash(duplicate)
            self.error('PDF_INDEX_PLAN_INVALID',lambda:vectors.verify_selection([duplicate,self.plans[1]],self.client))
        retrieve.assert_not_called()

    def test_two_valid_257_chunk_plans_exceed_selection_budget_before_sdk_capture(self):
        plans=[]
        for position in range(2):
            row,bundle=self.fixture(document_id='large-'+str(position),block_count=257)
            plan=vectors.build_plan(row,bundle,'pdf-pub-'+uuid.uuid4().hex,fixtures.synthetic_embed)
            vectors.validate_plan(plan,row,bundle,plan['publication_id'])
            self.assertEqual(len(plan['points']),257);plans.append(plan)
        with patch.object(self.client,'retrieve',wraps=self.client.retrieve) as retrieve:
            caught=self.error('PDF_SEARCH_SELECTION_TOO_LARGE',lambda:vectors.verify_selection(plans,self.client))
        self.assertEqual(caught.status_code,422);retrieve.assert_not_called()

    def test_mixed_complete_profiles_are_rejected_before_sdk_capture(self):
        row,bundle=self.fixture(document_id='different-profile')
        # Explicit adapter fixture: production-marked metadata and synthetic
        # vectors test profile incompatibility, not real model inference.
        def production_fixture(texts):
            return vectors._profile(synthetic=False),list(fixtures.synthetic_embed(texts))
        with patch.object(vectors,'_production_embedding',side_effect=production_fixture):
            other=vectors.build_plan(row,bundle,'pdf-pub-'+uuid.uuid4().hex)
        vectors.validate_plan(other,row,bundle,other['publication_id'])
        self.assertNotEqual(other['profile_sha256'],self.plans[0]['profile_sha256'])
        with patch.object(self.client,'retrieve',wraps=self.client.retrieve) as retrieve:
            self.error('PDF_SEARCH_PROFILE_MISMATCH',lambda:vectors.verify_selection([self.plans[0],other],self.client))
        retrieve.assert_not_called()


if __name__=='__main__': unittest.main()
