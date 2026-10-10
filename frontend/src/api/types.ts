// 后端接口与证据图的数据结构（与 datamind/api.py、agent.py 对齐）

export type RouteMode = 'auto' | 'sql' | 'rag' | 'industrial'

export interface DocumentRef {
  document_id: string
  document_version: number
  source_sha256: string
  parser_version: string
}

export interface Scope {
  database_sources?: string[]
  document_collections?: string[]
  document_refs?: DocumentRef[]
}

export interface ChatPayload {
  question: string
  mode?: RouteMode
  scope?: Scope
}

/** SSE 里 `event: trace` 的事件（也是 trace_events 表的一行） */
export interface TraceEvent {
  event_id: string
  run_id: string
  event_type: string
  agent_name: string
  status: 'succeeded' | 'running' | 'warning' | 'failed'
  started_at: string
  input_summary: Record<string, any>
  output_summary: Record<string, any>
  error_code: string | null
  tool_name: string | null
}

export type RunStatus =
  | 'COMPLETED'
  | 'ABSTAINED'
  | 'FAILED_WITH_EVIDENCE'
  | 'NEEDS_CLARIFICATION'

export interface SqlResult {
  sql?: string
  columns?: string[]
  rows?: any[][]
  row_count?: number
  elapsed_ms?: number
  source_id?: string
  validator_findings?: string[]
  [key: string]: any
}

/** 引用的来源块（后端 source_blocks 的逐块定位） */
export interface CitationSourceBlock {
  block_id?: string
  page_no?: number
  bbox?: number[]
  [key: string]: any
}

export interface Citation {
  chunk_id: string
  document_id: string
  title?: string
  /** 后端真实返回的原文摘录字段（旧数据的兜底字段见 excerpt / text） */
  quote?: string
  text?: string
  excerpt?: string
  /** 后端真实返回的页码区间；page_no 是旧数据的单页兜底 */
  page_start?: number
  page_end?: number
  page_no?: number
  section_path?: string[]
  source_blocks?: CitationSourceBlock[]
  /** 相对路径：'/v1/documents/{id}/file?...' 与 '/v1/documents/{id}/pages/{n}?...' */
  source_url?: string
  page_url?: string
  document_version?: number
  locator?: any
  [key: string]: any
}

export interface RagResult {
  citations?: Citation[]
  claims?: any[]
  retrieval?: any
  answer_coverage?: any
  [key: string]: any
}

/* ── 工业跨源核验（mode: 'industrial' → output_summary.industrial_result）──
   下面每个字段名都对着 127.0.0.1:8000 的真实响应核过一遍：
   2026-09 A102 工序 30 那次运行的 10 行 rows / 17 条 facts 都在。 */

/** rows[].issues[]：冲突双方原值都保留在这里，前端两方都要显示 */
export interface IndustrialIssue {
  /** value_conflict / version_overlap / unit_or_value / report_gap / measurement_context … */
  kind: string
  /** unresolved / needs_review / insufficient_evidence / resolved_by_authority … */
  status: string
  /** NO_REGISTERED_AUTHORITY_RULE / UNKNOWN_UNIT / REPORT_MISSING_OR_AMBIGUOUS … */
  reason?: string
  /** 冲突双方原值。有值时必须并陈，不允许只显示一侧 */
  left_value?: string | number | null
  right_value?: string | number | null
  fact_ids?: string[]
  authority_rule_ids?: string[]
  [key: string]: any
}

/** rows[].rules[]：本行实际套用的规则（rule_id 可查，版本可复现） */
export interface IndustrialRule {
  rule_id: string
  rule_version?: string
  /** inclusive_temperature_range 等判据会带结论 */
  passed?: boolean
  expression?: string
  conversion?: string
  left_value?: string | number | null
  right_value?: string | number | null
  event_id?: string
  [key: string]: any
}

/** 核验表的一行 = 一条 measurement 的跨源核验结论 */
export interface IndustrialRow {
  batch_id: string
  part_id: string
  produced_at: string
  recorded_revision: string | null
  applicable_revision: string | null
  measurement_id: string
  operation_no: string
  metric: string
  /** 数据库实测值与其原始单位 */
  value: string | number
  unit: string
  /** 归一值可能为 null（UNKNOWN_UNIT 那类未消解的情况） */
  normalized_value: string | number | null
  normalized_unit: string | null
  measured_at: string
  method: string
  quality_id: string | null
  report_id: string | null
  quality_method: string | null
  /** 质检报告侧的原值，用来和 value 对照 */
  report_value?: string | number | null
  report_unit?: string | null
  report_fact_id?: string | null
  requirement_fact_id?: string | null
  compliance: string
  agreement: string
  issues: IndustrialIssue[]
  rules: IndustrialRule[]
  [key: string]: any
}

export interface IndustrialSummary {
  batch_count: number
  measurement_count: number
  /** 超限 measurement_id */
  out_of_range: string[]
  /** 存在来源冲突（agreement === 'unresolved'）的 measurement_id */
  conflicts: string[]
  /** 已被已登记权威规则裁决的 measurement_id */
  authority_resolved: string[]
  /** 需人工复核的 measurement_id */
  needs_review: string[]
}

export interface IndustrialResult {
  kind: string
  status: string
  answer: string
  /** { part_id, operation_no, start, end, month, metric } */
  scope?: Record<string, any>
  rule_version?: string
  authority_version?: string
  /** { dataset_version, seed_sha256, pdf_sha256, provenance } */
  dataset?: Record<string, any>
  summary?: IndustrialSummary
  rows?: IndustrialRow[]
  facts?: any[]
  sql_rows?: any[]
  sql?: string
  bound_params?: any[]
  sql_hash?: string
  source_snapshot_id?: string
  elapsed_ms?: number
  model_used?: boolean
  partial?: boolean
  [key: string]: any
}

export interface FinalResult {
  run_id: string
  status: RunStatus
  answer: string
  confidence_level: string
  error_code?: string | null
  sql_result?: SqlResult
  rag_result?: RagResult
  industrial_result?: IndustrialResult
}

export type NodeType =
  | 'SQLQuery'
  | 'SQLRow'
  | 'Claim'
  | 'Document'
  | 'Chunk'
  | 'DocumentFact'
  | 'VerificationCheck'
  | 'ConflictRecord'
  | 'AuthorityRule'

export interface EvidenceNode {
  evidence_id: string
  run_id: string
  node_type: NodeType
  ref_key: string
  payload: any
  created_at?: string
}

export type EdgeType = 'derived_from' | 'supports' | 'retrieved_from' | 'resolved_by'

export interface EvidenceEdge {
  edge_id?: string
  run_id: string
  src_evidence_id: string
  dst_evidence_id: string
  edge_type: EdgeType
}

export interface Claim {
  claim_id: string
  run_id: string
  text: string
  status: string
  evidence_ids: string[]
  confidence_level: string
  verification: any
}

export interface EvidenceGraph {
  nodes: EvidenceNode[]
  edges: EvidenceEdge[]
  claims: Claim[]
}

/** 聊天界面里的一条消息（用户或助手） */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status?: 'running' | 'done' | 'error'
  thinking?: TraceEvent[]
  sqlResult?: SqlResult
  citations?: Citation[]
  /** 工业核验结果：核验表的数据源，渲染在答案正文下方 */
  industrialResult?: IndustrialResult
  runStatus?: RunStatus
  confidence?: string
  error?: string
  runId?: string
}
