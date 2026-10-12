/**
 * 图纸与实体（/cad）共用文案、门槛原因与错误解释。
 *
 * WHY 单独一个模块：本页有**四件来源不同的事实**，必须逐字一致地在各处重复说明，
 * 写散在各个组件里迟早会被改歪：
 *   1. `features[].association_status` 是**机器**抽取（原文/几何）时的判定，落库即冻结，
 *      人工复核**不会**改写它 —— 把它当成人做出的结论就是篡改责任人；
 *   2. `review_gate` / `association_reviews` 才是**人工**关联复核的结果；
 *   3. `provenance` 只是**登记人自述**（登记声明），服务端并不验证它的真实性；
 *   4. 两道 gate 的 `excluded[]` 必须连同原因一起显示 —— 藏起来的拒绝等于假通过。
 */

/** 统一错误视图：**永远**把 error_code 和解释一起带出来，不让 409 变成一句「失败了」。 */
export interface CadErrorView {
  code: string
  text: string
}

/* ── 机器判定 vs 人工结论：两套永远不许混用的状态文案 ───────────────────── */

/** `features[].association_status`：机器抽取器的意见，冻结不变。 */
export const MACHINE_STATUS_TEXT: Record<string, string> = {
  confirmed: '机器判定：抽取时已确认（原生引用，非人工）',
  ambiguous: '机器判定：存在多个候选，未定',
  unresolved: '机器判定：无法解析',
}

export function machineStatusText(status?: string | null): string {
  if (!status) return '机器判定：未提供'
  return MACHINE_STATUS_TEXT[status] ?? `机器判定：${status}`
}

/** `DrawingFeatureReview.decision`：人做出的决定。 */
export const DECISION_TEXT: Record<string, string> = {
  confirmed: '人工确认',
  rejected: '人工驳回',
  needs_review: '人工标记需复核',
}

export function decisionText(decision?: string | null): string {
  if (!decision) return '—'
  return DECISION_TEXT[decision] ?? decision
}

/* ── 使用门槛原因（inspection_gate / review_gate 的 excluded[].reason）───── */

/**
 * 门槛原因 → 中文。**未映射的代码原样显示**，绝不用一句含糊的中文盖过它：
 * 看不懂的拒绝也比假装通过强。
 */
export const GATE_REASON_TEXT: Record<string, string> = {
  CAD_BUSINESS_BINDING_MISMATCH:
    '图纸业务身份绑定不一致（图号/零件号与来源对不上），整道门槛不可用。这是原文级冲突：人工复核或审批都消除不了它',
  CAD_ASSOCIATION_UNCERTAIN: '标注与目标圆的关联不确定（多候选，或机器无法判定）',
  CAD_VALUE_OR_UNIT_UNKNOWN:
    '标注数值或单位未知（单位只认 DXF 的 INSUNITS 设置，本页不从几何或文件名猜）',
  CAD_GEOMETRY_ANNOTATION_CONFLICT:
    '几何与标注冲突（名义值与候选圆的几何对不上）。这是原文事实，人工复核不会消除它',
  CAD_ASSOCIATION_NOT_REVIEWED: '该要素在本版本尚未被人工关联复核（复核不跨版本继承）',
  CAD_ASSOCIATION_REVIEW_PENDING: '人工复核的判定是「需复核」，尚未确认',
  CAD_ASSOCIATION_REJECTED: '人工复核已驳回该关联',
}

export function gateReasonText(reason?: string | null): string {
  if (!reason) return '（服务端未给出原因代码）'
  return GATE_REASON_TEXT[reason] ?? `未映射的门槛原因代码：${reason}（原文照录，不做猜测性解释）`
}

/* ── 登记声明（provenance）：自述，不是已验证事实 ───────────────────────── */

export function provenanceText(provenance?: string | null): string {
  if (provenance === 'synthetic_demo') return '合成演示数据（登记声明）'
  if (provenance === 'authorized_external') return '授权外部数据（登记声明）'
  if (!provenance) return '来源未标注（登记声明）'
  return `${provenance}（登记声明）`
}

/** （登记声明）的含义必须能就地读到，不能只让读者猜。 */
export const PROVENANCE_NOTE =
  '「（登记声明）」= 登记人在登记时自述的来源类别，服务端不验证它是否属实；把它当成已核实的来源就是过度解读。'

/* ── 错误码 → 中文解释 ──────────────────────────────────────────────────── */

