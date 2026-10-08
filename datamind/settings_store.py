"""模型接入配置的持久化（datamind_meta.app_settings）。

支持多套「接入配置」（provider profile）：协议 / 地址 / 模型 / 密钥 / 鉴权方式，
其中一套为当前启用（active）。密钥只存在于元数据库中：不落任何文件、
HTTP 接口只回显掩码、readonly_user 对该表没有权限。
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .providers import (ANTHROPIC, DEFAULT_MAX_TOKENS, OPENAI, PROTOCOLS,
                        normalize_base_url as _normalize_base_url)

SETTING_KEY = "model.gateway"          # 兼容旧版单配置
PROFILES_KEY = "model.profiles"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
PROFILE_FIELDS = ("name", "protocol", "base_url", "model", "api_key", "max_tokens",
                  "timeout_s", "auth_style", "headers", "proxy", "note")

_table_ready = False


def write_dsn() -> str:
    return os.environ.get("DATAMIND_META_WRITE_DSN") or (
        "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev")


def _ensure_schema(conn) -> None:
    global _table_ready
    if _table_ready:
        return
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS app_settings (
                         setting_key VARCHAR(64) PRIMARY KEY,
                         value       JSONB NOT NULL,
                         updated_at  TIMESTAMPTZ NOT NULL DEFAULT now())""")
    conn.commit()
    try:
        with conn.cursor() as cur:
            cur.execute("REVOKE ALL ON app_settings FROM readonly_user")
        conn.commit()
    except psycopg2.Error:
        conn.rollback()
    _table_ready = True


def _read(conn, key: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT value FROM app_settings WHERE setting_key=%s", (key,))
        row = cur.fetchone()
    return row["value"] if row else None


def _write(conn, key: str, value) -> None:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO app_settings(setting_key, value, updated_at)
                       VALUES (%s, %s, now())
                       ON CONFLICT (setting_key)
                       DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                    (key, Json(value)))


def normalize_base_url(base_url: str | None) -> str:
    return _normalize_base_url(base_url)


def mask_key(api_key: str | None) -> str:
    key = api_key or ""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "…" + key[-2:]
    return key[:6] + "…" + key[-4:]


# ---------- 无密钥导出的脱敏 ----------
#
# 「不含密钥」不等于「不含认证材料」：headers 里可能带 Authorization / x-api-key /
# Cookie，proxy 的 URL 里可能内嵌 user:pass。导出路径必须先过 redact_profile()，
# 由它统一剔除这些认证材料，返回的字典里不再出现任何明文密钥 / 口令。

#: 显式列出的敏感头（大小写不敏感）
SENSITIVE_HEADERS = frozenset({
    "authorization", "proxy-authorization", "www-authenticate",
    "api-key", "apikey", "x-api-key", "x-api-token", "x-goog-api-key",
    "x-auth-token", "x-access-token", "authentication",
    "cookie", "set-cookie",
})

#: 原始 headers 文本里命中这些词就不再信任（整条丢弃，避免漏网）
_CREDENTIAL_HINT = re.compile(
    r"(?i)(authorization|api[-_]?key|cookie|token|secret|bearer|password|passwd)")

_REDACTED = "[REDACTED]"


def is_sensitive_header(name) -> bool:
    """判断一个请求头是否属于认证材料（authorization/api-key/cookie/token 等）。"""
    key = str(name or "").strip().lower()
    if not key:
        return False
    if key in SENSITIVE_HEADERS:
        return True
    if key.endswith(("-key", "-token", "-secret", "-password")):
        return True
    return "authorization" in key


def redact_headers(headers) -> dict | str:
    """剔除敏感头；非敏感头保留。兼容 {"__raw__": "<原始文本>"} 这种形态。"""
    if not headers:
        return headers if headers is not None else {}
    if isinstance(headers, str):
        # 原始文本：逐行过滤，仍有凭据迹象就整条丢弃
        kept = []
        for line in headers.splitlines():
            name, sep, _ = line.partition(":")
            if sep and is_sensitive_header(name):
                continue
            kept.append(line)
        cleaned = "\n".join(kept)
        return "" if _CREDENTIAL_HINT.search(cleaned) else cleaned
    if isinstance(headers, dict):
        cleaned = {}
        for name, value in headers.items():
            if name == "__raw__":
                cleaned[name] = redact_headers(value)
                continue
            if is_sensitive_header(name):
                continue
            cleaned[str(name)] = value
        return cleaned
    return ""


