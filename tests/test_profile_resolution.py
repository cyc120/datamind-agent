"""F05/F06 回归：profile 无密钥脱敏 + 统一解析入口。

覆盖点：
  * F05：``redact_profile()`` 之后不再出现 api_key / 口令 / Authorization 头值；
    导出端点（include_keys=False）实际走脱敏函数。
  * F06：探活与模型列表共用 ``datamind.api.resolve_profile()``，同一请求解析一致
    （含 proxy）。

全部用例不发起真实网络请求，也不依赖数据库（stored 显式注入）。
"""
import inspect
import json
import unittest
from unittest.mock import patch

from datamind import api as api_module
from datamind.api import ProfileRequest, resolve_profile
from datamind.settings_store import redact_profile

SECRET_KEY = "sk-live-super-secret-0123456789"
AUTH_HEADER_VALUE = "Bearer " + SECRET_KEY
PROXY_PASSWORD = "p@ssw0rd-hunter2"
PROXY_USER = "relayuser"
RAW_PROFILE = {
    "id": "p-secret01",
    "name": "含认证材料的配置",
    "protocol": "openai",
    "base_url": "https://relay.example.com/v1",
    "model": "deepseek-chat",
    "api_key": SECRET_KEY,
    "max_tokens": 4096,
    "timeout_s": 30.0,
    "auth_style": "both",
    "headers": {
        "Authorization": AUTH_HEADER_VALUE,
        "x-api-key": SECRET_KEY,
        "Cookie": "session=abc123",
        "X-Custom-Header": "keep-me",
    },
    "proxy": f"http://{PROXY_USER}:{PROXY_PASSWORD}@relay.example.com:8080",
    "note": "导出时不能带出任何认证材料",
}


class RedactProfileTest(unittest.TestCase):
    """F05：无密钥导出必须不含任何认证材料。"""

    def test_redacted_json_has_no_credentials(self):
        redacted = redact_profile(RAW_PROFILE)
        text = json.dumps(redacted, ensure_ascii=False)

        for secret in (SECRET_KEY, PROXY_PASSWORD, AUTH_HEADER_VALUE):
            self.assertNotIn(secret, text, f"脱敏结果仍残留：{secret}")
        self.assertNotIn(PROXY_USER, text, "代理用户名仍残留")
        self.assertNotIn("Authorization", text, "敏感头名仍被导出")
        self.assertNotIn("x-api-key", text.lower(), "敏感头名仍被导出")
        self.assertNotIn("cookie", text.lower(), "敏感头名仍被导出")

    def test_fields_removed_and_replaced(self):
        redacted = redact_profile(RAW_PROFILE)
        # api_key 整体移除（不是掩码，导出里不再出现该字段）
        self.assertNotIn("api_key", redacted)
        # 非敏感头保留，敏感头被剔除
        self.assertEqual(redacted["headers"], {"X-Custom-Header": "keep-me"})
        # proxy 去掉 userinfo，主机与端口保留
        self.assertNotIn("@", redacted["proxy"])
        self.assertIn("relay.example.com:8080", redacted["proxy"])
        # 其余非敏感字段原样保留
        self.assertEqual(redacted["base_url"], RAW_PROFILE["base_url"])
        self.assertEqual(redacted["model"], RAW_PROFILE["model"])

    def test_does_not_mutate_input(self):
        original = json.loads(json.dumps(RAW_PROFILE))
        redact_profile(RAW_PROFILE)
        self.assertEqual(RAW_PROFILE, original, "脱敏函数不应修改入参")

    def test_raw_headers_text_is_scrubbed(self):
        payload = dict(RAW_PROFILE, headers={"__raw__": f"Authorization: {AUTH_HEADER_VALUE}\nX-Ok: 1"})
        redacted = redact_profile(payload)
        text = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn(SECRET_KEY, text)
        self.assertNotIn("Authorization", text)

    def test_unparseable_proxy_with_userinfo_is_dropped(self):
        payload = dict(RAW_PROFILE, proxy=f"{PROXY_USER}:{PROXY_PASSWORD}@host:3128")
        redacted = redact_profile(payload)
        self.assertNotIn(PROXY_PASSWORD, json.dumps(redacted, ensure_ascii=False))

    def test_export_endpoint_uses_redaction(self):
        """导出端点必须走 redact_profile：无密钥导出不含认证材料。"""
        state = {"active": "p-secret01", "items": [dict(RAW_PROFILE)]}
        with patch.object(api_module, "list_profiles", return_value=state):
            body = api_module.export_profiles(include_keys=False)
        text = json.dumps(body, ensure_ascii=False)
        for secret in (SECRET_KEY, PROXY_PASSWORD, AUTH_HEADER_VALUE):
            self.assertNotIn(secret, text)
        self.assertNotIn("api_key", body["profiles"][0])

    def test_export_with_keys_is_explicit_bypass(self):
        """include_keys=True 是显式搬迁导出，仍带密钥（保持既有语义）。"""
        state = {"active": "p-secret01", "items": [dict(RAW_PROFILE)]}
        with patch.object(api_module, "list_profiles", return_value=state):
            body = api_module.export_profiles(include_keys=True)
        self.assertEqual(body["profiles"][0]["api_key"], SECRET_KEY)