const AUTH_ERROR_TEXT: Record<string, string> = {
  LOGIN_REQUIRED: '需要身份：图纸接口不在公开读前缀（/v1/documents、/v1/runs、/assets/）里，匿名访问会被 401 拒绝。',
  SESSION_INVALID: '会话无效或已过期（SESSION_INVALID）：请重新登录后再读。',
  PERMISSION_DENIED:
    '当前身份没有该动作所需的角色（403 PERMISSION_DENIED）。登记需要 ADMIN；关联复核需要 REVIEWER；APPROVER 在复核接口同样会被拒。',
  CSRF_REQUIRED:
    '缺少或失效的 X-CSRF-Token（403 CSRF_REQUIRED）：令牌来自 GET /v1/auth/me。刷新页面重新取一次再提交。',
  ORIGIN_NOT_ALLOWED:
    '同源校验未通过（403 ORIGIN_NOT_ALLOWED）：只能用相对路径同源访问，绝对地址（如 http://127.0.0.1:8000/...）会被拒。',
}

const CAD_ERROR_TEXT: Record<string, string> = {
  CAD_ASSET_NOT_FOUND: '这份图纸资产不存在（可能已被移除）。',
  CAD_VERSION_NOT_FOUND: '该图纸版本不存在。',
  CAD_FEATURE_NOT_FOUND: '该尺寸要素不存在于这个版本。',
  CAD_TITLE_REQUIRED: '标题不能为空（422 CAD_TITLE_REQUIRED）。',
  CAD_STAGED_SOURCE_INVALID:
    '源文件不合法（422）：必须位于白名单暂存目录（data/cad_demo/development 或 data/cad_demo/authorized）内、后缀为 .dxf、且不被绝对路径指到别处。',
  CAD_STAGED_SOURCE_CHANGED: '暂存文件的内容与登记的 source_sha256 不一致：文件在登记前被改动过。',
  CAD_SOURCE_UNAVAILABLE: '源文件在对象存储里读不到。',
  CAD_SOURCE_VERSION_MISMATCH: '源文件哈希与存储记录不一致：内容不可信。',
  CAD_SOURCE_IDENTITY_INVALID: '源标识不是合法的 sha256。',
  CAD_REQUEST_ID_REUSED:
    '这个 request_id 已被「不同内容」或「另一位用户」用过：同 request_id 只有在内容完全一致时才是幂等重试。换一个 request_id 再登记。',
  CAD_REVIEW_REQUEST_ID_REUSED:
    '这个 request_id 已被「不同内容/要素/用户」用过：同 request_id 只有在内容完全一致时才是幂等重试。表单已换新 request_id，请重新提交。',
  CAD_VERSION_CHANGED:
    'previous_version / previous_source_sha256 已不是当前链尾：期间有人登记了新版本。已重新加载，请在对齐后重试。',
  CAD_SOURCE_ALREADY_CURRENT: '这份源文件与当前版本的源文件完全相同，不构成新版本。',
  CAD_ASSET_IDENTITY_CHANGED:
    '图号或零件号与上一版本不一致：这不能当成同一资产的新版本，需要改 document_id 重新登记。',
  CAD_REVIEW_VERSION_CHANGED:
    '你手上的版本指纹已经不是当前版本链尾（403/409 场景里的 CAS 校验失败）：通常是在你阅读期间有人登记了新版本。已按最新数据重新加载，请核对 v 号后再提交。',
  CAD_REVIEW_STATE_CHANGED:
    '该要素的复核链在提交前又前进了：你带的 previous_review_id 已不是链尾。已重新加载复核历史，请基于最新状态再提交。',
  CAD_REVIEW_INVALID: '复核请求体不合法（422 CAD_REVIEW_INVALID）。',
  CAD_REVIEW_TARGET_INVALID: '选中的目标 handle 不在该要素的 target_candidates 里。',
  CAD_REVIEW_TARGET_UNEXPECTED:
    '决策不是 confirmed 时不能带 selected_target（422 CAD_REVIEW_TARGET_UNEXPECTED）：本页在这种情况下发的是 null。',
  CAD_REVIEW_REASON_REQUIRED: '复核原因不能为空（422 CAD_REVIEW_REASON_REQUIRED）。',
  CAD_REVIEW_CHAIN_INVALID: '该要素的复核链断裂（previous_review_id 不连续），服务端拒绝读取这段历史。',
  CAD_REVIEW_HASH_MISMATCH: '历史复核记录自校验失败（record_sha256 对不上），服务端拒绝把它当作可信记录。',
  CAD_REVIEW_SNAPSHOT_MISMATCH: '历史复核记录与其快照/请求体对不上，服务端拒绝读取。',
  CAD_REVIEW_HISTORY_LIMIT: '该要素的复核历史超过 1000 条上限，服务端拒绝整体返回。',
}

