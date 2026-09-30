"""模型协议适配层：OpenAI 兼容 与 Anthropic Messages 两套协议。

页面「模型接入」保存的每个配置都带 protocol 字段，调用、探活、错误分类
全部走这里，问数引擎与探针不再各自拼请求。
"""
from __future__ import annotations

import json
import re
import time

import httpx

OPENAI = "openai"
ANTHROPIC = "anthropic"
PROTOCOLS = {OPENAI: "OpenAI 兼容", ANTHROPIC: "Anthropic Messages"}
ANTHROPIC_VERSION = "2023-06-01"

DEFAULT_TIMEOUT = 75.0
MODEL_LIST_TIMEOUT = 20.0
DEFAULT_MAX_TOKENS = 2048
CONNECT_TIMEOUT = 10.0
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

BALANCE_HINTS = ("quota", "balance", "insufficient", "欠费", "余额", "额度", "billing", "credit")


def normalize_base_url(base_url: str | None) -> str:
    """Accept what users paste: bare host, /v1, or a full endpoint."""
    base = (base_url or "").strip().strip('"').strip("'")
    for suffix in ("/chat/completions", "/messages", "/completions", "/models"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/")


VERSION_SEGMENT = re.compile(r"/v\d+(\.\d+)?$")


def endpoint(base_url: str, protocol: str, resource: str) -> str:
    """resource: chat | models.

    地址已经带版本段（/v1、/api/paas/v4、/api/v3）时直接拼接，否则补 /v1。
    这样 DeepSeek、百炼、智谱、火山方舟、Anthropic 官方与自建网关都能用同一种填法。
    """
    base = normalize_base_url(base_url)
    tail = {"chat": "messages" if protocol == ANTHROPIC else "chat/completions",
            "models": "models"}[resource]
    if VERSION_SEGMENT.search(base):
        return base + "/" + tail
    return base + "/v1/" + tail


def headers_for(profile: dict) -> dict:
    key = (profile.get("api_key") or "").strip()
    protocol = profile.get("protocol") or OPENAI
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if protocol == ANTHROPIC:
        headers["anthropic-version"] = ANTHROPIC_VERSION
        style = profile.get("auth_style") or "both"
        if style in {"x-api-key", "both"}:
            headers["x-api-key"] = key
        if style in {"bearer", "both"}:
            headers["Authorization"] = "Bearer " + key
    else:
        headers["Authorization"] = "Bearer " + key
    extra = profile.get("headers") or {}
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items()})
    return headers


def build_chat_payload(profile: dict, system: str | None, user: str, *, max_tokens: int | None = None) -> dict:
    protocol = profile.get("protocol") or OPENAI
    limit = max_tokens or int(profile.get("max_tokens") or 0)
    if protocol == ANTHROPIC:
        # Anthropic Messages 要求 max_tokens 必填
        payload = {"model": profile.get("model"), "max_tokens": limit or DEFAULT_MAX_TOKENS,
                   "temperature": 0, "messages": [{"role": "user", "content": user}]}
        if system:
            payload["system"] = system
        return payload
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    payload = {"model": profile.get("model"), "temperature": 0, "stream": False, "messages": messages}
    if limit:
        payload["max_tokens"] = limit      # 0 = 不限制（推理型模型可自由输出）
    return payload


def parse_chat_text(protocol: str, data: dict, *, allow_reasoning: bool = False) -> str:
    if protocol == ANTHROPIC:
        blocks = data.get("content") or []
        texts = [str(block.get("text") or "") for block in blocks
                 if isinstance(block, dict) and block.get("type") in (None, "text")]
        return "".join(texts).strip()
    choices = data.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    content = (message.get("content") or "").strip()
    if content:
        return content
    # 探测时允许把推理内容当作“有响应”；问数时不接受，避免把思维链当 SQL
    return (message.get("reasoning_content") or "").strip() if allow_reasoning else ""


def parse_models(data: dict) -> list[str]:
    return sorted(str(item.get("id")) for item in (data.get("data") or [])
                  if isinstance(item, dict) and item.get("id"))


