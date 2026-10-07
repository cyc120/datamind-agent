"""Controlled private model answers: verification, privacy and HTTP contract.

Unit tests inject the retrieval result and the generator, so no database, embedding
model, vector store or upstream provider is required. The HTTP test composes the
established disposable publication fixtures and stubs only the provider reply.
"""
import json
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi import HTTPException

from datamind import api, pdf_answer, pdf_vectors as vectors
from datamind.knowledge import KnowledgeRetriever
import test_pdf_index_api as fixtures


PUBLICATION_ID = 'pdf-pub-' + 'a' * 32
PLAN_SHA = 'b' * 64


def hit(chunk_id, text, title='私有原生文档'):
    return {'chunk_id': chunk_id, 'text': text, 'title': title, 'document_id': 'doc-private-1',
            'document_version': 1, 'publication_id': PUBLICATION_ID, 'page_no': 1,
            'plan_sha256': PLAN_SHA, 'profile_sha256': 'c' * 64, 'source_sha256': 'd' * 64,
            'parser_version': 'pdf-ocr-0.4', 'content_sha256': 'e' * 64, 'bbox': [0, 0, 10, 10],
            'retrieval_score': 0.91, 'source_url': '/v1/pdf-snapshots/doc-private-1/file?version=1',
            'page_url': '/v1/pdf-snapshots/doc-private-1/pages/1?version=1'}


def retrieval(hits, all_covered=True):
    return {'status': 'retrieved' if hits else 'empty', 'question': 'q', 'hits': hits,
            'coverage': {'all_covered': all_covered, 'documents': []},
            'selected_publications': [], 'candidate_chunks': len(hits),
            'public_knowledge_visible': False, 'source_authority_verified': False,
            'answer_generated': False}


def payload(question='这份私有文件给出了什么尺寸？'):
    return {'question': question,
            'publications': [{'publication_id': PUBLICATION_ID, 'expected_plan_sha256': PLAN_SHA}]}


def draft(text, chunk_id, quote):
    return {'abstain': False, 'claims': [{'text': text, 'citations': [{'chunk_id': chunk_id, 'quote': quote}]}]}