def redact_proxy(proxy: str | None) -> str:
    """去掉代理 URL 里的 userinfo（user:pass@host:port → host:port）。"""
    value = (proxy or "").strip()
    if not value:
        return ""
    candidate = value if "://" in value else "//" + value
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    if parts.username is None and parts.password is None:
        # 解析不出 userinfo，但文本里仍有 '@'：宁可整条去掉，不冒险导出
        return "" if "@" in value else value
    host = parts.hostname or ""
    if not host:
        return ""
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    if "://" in value:
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return "//" + netloc + parts.path


def _collect_secrets(source: dict) -> list[str]:
    """收集 payload 内所有需要抹掉的明文密钥 / 口令，供兜底扫描使用。"""
    secrets: list[str] = []

    def add(value) -> None:
        text = str(value or "").strip()
        if len(text) >= 4 and text not in secrets:
            secrets.append(text)

    add(source.get("api_key"))
    headers = source.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            if name == "__raw__":
                continue
            if is_sensitive_header(name):
                add(value)
    proxy = str(source.get("proxy") or "")
    if proxy:
        candidate = proxy if "://" in proxy else "//" + proxy
        try:
            parts = urlsplit(candidate)
        except ValueError:
            parts = None
        if parts is not None:
            add(parts.password)
            add(parts.username)
    return secrets