def upstream_message(body: str) -> str:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return (body or "")[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
        if isinstance(error, str) and error:
            return error[:300]
        for field in ("message", "msg", "detail"):
            if data.get(field):
                return str(data[field])[:300]
    return (body or "")[:300]


def classify_status(status: int, body: str = "") -> tuple[str, str]:
    """Map an upstream HTTP status to a stable error code the UI can explain."""
    message = upstream_message(body)
    lowered = message.lower()
    if status == 401:
        return "MODEL_AUTH_FAILED", message or "上游拒绝该密钥（401）"
    if status == 402 or any(hint in lowered for hint in BALANCE_HINTS):
        return "MODEL_BALANCE_EMPTY", message or "上游账户额度不足"
    if status == 403:
        return "MODEL_AUTH_FAILED", message or "上游拒绝访问（403）"
    if status == 404:
        return "MODEL_NOT_FOUND", message or "接口或模型不存在（404）"
    if status in (408, 504):
        return "MODEL_TIMEOUT", message or "上游超时"
    if status == 429:
        return "MODEL_RATE_LIMITED", message or "上游限流（429）"
    return "MODEL_UPSTREAM_ERROR", message or f"上游返回 HTTP {status}"


def _transport_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.TimeoutException):
        return "MODEL_TIMEOUT", "调用模型服务超时"
    if isinstance(exc, httpx.ConnectError):
        return "MODEL_UNREACHABLE", f"无法连接模型服务：{exc}"
    return "MODEL_UNREACHABLE", f"模型服务请求失败：{exc}"


def is_local(url: str) -> bool:
    host = (httpx.URL(url).host or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".local")


def make_client(url: str, profile: dict, timeout: float) -> httpx.Client:
    """httpx 会经 urllib 读取系统代理（macOS 上包括"忽略列表"以外的全局代理）。

    本机地址必须直连，否则 localhost 网关会被塞进系统代理导致挂起或 502；
    远端地址仍尊重系统/环境代理，也可以用 profile 里的 proxy 显式指定。
    """
    limits = httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT, timeout))
    proxy = (profile.get("proxy") or "").strip()
    if proxy:
        return httpx.Client(timeout=limits, follow_redirects=True, proxy=proxy, trust_env=False)
    if is_local(url):
        return httpx.Client(timeout=limits, follow_redirects=True, trust_env=False)
    return httpx.Client(timeout=limits, follow_redirects=True)


def _request(profile: dict, resource: str, *, payload: dict | None = None,
             timeout: float | None = None) -> tuple[dict | None, tuple[str, str] | None]:
    base_url = normalize_base_url(profile.get("base_url"))
    if not base_url:
        return None, ("MODEL_BASE_URL_MISSING", "请填写接口地址")
    if not (profile.get("api_key") or "").strip():
        return None, ("MODEL_KEY_MISSING", "请填写 API 密钥")
    url = endpoint(base_url, profile.get("protocol") or OPENAI, resource)
    limit = timeout or float(profile.get("timeout_s") or (DEFAULT_TIMEOUT if resource == "chat" else MODEL_LIST_TIMEOUT))
    try:
        with make_client(url, profile, limit) as client:
            response = client.request("POST" if payload is not None else "GET", url,
                                      headers=headers_for(profile), json=payload)
    except httpx.HTTPError as exc:
        return None, _transport_error(exc)
    if response.status_code >= 400:
        return None, classify_status(response.status_code, response.text)
    try:
        return response.json(), None
    except (json.JSONDecodeError, ValueError):
        return None, ("MODEL_INVALID_RESPONSE", "上游未返回 JSON")


def list_models(profile: dict, *, timeout: float | None = None) -> dict:
    data, error = _request(profile, "models", timeout=timeout)
    if error:
        code, message = error
        return {"ok": False, "error_code": code, "message": message}
    return {"ok": True, "models": parse_models(data or {}), "protocol": profile.get("protocol") or OPENAI}


def chat(profile: dict, system: str | None, user: str, *, timeout: float | None = None,
         max_tokens: int | None = None, allow_reasoning: bool = False) -> dict:
    if not (profile.get("model") or "").strip():
        return {"ok": False, "error_code": "MODEL_NAME_MISSING", "message": "请填写模型名称"}
    payload = build_chat_payload(profile, system, user, max_tokens=max_tokens)
    started = time.monotonic()
    data, error = _request(profile, "chat", payload=payload, timeout=timeout)
    elapsed = round((time.monotonic() - started) * 1000, 1)
    if error:
        code, message = error
        return {"ok": False, "error_code": code, "message": message, "elapsed_ms": elapsed,
                "model": profile.get("model"), "protocol": profile.get("protocol") or OPENAI}
    protocol = profile.get("protocol") or OPENAI
    text = parse_chat_text(protocol, data or {}, allow_reasoning=allow_reasoning)
    if not text:
        return {"ok": False, "error_code": "MODEL_EMPTY_RESPONSE", "message": "上游返回了空内容",
                "elapsed_ms": elapsed, "model": profile.get("model"), "protocol": protocol}
    return {"ok": True, "text": text, "model": (data or {}).get("model") or profile.get("model"),
            "elapsed_ms": elapsed, "protocol": protocol}


def test_chat(profile: dict, *, timeout: float | None = None) -> dict:
    """Minimal real conversation to prove the profile works end to end."""
    result = chat(profile, None, "只回复两个字：可用", timeout=timeout, max_tokens=64,
                  allow_reasoning=True)
    if result.get("ok"):
        result["reply"] = result.pop("text")[:120]
    return result
