"""F06 收口：profile 解析实现收敛为一处 + 请求侧敏感头补回（修 401 缺口）。

覆盖点：
  * **单一实现**：``api.resolve_profile()`` 与 ``sql_engine.settings()`` /
    ``model_profile()`` 都只是 ``settings_store.resolve_profile_values()`` 的投影。
    既有结构化断言（settings() 里不再有第二套字段拼装），也有随机化输入的恒等断言。
  * **等价性**：同一组输入（存储配置 + 环境 + 请求覆盖）下，两条链路在
    ``base_url/model/protocol/api_key/max_tokens/timeout_s/auth_style/proxy/headers``
    上完全一致。``headers`` 这条是防「又出现一次不一致」的核心（上一次是 proxy
    只在探活侧生效）。
  * **敏感头补回**：请求只提交非敏感头时，解析结果仍带已存敏感头；请求显式提交的
    敏感头值优先；已存没有该敏感头时不凭空造；``__clear__`` 仍是唯一删除通道。

全部使用注入 / 打桩：数据库入口（``_select_stored_profile`` / ``active_profile``）
被替换、环境变量在用例内完全受控、不发起任何网络请求。
"""
import copy
import inspect
import os
import random
import unittest
from unittest.mock import patch

import psycopg2

from datamind import api as api_module
from datamind import providers
from datamind import settings_store
from datamind import sql_engine
from datamind.api import ProfileRequest, resolve_profile
from datamind.settings_store import (ENV_FIELD_NAMES, PROFILE_VALUE_FIELDS,
                                     merge_sensitive_headers, resolve_profile_values)

FIELDS = PROFILE_VALUE_FIELDS
SENSITIVE = {"Authorization": "Bearer sk-live-store-secret",
             "x-api-key": "sk-live-store-secret",
             "Cookie": "session=store-secret"}
STORED = {"id": "p-secret01", "name": "含凭据的接入配置", "protocol": "openai",
          "base_url": "https://relay.example.com/v1", "model": "deepseek-chat",
          "api_key": "sk-live-store-secret", "max_tokens": 4096, "timeout_s": 30.0,
          "auth_style": "both", "headers": {**SENSITIVE, "X-Custom": "keep-me"},
          "proxy": "http://relayuser:relaypass@relay.example.com:8080",
          "note": "唯一解析入口"}


def clean_environ() -> dict:
    """真实环境去掉模型接入相关变量，保证用例不受外部环境干扰。"""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("NEW_API_", "DATAMIND_"))}


def _random_stored(rng: random.Random) -> dict:
    """随机化存储配置：含缺失字段、None、空白串与非法数值。"""
    stored = {}
    if rng.random() < 0.85:
        stored["name"] = rng.choice(["配置A", "", None])
    if rng.random() < 0.85:
        stored["api_key"] = rng.choice(["sk-live-store-secret", "  sk-x  ", "", None])
    if rng.random() < 0.9:
        stored["protocol"] = rng.choice([None, "", "openai", "anthropic", "Anthropic", "custom"])
    if rng.random() < 0.9:
        stored["base_url"] = rng.choice([None, "", "https://relay.example.com/v1",
                                         "http://localhost:4000/v1"])
    if rng.random() < 0.9:
        stored["model"] = rng.choice([None, "", "deepseek-chat", "claude-sonnet-4-5"])
    if rng.random() < 0.8:
        stored["max_tokens"] = rng.choice([None, 0, 100, 4096, "512", "bad"])
    if rng.random() < 0.8:
        stored["timeout_s"] = rng.choice([None, 0, 30, "45", "bad"])
    if rng.random() < 0.8:
        stored["auth_style"] = rng.choice([None, "", "both", "bearer", "x-api-key", "Bearer"])
    if rng.random() < 0.85:
        stored["headers"] = rng.choice([None, {}, {"X-Custom": "1"},
                                        {**SENSITIVE, "X-Custom": "1"},
                                        {"__raw__": "Authorization: Bearer raw\nX-Ok: 1"},
                                        ""])
    if rng.random() < 0.85:
        stored["proxy"] = rng.choice([None, "", "http://relay.example.com:8080",
                                      "http://u:p@relay.example.com:8080"])
    return stored


