"""SQL and document agent state machine with persisted trace and evidence."""
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
from .rag_engine import PROMPT_VERSION as RAG_PROMPT_VERSION, ask_documents
from .sql_engine import PROMPT_VERSION, ask, query_frame, settings, source_for, verification_passed


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
    return any(word in question.lower() for word in ("报告", "文档", "手册", "制度", "白皮书", "pdf", "知识库", "质检",
        "ohr-bench", "退款条件", "七天无理由", "多久到账", "退款时限", "续航", "会员权益", "有哪些权益", "计算公式", "预算", "巡检", "保修"))


def route_for(question: str, mode: str = "auto") -> str:
    if mode in {"sql", "rag", "industrial"}:
        return mode
    if '工序' in question and any(w in question for w in ('温度','批次')) and any(w in question for w in ('超限','超标','核验','报告','一致','冲突','工艺')):
        return 'industrial'
    if _is_document_question(question):
        if any(word in question for word in ("结合", "联合", "关联")) and any(
                word in question for word in ("数据库", "销售额最高", "销售额前三", "销量最高", "查询订单")):
            return "cross_source"
        return "rag"
    return "sql"


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


class DataMindAgent:
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
            if "sql" in result and result.get('kind') != 'industrial':
                final["sql_result"] = {k: _plain(result[k]) for k in
                                       ("sql", "sql_hash", "columns", "rows", "row_count", "elapsed_ms",
                                        "source_snapshot_id", "validator_findings")}
                # `executed` / `semantically_verified` / `verification_contract` /
                # `verification_reasons` come from `sql_engine.semantic_verification`: a query
                # that ran is not automatically one whose metric semantics were proven, and that
                # difference has to reach the API and the event log instead of being flattened
                # into "completed".
                for key in ('source_id','prompt_version','semantic_contract','model_used','query_frame','attempts','model_sql',
                            'executed','semantically_verified','verification_contract','verification_reasons'):
                    if key in result:
                        final['sql_result'][key] = _plain(result[key])
                if result.get('source_id')=='demo_industrial':
                    final['sql_result']['bound_params']=_plain(result['bound_params'])
        if result and "retrieval" in result:
            final["rag_result"] = _plain({k: result.get(k) for k in
                ("retrieval", "claims", "citations", "retrieved_evidence", "attempts", "elapsed_ms", "prompt_version", "answer_coverage")})
        if result and result.get('kind') == 'industrial':
            final['industrial_result'] = _plain(result)
        event_type = {"COMPLETED": "answer.completed", "ABSTAINED": "answer.abstained",
                      "FAILED_WITH_EVIDENCE": "run.failed"}.get(status, "answer.clarification")
        self._event(event_type, "orchestrator", "succeeded" if status == "COMPLETED" else "warning" if status == "ABSTAINED" else "failed",
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
        sql_payload.update({k:result[k] for k in ('source_id','semantic_contract','query_frame','prompt_version') if k in result})
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
                        (claim_id, self.run_id, answer, ("warned" if result.get('source_id')=='demo_industrial' else "verified") if verified else "insufficient_evidence",
                         [row_id], ("medium" if result.get('source_id')=='demo_industrial' else "high") if verified else "insufficient",
                         Json({"sql_hash_matches": True, "row_count": result["row_count"],
                               "validator_findings": result.get("validator_findings", []),
                               # The claim records which contract it was actually judged under, so a
                               # reader can tell "metric semantics proven" from "only the time scope
                               # was normalized" without re-deriving it from the finding list.
                               **({key: result[key] for key in
                                   ("semantically_verified", "verification_contract", "verification_reasons")
                                   if key in result}),
                               **({'synthetic_demo':True,'semantic_contract':result['semantic_contract'],
                                   'full_semantic_entailment':'not_independently_verified'} if result.get('source_id')=='demo_industrial' else {})})))
        self._event("evidence.attached", "verification_agent", "succeeded",
                    output_summary={"claim_id": claim_id, "evidence_ids": [sql_id, row_id]})

    def _record_document_evidence(self, result: dict):
        chunk_nodes, document_nodes = {}, {}
        used = {citation["chunk_id"]: citation for citation in result.get("citations", [])}
        with self.conn.cursor() as cur:
            for citation in result.get("retrieved_evidence", []):
                document_id, chunk_id = citation["document_id"], citation["chunk_id"]
                if document_id not in document_nodes:
                    document_nodes[document_id] = "ev-" + uuid.uuid4().hex[:16]
                    cur.execute("INSERT INTO evidence_nodes(evidence_id,run_id,node_type,ref_key,payload) VALUES (%s,%s,'Document',%s,%s)",
                        (document_nodes[document_id], self.run_id, document_id, Json(_plain({key: citation[key] for key in
                         ("document_id", "title", "document_version", "document_sha256", "source_url", "origin", "quality_detail")}))))
                chunk_nodes[chunk_id] = "ev-" + uuid.uuid4().hex[:16]
                payload = {**citation, **used.get(chunk_id, {}), "used_in_answer": chunk_id in used}
                cur.execute("INSERT INTO evidence_nodes(evidence_id,run_id,node_type,ref_key,payload) VALUES (%s,%s,'Chunk',%s,%s)",
                            (chunk_nodes[chunk_id], self.run_id, chunk_id, Json(_plain(payload))))
                cur.execute("INSERT INTO evidence_edges(run_id,src_evidence_id,dst_evidence_id,edge_type) VALUES (%s,%s,%s,'retrieved_from')",
                            (self.run_id, chunk_nodes[chunk_id], document_nodes[document_id]))
            for claim in result.get("claims", []):
                claim_id, node_id = "claim-" + uuid.uuid4().hex[:16], "ev-" + uuid.uuid4().hex[:16]
                evidence_ids = sorted({chunk_nodes[c["chunk_id"]] for c in claim["citations"]})
                cur.execute("INSERT INTO evidence_nodes(evidence_id,run_id,node_type,ref_key,payload) VALUES (%s,%s,'Claim',%s,%s)",
                            (node_id, self.run_id, claim_id, Json({"text": claim["text"]})))
                for evidence_id in evidence_ids:
                    cur.execute("INSERT INTO evidence_edges(run_id,src_evidence_id,dst_evidence_id,edge_type) VALUES (%s,%s,%s,'supports')",
                                (self.run_id, node_id, evidence_id))
                cur.execute("""INSERT INTO claims(claim_id,run_id,text,status,evidence_ids,confidence_level,verification)
                               VALUES (%s,%s,%s,'warned',%s,'medium',%s)""",
                            (claim_id, self.run_id, claim["text"], evidence_ids, Json(claim["verification"])))
        self._event("evidence.attached", "rag_agent", "succeeded",
                    output_summary={"chunk_count": len(chunk_nodes), "claim_count": len(result.get("claims", []))})

    def _run_rag(self, question: str, plan: dict, collections, *, document_refs=None, generator=None, retriever=None) -> dict:
        self._event("query.parsed", "query_understanding", "succeeded", output_summary={"route": "rag", "collections": collections,"document_refs":document_refs})
        self._event("plan.created", "planner", "succeeded", output_summary={"plan_id": plan["plan_id"], "tasks": plan["tasks"], "route": "rag"})
        self._event("tool.started", "rag_agent", "running", tool_name="knowledge.retrieve")

        def retrieved(result):
            self._event("retrieval.completed", "rag_agent", "succeeded", tool_name="knowledge.retrieve",
                output_summary={**result["retrieval"], "chunk_ids": [c["chunk_id"] for c in result["retrieved_evidence"]]})
            cfg = settings()
            with self.conn.cursor() as cur:
                cur.execute("UPDATE runs SET status='EXECUTING',versions=%s WHERE run_id=%s", (Json({
                    "kb": result["retrieval"]["snapshot_id"], "retriever": result["retrieval"]["retriever_version"],
                    "retrieval_mode": result["retrieval"]["mode"], "prompt": RAG_PROMPT_VERSION,
                    "model": cfg.get("NEW_API_MODEL"), "protocol": cfg.get("NEW_API_PROTOCOL"),
                    "profile": cfg.get("MODEL_PROFILE_NAME")}), self.run_id))

        def locator_lookup(block_ids):
            # Validated locators come from the contract store; a document parsed before the
            # contract simply has none, and the citation stays without a region.
            from ingestion.contract_store import read_locators
            return read_locators(block_ids, _read_dsn())

        def relation_lookup(block_ids):
            # Stored relations come from the contract store too; a document whose parse
            # produced no validated relation simply has none, and the citation carries an
            # empty list. They are shown with the citation, never used to rank evidence.
            from ingestion.contract_store import read_relations
            return read_relations(block_ids, _read_dsn())

        kwargs={'document_refs':document_refs} if document_refs is not None else {}
        result = ask_documents(question, collections=collections, generator=generator, retriever=retriever,
                               on_retrieval=retrieved, locator_lookup=locator_lookup,
                               relation_lookup=relation_lookup, **kwargs)
        self._record_document_evidence(result)
        if result["status"] == "abstained":
            self._event("verification.failed", "verification_agent", "warning", error_code=result["error_code"],
                        output_summary={"evidence_sufficient": False})
            return self._finish("ABSTAINED", result["answer"], "insufficient", error_code=result["error_code"], result=result)
        if result["status"] == "failed":
            code = result["error_code"]
            self._event("verification.failed", "verification_agent", "failed", error_code=code,
                        output_summary={"attempts": result["attempts"]})
            return self._finish("FAILED_WITH_EVIDENCE", MODEL_ERROR_MESSAGES.get(code,
                "文档答案的引用或数字未通过校验，请查看证据或补充文档。"), "insufficient", error_code=code, result=result)
        self._event("verification.passed", "verification_agent", "succeeded", output_summary={
            "excerpt_matches": True, "numeric_support": True, "semantic_entailment": "not_independently_verified"})
        return self._finish("COMPLETED", result["answer"], "medium", result=result)

    def run(self, question: str, *, database_sources: list[str] | None = None,
            document_collections: list[str] | None = None, document_refs: list[dict] | None = None, mode: str = "auto",
            generator=None, rag_generator=None, retriever=None) -> dict:
        sources = database_sources if database_sources is not None else ["demo_retail", "chinook", "demo_industrial"]
        route = "rag" if document_refs is not None and mode=="auto" else route_for(question, mode)
        tool = "industrial" if route == "industrial" else "rag" if route == "rag" else "sql"
        plan = {"plan_id": "plan-" + uuid.uuid4().hex[:12], "goal": question,
                "route": route,
                "budget": {"max_repair": 1, "top_k": max(5,len(document_refs or [])), "max_claims": 6, "max_output_tokens": 2200} if route == "rag" else
                          {"max_repair": 2, "max_rows": 100, "sql_timeout_ms": 5000},
                "tasks": [{"task_id": tool, "kind": tool + ".query", "depends_on": [], "status": "pending"},
                          {"task_id": "verify", "kind": "verify", "depends_on": [tool], "status": "pending"},
                          {"task_id": "answer", "kind": "answer", "depends_on": ["verify"], "status": "pending"}]}
        if route == 'industrial':
            plan['budget']={'max_rows':1000,'sql_timeout_ms':5000,'model_calls':0}
            plan['tasks']=[{'task_id':'requirements','kind':'document.fields','depends_on':[]},
                           {'task_id':'production','kind':'sql.fixed_query','depends_on':['requirements']},
                           {'task_id':'compare','kind':'rules.compare','depends_on':['production']},
                           {'task_id':'answer','kind':'answer.template','depends_on':['compare']}]
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO runs(run_id, question, status, plan, versions)
                           VALUES (%s,%s,'PLANNED',%s,%s)""",
                        (self.run_id, question, Json(plan), Json({"prompt": RAG_PROMPT_VERSION if route == "rag" else PROMPT_VERSION})))
        self._event("run.started", "orchestrator", "succeeded", input_summary={"question_length": len(question)})
        if route == "cross_source":
            return self._finish("FAILED_WITH_EVIDENCE", "数据库与文档的联合推理尚未接入，请分别选择数据库问数或文档问答。",
                                "insufficient", error_code="ROUTE_UNSUPPORTED")
        try:
            if document_refs is not None and route != 'rag':
                return self._finish('FAILED_WITH_EVIDENCE','指定文件范围仅用于文档问答，请选择文档模式。',
                                    'insufficient',error_code='DOCUMENT_SELECTION_MODE_INVALID')
            if route == 'industrial':
                return self._run_industrial(question, plan, database_sources, document_collections)
            if route == "rag":
                return self._run_rag(question, plan, document_collections, document_refs=document_refs, generator=rag_generator, retriever=retriever)
            if not sources or any(s not in {"demo_retail", "chinook", "demo_industrial"} for s in sources):
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
                            (Json({"schema": catalog.snapshot_id, "prompt": 'industrial-sql-v1' if source=='demo_industrial' else PROMPT_VERSION,
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
            if source=='demo_industrial':
                answer += '\n' + result['semantic_contract']
            # A weak contract must not produce a strong label: `semantically_verified` is False
            # for `time_scope_only` even when no finding fired, so such an answer is delivered
            # but downgraded to `insufficient` instead of claiming `high` confidence.
            verified = result.get("semantically_verified")
            if verified is None:
                verified = verification_passed(result["validator_findings"])
            self._record_evidence(result, answer, verified)
            self._event("verification.passed" if verified else "verification.failed", "verification_agent",
                        "succeeded" if verified else "failed",
                        output_summary={"sql_hash_matches": True, "row_count": result["row_count"],
                                        "validator_findings": result["validator_findings"],
                                        "verification_contract": result.get("verification_contract"),
                                        "verification_reasons": result.get("verification_reasons")})
            return self._finish("COMPLETED", answer, ("medium" if source=='demo_industrial' else "high") if verified else "insufficient", result=result)
        except Exception as exc:
            if isinstance(exc, ValueError) and str(exc) == "DOCUMENT_SELECTION_NOT_SEARCHABLE":
                # 与"版本/范围已变化"不同：文件存在且可见，但它没有可用切片，选中它只会
                # 得到无证据的回答。如实说明，而不是返回一个空引用的"成功"答案。
                return self._finish("FAILED_WITH_EVIDENCE",
                                    "所选文件尚未建立问答索引（没有可用于检索的切片），暂不可问答；请取消选择该文件后重试。",
                                    "insufficient", error_code=str(exc))
            if isinstance(exc, ValueError) and str(exc).startswith("DOCUMENT_SELECTION_"):
                return self._finish("FAILED_WITH_EVIDENCE", "选定文件的版本、状态或范围已变化，请刷新并重新选择。", "insufficient", error_code=str(exc))
            if isinstance(exc, ValueError) and str(exc) == "COLLECTION_NOT_ALLOWED":
                return self._finish("FAILED_WITH_EVIDENCE", "当前请求范围不允许访问该文档集合。", "insufficient", error_code="COLLECTION_NOT_ALLOWED")
            logging.exception("DataMind Agent run %s failed", self.run_id)
            self._event("verification.failed", "orchestrator", "failed", error_code="INTERNAL_ERROR")
            return self._finish("FAILED_WITH_EVIDENCE", "服务执行失败，请检查运行记录。", "insufficient",
                                error_code="INTERNAL_ERROR")

    def _run_industrial(self, question, plan, sources, collections):
        from .industrial import run, parse_question
        if sources is not None and set(sources) != {'demo_industrial'}:
            return self._finish('FAILED_WITH_EVIDENCE','当前请求范围不允许访问工业演示数据。','insufficient',error_code='SOURCE_NOT_ALLOWED')
        if collections is not None and set(collections) != {'industrial_demo'}:
            return self._finish('FAILED_WITH_EVIDENCE','当前请求范围不允许访问工业演示文档。','insufficient',error_code='COLLECTION_NOT_ALLOWED')
        try:
            scope=parse_question(question)
        except (ValueError,OverflowError):
            return self._finish('NEEDS_CLARIFICATION','请明确一个零件编号（如 A102）、工序号和年月，并询问温度核验。','insufficient',error_code='INDUSTRIAL_SCOPE_REQUIRED')
        self._event('query.parsed','query_understanding','succeeded',output_summary={'route':'industrial',**scope})
        self._event('plan.created','planner','succeeded',output_summary=plan)
        self._event('tool.started','industrial_agent','running',tool_name='industrial.compare')
        def step(kind,payload):
            self._event(kind,'industrial_agent','succeeded',output_summary=payload)
        try:
            result=run(question,dsn=_read_dsn(),on_step=step)
        except ValueError as exc:
            code=str(exc)
            return self._finish('FAILED_WITH_EVIDENCE','工业核验证据不可用或超出范围，请查看轨迹并补充来源。','insufficient',error_code=code)
        parser_versions=sorted({f['parser_version'] for f in result['facts']})
        with self.conn.cursor() as cur:
            cur.execute('UPDATE runs SET status=%s,versions=%s WHERE run_id=%s',('VERIFYING',Json({
                'rules':result['rule_version'],'authority':result['authority_version'],'dataset':result['dataset'],'sql_snapshot':result['source_snapshot_id'],
                'parser':parser_versions[0] if len(parser_versions)==1 else 'multiple' if parser_versions else None,
                'parsers':parser_versions,'model_used':False}),self.run_id))
        self._record_industrial_evidence(result)
        return self._finish('COMPLETED' if result['rows'] else 'ABSTAINED', result['answer'],
                            'medium' if result['rows'] else 'insufficient',
                            error_code=None if result['rows'] else 'INSUFFICIENT_EVIDENCE',result=result)

    def _record_industrial_evidence(self, result):
        """Retain both sources, rule checks and unresolved conflicts; no source writeback."""
        facts={f['fact_id']:f for f in result['facts']};nodes={}
        with self.conn, self.conn.cursor() as cur:
            def node(kind,ref,payload):
                key=(kind,ref)
                if key not in nodes:
                    eid='ev-'+uuid.uuid4().hex[:16];nodes[key]=eid
                    cur.execute('INSERT INTO evidence_nodes(evidence_id,run_id,node_type,ref_key,payload) VALUES (%s,%s,%s,%s,%s)',
                                (eid,self.run_id,kind,ref,Json(_plain(payload))))
                return nodes[key]
            def edge(left,right,kind):
                cur.execute('INSERT INTO evidence_edges(run_id,src_evidence_id,dst_evidence_id,edge_type) VALUES (%s,%s,%s,%s)',(self.run_id,left,right,kind))
            query=node('SQLQuery',result['sql_hash'],{k:result[k] for k in ('sql','bound_params','sql_hash','source_snapshot_id')})
            def fact_node(fid):
                fact=facts[fid];doc=node('Document',fact['document_id'],{k:fact[k] for k in ('document_id','document_version','source_sha256','title','source_url')})
                key=('DocumentFact',fid)
                created=key not in nodes
                fn=node('DocumentFact',fid,fact)
                if created: edge(fn,doc,'derived_from')
                return fn
            for row in result['rows']:
                rn=node('SQLRow',row['measurement_id'],{k:row.get(k) for k in ('part_id','batch_id','measurement_id','operation_no','metric','value','unit','measured_at','method','quality_id','report_id')})
                edge(rn,query,'derived_from')
                vn=node('VerificationCheck',row['measurement_id'],row);edge(vn,rn,'derived_from')
                for key in ('requirement_fact_id','report_fact_id'):
                    if row.get(key): edge(vn,fact_node(row[key]),'derived_from')
                for issue in row['issues']:
                    for fid in issue.get('fact_ids',[]): edge(vn,fact_node(fid),'derived_from')
                issues=[i for i in row['issues'] if i['kind'] in {'value_conflict','version_overlap','version_difference','entity_alignment','measurement_context'}]
                if row['agreement']=='resolved_equivalent':
                    issues.append({'kind':'unit_equivalence','status':'resolved_equivalent','reason':'DECIMAL_UNIT_CONVERSION_EQUAL'})
                for issue in issues:
                    cid='conf-'+uuid.uuid4().hex[:20]
                    left=[{'kind':'sql_measurement','measurement_id':row['measurement_id'],'snapshot':result['source_snapshot_id']}]
                    right=[{'fact_id':row['report_fact_id']}] if row.get('report_fact_id') else []
                    if issue['kind']=='version_overlap':
                        left=[{'fact_id':issue['fact_ids'][0]}];right=[{'fact_id':f} for f in issue['fact_ids'][1:]]
                    elif issue['kind']=='version_difference':right=[{'fact_id':row['requirement_fact_id']}]
                    record={'conflict_id':cid,'entity_key':{k:row[k] for k in ('part_id','operation_no','batch_id','measurement_id')},
                        'metric':row['metric'],'conflict_type':issue['kind'],'left_fact_refs':left,'right_fact_refs':right,
                        'comparison_context':{k:row[k] for k in ('method','measured_at','produced_at','unit')},
                        'normalized_values':{'production':row['normalized_value'],'report_raw':row['report_value'],'report_unit':row['report_unit']},
                        'rule_id':issue['reason'],'rule_version':result['rule_version'],'resolution_status':issue['status'],
                        'selected_fact_ref':{'fact_id':row['requirement_fact_id']} if issue['kind']=='version_difference' else None,
                        'reason':issue['reason'],'repair_attempt_refs':[]}
                    if issue.get('authority'):
                        record.update(rule_id=issue['authority']['rule_id'],rule_version=issue['authority']['rule_version'],
                                      selected_fact_ref=issue['selected_fact_ref'],authority=issue['authority'])
                        record['normalized_values'].update(selected=row['selected_value'],selected_unit=row['selected_unit'])
                    if issue.get('authority_rule_ids'):
                        record['authority_rule_ids']=issue['authority_rule_ids']
                    cur.execute('''INSERT INTO conflict_records(conflict_id,run_id,entity_key,metric,conflict_type,left_fact_refs,
                                   right_fact_refs,comparison_context,normalized_values,rule_id,rule_version,resolution_status,
                                   selected_fact_ref,reason,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (cid,self.run_id,Json(record['entity_key']),record['metric'],record['conflict_type'],Json(left),Json(right),
                         Json(record['comparison_context']),Json(record['normalized_values']),record['rule_id'],record['rule_version'],
                         record['resolution_status'],Json(record['selected_fact_ref']),record['reason'],Json(record)))
                    cn=node('ConflictRecord',cid,record);edge(cn,vn,'derived_from')
                    if issue.get('authority'):
                        authority=issue['authority'];an=node('AuthorityRule',authority['rule_id'],authority)
                        edge(cn,an,'resolved_by');edge(vn,an,'derived_from')
                text=f"测量 {row['measurement_id']}：工艺范围核验 {row['compliance']}；来源比较 {row['agreement']}。"
                claim_id='claim-'+uuid.uuid4().hex[:16];claim_node=node('Claim',claim_id,{'text':text});edge(claim_node,vn,'supports')
                cur.execute('INSERT INTO claims(claim_id,run_id,text,status,evidence_ids,confidence_level,verification) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (claim_id,self.run_id,text,'warned' if row['compliance'] in {'needs_review','insufficient_evidence'} or row['agreement'] in {'needs_review','insufficient_evidence','unresolved'} else 'verified',
                     [vn],'medium',Json({'rule_version':result['rule_version'],'synthetic_demo':True,'overall_quality_not_asserted':True})))
        self._event('evidence.attached','industrial_agent','succeeded',output_summary={'node_count':len(nodes),'measurement_count':len(result['rows'])})


# Preserve the existing Python entry point while the API now serves both routes.
SQLAgent = DataMindAgent
