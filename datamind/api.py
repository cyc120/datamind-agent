"""Local development API for the SQL Agent and its persisted trace."""
from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

from .agent import SQLAgent, _read_dsn
from .providers import (OPENAI, PROTOCOLS, list_models as provider_list_models,
                        test_chat as provider_test_chat)
from .settings_store import (DEFAULT_BASE_URL, DEFAULT_MODEL, PROFILE_FIELDS, activate_profile,
                             active_profile, clear_model_config, delete_profile, list_profiles,
                             load_model_config, normalize_base_url, public_model_config,
                             public_profile, public_profiles, save_model_config, save_profile)


class Scope(BaseModel):
    database_sources: list[str] | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    scope: Scope | None = None


class ProfileRequest(BaseModel):
    """接入配置（provider profile）；protocol 决定走 OpenAI 兼容还是 Anthropic 协议。"""
    id: str | None = Field(default=None, max_length=64)
    profile_id: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    protocol: str | None = Field(default=None, max_length=24)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    max_tokens: int | None = Field(default=None, ge=0, le=200000)
    timeout_s: float | None = Field(default=None, ge=0, le=600)
    auth_style: str | None = Field(default=None, max_length=24)
    headers: dict | None = None
    proxy: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=500)
    activate: bool | None = None


class ProfileImportRequest(BaseModel):
    profiles: list[dict]
    activate_first: bool = False


app = FastAPI(title="DataMind Agent", version="0.2.0")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
WEB = WEB_DIR / "index.html"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB)


@app.get("/health")
def health():
    try:
        with psycopg2.connect(_read_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "database": "ready", "agent": "sql-only"}
    except psycopg2.Error:
        raise HTTPException(503, detail={"error_code": "DATABASE_UNAVAILABLE"})


def _stored_config() -> dict:
    try:
        return load_model_config()
    except psycopg2.Error:
        raise HTTPException(503, detail={"error_code": "DATABASE_UNAVAILABLE"})


def _config_source(stored: dict) -> str:
    if stored.get("api_key"):
        return "app_settings"
    return "environment" if os.environ.get("NEW_API_KEY") else "unset"


def _profiles_safe() -> dict:
    try:
        data = public_profiles()
    except psycopg2.Error:
        raise HTTPException(503, detail={"error_code": "DATABASE_UNAVAILABLE"})
    data["protocols"] = [{"id": key, "label": label} for key, label in PROTOCOLS.items()]
    return jsonable_encoder(data)


def _profile_from_request(request: ProfileRequest) -> dict:
    """探活用的完整配置：指定配置 > 当前启用配置 > 环境变量 > 默认值，请求字段优先。"""
    try:
        state = list_profiles()
        stored = next((item for item in state["items"] if item.get("id") == request.profile_id), {})
        if not stored:
            stored = load_model_config()
    except psycopg2.Error:
        stored = {}
    profile = dict(stored)
    for field in ("protocol", "base_url", "model", "auth_style", "headers", "proxy",
                  "max_tokens", "timeout_s"):
        value = getattr(request, field, None)
        if value is not None:
            profile[field] = value
    profile["api_key"] = (request.api_key or "").strip() or stored.get("api_key", "")
    profile["base_url"] = profile.get("base_url") or os.environ.get("NEW_API_BASE_URL") or DEFAULT_BASE_URL
    profile["model"] = profile.get("model") or os.environ.get("NEW_API_MODEL") or DEFAULT_MODEL
    profile["api_key"] = profile.get("api_key") or os.environ.get("NEW_API_KEY") or ""
    return profile


@app.get("/v1/model/profiles")
def get_profiles():
    """已保存的接入配置列表（密钥只回显掩码）+ 可选协议。"""
    return _profiles_safe()