def _random_env(rng: random.Random) -> dict:
    env = {}

    def maybe(key, values):
        if rng.random() < 0.5:
            env[key] = rng.choice(values)

    maybe("NEW_API_KEY", ["sk-env-key", ""])
    maybe("NEW_API_BASE_URL", ["https://env.example.com/v1", ""])
    maybe("NEW_API_MODEL", ["env-model", ""])
    maybe("NEW_API_PROTOCOL", ["openai", "anthropic", "custom", ""])
    maybe("NEW_API_MAX_TOKENS", ["2048", "0", "bad"])
    maybe("NEW_API_TIMEOUT", ["60", "0", "bad"])
    maybe("NEW_API_AUTH_STYLE", ["bearer", "x-api-key", ""])
    maybe("NEW_API_HEADERS", ["X-Env: 1", ""])
    maybe("NEW_API_PROXY", ["http://env.example.com:3128", ""])
    return env


def _random_overrides(rng: random.Random) -> dict:
    """随机化请求显式字段（含空值：空值应视为未提供，落到存储/环境/默认值）。"""
    pool = {"base_url": [None, "", "https://override.example.com/v1"],
            "model": [None, "", "override-model"],
            "protocol": [None, "anthropic", "openai"],
            "api_key": [None, "", "   ", "sk-request-key"],
            "max_tokens": [None, 0, 512],
            "timeout_s": [None, 0, 12.5],
            "auth_style": [None, "bearer", "x-api-key"],
            "headers": [None, {}, {"X-Custom": "override"},
                        {"Authorization": "Bearer request-secret"}],
            "proxy": [None, "", "http://override.example.com:9090"]}
    overrides = {}
    for field, values in pool.items():
        value = rng.choice(values)
        if value is not None:
            overrides[field] = value
    return overrides


class SharedResolverEquivalenceTest(unittest.TestCase):
    """探活链路与问数链路必须来自同一份解析实现 / 同一组结果。"""

    def setUp(self):
        self.env = clean_environ()

    def _both_chains(self, stored, env, overrides=None):
        """同一份输入下取两条链路的结果（不触库、不联网）。"""
        request = ProfileRequest(profile_id="p-secret01", **(overrides or {}))
        with patch.dict(os.environ, {**self.env, **env}, clear=True), \
                patch.object(api_module, "_select_stored_profile",
                             return_value=copy.deepcopy(stored)), \
                patch.object(sql_engine, "active_profile",
                             return_value=copy.deepcopy(stored)):
            api_view = resolve_profile(request)
            sql_view = sql_engine.model_profile(sql_engine.settings())
            resolved = resolve_profile_values(request, stored=copy.deepcopy(stored),
                                              inherit_sensitive_headers=True)
            settings_view = sql_engine.settings()
        return api_view, sql_view, resolved, settings_view

    def test_stored_and_environment_agree_across_both_chains(self):
        for seed in range(60):
            rng = random.Random(seed)
            stored, env = _random_stored(rng), _random_env(rng)
            api_view, sql_view, _, _ = self._both_chains(stored, env)
            projected = {field: api_view[field] for field in FIELDS}
            self.assertEqual(projected, sql_view,
                             f"两条链路解析不一致（seed={seed}, stored={stored}, env={env}）")

    def test_request_overrides_reach_both_chains_identically(self):
        for seed in range(60):
            rng = random.Random(1000 + seed)
            stored, env = _random_stored(rng), _random_env(rng)
            overrides = _random_overrides(rng)
            api_view, _, resolved, _ = self._both_chains(stored, env, overrides)
            expected = {field: resolved[field] for field in FIELDS}
            self.assertEqual({field: api_view[field] for field in FIELDS}, expected,
                             f"api 侧不是唯一解析入口的投影（seed={seed}, overrides={overrides}）")
            # settings() → model_profile() 是同一份解析结果的 NEW_API_* 投影
            cfg = {ENV_FIELD_NAMES[field]: resolved[field] for field in FIELDS}
            if not resolved["api_key"]:
                cfg.pop("NEW_API_KEY")
            self.assertEqual(sql_engine.model_profile(cfg), expected,
                             f"model_profile 不是纯映射（seed={seed}）")

    def test_settings_is_the_new_api_projection_of_the_shared_resolver(self):
        for seed in range(60):
            rng = random.Random(2000 + seed)
            stored, env = _random_stored(rng), _random_env(rng)
            _, _, resolved, settings_view = self._both_chains(stored, env)
            for field, env_name in ENV_FIELD_NAMES.items():
                if field == "api_key":
                    continue          # 无密钥时 settings() 刻意不下发 NEW_API_KEY（见下）
                self.assertEqual(settings_view[env_name], resolved[field],
                                 f"{env_name} 不是共享解析结果的投影（seed={seed}）")
            self.assertEqual(settings_view["MODEL_CONFIG_SOURCE"], resolved["source"])
            if resolved["api_key"]:
                self.assertEqual(settings_view["NEW_API_KEY"], resolved["api_key"])
            else:
                self.assertFalse(settings_view.get("NEW_API_KEY"))
            if resolved["name"]:
                self.assertEqual(settings_view["MODEL_PROFILE_NAME"], resolved["name"])
            else:
                self.assertNotIn("MODEL_PROFILE_NAME", settings_view)

    def test_headers_are_part_of_the_equivalence(self):
        """headers 是上次漏传的字段（proxy 之外最容易再漏的一个）。"""
        for seed in range(30):
            rng = random.Random(500 + seed)
            stored, env = _random_stored(rng), _random_env(rng)
            api_view, sql_view, _, _ = self._both_chains(stored, env)
            self.assertEqual(api_view["headers"], sql_view["headers"], f"seed={seed}")
            self.assertEqual(api_view["proxy"], sql_view["proxy"], f"seed={seed}")

    def test_database_failure_falls_back_to_environment_on_both_chains(self):
        env = {"NEW_API_KEY": "sk-env-key", "NEW_API_BASE_URL": "https://env.example.com/v1",
               "NEW_API_MODEL": "env-model"}
        with patch.dict(os.environ, {**self.env, **env}, clear=True), \
                patch.object(api_module, "_select_stored_profile",
                             side_effect=psycopg2.OperationalError("down")), \
                patch.object(sql_engine, "active_profile",
                             side_effect=psycopg2.OperationalError("down")):
            api_view = resolve_profile(ProfileRequest(profile_id="p-secret01"))
            sql_view = sql_engine.model_profile(sql_engine.settings())
        self.assertEqual(api_view["api_key"], "sk-env-key")
        self.assertEqual(api_view["base_url"], "https://env.example.com/v1")
        self.assertEqual({field: api_view[field] for field in FIELDS}, sql_view)

    def test_settings_no_longer_assembles_fields_itself(self):
        """结构化断言：settings() 调共享解析入口，而不是自己拼 NEW_API_*。"""
        source = inspect.getsource(sql_engine.settings)
        self.assertIn("resolve_profile_values", source, "settings() 未走唯一解析入口")
        self.assertNotIn('stored.get("', source, "settings() 仍在自行取存储字段")
        self.assertNotIn('stored["', source, "settings() 仍在自行取存储字段")

    def test_api_resolver_delegates_to_the_shared_entry(self):
        source = inspect.getsource(api_module.resolve_profile)
        self.assertIn("resolve_profile_values", source, "api.resolve_profile 未走唯一解析入口")
        self.assertNotIn('os.environ.get("NEW_API_BASE_URL")', source)

    def test_shared_entry_reuses_the_existing_header_merge_tool(self):
        """补回敏感头必须复用 settings_store.merge_sensitive_headers()，不再写一套。"""
        source = inspect.getsource(resolve_profile_values)
        self.assertIn("merge_sensitive_headers", source)


