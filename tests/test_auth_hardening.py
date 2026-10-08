"""Security hardening regressions for the local API.

Covers the four confirmed defects:

* F01 — a probe that names a *different* host than the stored profile must never inherit
  the stored ``api_key`` (or its sensitive headers): it is rejected with
  ``MODEL_KEY_NOT_INHERITED`` before any upstream client is built. Same-source probes and
  explicitly submitted keys keep working.
* F02 — loopback no-login is decided from the transport peer only; ``X-Forwarded-For`` /
  ``X-Real-IP`` / ``Forwarded`` cannot change it, and the start scripts disable uvicorn's
  proxy headers.
* F03 — the expected origin is a fixed allow-list, never derived from ``Host``; sensitive
  reads carry the same origin check as mutations, and ``include_keys=true`` exports are
  admin + same-origin + ``no-store``.
* F04 — the middleware is default-deny: unlisted routes need an identity, mutations also
  need CSRF/origin, so ``/v1/chat``, ``/v1/chat/stream`` and ``/v1/diagnostics/sql`` are
  no longer open.

Everything here uses synthetic credentials only. No real upstream key appears in this
file, in its output, or in any assertion message.
"""
import inspect
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from datamind import api as api_module
from datamind.api import ProfileRequest, app
from datamind.auth import DEMO_CSRF, allowed_origins, demo_allowed, origin_check
from datamind.settings_store import resolve_profile_values

ROOT = Path(__file__).resolve().parents[1]

# ---- synthetic material only (never a real key) -------------------------------------
STORE_KEY = "sk-synthetic-store-key-0001"
REQUEST_KEY = "sk-synthetic-request-key-0002"
ENV_KEY = "sk-synthetic-env-key-0003"
STORED_PROFILE = {
    "id": "p-synth01", "name": "合成配置", "protocol": "openai",
    "base_url": "https://relay.example.com/v1", "model": "synthetic-model",
    "api_key": STORE_KEY,
    "headers": {"Authorization": "Bearer " + STORE_KEY, "X-Custom": "synthetic"},
    "proxy": "http://relayuser:syntheticpass@relay.example.com:8080",
}
EVIL_BASE = "https://evil.example.com/v1"


def make_request(*, method="GET", path="/", headers=None, client=("127.0.0.1", 41800)):
    """A minimal ASGI Request so the auth helpers can be exercised without HTTP."""
    raw = [(str(name).lower().encode(), str(value).encode())
           for name, value in (headers or {}).items()]
    scope = {"type": "http", "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": raw, "client": client, "server": ("testserver", 80)}
    return Request(scope)


class _NoForcedDemo(unittest.TestCase):
    """Unset DATAMIND_LOCAL_NO_LOGIN so loopback demo behaviour is deterministic."""

    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DATAMIND_LOCAL_NO_LOGIN", None)


