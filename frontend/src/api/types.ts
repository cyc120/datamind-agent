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

export interface FinalResult {
  run_id: string
  status: RunStatus
  answer: string
  confidence_level: string
  error_code?: string | null
  sql_result?: SqlResult
  rag_result?: RagResult
  industrial_result?: any
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
  runStatus?: RunStatus
  confidence?: string
  error?: string
  runId?: string
}
