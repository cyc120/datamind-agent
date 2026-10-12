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
  /** 会话归属：后端把它写进 runs.conversation_id（见 datamind/sessions.py）。
      仅在确有活动会话时下发——不传与传 null 在契约上都等于「不属于任何会话」。 */
  conversation_id?: string
}

/* ── 模型接入（GET /v1/model/config）────────────────────────────────────────
   实测 2026-10 在 127.0.0.1:8000 上的真实响应字段如下（照抄接口，不增不减）。
   注意：这份视图只回答「配了没有」——它**不含**任何连通性 / 最近检测结果，
   所以前端不能把它当成「模型可用」。 */
export interface ModelConfigView {
  id?: string | null
  name?: string | null
  protocol?: string | null
  protocol_label?: string | null
  base_url?: string | null
  model?: string | null
  /** 唯一能说明「密钥已配置」的字段；不代表密钥有效 */
  has_key: boolean
  key_masked?: string | null
  max_tokens?: number | null
  timeout_s?: number | null
  auth_style?: string | null
  headers?: Record<string, string> | null
  header_keys?: string[] | null
  has_proxy?: boolean
  /** 代理地址里是否夹带了 user:pass@ —— 为 true 时后端**只**回脱敏地址，凭据不下发 */
  proxy_has_credentials?: boolean
  proxy?: string | null
  note?: string | null
  updated_at?: string | null
  source?: string | null
  defaults?: Record<string, any> | null
  environment_fallback?: boolean
}

/** POST /v1/model/config/test 的响应（后端 providers.test_chat + api.py 补三个字段）。
    成功：{ok:true, model, elapsed_ms, protocol, reply, base_url, configured_model}
    失败：{ok:false, error_code, message, elapsed_ms, model, protocol, base_url, configured_model}
    仅 MODEL_NAME_MISSING 这一条会在 elapsed_ms 之前返回，故这些字段都可缺省。 */
