/**
 * 尺寸核验（DimensionsPage）共用的本地映射与纯函数。
 *
 * WHY 单独一个模块：本页有三个地方要解释机器码（对照结论、审批表单、登记表单），
 * 三处必须给**同一句**中文解释；把映射留在组件里就会漂移成三份措辞。
 *
 * 两条铁律（后端契约决定了它们，不是审美偏好）：
 *   1. reason 是机器码。**未收录的码必须原样露出**（核验暂停：<code>），绝不能
 *      因为「不认识了」就渲染成空白或一句笼统的失败 —— 那等于把后端的拒绝理由吞掉。
 *   2. FastAPI 自己的 422（pydantic 请求体校验、Query 边界、DIMENSION_TIMEZONE_REQUIRED
 *      这类 validator 抛错）**不带 error_code**，client.ts 把它归一成 VALIDATION_ERROR。
 *      这类错误只能给一句通用中文，绝不能把 "undefined" 或原始 JSON 数组印到界面上。
 */

/* ── 通用文案 ─────────────────────────────────────────────────────────── */

/** 422-without-code（pydantic / 契约 validator）的唯一文案入口 */
export const VALIDATION_GENERIC = '请核对必填字段、时间格式与依据长度。'

/** 未知原因码的前缀：任何未收录的码都以此开头原样露出，绝不吞掉 */
export const UNKNOWN_REASON_PREFIX = '核验暂停：'

/**
 * reason 码 → 中文解释。
 *
 * 覆盖范围 = 后端 cad_dimensions.py（服务层）∪ ingestion/cad_dimension_contract.py
 * （冻结评估器）∪ 关联复核门槛（cad_contract*.py）∪ 认证中间件（auth.py）。
 * 新增码时如果忘了补这条映射，界面会显示「核验暂停：<码>」——这是**有意**的失败方式：
 * 宁可露出一个生码，也不能假装「没有原因」。
 */