class SensitiveHeaderInheritanceTest(unittest.TestCase):
    """请求只提交非敏感头时，已存的敏感头必须补回，否则「测试连接」会 401。"""

    def setUp(self):
        self.env = clean_environ()
        self.stored = copy.deepcopy(STORED)

    def _resolve_from_store(self, **overrides):
        """模拟生产路径：配置由元数据库取回（stored 未显式注入）。"""
        payload = {"profile_id": self.stored["id"]}
        payload.update(overrides)
        request = ProfileRequest(**payload)
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(api_module, "_select_stored_profile",
                             return_value=copy.deepcopy(self.stored)):
            return resolve_profile(request)

    def test_editing_a_non_sensitive_header_keeps_stored_sensitive_headers(self):
        profile = self._resolve_from_store(headers={"X-Custom": "updated"})
        self.assertEqual(profile["headers"]["X-Custom"], "updated")
        for name, value in SENSITIVE.items():
            self.assertEqual(profile["headers"].get(name), value,
                             f"未提交的敏感头 {name} 被整段丢掉了")

    def test_request_sensitive_header_value_wins(self):
        profile = self._resolve_from_store(headers={"X-Custom": "updated",
                                                    "Authorization": "Bearer rotated"})
        self.assertEqual(profile["headers"]["Authorization"], "Bearer rotated")
        self.assertEqual(profile["headers"]["x-api-key"], SENSITIVE["x-api-key"],
                         "未提交的敏感头仍应补回")
        self.assertEqual(profile["headers"]["Cookie"], SENSITIVE["Cookie"])

    def test_blank_sensitive_header_keeps_stored_value(self):
        profile = self._resolve_from_store(headers={"X-Custom": "updated",
                                                    "Authorization": "   "})
        self.assertEqual(profile["headers"]["Authorization"], SENSITIVE["Authorization"])

    def test_sensitive_header_absent_from_store_is_not_invented(self):
        self.stored["headers"] = {"X-Custom": "keep-me"}
        profile = self._resolve_from_store(headers={"X-Custom": "updated"})
        self.assertEqual(profile["headers"], {"X-Custom": "updated"})
        for name in SENSITIVE:
            self.assertNotIn(name, profile["headers"])

    def test_clear_sentinel_is_still_the_only_removal_channel(self):
        profile = self._resolve_from_store(headers={"X-Custom": "updated",
                                                    "Authorization": "__clear__"})
        self.assertNotIn("Authorization", profile["headers"])
        self.assertEqual(profile["headers"]["x-api-key"], SENSITIVE["x-api-key"])
        self.assertEqual(profile["headers"]["Cookie"], SENSITIVE["Cookie"])

    def test_no_headers_submitted_keeps_stored_headers(self):
        profile = self._resolve_from_store()
        self.assertEqual(profile["headers"], self.stored["headers"])

    def test_effective_provider_headers_carry_the_restored_authorization(self):
        """补回必须落到真正发给上游的请求头上（否则 401 依旧）。"""
        profile = self._resolve_from_store(headers={"X-Custom": "updated"})
        headers = providers.headers_for(profile)
        self.assertEqual(headers["Authorization"], SENSITIVE["Authorization"])
        self.assertEqual(headers["x-api-key"], SENSITIVE["x-api-key"])
        self.assertEqual(headers["X-Custom"], "updated")

    def test_shared_entry_merges_sensitive_headers_by_default(self):
        request = ProfileRequest(headers={"X-Custom": "updated"})
        resolved = resolve_profile_values(request, stored=copy.deepcopy(self.stored))
        expected = merge_sensitive_headers({"X-Custom": "updated"}, self.stored["headers"])
        self.assertEqual(resolved["headers"], expected)
        self.assertEqual(resolved["headers"]["Authorization"], SENSITIVE["Authorization"])

    def test_merge_does_not_mutate_request_or_stored_headers(self):
        request_headers = {"X-Custom": "updated"}
        stored_headers = copy.deepcopy(self.stored["headers"])
        resolve_profile_values(ProfileRequest(headers=request_headers),
                               stored=copy.deepcopy(self.stored))
        self.assertEqual(request_headers, {"X-Custom": "updated"})
        self.assertEqual(stored_headers, self.stored["headers"])

    def test_probe_route_resolves_with_the_restored_sensitive_headers(self):
        """探活路由（未改路由代码）拿到的 profile 里必须有补回的 Authorization。"""
        captured = {}

        def fake_test_chat(profile, **kwargs):
            captured["profile"] = profile
            return {"ok": True, "reply": "可用"}

        request = ProfileRequest(profile_id=self.stored["id"], headers={"X-Custom": "updated"})
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(api_module, "_select_stored_profile",
                             return_value=copy.deepcopy(self.stored)), \
                patch.object(api_module, "provider_test_chat", side_effect=fake_test_chat):
            result = api_module.probe_chat(request)
        self.assertTrue(result["ok"])
        self.assertEqual(captured["profile"]["headers"]["Authorization"],
                         SENSITIVE["Authorization"])
        self.assertEqual(captured["profile"]["base_url"], self.stored["base_url"])

    def test_injecting_stored_does_not_change_the_merge_semantics(self):
        """同一个逻辑操作只有一种含义，不因调用方式而异。

        曾经这里是「显式注入 stored ⇒ 整体替换」的折中，用来同时满足一条记录缺陷行为的
        冻结断言。协调者判定：让参数翻转合并语义正是本缺陷的成因之一，因此补回敏感头是
        **无条件**的；那条冻结断言已改为断言新语义。本用例改为断言**两种调用方式等价**，
        这样折中一旦回归就会被抓住。
        """
        request = ProfileRequest(profile_id=self.stored["id"], headers={"X-Custom": "override"})
        default_path = resolve_profile(request, stored=copy.deepcopy(self.stored))
        explicit = resolve_profile(request, stored=copy.deepcopy(self.stored),
                                   inherit_sensitive_headers=True)
        self.assertEqual(default_path["headers"], explicit["headers"])
        self.assertEqual(default_path["headers"]["X-Custom"], "override")
        self.assertEqual(default_path["headers"]["Authorization"], SENSITIVE["Authorization"])

if __name__ == "__main__":
    unittest.main()