# ---------------------------------------------------------------------------- F01
class KeyInheritanceTest(unittest.TestCase):
    """F01: no implicit key inheritance towards a caller-chosen host."""

    def _probe_chat(self, provider, **payload):
        with patch.object(api_module, "_select_stored_profile",
                          return_value=dict(STORED_PROFILE)), \
                patch.object(api_module, "provider_test_chat", provider):
            return api_module.probe_chat(ProfileRequest(**payload))

    def test_cross_source_without_key_is_rejected_before_any_request(self):
        provider = Mock(return_value={"ok": True})
        with self.assertRaises(HTTPException) as caught:
            self._probe_chat(provider, base_url=EVIL_BASE)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["error_code"], "MODEL_KEY_NOT_INHERITED")
        provider.assert_not_called()

    def test_models_route_rejects_the_same_way(self):
        provider = Mock(return_value={"ok": True, "models": []})
        with patch.object(api_module, "_select_stored_profile",
                          return_value=dict(STORED_PROFILE)), \
                patch.object(api_module, "provider_list_models", provider):
            with self.assertRaises(HTTPException) as caught:
                api_module.probe_models(ProfileRequest(base_url=EVIL_BASE))
        self.assertEqual(caught.exception.detail["error_code"], "MODEL_KEY_NOT_INHERITED")
        provider.assert_not_called()

    def test_same_source_without_key_still_inherits(self):
        provider = Mock(return_value={"ok": True})
        self._probe_chat(provider, base_url=STORED_PROFILE["base_url"])
        profile = provider.call_args[0][0]
        self.assertEqual(profile["api_key"], STORE_KEY)
        self.assertFalse(profile["key_inheritance_blocked"])

    def test_explicit_key_is_used_and_the_stored_key_is_not(self):
        provider = Mock(return_value={"ok": True})
        self._probe_chat(provider, base_url=EVIL_BASE, api_key=REQUEST_KEY)
        profile = provider.call_args[0][0]
        self.assertEqual(profile["api_key"], REQUEST_KEY)
        self.assertNotIn(STORE_KEY, json.dumps(profile, default=str))

    def test_resolver_is_safe_by_default_and_can_opt_in_to_legacy(self):
        request = ProfileRequest(base_url=EVIL_BASE)
        safe = resolve_profile_values(request, stored=dict(STORED_PROFILE))
        self.assertTrue(safe["key_inheritance_blocked"])
        self.assertEqual(safe["api_key"], "")
        self.assertNotIn("Authorization", safe["headers"])
        legacy = resolve_profile_values(request, stored=dict(STORED_PROFILE),
                                        allow_cross_origin_key=True)
        self.assertFalse(legacy["key_inheritance_blocked"])
        self.assertEqual(legacy["api_key"], STORE_KEY)

    def test_environment_key_is_not_used_for_a_cross_source_probe(self):
        with patch.dict(os.environ, {"NEW_API_KEY": ENV_KEY}):
            safe = resolve_profile_values(ProfileRequest(base_url=EVIL_BASE),
                                          stored=dict(STORED_PROFILE))
        self.assertEqual(safe["api_key"], "")

    def test_port_normalisation_still_counts_as_same_source(self):
        provider = Mock(return_value={"ok": True})
        self._probe_chat(provider, base_url="https://relay.example.com:443/v1")
        self.assertEqual(provider.call_args[0][0]["api_key"], STORE_KEY)
        provider.reset_mock()
        with self.assertRaises(HTTPException):
            self._probe_chat(provider, base_url="https://relay.example.com:8443/v1")


# ---------------------------------------------------------------------------- F02
class ForwardedHeaderIsolationTest(_NoForcedDemo):
    """F02: the no-login decision never consults a forwarding header."""

    def test_loopback_demo_is_unaffected_by_forwarding_headers(self):
        for headers in ({"X-Forwarded-For": "203.0.113.9"},
                        {"X-Real-IP": "203.0.113.9"},
                        {"Forwarded": "for=203.0.113.9;proto=http"},
                        {"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "198.51.100.7",
                         "Forwarded": "for=192.0.2.1"}):
            self.assertTrue(demo_allowed(make_request(headers=headers, client=("127.0.0.1", 41800))),
                            f"转发头改变了本机判定：{headers}")

    def test_remote_peer_cannot_claim_loopback(self):
        for headers in ({"X-Forwarded-For": "127.0.0.1"}, {"X-Real-IP": "::1"},
                        {"Forwarded": "for=127.0.0.1"}):
            self.assertFalse(demo_allowed(make_request(headers=headers,
                                                       client=("203.0.113.9", 41800))),
                             f"远端节点伪造本机地址成功：{headers}")

    def test_explicitly_disabled_no_login_requires_login_everywhere(self):
        with patch.dict(os.environ, {"DATAMIND_LOCAL_NO_LOGIN": "0"}):
            self.assertFalse(demo_allowed(make_request(client=("127.0.0.1", 41800))))

    def test_loopback_demo_http_is_identical_with_and_without_forwarded_headers(self):
        headers = {"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9",
                   "Forwarded": "for=203.0.113.9"}
        with TestClient(app, client=("127.0.0.1", 41800)) as client:
            plain = client.get("/v1/auth/me").json()
            forwarded = client.get("/v1/auth/me", headers=headers).json()
        self.assertEqual(plain["user"]["username"], "local-admin")
        self.assertEqual(forwarded["user"], plain["user"])
        self.assertEqual(forwarded["csrf_token"], plain["csrf_token"])

    def test_remote_peer_cannot_become_demo_admin_over_http(self):
        with TestClient(app, client=("203.0.113.9", 41800)) as client:
            body = client.get("/v1/auth/me",
                              headers={"X-Forwarded-For": "127.0.0.1"}).json()
        self.assertIsNone(body["user"])

    def test_start_scripts_disable_uvicorn_proxy_headers(self):
        for relative in ("scripts/start_local.sh", "scripts/start_api_background.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("--no-proxy-headers", source,
                          f"{relative} 未禁用 uvicorn 代理头，XFF 可再次改写对端地址")