class ResolveProfileTest(unittest.TestCase):
    """F06：api.py 内 profile 解析统一为 resolve_profile。"""

    def _request(self, **overrides):
        payload = {"profile_id": "p-secret01"}
        payload.update(overrides)
        return ProfileRequest(**payload)

    def test_single_resolver_definition(self):
        source = inspect.getsource(api_module)
        self.assertEqual(source.count("def resolve_profile("), 1)
        # 旧的第二套解析函数必须消失
        self.assertNotIn("def _profile_from_request(", source)

    def test_request_based_entries_call_resolve_profile(self):
        for entry in (api_module.probe_models, api_module.probe_chat):
            self.assertIn("resolve_profile(", inspect.getsource(entry),
                          f"{entry.__name__} 未使用统一解析函数")

    def test_same_request_same_result_including_proxy(self):
        request = self._request()
        stored = dict(RAW_PROFILE)
        first = resolve_profile(request, stored=stored)
        second = resolve_profile(request, stored=stored)
        self.assertEqual(first, second)
        self.assertEqual(first["proxy"], RAW_PROFILE["proxy"])
        self.assertEqual(first["headers"], RAW_PROFILE["headers"])
        self.assertEqual(first["api_key"], SECRET_KEY)

    def test_probe_and_model_list_resolve_identically(self):
        """同一请求下，探活与模型列表解析出的 profile 必须一致（含 proxy）。"""
        request = self._request()
        stored = dict(RAW_PROFILE)
        captured = []
        real = api_module.resolve_profile

        def spy(req=None, *, stored=None):  # noqa: A002 - 与真实签名一致
            profile = real(req, stored=dict(RAW_PROFILE))
            captured.append(profile)
            return profile

        with patch.object(api_module, "resolve_profile", side_effect=spy), \
                patch.object(api_module, "provider_list_models",
                             return_value={"ok": True, "models": []}), \
                patch.object(api_module, "provider_test_chat",
                             return_value={"ok": True, "reply": "可用"}):
            api_module.probe_models(request)
            api_module.probe_chat(request)

        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0], captured[1])
        self.assertEqual(captured[0]["proxy"], RAW_PROFILE["proxy"])
        self.assertEqual(captured[0]["headers"]["Authorization"], AUTH_HEADER_VALUE)
        _ = stored  # 仅为可读性保留；真实解析由 spy 注入 stored

    def test_request_overrides_what_it_submits_and_inherits_stored_secret_headers(self):
        """A request header map replaces the non-secret headers it names.

        This assertion used to require *wholesale* replacement, which is precisely the
        defect: editing a non-secret header and probing dropped the stored
        Authorization / api-key / Cookie and got a 401 from the relay. Inheriting the
        stored secret headers is now unconditional — it must not depend on whether a
        caller injected ``stored``, because that turns one logical operation into two
        different meanings. Deleting a secret header has its own channel (``__clear__``).
        """
        request = self._request(proxy="http://mirror.internal:9090",
                                headers={"X-Custom-Header": "override"})
        profile = resolve_profile(request, stored=dict(RAW_PROFILE))
        self.assertEqual(profile["proxy"], "http://mirror.internal:9090")
        self.assertEqual(profile["headers"]["X-Custom-Header"], "override")
        # 未提交的敏感头必须从已存配置补回，而不是被整段替换掉。
        self.assertEqual(profile["headers"]["Authorization"], AUTH_HEADER_VALUE)
        self.assertEqual(profile["headers"]["x-api-key"], SECRET_KEY)
        self.assertEqual(profile["headers"]["Cookie"], "session=abc123")
        # 已存但没有出现在请求里的非敏感头同样保留（本次请求只改了一个键）。
        self.assertEqual(profile["headers"]["X-Custom-Header"], "override")

    def test_resolve_without_request_keeps_stored_proxy(self):
        profile = resolve_profile(stored=dict(RAW_PROFILE))
        self.assertEqual(profile["proxy"], RAW_PROFILE["proxy"])
        self.assertEqual(profile["api_key"], SECRET_KEY)


if __name__ == "__main__":
    unittest.main()
