"""模型接入：协议适配、配置管理、掩码与探活回归。"""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from datamind import providers
from datamind.api import app
from datamind.settings_store import (PROFILE_FIELDS, activate_profile, clear_model_config,
                                     delete_profile, list_profiles, load_model_config,
                                     mask_key, normalize_base_url, save_profile)
from datamind.sql_engine import ask, settings


class ProviderProtocolTest(unittest.TestCase):
    def test_endpoint_composition(self):
        cases = [("https://api.deepseek.com/v1", "openai", "chat", "https://api.deepseek.com/v1/chat/completions"),
                 ("https://api.deepseek.com", "openai", "chat", "https://api.deepseek.com/v1/chat/completions"),
                 ("https://api.anthropic.com", "anthropic", "chat", "https://api.anthropic.com/v1/messages"),
                 ("https://api.anthropic.com/v1/messages", "anthropic", "chat", "https://api.anthropic.com/v1/messages"),
                 ("https://open.bigmodel.cn/api/paas/v4", "openai", "chat",
                  "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
                 ("https://ark.cn-beijing.volces.com/api/v3", "openai", "chat",
                  "https://ark.cn-beijing.volces.com/api/v3/chat/completions"),
                 ("https://api.anthropic.com", "anthropic", "models", "https://api.anthropic.com/v1/models")]
        for base, protocol, resource, expected in cases:
            self.assertEqual(providers.endpoint(base, protocol, resource), expected)

    def test_anthropic_payload_and_headers(self):
        profile = {"protocol": "anthropic", "model": "claude-sonnet-4-5", "api_key": "sk-ant-1",
                   "max_tokens": 4096, "auth_style": "x-api-key", "base_url": "https://api.anthropic.com"}
        payload = providers.build_chat_payload(profile, "系统提示", "用户问题")
        self.assertEqual(payload["system"], "系统提示")
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "用户问题"}])
        headers = providers.headers_for(profile)
        self.assertEqual(headers["x-api-key"], "sk-ant-1")
        self.assertEqual(headers["anthropic-version"], providers.ANTHROPIC_VERSION)
        self.assertNotIn("Authorization", headers)
        bearer = providers.headers_for({**profile, "auth_style": "bearer"})
        self.assertEqual(bearer["Authorization"], "Bearer sk-ant-1")
        self.assertNotIn("x-api-key", bearer)

    def test_anthropic_max_tokens_is_required(self):
        profile = {"protocol": "anthropic", "model": "m", "api_key": "k", "max_tokens": 0}
        self.assertEqual(providers.build_chat_payload(profile, None, "hi")["max_tokens"],
                         providers.DEFAULT_MAX_TOKENS)

    def test_openai_payload_omits_unset_max_tokens(self):
        profile = {"protocol": "openai", "model": "deepseek-chat", "api_key": "sk-1", "max_tokens": 0}
        payload = providers.build_chat_payload(profile, "sys", "hi")
        self.assertNotIn("max_tokens", payload)
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])

    def test_response_parsing(self):
        anthropic = {"content": [{"type": "text", "text": '{"sql": "SELECT 1"}'}]}
        self.assertEqual(providers.parse_chat_text("anthropic", anthropic), '{"sql": "SELECT 1"}')
        openai = {"choices": [{"message": {"content": "答案", "reasoning_content": "思考"}}]}
        self.assertEqual(providers.parse_chat_text("openai", openai), "答案")
        reasoning_only = {"choices": [{"message": {"content": "", "reasoning_content": "思考中"}}]}
        self.assertEqual(providers.parse_chat_text("openai", reasoning_only), "")
        self.assertEqual(providers.parse_chat_text("openai", reasoning_only, allow_reasoning=True), "思考中")
        self.assertEqual(providers.parse_models({"data": [{"id": "b"}, {"id": "a"}]}), ["a", "b"])

    def test_local_hosts_bypass_system_proxy(self):
        """macOS/环境代理不应劫持 localhost 网关，否则会出现挂起或 502。"""
        env = {"HTTP_PROXY": "http://127.0.0.1:7897", "HTTPS_PROXY": "http://127.0.0.1:7897"}
        with patch.dict(os.environ, env, clear=False):
            local = providers.make_client("http://localhost:4000/v1/chat/completions", {}, 20)
            remote = providers.make_client("https://api.deepseek.com/v1/chat/completions", {}, 20)
            explicit = providers.make_client("https://api.deepseek.com/v1/chat/completions",
                                             {"proxy": "http://127.0.0.1:9999"}, 20)
        try:
            patterns = [str(key.pattern) for key in local._mounts]
            self.assertEqual(patterns, [], "本机地址必须直连，不能挂任何代理")
            self.assertTrue(providers.is_local("http://127.0.0.1:4000/v1"))
            self.assertTrue(providers.is_local("http://localhost:4000/v1"))
            self.assertFalse(providers.is_local("https://api.deepseek.com/v1"))
            self.assertTrue([k for k in remote._mounts], "远端应继续尊重系统/环境代理")
            self.assertIn("all://", [str(k.pattern) for k in explicit._mounts])
        finally:
            local.close(); remote.close(); explicit.close()

    def test_status_classification(self):
        cases = [(401, '{"error":{"message":"invalid api key"}}', "MODEL_AUTH_FAILED"),
                 (403, '{"error":{"message":"Insufficient Balance"}}', "MODEL_BALANCE_EMPTY"),
                 (402, "{}", "MODEL_BALANCE_EMPTY"),
                 (429, "{}", "MODEL_RATE_LIMITED"),
                 (404, '{"error":{"message":"model not found"}}', "MODEL_NOT_FOUND"),
                 (504, "{}", "MODEL_TIMEOUT"),
                 (500, "boom", "MODEL_UPSTREAM_ERROR")]
        for status, body, expected in cases:
            self.assertEqual(providers.classify_status(status, body)[0], expected)