export interface ModelTestResult {
  ok: boolean
  /** MODEL_NOT_FOUND / MODEL_AUTH_FAILED / MODEL_EMPTY_RESPONSE / MODEL_NAME_MISSING … */
  error_code?: string | null
  /** 后端给出的中文原因 */
  message?: string | null
  /** 成功时上游回复的前 120 字 */
  reply?: string | null
  elapsed_ms?: number | null
  model?: string | null
  protocol?: string | null
  base_url?: string | null
  /** 后端实际用于检测的模型（= 解析后的 profile.model） */
  configured_model?: string | null
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

/* ── 会话（服务端实现见 datamind/sessions.py）──────────────────────────────
   后端**没有** messages 表：一次问答就是 runs 里的一行（question / answer /
   status / started_at），runs.conversation_id 把它挂到会话上。所以下面
   SessionMessage 是「一个 run 一条」，前端渲染时摊平成用户气泡 + 助手气泡。 */

/** GET /v1/sessions 列表项 */
export interface SessionSummary {
  session_id: string
  title: string
  created_at: string
  updated_at: string
  /** 该会话下的 run 数（后端子查询得出） */
  run_count?: number
  /** 最近一次提问，用于列表副标题/悬停提示 */
  last_question?: string | null
}

/** 会话详情里的转写单元：一个 run 一条，不是一条聊天消息 */
export interface SessionMessage {
  run_id: string
  question: string
  answer?: string | null
  status?: string | null
  confidence_level?: string | null
  started_at?: string | null
}

export interface SessionListResult {
  sessions: SessionSummary[]
  limit: number
  offset: number
  has_more: boolean
}

export interface SessionDetail {
  session: SessionSummary
  messages: SessionMessage[]
}

/* ── 运行记录（GET /v1/runs，后端 datamind/api.py::list_runs）────────────────
   历史 run 在会话功能之前就存在，那时 conversation_id 从未被写入过，因此
   `conversation_id` 为 null 是**正常且必须如实呈现**的状态（UI 显示「未归属会话」），
   不能把它当成错误、也不能替它猜一个会话。 */

export interface RunSummary {
  run_id: string
  /** null = 该 run 早于会话功能，从未归属任何会话 */
  conversation_id?: string | null
  question: string
  status: RunStatus | string
  confidence_level?: string | null
  started_at: string
  ended_at?: string | null
}

export interface RunListResult {
  runs: RunSummary[]
  limit: number
  offset: number
  has_more: boolean
}

/** GET /v1/runs/{run_id}：后端 `SELECT *`，故除下列已知列外还可能有 plan/versions 等。 */
export interface RunDetail extends RunSummary {
  answer?: string | null
  plan?: any
  versions?: any
}

/* ══════════════════════════════════════════════════════════════════════════
   以下六个视图的接口契约（模型接入 / 字段复核 / 来源权威规则 / 图纸与实体 /
   尺寸核验 / 我的文件）。

   约定：字段名**照抄后端 JSON**，不重命名、不脑补默认值。后端视图里没有的键这里
   就不声明；声明成可空（`| null`）的键必须由 UI 显示为「未知/缺失」，而不是回落到
   一个看起来正常的值——那正是本仓库反复强调的「不要把没证明的事说成证明了」。
   ══════════════════════════════════════════════════════════════════════════ */

/* ── 模型接入：多配置（datamind/settings_store.py + datamind/api.py::_profiles_safe）──
   注意 `/v1/model/*` 全部路由都要求 **admin**，**包括读取**：配置里带端点与密钥掩码，
   中间件把它整体归到 admin 权限，所以 GET profile 列表也可能 403 PERMISSION_DENIED，
   UI 不能假设「读得到」。 */

/** 浏览器安全视图（后端 `public_profile()`）：**永不下发凭据值**。 */
export interface ModelProfile {
  id: string
  name: string | null
  protocol: string
  protocol_label: string
  base_url: string | null
  model: string | null
  /** **只**说明密钥字段填过值；既不说明密钥有效，也不说明模型能应答 */
  has_key: boolean
  key_masked: string
  max_tokens: number | null
  timeout_s: number | null
  auth_style: string
  /** 仅非敏感请求头有键值；敏感头（authorization/api-key/cookie…）不在其中 */
  headers: Record<string, string>
  /** 敏感请求头**只回显键名**：值是凭据，读接口也不下发 */
  header_keys: string[]
  has_proxy: boolean
  /** 已配置代理里是否带 user:pass@（凭据不下发，只此布尔量） */
  proxy_has_credentials: boolean
  /** 已去掉 userinfo 的代理地址，可能为 null */
  proxy: string | null
  note: string
  updated_at: string | null
  /** ⚠ create/update 的**返回体**里这个键恒为 false（后端 public_profile 默认参数），
      不能拿它判断「刚保存的成了当前启用」；只有 getModelProfiles() 列表里的 active 可信。 */
  active: boolean
}

/** `GET /v1/model/profiles` 的 protocols：可选协议清单（id 用于回传，label 用于显示） */
export interface ModelProtocolOption {
  id: string
  label: string
}

export interface ModelProfileListResult {
  active: string | null
  items: ModelProfile[]
  protocols: ModelProtocolOption[]
}

/**
 * 新建/更新/探活共用的请求体（后端 ProfileRequest，全部字段可选）。
 *
 * `api_key` 留空（不传或空串）在**更新**时表示「保持原密钥不变」，不是「清空」；
 * 只有 PUT /v1/model/config 把 "clear"/"none"/"-" 解释为清空（见 putModelConfig）。
 * `headers`/`proxy` 留空同样按键级合并保持原值——表单里看不到真实凭据是**设计**，
 * 所以不能把「表单空的」当成「用户想删掉」。
 */
export interface ModelProfileInput {
  id?: string | null
  profile_id?: string | null
  name?: string | null
  protocol?: string | null
  base_url?: string | null
  model?: string | null
  api_key?: string | null
  max_tokens?: number | null
  timeout_s?: number | null
  auth_style?: string | null
  headers?: Record<string, string> | null
  proxy?: string | null
  note?: string | null
  /** 保存后立即激活（后端 save_profile 读 activate） */
  activate?: boolean | null
}

/** 导出项：就是 PROFILE_FIELDS + id，默认（不含密钥）已由 redact_profile 脱敏 */
export interface ModelProfileExportItem {
  id?: string
  name?: string | null
  protocol?: string | null
  base_url?: string | null
  model?: string | null
  /** **仅** include_keys=true 时出现；这是把凭据原样搬走的显式导出 */
  api_key?: string | null
  max_tokens?: number | null
  timeout_s?: number | null
  auth_style?: string | null
  headers?: Record<string, string> | null
  proxy?: string | null
  note?: string | null
}

/** `GET /v1/model/profiles/export`：include_keys 是「这份导出里是否真的带了密钥」的证明 */
export interface ModelProfileExport {
  exported_at: string
  include_keys: boolean
  active: string | null
  profiles: ModelProfileExportItem[]
}

export interface ModelImportResult extends ModelProfileListResult {
  /** 实际写入条数；同名不覆盖，一律作为新配置写入 */
  imported: number
}

/**
 * `DELETE /v1/model/config` 的**唯一**返回体：只说明「已清空」。
 * 刻意不复用 ModelConfigView——那个有 21 个键，混用会让 UI 以为还能读到端点/模型。
 */
export interface ModelConfigCleared {
  has_key: false
  key_masked: ''
  source: 'unset'
}

/** `POST /v1/model/config/models`（后端 providers.list_models + 路由补 base_url/protocol） */
export interface ModelListResult {
  ok: boolean
  /** ok=true 时的上游模型 id 列表（已排序） */
  models?: string[]
  /** 后端实际使用协议；失败响应里也可能带 */
  protocol?: string
  base_url?: string | null
  /** ok=false：MODEL_AUTH_FAILED / REQUEST_FAILED / MODEL_KEY_NOT_INHERITED … */
  error_code?: string | null
  message?: string | null
}

/* ── 字段复核（GET /v1/documents*，datamind/api.py + field_store.py）──────────
   ⚠ 命名陷阱（必须显式映射，绝不能靠字段名相同就想当然）：
     * 列表 GET /v1/documents 里叫 **doc_version** 和 **sha256**；
     * 事实 GET /v1/documents/{id}/facts 里**同一个值**叫 **document_version**
       和 **source_sha256**。
   两边是同一行 document_assets 的两套别名，前端做类型映射时要写清楚，否则会出现
   「列表点进去版本对不上」的假冲突。 */

export interface DocumentSummary {
  document_id: string
  title: string
  collection: string
  file_type: string
  /** 来源类型（knowledge_base / upload …）；upload 行未过审前不可检索 */
  origin: string
  page_count: number | null
  /** 列表口径的版本号（事实接口叫 document_version） */
  doc_version: number
  /** 列表口径的来源哈希（事实接口叫 source_sha256） */
  sha256: string
  quality_score: number | null
  quality_detail: DocumentQualityDetail | null
  parser_version: string | null
  /** 派生事实：只有它为 true 才代表**检索器真的读得到** */
  searchable: boolean
  index_state: 'indexed' | 'no_chunks' | 'not_approved' | string
  /** 不可检索的原因码（UPLOAD_NOT_APPROVED / UPLOAD_HAS_NO_CHUNKS …），可检索时为 null */
  index_note: string | null
}

export interface DocumentListResult {
  documents: DocumentSummary[]
}

/** 解析质量明细：这里只声明 UI 真要读的两块，其余原样保留不解释 */
export interface DocumentQualityDetail {
  /** parse_contract 摘要（unsupported_features 说明「格式承载不了定位器」这类已声明的缺口） */
  parse_contract?: ParseContractSummary | null
  /** 事实抽取质量（facts 接口的 quality 就是这一块） */
  fact_extraction?: FactExtractionQuality | null
  [key: string]: unknown
}

export interface ParseContractSummary {
  /** 解析器**已声明**不支持的要素；空数组 ≠ 「全支持」，只说明没声明缺口 */
  unsupported_features?: string[]
  [key: string]: unknown
}

export interface FactExtractionQuality {
  extraction_version?: string
  fact_count: number
  /** 未能定位/无法通过校验的候选条数 */
  unresolved?: number
  arithmetic_checks?: FactArithmeticCheck[]
  approval_status?: string
  /** 抽取口径说明（例如「仅显式扫描单元格，实体 ID/批次/有效期未知」） */
  scope?: string
  [key: string]: unknown
}

export interface FactArithmeticCheck {
  entity_name?: string
  block_id?: string
  row?: number
  check: string
  /** passed / mismatch */
  status: string
  computed_percent?: string | null
  source_percent?: string | null
  /** 后端明说「未独立验证」时 UI 就不能声称已核验 */
  semantic_verification?: string
  [key: string]: unknown
}

/** 事实的逐块定位：每条引用都绑定 block_content_hash + 页框，任一项对不上该事实就不返回 */
export interface FactEvidenceRef {
  /** entity / header / value / lower / upper */
  role?: string
  field?: string
  field_value?: string
  block_id?: string
  page_no?: number
  /** [x0,y0,x1,y1]，坐标系见 coordinate_system（pdf_points） */
  bbox?: number[]
  page_size?: number[]
  coordinate_system?: string
  source_sha256?: string
  block_content_hash?: string
  [key: string]: unknown
}

export type FactDecision = 'confirmed' | 'rejected' | 'needs_review'
export type ApprovalDecision = 'approved' | 'rejected' | 'revoked'
/** not_approved=没有审批 · stale=有审批但绑的不是当前审查版本 · 其余为最后一次审批结论 */
export type FactFormalApprovalStatus = 'not_approved' | 'stale' | ApprovalDecision

export interface FactReviewRecord {
  review_id: string
  request_id?: string
  document_id: string
  fact_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  /** 被审查事实的载荷哈希：审查结论只对这一份载荷有效 */
  payload_sha256: string
  previous_review_id: string | null
  decision: FactDecision
  reviewer_label: string
  reviewer_user_id: string
  reviewer_roles: string[]
  reason: string
  created_at: string
}

export interface FactApprovalRecord {
  approval_id: string
  request_id?: string
  document_id: string
  fact_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  payload_sha256: string
  /** 审批必须绑一条「已确认」的人工复核 */
  review_id: string
  previous_approval_id: string | null
  decision: ApprovalDecision
  approver_label: string
  approver_user_id: string
  approver_roles: string[]
  reason: string
  created_at: string
}

/**
 * 一条事实（后端 fact payload + 当前复核/审批投影）。
 * 抽取器不同、fact_kind 不同，字段会少一些，所以业务字段可选；但 payload_sha256 /
 * latest_review / latest_approval / formal_approval_status 一定存在——UI 的「已复核/
 * 已批准」只允许看这三个，不允许看 approval_status（那是**源文件自己写的**声明）。
 */
export interface DocumentFact {
  fact_id: string
  document_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  extraction_version: string
  payload_sha256: string
  fact_kind?: 'measurement' | 'requirement' | string
  entity_name?: string | null
  entity_id?: string | null
  entity_type?: string | null
  metric?: string | null
  method?: string | null
  raw_header?: string | null
  raw_value?: string | null
  raw_unit?: string | null
  normalized_value?: string | null
  normalized_unit?: string | null
  normalized_range?: { min: string; max: string; min_inclusive: boolean; max_inclusive: boolean } | null
  operation_no?: string | null
  batch_id?: string | null
  measurement_id?: string | null
  source_revision?: string | null
  valid_from?: string | null
  valid_to?: string | null
  /** **源文件自己声明的**审批状态（已批准/未批准/unknown），不是本系统的审批结论 */
  approval_status?: string | null
  verification_status?: string | null
  evidence_refs: FactEvidenceRef[]
  ocr_confidence?: number | null
  /** 该载荷哈希下最后一次本地复核；没有则 null */
  latest_review: FactReviewRecord | null
  /** 该载荷哈希下最后一次本地审批；没有则 null */
  latest_approval: FactApprovalRecord | null
  formal_approval_status: FactFormalApprovalStatus
  [key: string]: unknown
}

/**
 * 「钉住版本」查询参数：version + sha256 **必须成对**给出。
 * 后端对「只给一个」返回 422 DOCUMENT_SELECTION_INVALID，对「给了但与当前行不一致」
 * 返回 409 DOCUMENT_SELECTION_CHANGED——这是**故意**的：宁可 4xx/裂图，也不能在
 * 声称「第 N 版」的框里渲染当前版本的字节。两个都不给 = 读当前版本。
 */
export interface DocumentSelection {
  version?: number | null
  sha256?: string | null
}

export interface DocumentFactsResult {
  document_id: string
  /** ⚠ 与列表的 doc_version 是同一个值（见上文命名陷阱） */
  document_version: number
  /** ⚠ 与列表的 sha256 是同一个值 */
  source_sha256: string
  parser_version: string
  facts: DocumentFact[]
  /** 后端已校验过定位器的事实条数（= facts.length） */
  fact_count: number
  quality: FactExtractionQuality | null
}

/** 复核/审批提交后的字段投影：只包含三个键，其余状态由调用方自行重读 */
export interface FactFieldState {
  latest_review: FactReviewRecord | null
  latest_approval: FactApprovalRecord | null
  formal_approval_status: FactFormalApprovalStatus
}

export interface FactReviewHistoryResult {
  reviews: FactReviewRecord[]
  /** 后端自带的语义说明：本地抽取保真度复核，与源权威/冲突消解无关 */
  semantics: string
}

export interface FactApprovalHistoryResult {
  approvals: FactApprovalRecord[]
  semantics: string
}

export interface FactReviewSubmitResult {
  review: FactReviewRecord
  field_state: FactFieldState | null
  semantics: string
}

export interface FactApprovalSubmitResult {
  approval: FactApprovalRecord
  field_state: FactFieldState | null
  semantics: string
}

/**
 * `POST .../reviews` 请求体（后端 FactReviewRequest，extra=forbid：多一个键就是 422）。
 * `reviewer_label` 若传，必须与当前登录身份一致，否则 422 REVIEWER_IDENTITY_SERVER_OWNED。
 */
export interface FactReviewInput {
  request_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  payload_sha256: string
  previous_review_id?: string | null
  decision: FactDecision
  reviewer_label?: string | null
  reason: string
}

/** `POST .../approvals` 请求体（后端 FactApprovalRequest）。审批人不得复核自己的复核记录。 */
export interface FactApprovalInput {
  request_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  payload_sha256: string
  review_id: string
  previous_approval_id?: string | null
  decision: ApprovalDecision
  reason: string
}

/* ── 来源权威规则（datamind/authority.py）──────────────────────────────────
   权威**只来自显式登记**：绝不能因为来源叫「质检报告」就默认它更权威。规则一经激活
   即不可改（只追加事件），所以 UI 展示的每条规则都必须带 payload_valid 一起显示——
   payload_valid=false 表示登记内容与哈希对不上，整份注册表都要按无效处理。 */

export interface AuthorityScope {
  part_id: string
  operation_no: string
  batch_id: string
  measurement_id: string
  metric: string
  method: string
}

export interface AuthorityDatasetRef {
  dataset_version: string
  seed_sha256: string
  pdf_sha256: string
}

/** 报告侧依据：把一条已复核+已批准的事实绑定成权威裁决的依据 */
export interface AuthorityReportBinding {
  document_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  fact_id: string
  payload_sha256: string
  review_id: string | null
  approval_id: string | null
}

export interface AuthorityContext {
  dataset: AuthorityDatasetRef
  scope: AuthorityScope
  production_row_sha256: string
  report: AuthorityReportBinding
}

export interface AuthorityBasis {
  document_id: string
  document_version: number
  source_sha256: string
  page_no: number
}

export interface AuthorityRulePayload {
  version: string
  context: AuthorityContext
  /** 裁决成「以哪一侧为准」；两侧原值都必须并陈，不允许只显示胜方 */
  selected_source: 'production' | 'report'
  valid_from: string
  valid_to: string
  basis: AuthorityBasis
  rationale: string
}

export interface AuthorityRule {
  rule_id: string
  request_id: string
  creator_user_id: string
  creator_label: string
  creator_roles: string[]
  payload: AuthorityRulePayload
  payload_sha256: string
  created_at: string
  request_payload?: Record<string, unknown>
  /** 下列字段只有 GET /v1/authority/rules（LEFT JOIN 最新事件）才带 */
  event_id?: string | null
  decision?: ApprovalDecision | null
  actor_user_id?: string | null
  actor_label?: string | null
  /** 最新激活事件里的理由（列名是 activation_reason，不是 reason） */
  activation_reason?: string | null
  /** false = 登记载荷与哈希不一致：本条不可作为权威依据 */
  payload_valid?: boolean
}

export interface AuthorityEvent {
  event_id: string
  request_id: string
  rule_id: string
  previous_event_id: string | null
  decision: ApprovalDecision
  actor_user_id: string
  actor_label: string
  actor_roles: string[]
  reason: string
  request_payload: Record<string, unknown>
  created_at: string
}

/** GET /v1/authority/candidates 的候选项：只列**未消解**的冲突，原值两侧都给 */
export interface AuthorityCandidate {
  context: AuthorityContext
  /** 提交时必须原样回传；服务端会重算并在不一致时 409 AUTHORITY_CONTEXT_CHANGED */
  candidate_sha256: string
  production_value: string | number
  production_unit: string
  report_value: string | number | null
  report_unit: string | null
  produced_at: string
  basis_suggestion: AuthorityBasis
  valid_from: string
  valid_to: string
  /** false = 报告侧事实尚未通过正式审批：此时**不能**登记规则（409 APPROVED_FIELD_REQUIRED） */
  eligible: boolean
}

export interface AuthorityRuleInput {
  request_id: string
  question: string
  measurement_id: string
  candidate_sha256: string
  selected_source: 'production' | 'report'
  valid_from: string
  valid_to: string
  basis: AuthorityBasis
  rationale: string
}

export interface AuthorityEventInput {
  request_id: string
  previous_event_id?: string | null
  decision: ApprovalDecision
  reason: string
}

/* ── 图纸与实体（datamind/cad_store.py / cad_reviews.py）───────────────────
   这里的一切只证明「本地解析与人工关联复核」，**不**构成图纸正式批准、生产绑定或
   质量结论；assessment 字段（inspection_gate / review_gate）与 semantics 必须一起渲染。 */

export interface DrawingSourceSnapshot {
  document_id: string
  document_version: number
  source_sha256: string
  format: 'dxf'
  parser_version: string
  provenance: 'synthetic_demo' | 'authorized_external' | string
  /** DXF 原始单位设置（0/1/4），单位由它推出而**不是**从几何或文件名猜的 */
  insunits: number
  unit: 'mm' | 'in' | 'unknown' | string
  coordinate_system: string
  layout: string
}

export interface CadPoint {
  x: number
  y: number
}

export interface CadBox {
  xmin: number
  ymin: number
  xmax: number
  ymax: number
}

/** 图元是多态联合：用 entity_type 判别后再读各自的几何字段 */
export interface CadEntity {
  handle: string
  layer: string
  view_id: string
  /** LINE / CIRCLE / TEXT */
  entity_type: string
  // LINE
  start?: CadPoint
  end?: CadPoint
  // CIRCLE
  center?: CadPoint
  radius?: number
  // TEXT
  text?: string
  insertion?: CadPoint
  bbox?: CadBox
  [key: string]: unknown
}

export interface CadView {
  view_id: string
  bbox: CadBox
}

/** 标注在图纸里的定位器：源哈希 + 图层 + 视图三者必须与图元一致，否则是过期定位 */
export interface CadLocator {
  locator_kind: string
  source_sha256: string
  handle: string
  layer: string
  view_id: string
  coordinate_system: string
  layout: string
}

export interface CadTitleField {
  value: string
  locator: CadLocator
}

/** 图纸业务身份（图号/版次/零件号）。图纸版次 **不等于** 工艺版次，不做隐式映射 */
export interface CadBusinessIdentity {
  drawing_id: CadTitleField
  drawing_revision: CadTitleField
  part_id: CadTitleField
  [key: string]: unknown
}

export interface CadAssociationEvidence {
  method: 'native_reference' | 'human_label' | string
  reference_id: string
  source_sha256: string
  annotation_handle: string
  target_handle: string
  rationale: string
}

/**
 * 一条尺寸要素。association_status 是**必须显示**的诚实字段：
 * confirmed 才代表「标注 ↔ 圆」的关联被人确认过；unresolved/ambiguous 都还没有。
 */
export interface DrawingFeature {
  feature_id: string
  feature_type: 'diameter' | string
  annotation_raw: string
  locator: CadLocator
  nominal_value: string | null
  tolerance_lower: string | null
  tolerance_upper: string | null
  unit: string
  association_status: 'confirmed' | 'ambiguous' | 'unresolved' | string
  target_candidates: string[]
  selected_target: string | null
  association_evidence: CadAssociationEvidence | null
  [key: string]: unknown
}

/** GET /v1/drawings 的一项 / 各 version 字段（public_version） */
export interface DrawingVersion {
  document_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  title: string
  bundle_sha256: string
  registrar_label: string
  created_at: string
  provenance: string
}

export interface DrawingListResult {
  drawings: DrawingVersion[]
}

/** 可用于登记的图纸源文件（只列白名单暂存目录里的 .dxf） */
export interface DrawingSourceEntry {
  source_path: string
  name: string
  source_sha256: string
  provenance: string
}

export interface DrawingSourceListResult {
  sources: DrawingSourceEntry[]
}

export interface DrawingVersionList {
  current_version: number
  versions: DrawingVersion[]
}

export interface DrawingDetail {
  version: DrawingVersion
  source: DrawingSourceSnapshot
  identity: CadBusinessIdentity
  metadata: Record<string, unknown>
}

export interface DrawingEntitiesResult {
  version: DrawingVersion
  entities: CadEntity[]
  views: CadView[]
}

/** 可用要素（annotation 与几何一致且已确认关联）；上下界是标注偏差，不是几何公差 */
export interface CadEligibleFeature {
  feature_id: string
  nominal_value?: string
  lower?: string
  upper?: string
  unit?: string
  annotation_handle?: string
  target_handle?: string
}

export interface CadExcludedFeature {
  feature_id: string
  /** CAD_ASSOCIATION_UNCERTAIN / CAD_VALUE_OR_UNIT_UNKNOWN / CAD_GEOMETRY_ANNOTATION_CONFLICT … */
  reason: string
}

/** 使用门槛：status != 'ready' 时 features 里的条目才可以被下游引用 */
export interface CadFeatureGate {
  status: 'ready' | 'needs_review' | string
  reason?: string
  features: CadEligibleFeature[]
  excluded: CadExcludedFeature[]
}

export interface CadAssociationState {
  feature_id: string
  feature_sha256: string
  latest_review: DrawingFeatureReview | null
  history_count: number
}

export interface DrawingFeaturesResult {
  version: DrawingVersion
  features: DrawingFeature[]
  /** 原样抽取/对象一致性门槛 */
  inspection_gate: CadFeatureGate
  association_reviews: CadAssociationState[]
  /** 叠加人工关联复核后的使用门槛 */
  review_gate: CadFeatureGate
  review_semantics: string
  semantics: string
}

/** 关联复核记录（public(row)：去掉 feature_snapshot/request_payload） */
export interface DrawingFeatureReview {
  review_id: string
  request_id?: string
  document_id: string
  document_version: number
  feature_id: string
  source_sha256: string
  parser_version: string
  bundle_sha256: string
  feature_sha256: string
  previous_review_id: string | null
  decision: FactDecision
  selected_target: string | null
  reason: string
  reviewer_user_id: string
  reviewer_label: string
  reviewer_roles: string[]
  created_at: string
}

export interface FeatureReviewHistory {
  document_version: number
  feature_id: string
  reviews: DrawingFeatureReview[]
  latest_review: DrawingFeatureReview | null
}

export interface DrawingFeatureReviewInput {
  request_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  bundle_sha256: string
  feature_sha256: string
  previous_review_id?: string | null
  decision: FactDecision
  /** confirmed 必填且必须属于 target_candidates；其它决策必须省略 */
  selected_target?: string | null
  reason: string
}

export interface DrawingRegistrationInput {
  request_id: string
  document_id: string
  title: string
  source_path: string
  source_sha256: string
  provenance: 'synthetic_demo' | 'authorized_external'
  previous_version: number
  /** required-but-nullable：首次登记必须显式传 null */
  previous_source_sha256: string | null
}

export interface DrawingRegistrationResult {
  version: DrawingVersion
  semantics: string
}

/* ── 尺寸核验（datamind/cad_dimensions.py）────────────────────────────────
   任何一条记录都是「本地注册 + 复核 + 使用审批」的模拟结果，**不**是制造授权，也
   **不**是产品质量结论：evaluate 的结果里 authentication_verified /
   formal_drawing_approval / product_quality_conclusion 三个都为 false，UI 必须照实说。 */

export interface DimensionSourceEntry {
  source_path: string
  name: string
  provenance: string
  source_sha256: string
}

export interface DimensionSourceListResult {
  sources: DimensionSourceEntry[]
}

/** 列表只回 id + 当前版本；要读内容必须再取详情 */
export interface DimensionMeasurementHead {
  measurement_id: string
  current_version: number
}

export interface DimensionBindingHead {
  binding_id: string
  current_version: number
}

export interface DimensionMeasurementList {
  measurements: DimensionMeasurementHead[]
  /** true = 后端只回了前 200 条：不得当作全集 */
  truncated: boolean
}

export interface DimensionBindingList {
  bindings: DimensionBindingHead[]
  truncated: boolean
}

/** 审计包裹层：所有尺寸记录都以 {payload, sha256, actor, registered_at} 返回 */
export interface DimensionAuditRecord<T> {
  payload: T
  sha256: string
  actor: { user_id: string; label: string; roles: string[] }
  registered_at: string
}

export interface DimensionMeasurementPayload {
  measurement_id: string
  source: { source_id: string; source_sha256: string; record_version: number; provenance: string }
  part_id: string
  batch_id: string
  operation_no: string
  process_revision: string
  metric: string
  feature_role: string
  method: string
  measured_at: string
  /** 原值文本；null = 源文件里就没有值（unknown unit 等） */
  value: string | null
  unit: string
}

export interface DimensionBindingPayload {
  binding_id: string
  binding_version: number
  document_id: string
  document_version: number
  source_sha256: string
  parser_version: string
  bundle_sha256: string
  feature_id: string
  feature_sha256: string
  target_handle: string
  drawing_id: string
  drawing_revision: string
  part_id: string
  measurement_id: string
  measurement_sha256: string
  measurement_source_sha256: string
  measurement_record_version: number
  batch_id: string
  operation_no: string
  process_revision: string
  metric: string
  feature_role: string
  method: string
  review_id: string
  review_sha256: string
  valid_from: string
  valid_to: string
  registrar_user_id: string
  registrar_roles: string[]
  rationale: string
}

export interface DimensionApprovalPayload {
  approval_id: string
  binding_id: string
  binding_version: number
  review_id: string
  review_sha256: string
  measurement_sha256: string
  binding_sha256: string
  previous_approval_id: string | null
  decision: ApprovalDecision
  reason: string
  approver_user_id: string
  approver_roles: string[]
  created_at: string
}

/** evaluate() 的结论：status='blocked' 时 comparison 必为 null、reason 给出阻断码 */
export interface DimensionEvaluation {
  contract_version: string
  status: 'compared' | 'blocked' | string
  reason: string | null
  comparison: DimensionComparison | null
  evidence: Record<string, unknown>
  /** 后端恒为 false：本地会话不等于外部认证 */
  authentication_verified: boolean
  /** 后端恒为 false：本流程不做图纸正式批准 */
  formal_drawing_approval: boolean
  /** 后端恒为 false：对照结果不是产品质量判定 */
  product_quality_conclusion: boolean
  semantics?: string
  [key: string]: unknown
}

/** 原值与归一值**都要**显示：inch/mm 换算后才有可比性，但原始记录不得被替换 */
export interface DimensionComparison {
  status: 'within_annotation_range' | 'outside_annotation_range' | string
  measurement_original: { value: string | null; unit: string }
  annotation_original: { raw: string; lower: string; upper: string; unit: string }
  normalized_mm: { measurement: string; lower: string; upper: string }
  annotation_handle: string
  target_handle: string
}

export interface DimensionMeasurementResult {
  measurement: DimensionAuditRecord<DimensionMeasurementPayload>
}

export interface DimensionBindingResult {
  binding: DimensionAuditRecord<DimensionBindingPayload>
  approvals: DimensionAuditRecord<DimensionApprovalPayload>[]
  current_result: DimensionEvaluation
  authentication: { verified: boolean; scope: string }
}

/** `POST /v1/dimensions/bindings` 只回新登记的绑定，**不含**审批链与当前结论
    （那些要 GET 详情才有），所以不能复用 DimensionBindingResult。 */
export interface DimensionBindingRegistrationResult {
  binding: DimensionAuditRecord<DimensionBindingPayload>
}

export interface DimensionApprovalResult {
  approval: DimensionAuditRecord<DimensionApprovalPayload>
}

/** 一次对照的持久化记录；replay=true 表示这次是**重放**既有记录而不是新算的 */
export interface DimensionComparisonRecord extends DimensionAuditRecord<DimensionComparisonPayload> {
  record_sha256: string
  replay: boolean
}

export interface DimensionComparisonPayload {
  comparison_id: string
  evaluation: Record<string, unknown>
  result: DimensionEvaluation
  authentication: { verified: boolean; scope: string }
}

/** 对照历史的一行：只给结论与绑定版本，细节要按 comparison_id 重放 */
export interface DimensionComparisonSummary {
  comparison_id: string
  binding_version: number
  status: string
  reason: string | null
  comparison: DimensionComparison | null
  registered_at: string
  record_sha256: string
}

export interface DimensionComparisonHistory {
  comparisons: DimensionComparisonSummary[]
  /** null = 没有更多；否则作为下一次 offset */
  next_offset: number | null
}

export interface DimensionMeasurementInput {
  request_id: string
  measurement_id: string
  source_path: string
  source_sha256: string
  provenance: 'synthetic_demo' | 'authorized_external'
  previous_version: number
  /** 后端是 required-but-nullable：首次登记必须显式传 null，省略会 422 */
  previous_sha256: string | null
}

export interface DimensionBindingInput {
  request_id: string
  binding_id: string
  previous_version: number
  /** required-but-nullable：首次登记必须显式传 null */
  previous_sha256: string | null
  document_id: string
  document_version: number
  source_sha256: string
  bundle_sha256: string
  feature_id: string
  feature_sha256: string
  target_handle: string
  measurement_id: string
  measurement_record_version: number
  measurement_sha256: string
  review_id: string
  review_sha256: string
  valid_from: string
  valid_to: string
  rationale: string
}

export interface DimensionApprovalInput {
  request_id: string
  binding_version: number
  binding_sha256: string
  /** required-but-nullable：链尾为空时必须显式传 null */
  previous_approval_id: string | null
  decision: ApprovalDecision
  reason: string
}

export interface DimensionComparisonInput {
  request_id: string
  binding_version: number
  binding_sha256: string
}

/* ── 我的文件 / 上传管道（ingestion/upload_pipeline.py）────────────────────
   三条必须照实呈现的事实：
     1. `POST /v1/uploads` 是 **raw bytes**（application/octet-stream + 查询参数），
        不是 multipart，也没有别的上传入口；
     2. 解析由**独立 worker 进程**推进（`python -m ingestion.upload_worker`），关闭页面
        不会推进它，所以 worker.alive=false 时必须说「后台处理器未运行」，
        不能把 queued 渲染成「解析中」；
     3. 审批只解除「未复核」闸门；本管道 **不写 chunks**（vector_index='deferred'），
        所以 approve 之后 searchable 仍可能是 false（index_state='no_chunks'）。
   `indexed`（approve 响应）就是 `searchable`，任一为 false 都不能说「已可检索」。 */

export type UploadJobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'partial_failure'
  | 'cancelled'

export interface UploadWorkerStatus {
  alive: boolean
  last_seen_at: string | null
  alive_seconds: number
  /** 心跳读不出来时的原因码（UPLOAD_WORKER_HEARTBEAT_UNREADABLE）；不是「未运行」的同义词 */
  error?: string
}

/** `POST /v1/uploads` 的 201 返回体（enqueue）：只是**排队成功**，不含解析结果 */
export interface UploadCreateResult {
  upload_id: string
  job_id: string
  filename: string
  media_type: string
  status: UploadJobStatus
  review_required: true
}

/** 列表/详情共有的部分（_item） */
export interface UploadListItem {
  upload_id: string
  job_id: string
  document_id: string
  filename: string | null
  media_type: string | null
  provenance: string | null
  /** **队列**状态（不是资产状态） */
  status: UploadJobStatus
  created_at: string | null
  review_state: 'approved' | 'pending_review' | string
  review_required: boolean
  /** 检索器（chunks）非空且已发布才为 true */
  searchable: boolean
  index_state: 'indexed' | 'no_chunks' | 'not_approved' | string
  index_note: string | null
  block_count: number
  locator_count: number
  unsupported_features: string[]
}

export interface UploadAnalysis {
  parse_status: string
  adapter?: string
  blocks: number
  locators_written: number
  relations: number
  block_types: Record<string, number>
  unsupported_features: string[]
  quality_findings: string[]
  [key: string]: unknown
}

/** 审批落库的审计块（quality_detail.review_approval），未审批时为 null */
export interface UploadReviewApproval {
  user_id?: string
  display_name?: string
  roles?: string[]
  approved_at?: string
  reason?: string | null
  indexed?: boolean
  block_count?: number
  chunk_count?: number
  [key: string]: unknown
}

export interface UploadDetail extends UploadListItem {
  source_sha256: string
  parser_version: string | null
  page_count: number | null
  collection: string
  analysis: UploadAnalysis
  /** 失败时的原因码 */
  error_code: string | null
  attempt: number
  vector_index: string
  vector_index_note: string
  wave_deadline_seconds: number
  wave_timeouts: number
  locator_stores: { contract_blocks: number; page_shaped: number }
  contract_url: string
  approval: UploadReviewApproval | null
  started_at: string | null
  finished_at: string | null
  /** 只有 GET /v1/uploads/{id} 带（with_worker=True） */
  worker?: UploadWorkerStatus
}

/** `POST /v1/uploads/{id}/approve`：detail + indexed（indexed === searchable） */
export interface UploadApproveResult extends Omit<UploadDetail, 'worker'> {
  indexed: boolean
}

export interface UploadListResult {
  uploads: UploadListItem[]
  count: number
  worker: UploadWorkerStatus
}

export interface UploadListParams {
  status?: UploadJobStatus | string
  media_type?: string
  /** ISO-8601 或 epoch 秒；其它格式后端 422 UPLOAD_FILTER_INVALID */
  since?: string
  limit?: number
}

export type UploadProvenance = 'synthetic_demo' | 'authorized_external'

/* ── 身份（GET /v1/auth/me；仅 CSRF 缓存用，其余界面不依赖登录）────────────
   `user` 为 null = 匿名（此时 csrf_token 也是 null）。角色：viewer|reviewer|approver|admin，
   admin 通过一切角色闸门；默认拒绝的中间件对未列入公开清单的路由一律要求身份。 */
export type UserRole = 'viewer' | 'reviewer' | 'approver' | 'admin'

export interface AuthUser {
  user_id: string
  username?: string | null
  display_name: string
  roles: UserRole[] | string[]
  [key: string]: unknown
}

export interface AuthMe {
  user: AuthUser | null
  csrf_token: string | null
}