class PdfAnswerUnitTests(unittest.TestCase):
    """Generation and verification, with the store and retrieval injected."""

    def run_answer(self, hits, reply, *, all_covered=True, generator=None, reverify=None):
        calls = []
        if generator is None:
            replies = reply if isinstance(reply, list) else [reply]

            def generator(question, evidence, feedback):
                calls.append({'question': question, 'evidence': evidence, 'feedback': feedback})
                item = replies[min(len(calls), len(replies)) - 1]
                if isinstance(item, Exception):
                    raise item
                return item
        with patch.object(pdf_answer, '_capture', return_value=[]), \
                patch.object(pdf_answer, '_reverify', side_effect=reverify) as check:
            result = pdf_answer.answer(payload(), 'synthetic-dsn', generator=generator,
                                       retrieval=lambda *a, **k: retrieval(hits, all_covered))
        return result, calls, check

    def verification_failed(self, result, code):
        # A failed excerpt/number check is retried once, so the final code is the
        # aggregate while the specific cause stays in the attempt log.
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error_code'], 'PDF_ANSWER_VERIFICATION_FAILED', result)
        self.assertIn(code, [attempt['error_code'] for attempt in result['attempts']])

    def test_completes_and_returns_only_private_verified_citations(self):
        text = '该零件的外径标注为 20.04 mm，公差区间为 19.9 至 20.1 mm。'
        target = hit('chunk-' + '1' * 8, text)
        reply = draft('外径标注为 20.04 mm，公差区间为 19.9 至 20.1 mm。',
                      target['chunk_id'], text)
        result, calls, check = self.run_answer([target], reply)

        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['answer_generated'], True)
        self.assertEqual(result['public_knowledge_visible'], False)
        self.assertEqual(result['source_authority_verified'], False)
        self.assertEqual(result['evidence_integrity_rechecked'], True)
        self.assertEqual(len(result['claims']), 1)
        self.assertEqual(result['claims'][0]['verification']['excerpt_matches'], True)
        self.assertEqual(result['claims'][0]['verification']['semantic_entailment'],
                         'not_independently_verified')
        self.assertEqual([c['number'] for c in result['citations']], [1])
        citation = result['citations'][0]
        self.assertEqual(citation['chunk_id'], target['chunk_id'])
        self.assertEqual(citation['publication_id'], PUBLICATION_ID)
        self.assertEqual(citation['plan_sha256'], PLAN_SHA)
        self.assertEqual(citation['source_url'], target['source_url'])
        self.assertIn('[1]', result['answer'])
        self.assertEqual(len(calls), 1)
        check.assert_called_once()
        self.assertEqual(result['retrieval']['answer_generated'], False)

    def test_evidence_passed_to_the_model_holds_only_selected_private_chunks(self):
        target = hit('chunk-' + '2' * 8, '原文只描述这一个私有片段。')
        _, calls, _ = self.run_answer([target], draft('原文只描述这一个私有片段。',
                                                      target['chunk_id'], '原文只描述这一个私有片段。'))
        self.assertEqual([item['chunk_id'] for item in calls[0]['evidence']], [target['chunk_id']])
        self.assertEqual(calls[0]['evidence'][0]['exact_text'], target['text'])

    def test_abstains_when_the_draft_abstains(self):
        target = hit('chunk-' + '3' * 8, '无关原文。')
        result, _, check = self.run_answer([target], {'abstain': True, 'claims': []})
        self.assertEqual(result['status'], 'abstained')
        self.assertEqual(result['error_code'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(result['claims'], [])
        check.assert_not_called()

    def test_empty_evidence_abstains_without_calling_the_model(self):
        result, calls, _ = self.run_answer([], None, generator=Mock(side_effect=AssertionError('no generation')))
        self.assertEqual(result['status'], 'abstained')
        self.assertEqual(result['error_code'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(result['answer_generated'], True)

    def test_incomplete_file_coverage_abstains_before_generation(self):
        target = hit('chunk-' + '4' * 8, '只有一个文件命中。')
        result, calls, _ = self.run_answer([target], None, all_covered=False,
                                           generator=Mock(side_effect=AssertionError('no generation')))
        self.assertEqual(result['status'], 'abstained')
        self.assertEqual(result['error_code'], 'PDF_PRIVATE_EVIDENCE_INCOMPLETE')

    def test_unknown_citation_fails_after_two_attempts(self):
        target = hit('chunk-' + '5' * 8, '原文段落内容足够长。')
        result, calls, _ = self.run_answer([target], draft('结论。', 'chunk-unknown00', '原文段落内容足够长。'))
        self.verification_failed(result, 'RAG_UNKNOWN_CITATION')
        self.assertEqual(len(calls), 2)
        self.assertEqual([a['error_code'] for a in result['attempts']], ['RAG_UNKNOWN_CITATION'] * 2)

    def test_excerpt_must_be_a_contiguous_original_fragment(self):
        target = hit('chunk-' + '6' * 8, '原文写的是外径 20.04 mm。')
        result, _, _ = self.run_answer([target], draft('外径是 30 mm。', target['chunk_id'], '外径是 30 mm。'))
        self.verification_failed(result, 'RAG_EXCERPT_MISMATCH')

    def test_numbers_must_appear_in_the_cited_excerpt_or_title(self):
        text = '原文写的是外径 20.04 mm。'
        target = hit('chunk-' + '7' * 8, text)
        result, _, _ = self.run_answer([target], draft('外径是 20.04 mm，长度 999 mm。', target['chunk_id'], text))
        self.verification_failed(result, 'RAG_UNSUPPORTED_NUMBER')

    def test_upstream_failure_is_reported_without_retry(self):
        target = hit('chunk-' + '8' * 8, '原文段落。')
        result, calls, _ = self.run_answer([target], RuntimeError('MODEL_TIMEOUT'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'MODEL_TIMEOUT')
        self.assertEqual(len(calls), 1)

    def test_second_attempt_can_repair_an_invalid_first_draft(self):
        text = '该批次温度上限为 850 摄氏度。'
        target = hit('chunk-' + '9' * 8, text)
        bad = draft('温度上限为 850 摄氏度。', target['chunk_id'], '不存在的片段')
        good = draft('温度上限为 850 摄氏度。', target['chunk_id'], text)
        result, calls, _ = self.run_answer([target], [bad, good])
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(calls), 2)
        self.assertEqual([a['error_code'] for a in result['attempts']], ['RAG_EXCERPT_MISMATCH'])
        self.assertEqual(result['citations'][0]['quote'], text)

    def test_evidence_change_after_generation_propagates(self):
        target = hit('chunk-' + 'a' * 8, '原文段落内容足够长。')
        reply = draft('原文段落内容足够长。', target['chunk_id'], '原文段落内容足够长。')

        def changed(*args, **kwargs):
            raise HTTPException(409, detail={'error_code': 'PDF_PUBLICATION_EVIDENCE_CHANGED'})

        with self.assertRaises(HTTPException) as caught:
            self.run_answer([target], reply, reverify=changed)
        self.assertEqual(caught.exception.detail['error_code'], 'PDF_PUBLICATION_EVIDENCE_CHANGED')

    def test_repeated_citations_to_one_chunk_merge_their_quotes(self):
        first = '第一段原文内容。'
        second = '第二段原文内容。'
        target = hit('chunk-' + 'b' * 8, first + second)
        reply = {'abstain': False, 'claims': [
            {'text': '第一段结论。', 'citations': [{'chunk_id': target['chunk_id'], 'quote': first}]},
            {'text': '第二段结论。', 'citations': [{'chunk_id': target['chunk_id'], 'quote': second}]}]}
        result, _, _ = self.run_answer([target], reply)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(result['citations']), 1)
        self.assertEqual(result['citations'][0]['quote'], first + '\n' + second)
        self.assertIn('[1]', result['answer'])


SEARCH = '/v1/pdf-knowledge/search'
ANSWER = '/v1/pdf-knowledge/answer'


class PdfAnswerAPITest(unittest.TestCase):
    """HTTP contract against a disposable published private version."""

    clean_fixtures = fixtures.PdfIndexAPITest.clean_fixtures
    client = fixtures.PdfIndexAPITest.client
    prepare_and_register = fixtures.PdfIndexAPITest.prepare_and_register
    publication_payload = fixtures.PdfIndexAPITest.publication_payload
    submit = fixtures.PdfIndexAPITest.submit
    drain = fixtures.PdfIndexAPITest.drain
    successful_publication = fixtures.PdfIndexAPITest.successful_publication
    assert_safe = fixtures.PdfIndexAPITest.assert_safe
    assert_error = fixtures.PdfIndexAPITest.assert_error

    def setUp(self):
        fixtures.PdfIndexAPITest.setUp(self)
        self.query_patch = patch.object(vectors, 'query_embedding', side_effect=self.query_vector)
        self.query_patch.start()
        self.addCleanup(self.query_patch.stop)

    def query_vector(self, question, profile, embedder=None):
        return vectors._normalize(next(fixtures.synthetic_embedder([question])))

    def search_payload(self, publication):
        return {'question': '这份私有原生 PDF 描述了哪些尺寸？',
                'publications': [{'publication_id': publication['publication_id'],
                                  'expected_plan_sha256': publication['plan_sha256']}]}

    def test_answers_only_from_selected_private_evidence(self):
        publication = self.successful_publication()
        body = self.admin.post(SEARCH, json=self.search_payload(publication)).json()
        self.assertEqual(body['status'], 'retrieved', body)
        target = body['hits'][0]
        reply = json.dumps(draft('所选私有文件的原文片段支持这一陈述。',
                                 target['chunk_id'], target['text']))
        with patch.object(pdf_answer, 'provider_chat', return_value={'ok': True, 'text': reply}) as provider, \
                patch.object(api, 'DataMindAgent', side_effect=AssertionError('no public agent')), \
                patch.object(KnowledgeRetriever, 'retrieve', side_effect=AssertionError('no public retrieval')):
            response = self.admin.post(ANSWER, json=self.search_payload(publication))

        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response)
        answer = response.json()
        self.assertEqual(answer['status'], 'completed', answer)
        self.assertEqual(answer['answer_generated'], True)
        self.assertEqual(answer['public_knowledge_visible'], False)
        self.assertEqual(answer['source_authority_verified'], False)
        self.assertEqual(answer['evidence_integrity_rechecked'], True)
        self.assertEqual(answer['retrieval']['answer_generated'], False)
        self.assertGreaterEqual(len(answer['citations']), 1)
        self.assertEqual({c['publication_id'] for c in answer['citations']},
                         {publication['publication_id']})
        self.assertEqual({c['chunk_id'] for c in answer['citations']}, {target['chunk_id']})
        self.assertTrue(answer['citations'][0]['quote'])
        self.assertIn('[1]', answer['answer'])
        provider.assert_called_once()

    def test_answer_requires_an_authenticated_admin(self):
        publication = self.successful_publication()
        body = self.search_payload(publication)
        with patch.object(pdf_answer, 'provider_chat', side_effect=AssertionError('must not generate')):
            anonymous = self.client().post(ANSWER, json=body)
        self.assertIn(anonymous.status_code, (401, 403), anonymous.text)

    def test_invalid_selection_is_rejected_before_generation(self):
        publication = self.successful_publication()
        body = self.search_payload(publication)
        body['publications'][0]['expected_plan_sha256'] = '0' * 64
        with patch.object(pdf_answer, 'provider_chat', side_effect=AssertionError('must not generate')):
            response = self.admin.post(ANSWER, json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['error_code'], 'PDF_INDEX_SELECTION_CHANGED')


if __name__ == '__main__':
    unittest.main()
