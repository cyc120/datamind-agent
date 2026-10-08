"""Anti-regression coverage for the default-deny request authorisation surface.

``POST /v1/chat``, ``POST /v1/chat/stream`` and ``POST /v1/diagnostics/sql`` used to be
reachable anonymously — a forged ``Origin`` still answered 200 and wrote to the database.
The middleware now decides by omission: only ``PUBLIC_PATHS`` plus the public read prefixes
are reachable without an identity, and every other path (including routes added later)
needs one, with ``X-CSRF-Token`` + a same-origin check for mutations.

The assertions below are deliberately behavioural rather than a mirror of the
implementation:

* the *whole* mutating surface the app actually exposes is behind the gate — the only
  public mutation is ``POST /v1/auth/login``, so an allow-list entry is required for any
  other mutating route to be anonymous;
* an *unlisted* mutating path is rejected by the middleware before routing (401, not 404),
  which is what "default deny" means: the answer does not depend on a route being
  enumerated as protected;
* the three routes answer 401 without an identity;
* loopback + the correct CSRF token still works, so the no-login browser path is intact.

No model provider is contacted: ``/v1/chat`` is stubbed, ``/v1/chat/stream`` is stubbed,
and ``/v1/diagnostics/sql`` runs its fixed read-only query. Synthetic fixtures only.
"""
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

import datamind.api as api_module
from datamind.api import app
from datamind.auth import DEMO_CSRF

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: The three routes that motivated the hardening; the mutation surface must cover them.
THREE_ROUTES = ("/v1/chat", "/v1/chat/stream", "/v1/diagnostics/sql")
ANONYMOUS_CLIENT = ("203.0.113.9", 41800)
LOOPBACK_CLIENT = ("127.0.0.1", 54321)


def _request(method, path, client=ANONYMOUS_CLIENT):
    """A minimal ASGI Request so ``_is_public_request``/``socket_host`` can be exercised."""
    scope = {"type": "http", "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [], "client": client, "server": ("testserver", 80)}
    return Request(scope)


def _mutating_routes():
    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = MUTATING_METHODS.intersection(route.methods or ())
        if methods:
            routes.append((route.path, sorted(methods)))
    return routes


def _concrete(path):
    """Replace ``{param}`` placeholders with a literal; the middleware sees this first."""
    return "/".join(part if not part.startswith("{") else "x" for part in path.split("/"))


class _NoForcedDemo(unittest.TestCase):
    """Unset DATAMIND_LOCAL_NO_LOGIN so anonymous vs. loopback is deterministic."""

    def setUp(self):
        self._previous = os.environ.pop("DATAMIND_LOCAL_NO_LOGIN", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._previous is not None:
            os.environ["DATAMIND_LOCAL_NO_LOGIN"] = self._previous


class DefaultDenySurfaceTest(_NoForcedDemo):
    """The gate is an allow-list: everything mutating is denied unless listed public."""

    def test_public_allow_list_exists_and_excludes_the_three_routes(self):
        self.assertIsInstance(api_module.PUBLIC_PATHS, frozenset)
        for path in THREE_ROUTES:
            self.assertNotIn(path, api_module.PUBLIC_PATHS,
                             f"{path} 被误列入公开清单")
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertFalse(api_module._is_public_request(_request(method, path)),
                                 f"{method} {path} 在无身份时仍被当作公开请求")

    def test_the_whole_mutating_surface_is_covered_and_only_login_is_public(self):
        routes = _mutating_routes()
        # The app really does expose the three routes (guards against a renamed/removed path).
        for path in THREE_ROUTES:
            self.assertIn(path, [route_path for route_path, _ in routes],
                          f"{path} 不再是应用里的变更型路由，覆盖断言需要同步更新")
        public_mutations = []
        for path, methods in routes:
            if all(api_module._is_public_request(_request(method, path)) for method in methods):
                public_mutations.append(path)
        # Whitelist semantics: a mutating route is anonymous ONLY by explicit entry in
        # PUBLIC_PATHS. Adding a public mutation therefore forces a deliberate edit here.
        self.assertEqual(sorted(set(public_mutations)), ["/v1/auth/login"],
                         f"出现未显式列入公开清单的变更型路由：{public_mutations}")

    def test_unlisted_mutating_path_is_denied_before_routing(self):
        # A path that matches no route at all still gets 401, not 404: the middleware denies
        # unknown paths by default instead of only guarding an enumerated prefix list.
        with TestClient(app) as client:
            response = client.post("/v1/definitely-not-a-real-mutation", json={})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], "LOGIN_REQUIRED")

    def test_unlisted_read_path_is_denied_but_public_read_prefix_is_not(self):
        with TestClient(app) as client:
            unlisted = client.get("/v1/definitely-not-a-real-read")
            public_read = client.get("/v1/documents/definitely-missing-document")
        # Unlisted reads are inside the gate too (no authentication-free surface by omission).
        self.assertEqual(unlisted.status_code, 401, unlisted.text)
        # A listed public read prefix is reachable anonymously and fails on data, not identity.
        self.assertEqual(public_read.status_code, 404, public_read.text)
        self.assertEqual(public_read.json()["detail"]["error_code"], "DOCUMENT_NOT_FOUND")