export const REASON_TEXT: Record<string, string> = {
  /* ── 使用审批链 ──────────────────────────────────────────────────── */
  DIMENSION_USE_APPROVAL_REQUIRED:
    '这条绑定还没有生效的使用审批：对照被暂停，直到链尾出现一条 approved，且它必须由独立审批人给出。',
  DIMENSION_USE_APPROVAL_APPROVED:
    '链尾审批结论是 approved（同意使用）；它只代表这道使用闸门被放开，不代表任何制造授权或产品质量判定。',
  DIMENSION_USE_APPROVAL_REJECTED: '链尾审批结论是 rejected（否决）：对照被暂停。',
  DIMENSION_USE_APPROVAL_REVOKED: '链尾审批已被撤销（revoked）：对照被暂停。',
  DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE:
    '没有可撤销的生效审批：只有当链尾结论是 approved 时才允许撤销，否则后端返回 409。',
  DIMENSION_INDEPENDENT_APPROVER_REQUIRED:
    '审批人必须独立于这条绑定的登记人，也必须是该绑定引用的关联复核人之外的人（403）。',
  DIMENSION_APPROVAL_CHAIN_INVALID: '审批链不连续、时间倒序或存在重复 id：链本身不可信，核对被暂停。',
  DIMENSION_APPROVAL_STATE_CHANGED:
    '提交时审批链尾已经变了（previous_approval_id 不是当前链尾），请刷新后重试。',
  DIMENSION_APPROVAL_VERSION_MISMATCH: '审批事件引用的绑定 / 复核 / 尺寸哈希与当前证据不一致。',
  DIMENSION_APPROVAL_PERMISSION_REQUIRED: '该审批事件记录下的审批人当时没有 approver/admin 角色。',
  DIMENSION_APPROVAL_HISTORY_LIMIT: '审批事件超过 1000 条上限，无法整体校验。',
  DIMENSION_REASON_REQUIRED: '依据（reason / rationale）不能为空。',

  /* ── 当前链头 / 版本 CAS ─────────────────────────────────────────── */
  DIMENSION_CURRENT_HEAD_MISMATCH:
    '引用的图纸 / 尺寸记录 / 复核 / 绑定已经不是当前链头：这是一份历史快照，核对被暂停。',
  DIMENSION_DRAWING_VERSION_MISMATCH: '绑定的图纸版本、来源哈希或 bundle 哈希与图纸快照不一致。',
  DIMENSION_DRAWING_IDENTITY_MISMATCH: '绑定的图号 / 版次 / 件号与图纸快照里的业务标识不一致。',
  DIMENSION_MEASUREMENT_VERSION_MISMATCH: '绑定的尺寸记录版本或哈希与这条尺寸记录不一致。',
  DIMENSION_REVIEW_VERSION_MISMATCH: '绑定引用的关联复核版本或哈希与证据不一致。',
  DIMENSION_FEATURE_MISMATCH: '绑定引用的要素快照哈希与图纸里的该要素不一致。',
  DIMENSION_PARSER_MISMATCH: '图纸快照的解析器版本与记录引用的 parser_version 不一致。',
  DIMENSION_INPUT_VERSION_CHANGED:
    '登记时上游（图纸或尺寸记录）已经不是提交的版本，请重新读取上游后提交。',
  DIMENSION_MEASUREMENT_STATE_CHANGED:
    '尺寸记录链尾已变（previous_version / previous_sha256 不匹配），请刷新后重试。',
  DIMENSION_BINDING_STATE_CHANGED: '绑定链尾已变（previous_version / previous_sha256 不匹配），请刷新后重试。',
  DIMENSION_REVIEW_STATE_CHANGED: '关联复核链尾已变，或提交的复核哈希不是该复核的当前内容。',
  DIMENSION_SOURCE_CHANGED: '源文件字节与提交的 source_sha256 不一致（文件被改过）。',
  DIMENSION_REQUEST_ID_REUSED: '这个 request_id 之前被另一次内容不同的请求用过，不能复用。',
  DIMENSION_MEASUREMENT_IDENTITY_CHANGED:
    '新版本的 source_id 或件号与上一版不同：这不是同一条尺寸记录的新版本。',
  DIMENSION_BINDING_IDENTITY_CHANGED: '新版本的图纸或尺寸记录与上一版不同：这不是同一条绑定的新版本。',

  /* ── 关联复核与 CAD 门槛 ─────────────────────────────────────────── */
  DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED:
    '这个要素没有已 confirmed 的关联复核（或选定目标与绑定不一致）：绑定必须先有一条人工确认过关联目标的复核。',
  DIMENSION_REVIEW_NOT_FOUND: '绑定引用的复核记录不存在。',
  DIMENSION_REVIEW_PERMISSION_REQUIRED: '该关联复核记录下的复核人当时没有 reviewer/admin 角色。',
  CAD_ASSOCIATION_UNCERTAIN: '标注与几何目标的关联不确定（多个候选或没有候选），必须先人工确认后再用。',
  CAD_GEOMETRY_ANNOTATION_CONFLICT: '标注值与几何量测互相矛盾，不能作为比较依据。',
  CAD_VALUE_OR_UNIT_UNKNOWN: '图纸标注的值或单位无法解析（unknown），无法参与比较。',

  /* ── 尺寸记录 / 生产范围 / 有效期 ────────────────────────────────── */
  DIMENSION_MEASUREMENT_VALUE_OR_UNIT_UNKNOWN: '尺寸记录的值缺失或单位是 unknown，无法归一到 mm 比较。',
  DIMENSION_PART_MISMATCH: '尺寸记录的件号与图纸快照的件号不一致。',
  DIMENSION_PRODUCTION_SCOPE_MISMATCH:
    '绑定的生产范围（批次 / 工序 / 工艺版次 / 指标 / 要素角色 / 测量方法）与这条尺寸记录不一致。',
  DIMENSION_OUTSIDE_VALIDITY: '测量时间不落在绑定的有效区间 [valid_from, valid_to) 内。',
  DIMENSION_MEASUREMENT_IN_FUTURE: '测量时间晚于本次评估时间。',
  DIMENSION_VALIDITY_INTERVAL_INVALID: '有效区间不合法（valid_from 必须早于 valid_to）。',
  DIMENSION_TIMEZONE_REQUIRED:
    '时间必须带时区偏移（例如 2026-01-01T00:00:00+00:00）：没有偏移就无法判定先后。',
  DIMENSION_MEASUREMENT_NOT_POSITIVE: '尺寸值必须为正数。',

  /* ── 记录完整性 ─────────────────────────────────────────────────── */
  DIMENSION_RECORD_HASH_MISMATCH: '记录载荷与落库哈希不一致：内容被改过或已损坏。',
  DIMENSION_COMPARISON_SNAPSHOT_MISMATCH:
    '落库的对照快照与用当时证据重算的结果不一致，因此不能作为历史结论展示。',
  DIMENSION_COMPARISON_INVALID: '对照记录本身不满足契约（字段缺失或格式错误）。',
  DIMENSION_BINDING_INVALID: '绑定载荷不满足契约（字段或格式不合法）。',
  DIMENSION_BINDING_EVIDENCE_INVALID: '绑定登记时冻结的证据无法复现出「等待使用审批」这一步。',
  DIMENSION_BINDING_SNAPSHOT_MISMATCH: '绑定版本行与绑定载荷不一致。',
  DIMENSION_MEASUREMENT_SNAPSHOT_MISMATCH: '尺寸版本行与源文件重建出的记录不一致。',
  DIMENSION_RECORD_INVALID: '记录行无法解析为当前契约。',
  DIMENSION_RECORD_NOT_FOUND: '记录不存在（id 写错或已被删除）。',
  DIMENSION_ACTOR_MISMATCH: '记录里的操作者与落库的操作者快照不一致。',
  DIMENSION_REGISTRAR_PERMISSION_REQUIRED: '登记这条绑定时的登记人没有 admin 角色。',

  /* ── 源文件 ─────────────────────────────────────────────────────── */
  DIMENSION_STAGED_SOURCE_INVALID: '源文件不在允许的暂存目录里，或不是 .json 常规文件。',
  DIMENSION_SOURCE_INVALID: '源文件内容不满足尺寸记录契约。',
  DIMENSION_SOURCE_SIZE_INVALID: '源文件为空或超过大小上限。',

  /* ── 认证中间件（401 / 403）─────────────────────────────────────── */
  LOGIN_REQUIRED: '需要登录：本次请求没有有效身份（401）。',
  SESSION_INVALID: '会话已失效，请重新登录（401）。',
  PERMISSION_DENIED: '当前身份没有这次操作的权限（403）。',
  CSRF_REQUIRED: '缺少或失效的 CSRF 令牌，请刷新页面后重试（403）。',
  ORIGIN_NOT_ALLOWED: '请求来源不被允许：必须与后端同源（403）。',
}

