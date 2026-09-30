"""Explicit SQL-agent state machine with persisted runs, trace and evidence."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import psycopg2
from psycopg2.extras import Json

from .catalog import Catalog
from .sql_engine import PROMPT_VERSION, ask, query_frame, settings, source_for


MODEL_ERROR_MESSAGES = {
    "MODEL_KEY_MISSING": "尚未配置模型 API 密钥，请在左侧「模型接入」中填写并保存后重试。",
    "MODEL_AUTH_FAILED": "模型服务拒绝了该 API 密钥，请在左侧「模型接入」中检查或更换密钥。",
    "MODEL_BALANCE_EMPTY": "上游模型账户额度不足或未开通计费，请在服务商侧充值后重试。",
    "MODEL_RATE_LIMITED": "上游模型服务限流，请稍后重试。",
    "MODEL_TIMEOUT": "调用模型服务超时，请重试。",
    "MODEL_UNREACHABLE": "无法连接模型服务，请检查「模型接入」中的接口地址与网络。",
    "MODEL_NOT_FOUND": "接口地址或模型名称不存在，请在左侧「模型接入」中核对。",
    "MODEL_EMPTY_RESPONSE": "模型返回了空内容（可能是模型名或推理参数不匹配），请检查模型配置。",
    "MODEL_UPSTREAM_ERROR": "上游模型服务返回错误，请稍后重试。",
}


def _plain(value):
    return json.loads(json.dumps(value, default=lambda x: str(x) if isinstance(x, Decimal) else x.isoformat()))


def _write_dsn() -> str:
    return settings().get("DATAMIND_META_WRITE_DSN") or (
        "host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev")


def _read_dsn() -> str:
    return settings().get("DATAMIND_META_DSN") or (
        "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev")


def _is_document_question(question: str) -> bool:
    return any(word in question.lower() for word in ("报告", "文档", "手册", "制度", "白皮书", "pdf", "知识库", "质检"))


def _result_answer(result: dict) -> str:
    if "EMPTY_RESULT" in result.get("validator_findings", []):
        return "在指定数据源和筛选条件下没有查到数据。"
    rows = result["rows"]
    columns = result["columns"]
    if len(rows) == 1 and len(columns) == 1:
        return f"查询结果：{rows[0][0]}。"
    if "ROW_LIMIT_REACHED" in result.get("validator_findings", []):
        return "查询已达到 100 行展示上限，以下仅为返回的前 100 行。"
    return f"查询得到 {result['row_count']} 行数据，详情见结果表。"


class SQLAgent:
    def __init__(self, *, run_id: str | None = None, sink: Callable[[dict], None] | None = None):
        self.run_id = run_id or "run-" + uuid.uuid4().hex[:16]
        self.sink = sink
        self.sequence = 0
        self.conn = psycopg2.connect(_write_dsn())
        self.conn.autocommit = True

    def close(self):
        self.conn.close()

    def _event(self, event_type: str, agent_name: str, status: str, *, input_summary=None,
               output_summary=None, error_code=None, tool_name=None) -> dict:
        self.sequence += 1
        event = {"event_id": f"evt-{self.run_id[4:]}-{self.sequence:04d}",
                 "run_id": self.run_id, "event_type": event_type, "agent_name": agent_name,
                 "status": status, "started_at": datetime.now(timezone.utc).isoformat(),
                 "input_summary": _plain(input_summary or {}), "output_summary": _plain(output_summary or {}),
                 "error_code": error_code, "tool_name": tool_name}
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO trace_events(event_id, run_id, agent_name, event_type, status,
                       started_at, input_summary, output_summary, error_code, tool_name)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (event["event_id"], self.run_id, agent_name, event_type, status,
                         event["started_at"], Json(event["input_summary"]), Json(event["output_summary"]),
                         error_code, tool_name))
        if self.sink:
            self.sink(event)
        return event

    def _finish(self, status: str, answer: str, confidence: str, *, error_code=None,
                result: dict | None = None) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE runs SET status=%s, answer=%s, confidence_level=%s, ended_at=now()
                           WHERE run_id=%s""", (status, answer, confidence, self.run_id))
        final = {"run_id": self.run_id, "status": status, "answer": answer,
                 "confidence_level": confidence, "error_code": error_code}
        if result and result.get("status") == "completed":
            final["sql_result"] = {k: _plain(result[k]) for k in
                                   ("sql", "sql_hash", "columns", "rows", "row_count", "elapsed_ms",
                                    "source_snapshot_id", "validator_findings")}
        event_type = "answer.completed" if status == "COMPLETED" else "run.failed" if status == "FAILED_WITH_EVIDENCE" else "answer.clarification"
        self._event(event_type, "orchestrator", "succeeded" if status == "COMPLETED" else "failed",
                    output_summary=final, error_code=error_code)
        return final

    def _record_evidence(self, result: dict, answer: str, verified: bool):
        sql_id = "ev-" + uuid.uuid4().hex[:16]
        row_id = "ev-" + uuid.uuid4().hex[:16]
        claim_node_id = "ev-" + uuid.uuid4().hex[:16]
        claim_id = "claim-" + uuid.uuid4().hex[:16]
        sql_payload = {"sql": result["sql"], "sql_hash": result["sql_hash"],
                       "source_snapshot_id": result["source_snapshot_id"],
                       "bound_params_redacted": ["***"] * len(result.get("bound_params", []))}
        row_payload = {"columns": result["columns"], "rows": _plain(result["rows"]),
                       "row_count": result["row_count"]}
        with self.conn.cursor() as cur:
            for eid, kind, ref, payload in ((sql_id, "SQLQuery", result["sql_hash"], sql_payload),
                                           (row_id, "SQLRow", result["sql_hash"], row_payload),
                                           (claim_node_id, "Claim", claim_id, {"text": answer})):
                cur.execute("""INSERT INTO evidence_nodes(evidence_id, run_id, node_type, ref_key, payload)
                               VALUES (%s,%s,%s,%s,%s)""", (eid, self.run_id, kind, ref, Json(payload)))
            cur.execute("""INSERT INTO evidence_edges(run_id, src_evidence_id, dst_evidence_id, edge_type)
                           VALUES (%s,%s,%s,'derived_from'),(%s,%s,%s,'supports')""",
                        (self.run_id, row_id, sql_id, self.run_id, claim_node_id, row_id))
            cur.execute("""INSERT INTO claims(claim_id, run_id, text, status, evidence_ids,
                           confidence_level, verification) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (claim_id, self.run_id, answer, "verified" if verified else "insufficient_evidence",
                         [row_id], "high" if verified else "insufficient",
                         Json({"sql_hash_matches": True, "row_count": result["row_count"],
                               "validator_findings": result.get("validator_findings", [])})))
        self._event("evidence.attached", "verification_agent", "succeeded",
                    output_summary={"claim_id": claim_id, "evidence_ids": [sql_id, row_id]})

    def run(self, question: str, *, database_sources: list[str] | None = None,
            generator=None) -> dict:
        sources = database_sources if database_sources is not None else ["demo_retail", "chinook"]
        plan = {"plan_id": "plan-" + uuid.uuid4().hex[:12], "goal": question,
                "budget": {"max_repair": 2, "max_rows": 100, "sql_timeout_ms": 5000},
                "tasks": [{"task_id": "sql", "kind": "sql.query", "depends_on": [], "status": "pending"},
                          {"task_id": "verify", "kind": "verify", "depends_on": ["sql"], "status": "pending"},
                          {"task_id": "answer", "kind": "answer", "depends_on": ["verify"], "status": "pending"}]}
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO runs(run_id, question, status, plan, versions)
                           VALUES (%s,%s,'PLANNED',%s,%s)""",
                        (self.run_id, question, Json(plan), Json({"prompt": PROMPT_VERSION})))
        self._event("run.started", "orchestrator", "succeeded", input_summary={"question_length": len(question)})
        if _is_document_question(question):
            return self._finish("FAILED_WITH_EVIDENCE", "文档或跨源问题尚未接入 Agent，请先用数据库问数问题测试。",
                                "insufficient", error_code="ROUTE_UNSUPPORTED")
        try:
            if not sources or any(s not in {"demo_retail", "chinook"} for s in sources):
                return self._finish("FAILED_WITH_EVIDENCE", "当前请求范围不允许访问该数据源。",
                                    "insufficient", error_code="SOURCE_NOT_ALLOWED")
            source = source_for(question, sources[0] if len(sources) == 1 else None)
            if source not in sources:
                return self._finish("FAILED_WITH_EVIDENCE", "当前请求范围不允许访问该数据源。",
                                    "insufficient", error_code="SOURCE_NOT_ALLOWED")
            catalog = Catalog(_read_dsn(), source)
            frame = query_frame(question, catalog, catalog.retrieve(question)["candidates"])
            self._event("query.parsed", "query_understanding", "succeeded", output_summary=frame)
            self._event("plan.created", "planner", "succeeded",
                        output_summary={"plan_id": plan["plan_id"], "tasks": plan["tasks"], "source_id": source})
            with self.conn.cursor() as cur:
                cur.execute("UPDATE runs SET status='EXECUTING', versions=%s WHERE run_id=%s",
                            (Json({"schema": catalog.snapshot_id, "prompt": "sql-v1",
                                   "model": settings().get("NEW_API_MODEL"),
                                   "protocol": settings().get("NEW_API_PROTOCOL"),
                                   "profile": settings().get("MODEL_PROFILE_NAME")}), self.run_id))
            self._event("tool.started", "sql_agent", "running", tool_name="nl2sql",
                        input_summary={"source_id": source, "snapshot_id": catalog.snapshot_id})
            result = ask(question, source, generator=generator, run_id=self.run_id)
            if result["status"] == "clarification_required":
                return self._finish("NEEDS_CLARIFICATION", result["clarification"], "insufficient",
                                    error_code="AMBIGUOUS_QUERY")
            if result["status"] != "completed":
                code = result.get("error_code", "SQL_EXECUTION_ERROR")
                self._event("verification.failed", "verification_agent", "failed",
                            output_summary={"attempts": result.get("attempts", [])}, error_code=code)
                message = MODEL_ERROR_MESSAGES.get(code, f"查询未能完成，错误码：{code}。")
                return self._finish("FAILED_WITH_EVIDENCE", message, "insufficient", error_code=code)
            self._event("sql.validated", "sql_guard", "succeeded",
                        output_summary={"sql_hash": result["sql_hash"], "tables": result["tables"]})
            self._event("sql.executed", "sql_agent", "succeeded", tool_name="postgresql",
                        output_summary={"row_count": result["row_count"], "elapsed_ms": result["elapsed_ms"],
                                        "sql_hash": result["sql_hash"]})
            answer = _result_answer(result)
            verified = "EMPTY_RESULT" not in result["validator_findings"]
            self._record_evidence(result, answer, verified)
            self._event("verification.passed" if verified else "verification.failed", "verification_agent",
                        "succeeded" if verified else "failed",
                        output_summary={"sql_hash_matches": True, "row_count": result["row_count"],
                                        "validator_findings": result["validator_findings"]})
            return self._finish("COMPLETED", answer, "high" if verified else "insufficient", result=result)
        except Exception:
            logging.exception("SQL Agent run %s failed", self.run_id)
            self._event("verification.failed", "orchestrator", "failed", error_code="INTERNAL_ERROR")
            return self._finish("FAILED_WITH_EVIDENCE", "服务执行失败，请检查运行记录。", "insufficient",
                                error_code="INTERNAL_ERROR")