class AnonymousMutationRejectionTest(_NoForcedDemo):
    """Without an identity the three routes must not run, let alone write."""

    def test_three_routes_reject_anonymous_http(self):
        with TestClient(app) as client:
            for path in THREE_ROUTES:
                payload = None if path == "/v1/diagnostics/sql" else {"question": "只读自检"}
                with self.subTest(path=path):
                    response = client.post(path, json=payload) if payload is not None \
                        else client.post(path)
                    self.assertIn(response.status_code, (401, 403), response.text)
                    self.assertEqual(response.json()["detail"]["error_code"], "LOGIN_REQUIRED")

    def test_every_mutating_route_denies_an_anonymous_http_request(self):
        with TestClient(app) as client:
            for path, methods in _mutating_routes():
                if path == "/v1/auth/login":
                    continue  # the single allow-listed public mutation
                for method in methods:
                    with self.subTest(path=path, method=method):
                        response = client.request(method, _concrete(path))
                        self.assertIn(response.status_code, (401, 403), response.text)
                        self.assertEqual(response.json()["detail"]["error_code"],
                                         "LOGIN_REQUIRED")

    def test_loopback_demo_still_needs_csrf_and_same_origin(self):
        with TestClient(app, client=LOOPBACK_CLIENT) as client:
            missing = client.post("/v1/chat", json={"question": "只读自检"})
            forged = client.post("/v1/chat", json={"question": "只读自检"},
                                 headers={"X-CSRF-Token": DEMO_CSRF,
                                          "Origin": "http://evil.example"})
        self.assertEqual(missing.status_code, 403, missing.text)
        self.assertEqual(missing.json()["detail"]["error_code"], "CSRF_REQUIRED")
        self.assertEqual(forged.status_code, 403, forged.text)
        self.assertEqual(forged.json()["detail"]["error_code"], "ORIGIN_NOT_ALLOWED")


class LoopbackBrowserPathTest(_NoForcedDemo):
    """The real browser path (loopback peer + CSRF) keeps working after the hardening."""

    def test_diagnostics_fixed_query_runs_on_loopback(self):
        with TestClient(app, client=LOOPBACK_CLIENT) as client:
            response = client.post("/v1/diagnostics/sql",
                                   headers={"X-CSRF-Token": DEMO_CSRF})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "COMPLETED")
        self.assertEqual(body["sql_result"]["rows"], [[997]])

    def test_chat_runs_on_loopback_with_a_stubbed_agent(self):
        agent = Mock()
        agent.run.return_value = {"status": "COMPLETED", "answer": "打桩回答"}
        with TestClient(app, client=LOOPBACK_CLIENT) as client:
            with patch.object(api_module, "DataMindAgent", return_value=agent):
                response = client.post("/v1/chat", json={"question": "回环自检"},
                                       headers={"X-CSRF-Token": DEMO_CSRF})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "COMPLETED")
        agent.run.assert_called_once()
        agent.close.assert_called_once_with()

    def test_chat_stream_runs_on_loopback_with_a_stubbed_agent(self):
        def factory(*, sink):
            agent = Mock()

            def run(question, **scope):
                sink({"event_type": "answer.completed",
                      "output_summary": {"status": "COMPLETED"}})
                return {"status": "COMPLETED"}

            agent.run.side_effect = run
            return agent

        with TestClient(app, client=LOOPBACK_CLIENT) as client:
            with patch.object(api_module, "DataMindAgent", side_effect=factory):
                response = client.post("/v1/chat/stream", json={"question": "回环自检"},
                                       headers={"X-CSRF-Token": DEMO_CSRF})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("answer.completed", response.text)


if __name__ == "__main__":
    unittest.main()