const SNAPSHOT_ERROR_TEXT =
  '存储快照未通过服务端自校验（CAD_SNAPSHOT_*：哈希/身份/结构与登记内容对不上）。服务端因此拒绝把这份内容当作可信图纸读取 —— 这不是本页能修复的状态，也不代表「部分可用」。'

/** 可恢复的 409：指纹过期或 request_id 复用，重新加载后重试即可，不代表数据损坏。 */
const CONFLICT_CODES = new Set<string>([
  'CAD_REVIEW_VERSION_CHANGED',
  'CAD_REVIEW_STATE_CHANGED',
  'CAD_REVIEW_REQUEST_ID_REUSED',
  'CAD_REQUEST_ID_REUSED',
  'CAD_VERSION_CHANGED',
  'CAD_SOURCE_ALREADY_CURRENT',
  'CAD_ASSET_IDENTITY_CHANGED',
])

export function isCadConflict(code: unknown): boolean {
  if (typeof code !== 'string' || !code) return false
  return CONFLICT_CODES.has(code) || code.startsWith('CAD_SNAPSHOT')
}

export const CONFLICT_NOTE =
  '以上 409 都是可恢复的：它只说明「你手上的版本指纹或 request_id 已经不是最新的」，按当前数据重新加载再提交即可，不代表数据损坏，也不代表你操作错了。'

/**
 * 异常 → {code, text}。优先认 errorCode，其次在 message 里找 UPPER_SNAKE 码。
 * FastAPI 自己的 422（pydantic / Query 边界）**不带 error_code**，client.ts 会把它
 * 标成 VALIDATION_ERROR —— 这里显式讲清楚，不让它退化成一段原始 JSON。
 */
export function cadError(e: any, fallback: string): CadErrorView {
  const code = typeof e?.errorCode === 'string' && e.errorCode ? e.errorCode : ''
  const raw = typeof e?.message === 'string' ? e.message.trim() : ''
  let text = ''
  if (code && AUTH_ERROR_TEXT[code]) text = AUTH_ERROR_TEXT[code]
  else if (code === 'VALIDATION_ERROR')
    text = `请求体未通过 FastAPI/pydantic 校验（422 且**不带** error_code）：${raw || '参数不合法'}`
  else if (code.startsWith('CAD_SNAPSHOT')) text = SNAPSHOT_ERROR_TEXT
  else if (code && CAD_ERROR_TEXT[code]) text = CAD_ERROR_TEXT[code]
  else if (raw) text = `${fallback}：${raw}`
  else if (code) text = `${fallback}（${code}）`
  else text = fallback
  return { code, text }
}

/* ── request_id：同内容同 id 的幂等重试 ─────────────────────────────────── */

/**
 * 服务端把 (request_id, 内容, 操作者) 当成幂等键：
 *   - 内容完全一致 → 返回既有记录（这才是「重试」）；
 *   - 内容/操作者不同 → 409 *_REQUEST_ID_REUSED。
 * 所以这里记住「内容签名 → request_id」：重试同一份内容复用同一个 id，
 * 内容一改就换新 id，避免用户改了表单却撞上 409。
 */
const requestIds = new Map<string, { sig: string; id: string }>()

function randomHex(length: number): string {
  const bytes = new Uint8Array(Math.ceil(length / 2))
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, length)
}

export function stableRequestId(prefix: string, signature: string): string {
  const hit = requestIds.get(prefix)
  if (hit && hit.sig === signature) return hit.id
  const id = `${prefix}-${randomHex(24)}`
  requestIds.set(prefix, { sig: signature, id })
  return id
}

export function forgetRequestId(prefix: string): void {
  requestIds.delete(prefix)
}

export function forgetAllRequestIds(): void {
  requestIds.clear()
}

/** 当前记住的 id（仅用于显示，不参与提交）。 */
export function peekRequestId(prefix: string): string {
  return requestIds.get(prefix)?.id ?? ''
}

/* ── 展示小工具 ─────────────────────────────────────────────────────────── */

/** 哈希只展示前缀：够人肉比对，又不会把整屏塞满。 */
export function shortHash(value?: string | null, length = 12): string {
  if (!value) return '—'
  return value.length > length ? `${value.slice(0, length)}…` : value
}

/** 数值字段为空时的统一占位：空 ≠ 0。 */
export function orDash(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

export function listText(values?: string[] | null): string {
  if (!Array.isArray(values) || !values.length) return '—'
  return values.join(', ')
}