@app.get("/v1/model/profiles/export")
def export_profiles(include_keys: bool = Query(default=False)):
    state = list_profiles()
    items = []
    for item in state["items"]:
        payload = {field: item.get(field) for field in PROFILE_FIELDS}
        payload["id"] = item.get("id")
        if not include_keys:
            payload.pop("api_key", None)
        items.append(payload)
    return jsonable_encoder({"exported_at": datetime.now(timezone.utc).isoformat(),
                             "include_keys": include_keys, "active": state.get("active"),
                             "profiles": items})


@app.post("/v1/model/profiles/import")
def import_profiles(request: ProfileImportRequest):
    """导入配置（ccswitch 风格的搬迁）：同名不覆盖，一律作为新配置写入。"""
    imported = []
    for raw in request.profiles[:50]:
        if not isinstance(raw, dict):
            continue
        payload = {field: raw.get(field) for field in PROFILE_FIELDS if raw.get(field) is not None}
        payload["id"] = ""
        if not payload.get("name"):
            payload["name"] = "导入配置"
        imported.append(public_profile(save_profile(payload)))
    if request.activate_first and imported:
        activate_profile(imported[0]["id"])
    return jsonable_encoder({"imported": len(imported), **_profiles_safe()})


@app.post("/v1/model/profiles")
def create_profile(request: ProfileRequest):
    raw = request.model_dump(exclude_none=True)
    raw.pop("profile_id", None)
    raw["id"] = ""
    if not (raw.get("name") or "").strip():
        raise HTTPException(400, detail={"error_code": "PROFILE_NAME_REQUIRED"})
    if not (raw.get("api_key") or "").strip():
        raw.pop("api_key", None)
    return jsonable_encoder(public_profile(save_profile(raw)))


@app.put("/v1/model/profiles/{profile_id}")
def update_profile(profile_id: str, request: ProfileRequest):
    state = list_profiles()
    if not any(item.get("id") == profile_id for item in state["items"]):
        raise HTTPException(404, detail={"error_code": "PROFILE_NOT_FOUND"})
    raw = request.model_dump(exclude_none=True)
    raw.pop("profile_id", None)
    raw["id"] = profile_id
    if not (raw.get("api_key") or "").strip():
        raw.pop("api_key", None)      # 留空 = 保持原密钥
    return jsonable_encoder(public_profile(save_profile(raw)))


@app.delete("/v1/model/profiles/{profile_id}")
def remove_profile(profile_id: str):
    if not delete_profile(profile_id):
        raise HTTPException(404, detail={"error_code": "PROFILE_NOT_FOUND"})
    return _profiles_safe()


@app.post("/v1/model/profiles/{profile_id}/activate")
def activate(profile_id: str):
    try:
        activate_profile(profile_id)
    except KeyError:
        raise HTTPException(404, detail={"error_code": "PROFILE_NOT_FOUND"})
    return _profiles_safe()


@app.get("/v1/model/config")
def get_model_config():
    """当前启用配置（兼容单配置调用方）。"""
    stored = _stored_config()
    view = public_model_config(stored, source=_config_source(stored))
    view["environment_fallback"] = bool(os.environ.get("NEW_API_KEY"))
    return jsonable_encoder(view)


@app.put("/v1/model/config")
def put_model_config(request: ProfileRequest):
    stored = _stored_config()
    if request.api_key is None or not request.api_key.strip():
        api_key = stored.get("api_key", "")
    elif request.api_key.strip().lower() in {"clear", "none", "-"}:
        api_key = ""
    else:
        api_key = request.api_key.strip()
    base_url = normalize_base_url(request.base_url) or stored.get("base_url") or DEFAULT_BASE_URL
    model = (request.model or stored.get("model") or DEFAULT_MODEL).strip()
    save_model_config(base_url, model, api_key, protocol=request.protocol or stored.get("protocol") or OPENAI)
    return jsonable_encoder(public_model_config(active_profile(),
                                                source="app_settings" if api_key else "unset"))