# ---------------------------------------------------------------------------- F03
class FixedOriginAllowListTest(_NoForcedDemo):
    """F03: fixed origin allow-list, never derived from the request's own Host."""

    def test_self_declared_host_cannot_authorise_itself(self):
        request = make_request(headers={"host": "evil.example", "origin": "http://evil.example"})
        with self.assertRaises(HTTPException) as caught:
            origin_check(request)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error_code"], "ORIGIN_NOT_ALLOWED")

    def test_allow_listed_origins_pass(self):
        for origin in sorted(allowed_origins()):
            origin_check(make_request(headers={"host": "anything.invalid", "origin": origin}))

    def test_missing_origin_stays_allowed(self):
        origin_check(make_request(headers={"host": "evil.example"}))

    def test_cross_site_fetch_metadata_is_rejected(self):
        with self.assertRaises(HTTPException):
            origin_check(make_request(headers={"sec-fetch-site": "cross-site"}))

    def test_environment_can_extend_the_allow_list(self):
        with patch.dict(os.environ, {"DATAMIND_ALLOWED_ORIGINS": "https://dev.example"}):
            origin_check(make_request(headers={"origin": "https://dev.example"}))
        with self.assertRaises(HTTPException):
            origin_check(make_request(headers={"origin": "https://dev.example"}))

    def test_sensitive_read_rejects_cross_origin_without_a_session(self):
        with TestClient(app, client=("203.0.113.9", 41800)) as client:
            response = client.get("/v1/model/profiles",
                                  headers={"Host": "evil.example",
                                           "Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["error_code"], "ORIGIN_NOT_ALLOWED")

    def test_sensitive_read_from_an_allowed_origin_still_requires_identity(self):
        with TestClient(app, client=("203.0.113.9", 41800)) as client:
            response = client.get("/v1/model/profiles",
                                  headers={"Origin": "http://127.0.0.1:8000"})
        self.assertEqual(response.status_code, 401)


# ---------------------------------------------------------------------------- F04
class DefaultDenyMutationRoutesTest(_NoForcedDemo):
    """F04: no route is open by omission; the three chat/diagnostic routes are covered."""

    MUTATIONS = (("/v1/chat", {"question": "只读自检"}),
                 ("/v1/chat/stream", {"question": "只读自检"}),
                 ("/v1/diagnostics/sql", None))

    def _post(self, client, path, payload, headers=None):
        return client.post(path, json=payload, headers=headers) if payload is not None \
            else client.post(path, headers=headers)

    def test_the_three_routes_are_not_public(self):
        for path, _ in self.MUTATIONS:
            request = make_request(method="POST", path=path, client=("203.0.113.9", 41800))
            self.assertFalse(api_module._is_public_request(request),
                             f"{path} 仍被当作公开路由")

    def test_anonymous_mutations_are_rejected(self):
        with TestClient(app, client=("203.0.113.9", 41800)) as client:
            for path, payload in self.MUTATIONS:
                response = self._post(client, path, payload)
                self.assertEqual(response.status_code, 401, (path, response.text))
                self.assertEqual(response.json()["detail"]["error_code"], "LOGIN_REQUIRED")

    def test_forged_origin_is_rejected_even_for_the_demo_admin(self):
        headers = {"X-CSRF-Token": DEMO_CSRF, "Origin": "http://evil.example"}
        with TestClient(app, client=("127.0.0.1", 41800)) as client:
            for path, payload in self.MUTATIONS:
                response = self._post(client, path, payload, headers)
                self.assertEqual(response.status_code, 403, (path, response.text))
                self.assertEqual(response.json()["detail"]["error_code"], "ORIGIN_NOT_ALLOWED")

    def test_missing_csrf_is_rejected_for_the_demo_admin(self):
        with TestClient(app, client=("127.0.0.1", 41800)) as client:
            for path, payload in self.MUTATIONS:
                response = self._post(client, path, payload)
                self.assertEqual(response.status_code, 403, (path, response.text))
                self.assertEqual(response.json()["detail"]["error_code"], "CSRF_REQUIRED")

    def test_authenticated_same_origin_mutations_still_run(self):
        agent = Mock()
        agent.run.return_value = {"status": "COMPLETED", "sql_result": {"rows": [[1]]}}
        headers = {"X-CSRF-Token": DEMO_CSRF}
        with TestClient(app, client=("127.0.0.1", 41800)) as client:
            with patch.object(api_module, "DataMindAgent", return_value=agent):
                chat = client.post("/v1/chat", json={"question": "只读自检"}, headers=headers)
                stream = client.post("/v1/chat/stream", json={"question": "只读自检"},
                                     headers=headers)
                diagnostic = client.post("/v1/diagnostics/sql", headers=headers)
        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertEqual(stream.status_code, 200, stream.text)
        self.assertEqual(diagnostic.status_code, 200, diagnostic.text)
        self.assertEqual(diagnostic.json()["status"], "COMPLETED")

    def test_middleware_is_default_deny_not_prefix_gated(self):
        source = inspect.getsource(api_module.identity_permissions)
        self.assertIn("_is_public_request", source)
        self.assertNotIn("if permission or authority_path", source,
                         "中间件又回到「枚举受保护前缀、其余放行」的写法")
        for path in ("/", "/health", "/v1/auth/login"):
            self.assertIn(path, api_module.PUBLIC_PATHS)


# --------------------------------------------------------------- F05/F07 + health
class ExportAndHealthHardeningTest(_NoForcedDemo):
    """Smaller hardening: no-store export, no GET-time identity write, vector probe."""

    def test_export_endpoint_is_admin_same_origin_and_no_store(self):
        with TestClient(app, client=("127.0.0.1", 41800)) as client:
            response = client.get("/v1/model/profiles/export",
                                  headers={"X-CSRF-Token": DEMO_CSRF})
        # The demo identity is an admin, so the request reaches the route; the response
        # must still be marked uncacheable because it may carry key material.
        self.assertIn(response.status_code, (200, 503))
        if response.status_code == 200:
            self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_export_cross_origin_is_rejected(self):
        with TestClient(app, client=("127.0.0.1", 41800)) as client:
            response = client.get("/v1/model/profiles/export",
                                  headers={"X-CSRF-Token": DEMO_CSRF,
                                           "Origin": "http://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_auth_me_does_not_write_on_get(self):
        with patch("datamind.auth.ensure_demo_user",
                   side_effect=AssertionError("GET /v1/auth/me must not write")):
            with TestClient(app, client=("127.0.0.1", 41800)) as client:
                body = client.get("/v1/auth/me").json()
        self.assertEqual(body["user"]["username"], "local-admin")

    def test_qdrant_probe_failure_degrades_health_without_500(self):
        class _Cursor:
            def __init__(self):
                self.results = [(1,), (3,)]

            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return self.results.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        class _Connection:
            def cursor(self, *_args, **_kwargs):
                return _Cursor()

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        with patch.object(api_module.psycopg2, "connect", return_value=_Connection()), \
                patch.object(api_module, "_vector_store_status", return_value="unavailable"):
            body = api_module.health()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "ready")
        self.assertEqual(body["vector_store"], "unavailable")
        self.assertEqual(body["knowledge"], "degraded")
        self.assertEqual(body["document_count"], 3)

    def test_unreachable_qdrant_is_reported_not_raised(self):
        with patch.dict(os.environ, {"DATAMIND_QDRANT_URL": "http://127.0.0.1:1"}):
            self.assertEqual(api_module._vector_store_status(), "unavailable")


if __name__ == "__main__":
    unittest.main()
