"""HTTP checks for the Vue entry point after retiring the legacy frontend.

Use disposable static files; these tests need no database or frontend build.
"""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from datamind import api


class FrontendServingTests(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        directory = self.resources.enter_context(TemporaryDirectory())
        self.vue_dir = Path(directory)
        self.index = self.vue_dir / 'index.html'
        self.html = ('<!doctype html><html><body><div id="app"></div>'
                     '<script src="/assets/vue/assets/app-check.js"></script>'
                     '</body></html>')
        self.index.write_text(self.html, encoding='utf-8')
        asset_dir = self.vue_dir / 'assets'
        asset_dir.mkdir()
        (asset_dir / 'app-check.js').write_text('/* Vue asset check */', encoding='utf-8')
        self.resources.enter_context(patch.object(api, 'VUE_INDEX', self.index))
        static_route = next(route for route in api.app.routes if route.name == 'vue')
        self.resources.enter_context(patch.object(
            static_route, 'app', StaticFiles(directory=self.vue_dir)))
        self.client = self.resources.enter_context(TestClient(api.app))

    def test_home_serves_vue_with_resolvable_asset_urls(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, self.html)
        self.assertIn('text/html', response.headers['content-type'])
        asset = self.client.get('/assets/vue/assets/app-check.js')
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset.text, '/* Vue asset check */')

    def test_retired_assets_are_unavailable(self):
        for filename in ('index.html', 'app.js', 'multifile.js', 'style.css'):
            with self.subTest(filename=filename):
                response = self.client.get('/assets/' + filename)
                self.assertEqual(response.status_code, 404)

    def test_missing_build_returns_actionable_error_without_legacy_fallback(self):
        self.index.unlink()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error_code'], 'FRONTEND_NOT_BUILT')
        self.assertIn('npm run deploy', response.json()['detail'])
        self.assertEqual(self.client.get('/assets/vue/assets/app-check.js').status_code, 200)


if __name__ == '__main__':
    unittest.main()