@app.delete("/v1/model/config")
def delete_model_config():
    clear_model_config()
    return {"has_key": False, "key_masked": "", "source": "unset"}


@app.post("/v1/model/config/models")
def probe_models(request: ProfileRequest):
    profile = _profile_from_request(request)
    result = provider_list_models(profile)
    result["base_url"] = profile.get("base_url")
    result["protocol"] = profile.get("protocol") or OPENAI
    return jsonable_encoder(result)


@app.post("/v1/model/config/test")
def probe_chat(request: ProfileRequest):
    profile = _profile_from_request(request)
    result = provider_test_chat(profile)
    result["base_url"] = profile.get("base_url")
    result["configured_model"] = profile.get("model")
    return jsonable_encoder(result)

@app.post("/v1/chat")
def chat(request: ChatRequest):
    agent = SQLAgent()
    try:
        return jsonable_encoder(agent.run(request.question,
                                         database_sources=request.scope.database_sources if request.scope else None))
    finally:
        agent.close()


@app.post("/v1/diagnostics/sql")
def diagnostic_sql():
    """Fixed query proving browser → Agent → Guard → readonly DB without a model call."""
    question = "2024年一共有多少笔订单？"
    sql = ("SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' "
           "AND created_at < '2025-01-01'")
    agent = SQLAgent()
    try:
        return jsonable_encoder(agent.run(question, database_sources=["demo_retail"],
                                         generator=lambda *_: {"sql": sql, "interpretation": "固定链路自检"}))
    finally:
        agent.close()


@app.post("/v1/chat/stream")
def chat_stream(request: ChatRequest):
    updates: queue.Queue = queue.Queue()
    agent = SQLAgent(sink=updates.put)

    def worker():
        try:
            agent.run(request.question, database_sources=request.scope.database_sources if request.scope else None)
        finally:
            agent.close()
            updates.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def events():
        while True:
            try:
                event = updates.get(timeout=15)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                break
            yield "event: trace\ndata: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run_exists(cur, run_id: str):
    cur.execute("SELECT run_id FROM runs WHERE run_id=%s", (run_id,))
    if not cur.fetchone():
        raise HTTPException(404, detail={"error_code": "RUN_NOT_FOUND"})


@app.get("/v1/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=200)):
    """Recent runs for the 「运行记录」 view."""
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT run_id, question, status, confidence_level, started_at, ended_at
                       FROM runs ORDER BY started_at DESC LIMIT %s""", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    return {"runs": jsonable_encoder(rows)}


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str):
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, detail={"error_code": "RUN_NOT_FOUND"})
    return jsonable_encoder(dict(row))


@app.get("/v1/runs/{run_id}/events")
def get_events(run_id: str, after: str | None = Query(default=None)):
    if after and not after.startswith("evt-" + run_id[4:] + "-"):
        raise HTTPException(400, detail={"error_code": "INVALID_CURSOR"})
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        _run_exists(cur, run_id)
        cur.execute("""SELECT * FROM trace_events WHERE run_id=%s AND event_id > %s
                       ORDER BY event_id LIMIT 200""", (run_id, after or ""))
        rows = [dict(r) for r in cur.fetchall()]
    return {"events": jsonable_encoder(rows), "next_cursor": rows[-1]["event_id"] if rows else after}


@app.get("/v1/runs/{run_id}/evidence")
def get_evidence(run_id: str):
    with psycopg2.connect(_read_dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        _run_exists(cur, run_id)
        cur.execute("SELECT * FROM evidence_nodes WHERE run_id=%s ORDER BY created_at, evidence_id", (run_id,))
        nodes = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM evidence_edges WHERE run_id=%s ORDER BY edge_id", (run_id,))
        edges = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM claims WHERE run_id=%s ORDER BY created_at", (run_id,))
        claims = [dict(r) for r in cur.fetchall()]
    return jsonable_encoder({"nodes": nodes, "edges": edges, "claims": claims})
