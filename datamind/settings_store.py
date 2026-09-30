"""模型接入配置的持久化（datamind_meta.app_settings）。

支持多套「接入配置」（provider profile）：协议 / 地址 / 模型 / 密钥 / 鉴权方式，
其中一套为当前启用（active）。密钥只存在于元数据库中：不落任何文件、
HTTP 接口只回显掩码、readonly_user 对该表没有权限。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

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