def _scrub_secrets(value, secrets: list[str]):
    """递归兜底：把字符串里残留的密钥 / 口令替换成 [REDACTED]。"""
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, _REDACTED)
        return value
    if isinstance(value, dict):
        return {key: _scrub_secrets(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_secrets(item, secrets) for item in value]
    return value


def redact_profile(payload: dict) -> dict:
    """返回不含任何认证材料的 profile 副本：无密钥导出 / 读取的唯一脱敏入口。

    处理内容：
      * ``api_key``：整体移除；
      * ``headers``：剔除 authorization / api-key / x-api-key / cookie 等敏感头，
        ``__raw__`` 原始文本按行过滤、仍有凭据迹象则整条丢弃；
      * ``proxy``：去掉 URL 中的 userinfo（user:pass@），无法安全解析则整条丢弃；
      * 兜底：任何字段里仍残留的密钥 / 口令值替换为 ``[REDACTED]``。
    """
    source = payload if isinstance(payload, dict) else {}
    secrets = _collect_secrets(source)
    redacted: dict = {}
    for field, value in source.items():
        if field == "api_key":
            continue
        if field == "headers":
            redacted["headers"] = redact_headers(value)
        elif field == "proxy":
            redacted["proxy"] = redact_proxy(value)
        else:
            redacted[field] = value
    return _scrub_secrets(redacted, secrets)


def _clean_profile(raw: dict, *, existing: dict | None = None) -> dict:
    merged = dict(existing or {})
    for field in PROFILE_FIELDS:
        if field in raw and raw[field] is not None:
            merged[field] = raw[field]
    merged["name"] = (merged.get("name") or "").strip() or "未命名配置"
    protocol = (merged.get("protocol") or OPENAI).strip().lower()
    merged["protocol"] = protocol if protocol in PROTOCOLS else OPENAI
    merged["base_url"] = normalize_base_url(merged.get("base_url")) or (
        "https://api.anthropic.com" if merged["protocol"] == ANTHROPIC else DEFAULT_BASE_URL)
    merged["model"] = (merged.get("model") or "").strip()
    merged["api_key"] = (merged.get("api_key") or "").strip()
    merged["auth_style"] = (merged.get("auth_style") or "both").strip().lower()
    try:
        merged["max_tokens"] = max(0, int(merged.get("max_tokens") or 0))   # 0 = 不限制
    except (TypeError, ValueError):
        merged["max_tokens"] = 0
    try:
        merged["timeout_s"] = float(merged.get("timeout_s") or 0) or None
    except (TypeError, ValueError):
        merged["timeout_s"] = None
    headers = merged.get("headers")
    if isinstance(headers, str):
        headers = {"__raw__": headers}
    merged["headers"] = headers if isinstance(headers, dict) else {}
    merged["proxy"] = (merged.get("proxy") or "").strip()
    merged["note"] = (merged.get("note") or "").strip()
    return merged


def _load_state(conn) -> dict:
    """Read profiles; migrate the legacy single-config row on first use."""
    state = _read(conn, PROFILES_KEY)
    if state and isinstance(state, dict):
        state.setdefault("items", [])
        state.setdefault("active", None)
        return state
    legacy = _read(conn, SETTING_KEY)
    items = []
    if legacy:
        migrated = _clean_profile({"name": "迁移配置", "protocol": OPENAI, **legacy})
        migrated["id"] = "p-" + uuid.uuid4().hex[:8]
        items.append(migrated)
    state = {"active": items[0]["id"] if items else None, "items": items}
    if items:
        _write(conn, PROFILES_KEY, state)
    return state


def list_profiles() -> dict:
    with psycopg2.connect(write_dsn()) as conn:
        _ensure_schema(conn)
        state = _load_state(conn)
    items = [_clean_profile(item, existing=item) for item in state["items"]]
    for item in items:
        item.setdefault("id", "p-" + uuid.uuid4().hex[:8])
    active = state.get("active")
    if active and not any(item["id"] == active for item in items):
        active = items[0]["id"] if items else None
    return {"active": active, "items": items}


def _store_state(conn, state: dict) -> None:
    _write(conn, PROFILES_KEY, state)


def save_profile(raw: dict) -> dict:
    with psycopg2.connect(write_dsn()) as conn:
        _ensure_schema(conn)
        state = _load_state(conn)
        pid = (raw.get("id") or "").strip()
        existing = next((item for item in state["items"] if item.get("id") == pid), None)
        profile = _clean_profile(raw, existing=existing)
        profile["id"] = existing["id"] if existing else (pid or "p-" + uuid.uuid4().hex[:8])
        profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        if existing:
            existing.clear()
            existing.update(profile)
        else:
            state["items"].append(profile)
        if raw.get("activate") or not state.get("active"):
            state["active"] = profile["id"]
        _store_state(conn, state)
    return profile


def delete_profile(pid: str) -> bool:
    with psycopg2.connect(write_dsn()) as conn:
        _ensure_schema(conn)
        state = _load_state(conn)
        before = len(state["items"])
        state["items"] = [item for item in state["items"] if item.get("id") != pid]
        if state.get("active") == pid:
            state["active"] = state["items"][0]["id"] if state["items"] else None
        _store_state(conn, state)
    return len(state["items"]) < before


def activate_profile(pid: str) -> dict:
    with psycopg2.connect(write_dsn()) as conn:
        _ensure_schema(conn)
        state = _load_state(conn)
        if not any(item.get("id") == pid for item in state["items"]):
            raise KeyError(pid)
        state["active"] = pid
        _store_state(conn, state)
    return {"active": pid}


def active_profile() -> dict:
    """当前启用的接入配置；没有配置时返回 {}。"""
    state = list_profiles()
    active = state.get("active")
    for item in state["items"]:
        if item.get("id") == active:
            return item
    return {}


def public_profile(profile: dict, *, active: bool = False) -> dict:
    """浏览器安全视图：不含明文密钥。"""
    key = profile.get("api_key") or ""
    return {"id": profile.get("id"), "name": profile.get("name"),
            "protocol": profile.get("protocol") or OPENAI,
            "protocol_label": PROTOCOLS.get(profile.get("protocol") or OPENAI, ""),
            "base_url": profile.get("base_url"), "model": profile.get("model"),
            "has_key": bool(key), "key_masked": mask_key(key),
            "max_tokens": profile.get("max_tokens"), "timeout_s": profile.get("timeout_s"),
            "auth_style": profile.get("auth_style") or "both", "headers": profile.get("headers") or {},
            "proxy": profile.get("proxy") or "",
            "note": profile.get("note") or "", "updated_at": profile.get("updated_at"),
            "active": active}


def public_profiles() -> dict:
    state = list_profiles()
    return {"active": state.get("active"),
            "items": [public_profile(item, active=item.get("id") == state.get("active"))
                      for item in state["items"]]}


# ---------- 兼容旧版单配置接口（供早期调用方与回归用例使用） ----------

def load_model_config() -> dict:
    return active_profile()


def save_model_config(base_url: str, model: str, api_key: str, **extra) -> dict:
    existing = active_profile()
    payload = {"id": existing.get("id"), "name": existing.get("name") or "默认接入",
               "protocol": existing.get("protocol") or OPENAI,
               "base_url": base_url, "model": model, "api_key": api_key}
    payload.update(extra)
    return save_profile(payload)


def clear_model_config() -> None:
    existing = active_profile()
    if not existing:
        return
    save_profile({**{field: existing.get(field) for field in PROFILE_FIELDS},
                  "id": existing.get("id"), "api_key": ""})


def public_model_config(config: dict | None = None, *, source: str = "unset") -> dict:
    stored = config if config is not None else {}
    view = public_profile(stored, active=True)
    view["source"] = source
    view["defaults"] = {"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL}
    del view["active"]
    return view