class ProfileAPITest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        from auth_fixtures import login
        login(self.client,roles=('admin',))
        self.state = list_profiles()

    def tearDown(self):
        current = list_profiles()
        keep = {item["id"] for item in self.state["items"]}
        for item in current["items"]:
            if item["id"] not in keep:
                delete_profile(item["id"])
        for item in self.state["items"]:
            save_profile({field: item.get(field) for field in PROFILE_FIELDS} | {"id": item["id"]})
        if self.state.get("active"):
            activate_profile(self.state["active"])
        self.client.close()
        from auth_fixtures import cleanup
        cleanup(self.client)

    def create(self, **overrides):
        payload = {"name": "测试配置", "protocol": "anthropic", "base_url": "https://api.anthropic.com",
                   "model": "claude-sonnet-4-5", "api_key": "sk-ant-secret-987654321"}
        payload.update(overrides)
        response = self.client.post("/v1/model/profiles", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_create_masks_key_and_lists(self):
        created = self.create()
        self.assertTrue(created["has_key"])
        self.assertNotIn("sk-ant-secret-987654321", str(created))
        listing = self.client.get("/v1/model/profiles").json()
        entry = next(item for item in listing["items"] if item["id"] == created["id"])
        self.assertEqual(entry["protocol"], "anthropic")
        self.assertNotIn("sk-ant-secret-987654321", self.client.get("/v1/model/profiles").text)
        self.assertTrue(any(p["id"] == "anthropic" for p in listing["protocols"]))

    def test_update_keeps_key_when_blank(self):
        created = self.create()
        response = self.client.put("/v1/model/profiles/" + created["id"],
                                   json={"name": "改名后", "model": "claude-opus-4-1", "api_key": ""})
        body = response.json()
        self.assertEqual(body["name"], "改名后")
        self.assertEqual(body["model"], "claude-opus-4-1")
        self.assertTrue(body["has_key"], "留空不应清空密钥")

    def test_activate_switches_active_profile_and_engine(self):
        created = self.create(name="Anthropic 切换测试")
        self.client.post("/v1/model/profiles/" + created["id"] + "/activate")
        listing = self.client.get("/v1/model/profiles").json()
        self.assertEqual(listing["active"], created["id"])
        cfg = settings()
        self.assertEqual(cfg["NEW_API_PROTOCOL"], "anthropic")
        self.assertEqual(cfg["NEW_API_MODEL"], "claude-sonnet-4-5")
        self.assertEqual(cfg["NEW_API_BASE_URL"], "https://api.anthropic.com")
        self.assertEqual(self.client.get("/v1/model/config").json()["protocol"], "anthropic")

    def test_delete_profile(self):
        created = self.create(name="待删除")
        self.assertEqual(self.client.delete("/v1/model/profiles/" + created["id"]).status_code, 200)
        self.assertIsNone(next((p for p in self.client.get("/v1/model/profiles").json()["items"]
                                if p["id"] == created["id"]), None))
        self.assertEqual(self.client.delete("/v1/model/profiles/" + created["id"]).status_code, 404)

    def test_export_and_import_without_keys(self):
        created = self.create(name="导出源")
        exported = self.client.get("/v1/model/profiles/export").json()
        self.assertFalse(exported["include_keys"])
        self.assertNotIn("sk-ant-secret-987654321", str(exported))
        self.assertTrue(any(p["name"] == "导出源" for p in exported["profiles"]))
        payload = {"profiles": [{"name": "导入配置", "protocol": "openai",
                                 "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}]}
        imported = self.client.post("/v1/model/profiles/import", json=payload).json()
        self.assertEqual(imported["imported"], 1)
        entry = next(p for p in imported["items"] if p["name"] == "导入配置")
        self.assertFalse(entry["has_key"])

    def test_probe_uses_overrides_and_profile_key(self):
        created = self.create(name="探活配置")
        with patch("datamind.api.provider_test_chat",
                   return_value={"ok": True, "model": "claude-sonnet-4-5", "reply": "可用", "elapsed_ms": 1.0}) as fake:
            result = self.client.post("/v1/model/config/test", json={"profile_id": created["id"]}).json()
        self.assertTrue(result["ok"])
        profile = fake.call_args[0][0]
        self.assertEqual(profile["protocol"], "anthropic")
        self.assertEqual(profile["api_key"], "sk-ant-secret-987654321")
        self.assertEqual(profile["base_url"], "https://api.anthropic.com")
        # 同一来源（host 相同）不提交 api_key：允许继承已存密钥（便利性保留）。
        with patch("datamind.api.provider_list_models",
                   return_value={"ok": True, "models": ["claude-sonnet-4-5"]}) as fake:
            same_origin = self.client.post("/v1/model/config/models", json={
                "profile_id": created["id"], "base_url": "https://api.anthropic.com/v1"}).json()
        self.assertEqual(same_origin["models"], ["claude-sonnet-4-5"])
        self.assertEqual(fake.call_args[0][0]["api_key"], "sk-ant-secret-987654321")

        # 换到**另一来源**又不提交 api_key：绝不继承已存密钥，否则等于把密钥送到调用方
        # 指定的任意地址（F01）。本条曾断言"200 且继承"，那正是被修掉的缺陷行为。
        with patch("datamind.api.provider_list_models") as leak:
            refused = self.client.post("/v1/model/config/models", json={
                "profile_id": created["id"], "base_url": "https://relay.example.com"})
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(refused.json()["detail"]["error_code"], "MODEL_KEY_NOT_INHERITED")
        leak.assert_not_called()

        # 换来源但**显式提交**自己的 key：这是合法的自带密钥用法，必须可用。
        with patch("datamind.api.provider_list_models",
                   return_value={"ok": True, "models": ["claude-sonnet-4-5"]}) as fake:
            relayed = self.client.post("/v1/model/config/models", json={
                "profile_id": created["id"], "base_url": "https://relay.example.com",
                "api_key": "sk-relay-own-000"}).json()
        self.assertEqual(relayed["models"], ["claude-sonnet-4-5"])
        self.assertEqual(fake.call_args[0][0]["api_key"], "sk-relay-own-000")

    def test_missing_key_paths(self):
        clear_model_config()
        env = {k: v for k, v in os.environ.items() if not k.startswith("NEW_API_")}
        with patch("datamind.sql_engine.active_profile", return_value={}), patch.dict(os.environ, env, clear=True):
            self.assertNotIn("NEW_API_KEY", settings())
            result = ask("2024年一共有多少笔订单？")
            self.assertEqual(result["error_code"], "MODEL_KEY_MISSING")

    def test_mask_and_normalize(self):
        self.assertNotIn("abcdefghijklmnop", mask_key("sk-abcdefghijklmnopqrstuvwxyz"))
        self.assertEqual(normalize_base_url("https://api.anthropic.com/v1/messages"), "https://api.anthropic.com/v1")
        self.assertEqual(providers.endpoint("https://api.anthropic.com/v1", "anthropic", "chat"),
                         "https://api.anthropic.com/v1/messages")

    def test_runs_list_endpoint_shape(self):
        self.assertEqual(self.client.get("/v1/runs", params={"limit": 1}).status_code, 200)
