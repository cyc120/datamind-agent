"""F05 读取侧残留回归：GET /v1/model/profiles 不再下发凭据 + 保存侧键级合并。

覆盖点：
  * ``public_profile()``（以及 ``GET /v1/model/profiles``）的 JSON 文本里不出现
    已存的 ``Authorization`` 头值、``api_key``、proxy 的 userinfo；敏感头只回显
    键名（``header_keys``），非敏感头照旧回显键值；
  * 保存合并语义：敏感头缺失 / 值为空 = 保持已存值不变，显式非空新值 = 替换，
    非敏感头保持旧的覆盖语义；proxy 提交脱敏视图或空值都不会清掉 userinfo；
  * 「浏览器只能看到脱敏视图」的往返（read → 原样回传 → save）不会抹掉真实凭据；
  * ``web/app.js`` 的「留空表示保持不变」提交逻辑（结构化断言）+ ``node --check``。

全部用例不连数据库、不访问网络：DB 访问被内存桩替换，前端只做静态断言。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from datamind import api as api_module
from datamind import settings_store
from datamind.settings_store import CLEAR_SENTINEL, PROFILES_KEY

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"

SECRET_KEY = "sk-live-read-side-0123456789"
AUTH_VALUE = "Bearer " + SECRET_KEY
COOKIE_VALUE = "session=readside-secret"
PROXY_USER = "relayuser"
PROXY_PASSWORD = "proxy-pass-123"
PROXY = f"http://{PROXY_USER}:{PROXY_PASSWORD}@relay.example.com:8080"
REDACTED_PROXY = "http://relay.example.com:8080"

STORED = {
    "id": "p-readside1",
    "name": "含凭据的接入配置",
    "protocol": "openai",
    "base_url": "https://relay.example.com/v1",
    "model": "deepseek-chat",
    "api_key": SECRET_KEY,
    "max_tokens": 4096,
    "timeout_s": 30.0,
    "auth_style": "both",
    "headers": {
        "Authorization": AUTH_VALUE,
        "x-api-key": SECRET_KEY,
        "Cookie": COOKIE_VALUE,
        "X-Custom-Header": "keep-me",
    },
    "proxy": PROXY,
    "note": "读取侧与导出都不得带出这些认证材料",
    "updated_at": "2026-10-08T00:00:00+00:00",
}


class _FakeMetaStore:
    """最小内存桩：只替换 settings_store 的 DB 读写，不连真实数据库。"""

    def __init__(self, items):
        self.rows = {PROFILES_KEY: {
            "active": items[0]["id"] if items else None,
            "items": [json.loads(json.dumps(item)) for item in items],
        }}

    def read(self, conn, key):
        return self.rows.get(key)

    def write(self, conn, key, value):
        self.rows[key] = value

    def items(self):
        return self.rows[PROFILES_KEY]["items"]

    def item(self):
        return self.items()[0]

    def save(self, raw):
        with patch.object(settings_store.psycopg2, "connect", return_value=MagicMock()), \
                patch.object(settings_store, "_ensure_schema", lambda conn: None), \
                patch.object(settings_store, "_read", side_effect=self.read), \
                patch.object(settings_store, "_write", side_effect=self.write):
            return settings_store.save_profile(raw)


class PublicProfileReadSideTest(unittest.TestCase):
    """读取侧：public_profile() / GET /v1/model/profiles 不下发凭据值。"""

    def test_json_text_has_no_credentials(self):
        text = json.dumps(settings_store.public_profile(STORED), ensure_ascii=False)
        for secret in (SECRET_KEY, AUTH_VALUE, COOKIE_VALUE, PROXY_PASSWORD, PROXY_USER):
            self.assertNotIn(secret, text, f"读取视图仍残留凭据：{secret}")
        self.assertNotIn("api_key", text, "读取视图不应出现 api_key 字段")

    def test_sensitive_headers_echo_keys_only(self):
        view = settings_store.public_profile(STORED)
        # 键名可见（表单据此提示"已配置"），值一律不下发
        for name in ("Authorization", "x-api-key", "Cookie"):
            self.assertIn(name, view["header_keys"], f"敏感头键名应回显：{name}")
        self.assertNotIn("Authorization", view["headers"])
        self.assertNotIn("x-api-key", view["headers"])
        self.assertNotIn("Cookie", view["headers"])
        self.assertEqual(view["headers"], {"X-Custom-Header": "keep-me"})
        self.assertNotIn("X-Custom-Header", view["header_keys"])

    def test_proxy_userinfo_stripped_from_read_view(self):
        view = settings_store.public_profile(STORED)
        self.assertEqual(view["proxy"], REDACTED_PROXY)
        self.assertNotIn("@", view["proxy"])
        self.assertTrue(view["has_proxy"])
        self.assertTrue(view["proxy_has_credentials"])

    def test_api_key_only_masked(self):
        view = settings_store.public_profile(STORED)
        self.assertTrue(view["has_key"])
        self.assertNotIn(SECRET_KEY, view["key_masked"])
        self.assertIn("…", view["key_masked"])

    def test_raw_header_text_never_echoes_values(self):
        view = settings_store.public_profile(
            {"headers": {"__raw__": f"Authorization: {AUTH_VALUE}\nX-Ok: 1"}})
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(SECRET_KEY, text)
        self.assertIn("Authorization", view["header_keys"])
        self.assertEqual(view["headers"], {"X-Ok": "1"})

    def test_profiles_endpoint_uses_redacted_view(self):
        """GET /v1/model/profiles 走同一份读取视图。"""
        state = {"active": STORED["id"], "items": [dict(STORED)]}
        with patch.object(settings_store, "list_profiles", return_value=state):
            body = api_module.get_profiles()
        text = json.dumps(body, ensure_ascii=False)
        for secret in (SECRET_KEY, AUTH_VALUE, COOKIE_VALUE, PROXY_PASSWORD):
            self.assertNotIn(secret, text, f"列表接口仍残留凭据：{secret}")
        item = body["items"][0]
        self.assertIn("Authorization", item["header_keys"])
        self.assertNotIn("Authorization", item["headers"])
        self.assertEqual(item["proxy"], REDACTED_PROXY)
        self.assertTrue(item["active"])

    def test_export_without_keys_uses_the_same_redaction(self):
        state = {"active": STORED["id"], "items": [dict(STORED)]}
        with patch.object(api_module, "list_profiles", return_value=state):
            body = api_module.export_profiles(include_keys=False)
        text = json.dumps(body, ensure_ascii=False)
        for secret in (SECRET_KEY, AUTH_VALUE, COOKIE_VALUE, PROXY_PASSWORD):
            self.assertNotIn(secret, text)
        self.assertNotIn("api_key", body["profiles"][0])

    def test_export_with_keys_stays_an_explicit_migration_path(self):
        """include_keys=True 是刻意的显式索取密钥路径（带密钥搬迁），保持不变。"""
        state = {"active": STORED["id"], "items": [dict(STORED)]}
        with patch.object(api_module, "list_profiles", return_value=state):
            body = api_module.export_profiles(include_keys=True)
        self.assertTrue(body["include_keys"])
        self.assertEqual(body["profiles"][0]["api_key"], SECRET_KEY)


class SaveMergeSemanticsTest(unittest.TestCase):
    """保存侧：敏感头 / proxy 的键级合并，空值不得覆盖真实凭据。"""

    def setUp(self):
        self.store = _FakeMetaStore([STORED])
        self.pid = STORED["id"]

    def headers(self):
        return self.store.item()["headers"]

    def test_omitted_sensitive_header_keeps_stored_value(self):
        self.store.save({"id": self.pid, "headers": {"X-Custom-Header": "updated"}})
        self.assertEqual(self.headers()["Authorization"], AUTH_VALUE)
        self.assertEqual(self.headers()["x-api-key"], SECRET_KEY)
        self.assertEqual(self.headers()["Cookie"], COOKIE_VALUE)
        self.assertEqual(self.headers()["X-Custom-Header"], "updated")

    def test_blank_sensitive_header_keeps_stored_value(self):
        self.store.save({"id": self.pid, "headers": {"Authorization": "   "}})
        self.assertEqual(self.headers()["Authorization"], AUTH_VALUE)

    def test_new_non_empty_value_replaces(self):
        self.store.save({"id": self.pid, "headers": {"Authorization": "Bearer rotated"}})
        self.assertEqual(self.headers()["Authorization"], "Bearer rotated")
        self.assertEqual(self.headers()["x-api-key"], SECRET_KEY, "未提交的敏感头应保留")

    def test_clear_sentinel_is_the_only_removal_channel(self):
        self.store.save({"id": self.pid, "headers": {"Authorization": CLEAR_SENTINEL}})
        self.assertNotIn("Authorization", self.headers())
        self.assertEqual(self.headers()["x-api-key"], SECRET_KEY)

    def test_absent_headers_field_keeps_stored_headers(self):
        self.store.save({"id": self.pid, "note": "只改备注"})
        self.assertEqual(self.headers(), STORED["headers"])

    def test_non_sensitive_headers_keep_replace_semantics(self):
        self.store.save({"id": self.pid, "headers": {"X-New": "1"}})
        self.assertNotIn("X-Custom-Header", self.headers(), "非敏感头仍按提交内容整体替换")
        self.assertEqual(self.headers()["X-New"], "1")
        self.assertEqual(self.headers()["Authorization"], AUTH_VALUE, "敏感头必须保留")

    def test_proxy_redacted_view_keeps_userinfo(self):
        self.store.save({"id": self.pid, "proxy": REDACTED_PROXY})
        self.assertEqual(self.store.item()["proxy"], PROXY)

    def test_proxy_blank_and_absent_keep_userinfo(self):
        self.store.save({"id": self.pid, "proxy": "   "})
        self.assertEqual(self.store.item()["proxy"], PROXY)
        self.store.save({"id": self.pid, "note": "不动代理"})
        self.assertEqual(self.store.item()["proxy"], PROXY)

    def test_proxy_new_value_replaces(self):
        self.store.save({"id": self.pid, "proxy": "http://mirror.internal:9090"})
        self.assertEqual(self.store.item()["proxy"], "http://mirror.internal:9090")

    def test_proxy_clear_sentinel_clears(self):
        self.store.save({"id": self.pid, "proxy": CLEAR_SENTINEL})
        self.assertEqual(self.store.item()["proxy"], "")

    def test_browser_round_trip_never_erases_credentials(self):
        """浏览器只能拿到脱敏视图；把它原样回传保存，真实凭据必须活下来。"""
        view = settings_store.public_profile(self.store.item())
        self.store.save({"id": self.pid, "name": view["name"], "model": view["model"],
                         "headers": view["headers"], "proxy": view["proxy"]})
        self.assertEqual(self.headers()["Authorization"], AUTH_VALUE)
        self.assertEqual(self.headers()["x-api-key"], SECRET_KEY)
        self.assertEqual(self.headers()["Cookie"], COOKIE_VALUE)
        self.assertEqual(self.store.item()["proxy"], PROXY)

    def test_saved_headers_still_contain_no_public_leak(self):
        """合并后写回库里的仍是真实凭据（服务端保留），而读取视图依旧脱敏。"""
        self.store.save({"id": self.pid, "headers": {"X-Custom-Header": "x"}})
        text = json.dumps(settings_store.public_profile(self.store.item()), ensure_ascii=False)
        self.assertNotIn(AUTH_VALUE, text)
        self.assertEqual(self.headers()["Authorization"], AUTH_VALUE)


class FrontendCommitSemanticsTest(unittest.TestCase):
    """web/app.js：留空表示保持不变，改动才回传。"""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def _function_body(self, name):
        marker = "function " + name + "("
        start = self.source.index(marker)
        brace = self.source.index("{", start)
        depth = 0
        for index in range(brace, len(self.source)):
            char = self.source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return self.source[brace:index + 1]
        self.fail("未找到函数 " + name)

    def test_blank_means_keep_hint_is_present(self):
        self.assertIn("留空表示保持不变", self.source)

    def test_form_renders_sensitive_header_key_inputs(self):
        body = self._function_body("renderSecretHeaders")
        self.assertIn("header_keys", self.source)
        self.assertIn("data-header-name", body)
        self.assertIn("input.placeholder = KEEP_HINT", body)   # 空输入框 + 留空提示
        self.assertNotIn("input.value =", body, "敏感头输入框不能预填任何值")
        # 提交侧读取同一批输入框
        self.assertIn("data-header-name", self._function_body("collectHeaders"))

    def test_form_payload_does_not_resend_blank_secrets(self):
        body = self._function_body("formPayload")
        self.assertIn("collectHeaders()", body)
        self.assertIn("proxyFieldValue()", body)
        self.assertNotIn("proxy: $('proxy').value.trim()", self.source,
                         "proxy 不能再无条件回传（会覆盖库里的 userinfo）")
        self.assertNotIn("payload.headers = JSON.parse", body,
                         "headers 必须经 collectHeaders 合并后再提交")

    def test_blank_sensitive_header_is_skipped_client_side(self):
        body = self._function_body("collectHeaders")
        self.assertIn("if (!value) { return; }", body)
        self.assertIn("if (!edited && !typed) { return null; }", body)

    def test_proxy_uses_displayed_value_to_detect_changes(self):
        body = self._function_body("proxyFieldValue")
        self.assertIn("STATE.proxyDisplayed", body)
        self.assertIn("return null", body)
        self.assertIn("CLEAR_TOKEN", self.source)

    def test_replacing_a_credentialed_proxy_warns(self):
        body = self._function_body("proxyDropsCredentials")
        self.assertIn("proxyHasCredentials", body)
        self.assertIn("proxyDropsCredentials(payload)", self._function_body("saveProfile"))

    def test_app_js_is_syntactically_valid(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用，跳过语法检查")
        result = subprocess.run([node, "--check", str(APP_JS)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
