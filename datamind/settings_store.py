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

#: 显式删除哨兵。读取侧不再下发敏感值，所以「空值」只能表示「保持不变」；
#: 要主动删除一个敏感头或一个代理，必须显式提交这个哨兵（见下方合并规则）。
CLEAR_SENTINEL = "__clear__"


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


# ---------- 读取侧「敏感头只回显键名」+ 保存侧「键级合并」 ----------
#
# 读取侧（public_profile）不再下发任何凭据值：敏感头只回显键名（header_keys），
# proxy 去掉 userinfo。浏览器因此看不到 Authorization / api-key / Cookie 的值，
# 保存时也就无法把它们原样回传，所以保存侧必须做键级合并，规则如下：
#   * 敏感头缺失 / None / 空白串 → 沿用已存值（绝不用空值覆盖真实凭据）；
#   * 敏感头提交了新的非空值 → 替换；
#   * 已存但本次未提交的敏感头 → 原样保留；
#   * 敏感头提交 CLEAR_SENTINEL → 显式删除（唯一删除通道）；
#   * 非敏感头保持旧语义：以提交的字典为准（缺失即删除）。
# proxy 同理：缺失 / 空白 / 等于已存值的脱敏视图 → 保持原值（含 userinfo）；
# 提交新的非空值 → 替换；提交 CLEAR_SENTINEL → 清空。


def _iter_header_pairs(text):
    """逐行解析 ``Name: value`` 形态的原始请求头文本；没有冒号的行一律丢弃。"""
    for line in str(text or "").splitlines():
        name, sep, value = line.partition(":")
        name = name.strip()
        if sep and name:
            yield name, value.strip()


def _headers_dict(headers) -> dict:
    """把 headers 归一成键值字典；``{"__raw__": "<文本>"}`` 按行解析成键值。"""
    if isinstance(headers, dict):
        pairs: dict = {}
        raw = headers.get("__raw__")
        if raw is not None:
            pairs.update(_iter_header_pairs(raw))
        for name, value in headers.items():
            if name != "__raw__":
                pairs[str(name)] = value
        return pairs
    if isinstance(headers, str):
        return dict(_iter_header_pairs(headers))
    return {}


def split_public_headers(headers) -> tuple[dict, list[str]]:
    """把 headers 拆成「可回显的非敏感键值」与「只回显键名的敏感头」。

    敏感头（``is_sensitive_header``）的值永远不进返回值；原始文本里没有冒号、
    无法命名的行也一律丢弃，宁可少回显也不冒险下发凭据。
    """
    visible: dict = {}
    secret_names: list[str] = []
    for name, value in _headers_dict(headers).items():
        if is_sensitive_header(name):
            if name not in secret_names:
                secret_names.append(name)
        else:
            visible[name] = value
    return visible, secret_names


def merge_sensitive_headers(incoming, existing) -> dict:
    """敏感头的键级合并（保存语义）：读取侧看不到的值不会被空值抹掉。

    规则见本段开头的注释；``existing`` 中的键做大小写不敏感匹配。
    """
    submitted = _headers_dict(incoming)
    stored = _headers_dict(existing)
    stored_by_key = {name.lower(): (name, value) for name, value in stored.items()}
    submitted_keys = {name.lower() for name in submitted}
    merged: dict = {}
    for name, value in submitted.items():
        if not is_sensitive_header(name):
            merged[name] = value
            continue
        text = "" if value is None else str(value).strip()
        if text == CLEAR_SENTINEL:
            continue                      # 显式删除
        if text:
            merged[name] = value          # 显式新值：替换
            continue
        previous = stored_by_key.get(name.lower())
        if previous:
            merged[previous[0]] = previous[1]   # 空值 = 保持已存值不变
    for name, value in stored.items():
        if is_sensitive_header(name) and name.lower() not in submitted_keys:
            merged.setdefault(name, value)      # 未提交的敏感头：保留，不带出也不清空
    return merged


def proxy_has_userinfo(proxy) -> bool:
    """proxy 里是否含 userinfo（凭据）或无法解析的 ``@``。"""
    value = str(proxy or "").strip()
    if not value:
        return False
    candidate = value if "://" in value else "//" + value
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return "@" in value
    if parts.username is not None or parts.password is not None:
        return True
    return "@" in value


