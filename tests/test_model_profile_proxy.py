"""F06 (remainder): the proxy in a stored profile must reach the question paths.

Probing already resolved `proxy`, but `settings()`/`model_profile()` dropped it, so a
deployment behind a relay had probing succeed while SQL/RAG/PDF answers silently did not
use the proxy. The transport itself already honours `profile['proxy']`.
"""
from pathlib import Path
import unittest
from unittest.mock import patch

from datamind import providers, rag_engine, sql_engine

ROOT = Path(__file__).resolve().parents[1]
PROXY = 'http://relay.example.com:8080'
STORED = {'api_key': 'k', 'base_url': 'https://relay.example.com/v1', 'model': 'm',
          'protocol': 'openai', 'max_tokens': 100, 'timeout_s': 30, 'auth_style': 'both',
          'headers': {}, 'name': 'relay', 'proxy': PROXY}


def hit():
    return {'chunk_id': 'chunk-1', 'document_id': 'doc-1', 'title': '制度', 'collection': 'demo_kb',
            'origin': 'curated', 'document_version': 1, 'document_sha256': 'a' * 64,
            'content_hash': 'b' * 64, 'parser_version': 'pdf-ocr-0.4', 'quality_detail': {},
            'page_start': 1, 'page_end': 1, 'block_ids': ['blk-1'], 'source_blocks': [],
            'section_path': [], 'retrieval_score': 0.9, 'retrieval_scores': {}, 'file_type': 'pdf',
            'exact_text': '退款需在七个工作日内提出。'}


class StoredProxyChainTests(unittest.TestCase):
    def test_settings_carries_the_stored_proxy(self):
        with patch.object(sql_engine, 'active_profile', return_value=STORED):
            self.assertEqual(sql_engine.settings()['NEW_API_PROXY'], PROXY)

    def test_model_profile_exposes_the_proxy(self):
        with patch.object(sql_engine, 'active_profile', return_value=STORED):
            self.assertEqual(sql_engine.model_profile()['proxy'], PROXY)

    def test_absence_of_a_proxy_is_an_empty_string_not_none(self):
        # make_client does `(profile.get("proxy") or "").strip()`, so None and "" behave
        # the same; the contract is asserted so a caller never has to guess.
        with patch.object(sql_engine, 'active_profile', return_value={}):
            self.assertEqual(sql_engine.model_profile()['proxy'], '')

    def test_environment_proxy_is_still_honoured_without_a_stored_profile(self):
        with patch.object(sql_engine, 'active_profile', return_value={}), \
                patch.dict('os.environ', {'NEW_API_PROXY': 'http://env.example.com:3128'}, clear=False):
            self.assertEqual(sql_engine.model_profile()['proxy'], 'http://env.example.com:3128')


class QuestionPathProxyTests(unittest.TestCase):
    def test_the_rag_draft_passes_the_proxy_to_the_transport(self):
        captured = {}

        def fake_chat(profile, system, user, **kwargs):
            captured['profile'] = profile
            return {'ok': True, 'text': '{"claims": []}'}

        with patch.object(sql_engine, 'active_profile', return_value=STORED), \
                patch.object(rag_engine, 'provider_chat', side_effect=fake_chat):
            rag_engine._draft('退款条件', [hit()])
        self.assertEqual(captured['profile']['proxy'], PROXY)
        self.assertEqual(captured['profile']['base_url'], STORED['base_url'])

    def test_both_question_modules_build_the_profile_from_the_shared_helper(self):
        for module in (rag_engine,):
            source = Path(module.__file__).read_text(encoding='utf-8')
            self.assertIn('model_profile()', source, module.__name__)
        pdf_source = (ROOT / 'datamind/pdf_answer.py').read_text(encoding='utf-8')
        self.assertIn('model_profile()', pdf_source)


class TransportProxyTests(unittest.TestCase):
    """The transport decision, asserted on the arguments it passes to httpx."""

    def client_kwargs(self, url, profile, env=None):
        with patch.dict('os.environ', env or {}, clear=False), \
                patch.object(providers.httpx, 'Client') as client:
            providers.make_client(url, profile, 20)
        return client.call_args.kwargs

    def test_an_explicit_profile_proxy_is_used_for_a_remote_url(self):
        kwargs = self.client_kwargs('https://api.example.com/v1/chat/completions', {'proxy': PROXY})
        self.assertEqual(kwargs['proxy'], PROXY)
        self.assertFalse(kwargs['trust_env'], '显式 proxy 时不应再叠加系统代理')

    def test_a_local_url_stays_direct_even_with_an_explicit_proxy(self):
        # Regression: carrying the proxy into the question paths must not route the
        # localhost gateway through a relay (hang or 502).
        kwargs = self.client_kwargs('http://localhost:4000/v1/chat/completions', {'proxy': PROXY})
        self.assertNotIn('proxy', kwargs)
        self.assertFalse(kwargs['trust_env'])

    def test_a_remote_url_without_a_profile_proxy_still_honours_the_environment(self):
        kwargs = self.client_kwargs('https://api.example.com/v1/chat/completions', {})
        self.assertNotIn('proxy', kwargs)
        self.assertNotIn('trust_env', kwargs, '未指定 proxy 时应继续尊重系统/环境代理')


if __name__ == '__main__':
    unittest.main()