/** 把（可能缺失的）reason 码翻成中文；未收录的码原样露出。 */
export function reasonText(code: string | null | undefined): string {
  const key = typeof code === 'string' ? code.trim() : ''
  if (!key) return '后端没有给出原因码。'
  return REASON_TEXT[key] ?? `${UNKNOWN_REASON_PREFIX}${key}`
}

/**
 * 使用审批链上 `decision` 的中文说法。
 * 界面上原来直接显示 approved / rejected / revoked 三个英文枚举，普通用户读不懂；
 * 未收录的取值仍原样露出（不猜、不吞）。
 */
export const APPROVAL_DECISION_TEXT: Record<string, string> = {
  approved: '同意使用',
  rejected: '否决',
  revoked: '已撤销',
}

export function approvalDecisionText(decision?: string | null): string {
  if (!decision) return '—'
  return APPROVAL_DECISION_TEXT[decision] ?? decision
}

/** 关联复核结论（association review 的 decision）的中文说法。 */
export const REVIEW_DECISION_TEXT: Record<string, string> = {
  confirmed: '已确认',
  rejected: '已驳回',
  needs_review: '仍需复核',
}

export function reviewDecisionText(decision?: string | null): string {
  if (!decision) return '—'
  return REVIEW_DECISION_TEXT[decision] ?? decision
}

/** 机器关联状态（features[].association_status）的中文说法：它是机器判定，不是人工结论。 */
export const MACHINE_ASSOCIATION_TEXT: Record<string, string> = {
  confirmed: '机器判定：抽取时已关联',
  ambiguous: '机器判定：有多个候选，未定',
  unresolved: '机器判定：无法解析',
}

export function featureAssociationText(status?: string | null): string {
  if (!status) return '未给出'
  return MACHINE_ASSOCIATION_TEXT[status] ?? status
}

/**
 * 任何 API 异常 → 一句中文。
 *
 * 三种情况必须分开处理（见文件头注释）：
 *   · 422-without-code → 通用中文，绝不打印原始 detail 数组 / undefined；
 *   · 已收录的码 → 「中文解释（错误码：CODE）」：先给一句人话，码放后面便于对照日志；
 *   · 其余 → 带上码与后端 message（若 message 里出现 undefined 就丢掉它）。
 */