def merge_proxy_value(incoming, existing) -> str:
    """proxy 的保存合并：读取侧只下发去掉 userinfo 的地址，空值/脱敏值不得覆盖凭据。

    ``CLEAR_SENTINEL`` 是唯一的清空通道；提交其他非空值即视为替换（若想保留
    代理凭据，请在新值里带上 userinfo）。
    """
    current = str(existing or "").strip()
    if incoming is None:
        return current
    text = str(incoming).strip()
    if not text or text == current:
        return current                    # 空值 / 原样回传 = 保持不变
    if text == CLEAR_SENTINEL:
        return ""
    if current and text == redact_proxy(current):
        return current                    # 提交的是脱敏视图（不含 userinfo）→ 保留原值
    return text


# ---------- 唯一解析入口（探活 / 问数链路共用） ----------
#
# 此前有两套实现各写一遍字段拼装：api.resolve_profile()（探活与模型列表）与
# sql_engine.settings() + model_profile()（SQL/RAG/PDF 问数）。两套已经出现过一次
# 不一致（proxy 只在探活侧生效）。现在两边都只调用 resolve_profile_values()：
#   * api.resolve_profile()  → PROFILE_VALUE_FIELDS 的规范化视图；
#   * sql_engine.settings()  → 同一结果的 NEW_API_* 视图（键名沿用历史契约）。
# 优先级：请求显式字段 > 指定/启用配置 > 环境变量 > 默认值。

#: resolve_profile_values() 产出的规范化 profile 字段（协议层直接消费的键名）
PROFILE_VALUE_FIELDS = ("protocol", "base_url", "model", "api_key", "max_tokens",
                        "timeout_s", "auth_style", "headers", "proxy")

#: 规范化字段 → 环境变量名（sql_engine.settings() 的 NEW_API_* 视图）
ENV_FIELD_NAMES = {"protocol": "NEW_API_PROTOCOL", "base_url": "NEW_API_BASE_URL",
                   "model": "NEW_API_MODEL", "api_key": "NEW_API_KEY",
                   "max_tokens": "NEW_API_MAX_TOKENS", "timeout_s": "NEW_API_TIMEOUT",
                   "auth_style": "NEW_API_AUTH_STYLE", "headers": "NEW_API_HEADERS",
                   "proxy": "NEW_API_PROXY"}


def _request_value(request, field):
    """从请求对象（ProfileRequest 或同形映射）取显式字段；未提供返回 None。"""
    if request is None:
        return None
    if isinstance(request, dict):
        return request.get(field)
    return getattr(request, field, None)


def _missing(value) -> bool:
    """None / 空白串视为「未提供」；0 与 {} 是显式值，不算缺失。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _stored_is_active(stored) -> bool:
    """存储配置是否自带密钥（决定它是否参与解析，见 resolve_profile_values）。"""
    return bool(isinstance(stored, dict) and str(stored.get("api_key") or "").strip())


def _config_source_value(base: dict, env) -> str:
    """配置来源：app_settings > environment > unset（沿用历史 MODEL_CONFIG_SOURCE）。"""
    if base:
        return "app_settings"
    return "environment" if env.get("NEW_API_KEY") else "unset"


def resolve_profile_values(request=None, *, stored=None, environ=None,
                           inherit_sensitive_headers: bool = True) -> dict:
    """唯一 profile 解析入口：请求显式字段 > 指定/启用配置 > 环境变量 > 默认值。

    返回 ``PROFILE_VALUE_FIELDS`` 的规范化字段集合，外加两个元数据键
    ``name``（启用配置名）与 ``source``（app_settings / environment / unset）。
    api.resolve_profile() 与 sql_engine.settings() 都只做这一份结果的投影，
    不再各自拼装字段。

    ``inherit_sensitive_headers``：请求提交了 ``headers`` 但没带已存配置里的敏感头
    （Authorization / api-key / Cookie…）时，是否用 ``merge_sensitive_headers()``
    从已存配置补回。默认补回：浏览器只看得到脱敏视图（``public_profile()`` 敏感头
    只出键名），编辑非敏感头后重新「测试连接」时敏感头不会被整段丢掉（否则 401）。
    请求显式提交的敏感头值始终优先；已存没有该敏感头时不会凭空造。

    保守侧（两条实现原有的优先级差异）
    ----------------------------------
    旧实现并不一致：``api.resolve_profile()`` 会把**存储配置的端点 / 请求头 / 代理**
    与**环境变量里的 ``NEW_API_KEY``** 拼在一起；``sql_engine.settings()`` 只要存储
    配置没有密钥就整体忽略它、完全走环境变量（端点也回退到 env / 默认值）。
    前者可能把环境里的真实密钥发往存储配置里写着的（第三方）端点，因此这里取**更不
    外泄凭据**的一侧：**存储配置只有在自带 ``api_key`` 时才参与解析**。问数链路的
    行为因此保持不变；探活侧失去的只是「无密钥存储配置 + env 密钥」这一种组合。
    """
    env = os.environ if environ is None else environ
    raw_stored = stored if isinstance(stored, dict) else {}
    base = raw_stored if _stored_is_active(raw_stored) else {}

    def pick(field):
        value = _request_value(request, field)
        if field == "api_key" and value is not None:
            value = str(value).strip() or None
        if not _missing(value):
            return value
        value = base.get(field)
        if not _missing(value):
            return value
        value = env.get(ENV_FIELD_NAMES[field])
        return None if _missing(value) else value

    protocol = str(pick("protocol") or "").strip().lower() or OPENAI
    base_url = str(pick("base_url") or "").strip() or DEFAULT_BASE_URL
    model = str(pick("model") or "").strip() or DEFAULT_MODEL
    api_key = str(pick("api_key") or "").strip()
    try:
        max_tokens = max(0, int(pick("max_tokens") or 0))       # 0 = 不限制
    except (TypeError, ValueError):
        max_tokens = 0
    try:
        timeout_s = float(pick("timeout_s") or 0) or None
    except (TypeError, ValueError):
        timeout_s = None
    auth_style = str(pick("auth_style") or "").strip().lower() or "both"

    request_headers = _request_value(request, "headers")
    if request_headers is not None:
        headers = (merge_sensitive_headers(request_headers, base.get("headers"))
                   if inherit_sensitive_headers else request_headers)
    else:
        headers = pick("headers")
        headers = {} if headers is None else headers

    proxy = pick("proxy")
    proxy = "" if proxy is None else str(proxy).strip()

    return {"protocol": protocol, "base_url": base_url, "model": model, "api_key": api_key,
            "max_tokens": max_tokens, "timeout_s": timeout_s, "auth_style": auth_style,
            "headers": headers, "proxy": proxy,
            "name": str(base.get("name") or ""), "source": _config_source_value(base, env)}


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
    if isinstance(headers, dict) and isinstance(existing, dict) and "headers" in raw:
        # 读取侧不下发敏感头值：浏览器回传的 headers 里敏感头缺失或为空 = 保持不变
        headers = merge_sensitive_headers(headers, existing.get("headers"))
    merged["headers"] = headers if isinstance(headers, dict) else {}
    if isinstance(existing, dict) and "proxy" in raw:
        # 同理：空白或脱敏视图不得覆盖已存的代理凭据
        merged["proxy"] = merge_proxy_value(raw.get("proxy"), existing.get("proxy"))
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
    """浏览器安全视图：不下发任何凭据值。

    * ``api_key`` 只回掩码；
    * 敏感头（authorization / api-key / cookie 等）**只回显键名**（``header_keys``），
      值一律不下发；非敏感头继续以 ``headers`` 回显键值；
    * ``proxy`` 去掉 userinfo（``redact_proxy``）；``has_proxy`` /
      ``proxy_has_credentials`` 两个布尔量只告诉表单「代理已配置 / 凭据不下发」。
    表单因此不会（也无法）回传真实凭据值，保存侧靠 ``_clean_profile`` 的
    键级合并把「缺失或空值」解释为「保持不变」。
    """
    key = profile.get("api_key") or ""
    headers, header_keys = split_public_headers(profile.get("headers"))
    proxy = str(profile.get("proxy") or "").strip()
    return {"id": profile.get("id"), "name": profile.get("name"),
            "protocol": profile.get("protocol") or OPENAI,
            "protocol_label": PROTOCOLS.get(profile.get("protocol") or OPENAI, ""),
            "base_url": profile.get("base_url"), "model": profile.get("model"),
            "has_key": bool(key), "key_masked": mask_key(key),
            "max_tokens": profile.get("max_tokens"), "timeout_s": profile.get("timeout_s"),
            "auth_style": profile.get("auth_style") or "both",
            "headers": headers, "header_keys": header_keys,
            "has_proxy": bool(proxy), "proxy_has_credentials": proxy_has_userinfo(proxy),
            "proxy": redact_proxy(proxy),
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