export function apiErrorText(e: any, fallback: string): string {
  const code = typeof e?.errorCode === 'string' ? e.errorCode : ''
  const status = typeof e?.status === 'number' ? e.status : 0
  // FastAPI 自己的 422 没有 error_code（client.ts 归一成 VALIDATION_ERROR / HTTP_422）。
  if (code === 'VALIDATION_ERROR' || code === 'HTTP_422' || status === 422) return VALIDATION_GENERIC
  if (code && REASON_TEXT[code]) return `${REASON_TEXT[code]}（错误码：${code}）`
  const raw = typeof e?.message === 'string' ? e.message.trim() : ''
  // 「never print undefined」：后端在某些 422 下会把 detail 拼成含 undefined 的串，直接丢掉。
  const message = raw && !/undefined/i.test(raw) ? raw : ''
  if (code) return message ? `${fallback}（${code}）：${message}` : `${fallback}（${code}）`
  return message || fallback
}

/* ── 纯函数小工具 ─────────────────────────────────────────────────────── */

/**
 * 前端生成的幂等键。后端把它当 request_id 落库：同一个 request_id 重复提交时
 * 只回放已存记录而不是再写一条（所以重试是安全的，换内容必须换 id）。
 */
export function newRequestId(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID().replace(/-/g, '')
  return `req-${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`
}

/** 带时区的 ISO 串 → 「2026-10-12 10:34」（本机时区，分钟精度）。 */
export function whenText(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 哈希只显示前 12 位：够用于人工比对，又不至于把卡片撑爆。全值留在 title 里。 */
export function shortHash(value?: string | null): string {
  const text = typeof value === 'string' ? value : ''
  if (!text) return '—'
  return text.length > 12 ? `${text.slice(0, 12)}…` : text
}

/** 值/单位显示：null 值必须说「源文件里就没有值」，不能渲染成空字符串。 */
export function valueWithUnit(value: string | null | undefined, unit: string | null | undefined): string {
  const u = typeof unit === 'string' && unit ? unit : '单位未知'
  if (value === null || value === undefined || value === '') return `（无值，单位 ${u}）`
  return `${value} ${u}`
}

/* ── 与后端契约同源的格式校验（本地先拦一次，省一次往返）─────────────── */

/** Identifier: ^[A-Za-z0-9][A-Za-z0-9_.-]*$ 且 ≤128 */
export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/
/** Handle: ^[1-9A-F][0-9A-F]*$ 且 ≤32 */
export const HANDLE_PATTERN = /^[1-9A-F][0-9A-F]*$/
/** Sha256: ^[0-9a-f]{64}$ */
export const SHA_PATTERN = /^[0-9a-f]{64}$/
/** Timestamp 必须带时区偏移（Z 或 ±HH:MM）；后端 instant() 会拒绝 naive 时间 */
export const TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/

/** 单个字段的本地校验：返回中文错误，空串表示本地看不出问题。 */
export function checkIdentifier(label: string, value: string, max = 128): string {
  const text = (value ?? '').trim()
  if (!text) return `${label}不能为空`
  if (text.length > max) return `${label}过长（最多 ${max} 字符）`
  if (!ID_PATTERN.test(text)) return `${label}只能包含字母、数字、下划线、点与连字符，且必须以字母或数字开头`
  return ''
}

export function checkHandle(label: string, value: string): string {
  const text = (value ?? '').trim()
  if (!text) return `${label}不能为空`
  if (text.length > 32) return `${label}过长（最多 32 字符）`
  if (!HANDLE_PATTERN.test(text)) return `${label}必须是 1-9A-F 开头的大写十六进制句柄`
  return ''
}

export function checkSha(label: string, value: string): string {
  const text = (value ?? '').trim()
  if (!text) return `${label}不能为空`
  if (!SHA_PATTERN.test(text)) return `${label}必须是 64 位小写十六进制 sha256`
  return ''
}

/** 时间校验的文案直接复用后端码的中文解释，保证前后端说法一致。 */
export function checkTimestamp(label: string, value: string): string {
  const text = (value ?? '').trim()
  if (!text) return `${label}不能为空`
  if (text.length < 20 || text.length > 40) return `${label}长度不合法（应为 20–40 字符的 ISO 8601）`
  if (!TIMESTAMP_PATTERN.test(text)) return `${label}：${REASON_TEXT.DIMENSION_TIMEZONE_REQUIRED}`
  return ''
}
