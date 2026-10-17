import { computed, ref } from 'vue'

import {
  authorityCandidates,
  listAuthorityRules,
  me,
  registerAuthorityRule,
  submitAuthorityEvent,
} from '../../api/client'
import type {
  ApprovalDecision,
  AuthorityBasis,
  AuthorityCandidate,
  AuthorityRule,
  AuthorityRuleInput,
  AuthUser,
} from '../../api/types'

/**
 * 「来源权威规则」的状态与动作（后端 datamind/authority.py）。
 *
 * 三条后端事实决定了本模块的形状：
 *
 *   1. 规则一经登记**不可修改**，只能追加事件（source_authority_events）。所以「状态」
 *      不是服务端的一个列，而是**链尾事件的 decision**：没有事件 = 待审批。UI 显示的
 *      「待审批 / 已批准 / 已驳回 / 已撤销」全部由这一条推导，绝不另设一份本地状态。
 *   2. `payload_valid=false` 表示登记载荷与 payload_sha256（或契约字段集合）对不上。
 *      这条规则不能作为依据，而且只要注册表里存在这样一条，运行时裁决就整体 fail closed
 *      （AUTHORITY_REGISTRY_INVALID）——不是「跳过坏的那条、按好的判」。所以对它的处理是
 *      只显示一行失败说明，既不展示载荷详情、也不给任何启用按钮。
 *   3. **登记/事件的响应体里没有 decision、没有 event 链**：刚登记完就显示「已批准」会是
 *      撒谎。因此任何写操作成功后都必须重新拉 GET /v1/authority/rules（本模块的
 *      reloadRules 就是干这个的），事件动作还要带上链尾 event_id 作为 previous_event_id。
 *
 * WHY 不做模块级单例（与 useSessions 不同）：会话列表被侧边栏和主区同时读，必须共享同一
 * 份 activeId；权威页只有一个消费者，而规则行上的可操作性**与账户绑定**（创建人不能批准
 * 自己的规则）。旧版前端出过「退出后规则列表还留着上一个账户的可操作卡片」的问题，所以
 * 这里刻意让状态随组件卸载一起消失，不跨账户残留。
 */

/* ── 错误信封 → 人能读的失败说明 ─────────────────────────────────────────
   后端错误恒为 {"detail":{"error_code":"CODE"}}；FastAPI 自己的 422 没有 error_code，
   client.ts 会把它标成 VALIDATION_ERROR。UI 必须把**错误码本身**也显示出来：
   中文解释是给人看的，码是给排查与复现用的，两者都不能吞掉。 */

export interface AuthorityFailure {
  code: string
  message: string
  status: number | null
}

const ERROR_TEXT: Record<string, string> = {
  // 身份 / 权限（默认拒绝中间件）
  LOGIN_REQUIRED: '需要先登录：查询候选与登记规则要 admin（管理员），批准 / 驳回 / 撤销要 approver（审批员）',
  SESSION_INVALID: '登录会话已失效，请重新登录后再操作',
  PERMISSION_DENIED:
    '当前账户角色不足：登记规则要 admin（管理员），批准 / 驳回 / 撤销要 approver（审批员）；admin 可以通过所有角色门',
  CSRF_REQUIRED: '这次写操作缺少安全令牌，请刷新页面重新登录后再试',
  ORIGIN_NOT_ALLOWED: '请求来源不被允许：只能从本站页面发起，不能用外部地址直连',
  VALIDATION_ERROR: '请求内容没通过字段校验（服务端返回 422，没有给出错误码）',
  // 登记表单
  AUTHORITY_VALIDITY_INVALID:
    '有效期不合法：时间必须带时区偏移（如 +08:00），并且起点 ≤ 测量时间 < 终点',
  AUTHORITY_RATIONALE_REQUIRED: '登记理由不能为空',
  AUTHORITY_CONTEXT_CHANGED: '候选的上下文已经变了（生产记录、报告事实或版本已变），请重新查询候选后再登记',
  APPROVED_FIELD_REQUIRED: '报告字段尚未独立审批，请先到字段复核处理。',
  AUTHORITY_BASIS_INVALID: '依据无效：必须是已发布的 PDF，且版本、SHA-256、页码都要对得上（上传件不能作依据）',
  AUTHORITY_REQUEST_ID_REUSED: '这次提交的标识已经用于另一份内容，已重新加载最新状态，请重新发起',
  // 事件（启用 / 驳回 / 撤销）
  AUTHORITY_RULE_NOT_FOUND: '这条规则不存在（可能已超出注册表范围）',
  AUTHORITY_REASON_REQUIRED: '审批理由不能为空',
  AUTHORITY_STATE_CHANGED: '规则的链尾已经变了（另一个账户刚操作过），已重新加载，请基于最新状态重新操作',
  AUTHORITY_PAYLOAD_CHANGED: '登记的载荷与哈希对不上：这一条不能作为权威依据，服务端已拒绝启用',
  AUTHORITY_CREATOR_CANNOT_APPROVE: '不能启用 / 驳回 / 撤销自己登记的规则，必须换另一个审批账户',
  AUTHORITY_CANNOT_APPROVE: '不能启用 / 驳回 / 撤销自己登记的规则，必须换另一个审批账户',
  NO_AUTHORITY_APPROVAL_TO_REVOKE: '撤销之前必须有一条已批准的记录（待审批或已驳回的规则没有可撤销的批准）',
  // 注册表整体
  AUTHORITY_REGISTRY_LIMIT: '注册表超过 1000 条上限，服务端拒绝读取（503），本页不做截断展示',
  // 候选查询（问题解析不出来时后端返回 422 + 原因串，这些码没有中文信封）
  INDUSTRIAL_SCOPE_REQUIRED:
    '问题没有解析出唯一范围：需要同时包含 A### 零件号、「工序 N」、年月 与「温度」字样',
  INDUSTRIAL_DATA_NOT_READY: '工业演示数据还没准备好，候选查询无法进行',
  SOURCE_VERSION_MISMATCH: '数据集或来源版本对不上，候选查询被拒绝（不做版本降级）',
  INDUSTRIAL_ROW_LIMIT: '命中的行数超过 1000 上限，服务端拒绝返回候选',
  INVALID_TEMPERATURE: '这个数值不是可比的工业温度值（本页只处理温度冲突）',
  UNKNOWN_UNIT: '单位不在可换算清单里，没法比较',
}

/** 这些 409 都是「世界变了」而不是「你填错了」：显示码，然后重新拉规则（+候选）再让人决定。 */
export const RECOVERABLE_CODES = new Set([
  'AUTHORITY_CONTEXT_CHANGED',
  'APPROVED_FIELD_REQUIRED',
  'AUTHORITY_BASIS_INVALID',
  'AUTHORITY_STATE_CHANGED',
  'AUTHORITY_PAYLOAD_CHANGED',
  'AUTHORITY_REQUEST_ID_REUSED',
])

function describe(code: string, raw: string, fallback: string): string {
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  if (raw) {
    for (const key of Object.keys(ERROR_TEXT)) {
      if (raw.includes(key)) return ERROR_TEXT[key]
    }
    return `${fallback}：${raw}`
  }
  return fallback
}

/** 任意异常 → {code,message,status}。code 原样保留，绝不改写成别的东西。 */
export function toFailure(e: unknown, fallback: string): AuthorityFailure {
  const err = e as { errorCode?: unknown; message?: unknown; status?: unknown } | null
  const code = typeof err?.errorCode === 'string' ? err.errorCode : ''
  const raw = typeof err?.message === 'string' ? err.message : ''
  const status = typeof err?.status === 'number' ? err.status : null
  return { code, message: describe(code, raw, fallback), status }
}

export function failureText(f: AuthorityFailure | null): string {
  if (!f) return ''
  return f.code ? `${f.message}（错误码：${f.code}）` : f.message
}

function localFailure(code: string, message: string): AuthorityFailure {
  return { code, message, status: null }
}

/* ── 规则状态的推导（服务端没有 status 列）───────────────────────────── */

export type AuthorityRuleState = 'pending' | 'approved' | 'rejected' | 'revoked' | 'unknown'

export const RULE_STATE_TEXT: Record<AuthorityRuleState, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  revoked: '已撤销',
  unknown: '未知状态',
}

/**
 * 链尾事件决定状态。
 * decision 为空 = 从来没有事件 = 待审批（**不是**「未知」，这是正常的初始状态）。
 * 出现了三个字面值之外的 decision 只能说「未知」，不替服务端猜。
 */
export function ruleState(rule: AuthorityRule): AuthorityRuleState {
  const d = rule?.decision
  if (d === 'approved' || d === 'rejected' || d === 'revoked') return d
  if (d == null) return 'pending'
  return 'unknown'
}

/** 裁决「以哪一侧为准」。两侧原值都必须并陈，这个函数只用来标注被选中的一侧。 */
export function sourceText(source?: string | null): string {
  if (source === 'production') return '生产数据（SQL 实测）'
  if (source === 'report') return '报告字段（PDF 事实）'
  return '未指定'
}

/** 依据页链接：只用相对路径（绝对 http://127.0.0.1:8000 会被同源校验拒绝）。 */
export function basisPageHref(
  documentId?: string | null,
  pageNo?: string | number | null,
): string {
  const id = typeof documentId === 'string' ? documentId.trim() : ''
  const page = Number(pageNo)
  if (!id || !Number.isInteger(page) || page < 1) return ''
  return `/v1/documents/${encodeURIComponent(id)}/file#page=${page}`
}

export function basisHref(basis?: AuthorityBasis | null): string {
  return basisPageHref(basis?.document_id, basis?.page_no)
}

/* ── 登记表单的草稿 ───────────────────────────────────────────────────── */

/** 表单里的数字/时间一律按**字符串**保存：naive 与带时区的串必须原样交给服务端判，不在前端悄悄补时区。 */
export interface AuthorityDraft {
  request_id: string
  question: string
  measurement_id: string
  candidate_sha256: string
  selected_source: '' | 'production' | 'report'
  valid_from: string
  valid_to: string
  document_id: string
  document_version: string
  source_sha256: string
  page_no: string
  rationale: string
}

let requestSeq = 0

/**
 * 幂等键。同一次填写的**重试沿用同一个 id**：网络超时后重试不会登记出第二条规则
 * （服务端同 id 同载荷直接返回原规则，同 id 不同载荷 → 409 AUTHORITY_REQUEST_ID_REUSED）。
 * 只有「换候选重新填」或「提交成功后」才生成新的 id。
 * 格式必须匹配后端的 ^[A-Za-z0-9_-]+$ 且 ≤64。
 */
function newRequestId(prefix: string): string {
  requestSeq += 1
  const rand = Math.random().toString(36).slice(2, 10)
  return `${prefix}-${Date.now().toString(36)}-${requestSeq.toString(36)}-${rand}`
}

export function emptyDraft(question = ''): AuthorityDraft {
  return {
    request_id: newRequestId('authority-reg'),
    question,
    measurement_id: '',
    candidate_sha256: '',
    selected_source: '',
    valid_from: '',
    valid_to: '',
    document_id: '',
    document_version: '',
    source_sha256: '',
    page_no: '',
    rationale: '',
  }
}

/** 候选 → 草稿。basis / 有效期全部来自候选的建议值（它们已经带有 +08:00）。 */
export function draftFromCandidate(candidate: AuthorityCandidate, question: string): AuthorityDraft {
  const basis = candidate?.basis_suggestion
  return {
    request_id: newRequestId('authority-reg'),
    question,
    measurement_id: candidate?.context?.scope?.measurement_id ?? '',
    candidate_sha256: candidate?.candidate_sha256 ?? '',
    // 刻意**不**预选来源：来源名字不构成权威，谁更权威必须由人显式指定。
    selected_source: '',
    valid_from: candidate?.valid_from ?? '',
    valid_to: candidate?.valid_to ?? '',
    document_id: basis?.document_id ?? '',
    document_version: basis?.document_version != null ? String(basis.document_version) : '',
    source_sha256: basis?.source_sha256 ?? '',
    page_no: basis?.page_no != null ? String(basis.page_no) : '',
    rationale: '',
  }
}

/* ── 有效期校验（镜像服务端规则，用来在提交前拦住必然的 422）────────────── */

const OFFSET_RE = /(Z|[+-]\d{2}:\d{2})$/

/** 是否带显式时区偏移。naive 串（2026-10-01T08:00）会被后端 422 AUTHORITY_VALIDITY_INVALID。 */
export function hasExplicitOffset(value: string): boolean {
  return OFFSET_RE.test((value || '').trim())
}

/** valid_from ≤ produced_at < valid_to（左闭右开）。无法解析时返回 null = 判不了。 */
export function windowContains(from: string, to: string, producedAt: string): boolean | null {
  const f = Date.parse(from)
  const t = Date.parse(to)
  const p = Date.parse(producedAt)
  if (Number.isNaN(f) || Number.isNaN(t) || Number.isNaN(p)) return null
  return f < t && f <= p && p < t
}

const SHA256_RE = /^[a-f0-9]{64}$/

/** 草稿的本地校验：只拦「必然失败」的请求，判不了的（窗口/依据发布状态）交给服务端判。 */
export function validateDraft(draft: AuthorityDraft, producedAt: string): AuthorityFailure | null {
  if (!draft.question.trim()) {
    return localFailure('VALIDATION_ERROR', 'question 不能为空：服务端要用它重新解析候选')
  }
  if (!draft.measurement_id.trim()) {
    return localFailure('VALIDATION_ERROR', 'measurement_id 不能为空（必须来自候选的 scope）')
  }
  if (!SHA256_RE.test(draft.candidate_sha256.trim())) {
    return localFailure('VALIDATION_ERROR', 'candidate_sha256 必须是候选上的 64 位十六进制值')
  }
  if (draft.selected_source !== 'production' && draft.selected_source !== 'report') {
    return localFailure('VALIDATION_ERROR', '必须显式选择以哪一侧为准（production / report），没有默认值')
  }
  if (!hasExplicitOffset(draft.valid_from) || !hasExplicitOffset(draft.valid_to)) {
    return localFailure(
      'AUTHORITY_VALIDITY_INVALID',
      '有效期必须带显式时区偏移（如 2026-10-01T00:00:00+08:00）；naive 时间会被服务端 422 拒绝',
    )
  }
  // 只有**确定**窗口不含 produced_at 才在本地拦下。解析不出来（null，例如服务端给了
  // 6 位小数秒这种 JS 不一定认的写法）时不敢替服务端下结论，放行让它自己判——
  // 本地多拦一次就会把一个合法登记挡在门外，那比多一次往返更糟。
  const inWindow = windowContains(draft.valid_from, draft.valid_to, producedAt)
  if (inWindow === false) {
    return localFailure(
      'AUTHORITY_VALIDITY_INVALID',
      `规则必须覆盖候选的 produced_at（${producedAt}），即 valid_from ≤ produced_at < valid_to；当前窗口不满足`,
    )
  }
  const version = Number(draft.document_version)
  if (!Number.isInteger(version) || version < 1) {
    return localFailure('VALIDATION_ERROR', 'document_version 必须是不小于 1 的整数')
  }
  if (!draft.document_id.trim()) {
    return localFailure('VALIDATION_ERROR', 'basis.document_id 不能为空')
  }
  if (!SHA256_RE.test(draft.source_sha256.trim())) {
    return localFailure('VALIDATION_ERROR', 'basis.source_sha256 必须是 64 位十六进制值')
  }
  const page = Number(draft.page_no)
  if (!Number.isInteger(page) || page < 1) {
    return localFailure('VALIDATION_ERROR', 'basis.page_no 必须是不小于 1 的整数')
  }
  const rationale = draft.rationale.trim()
  if (rationale.length < 10 || rationale.length > 1000) {
    return localFailure('VALIDATION_ERROR', 'rationale 长度必须是 10..1000 字（后端 AuthorityRequest 的约束）')
  }
  return null
}

/* ── 组合式入口 ───────────────────────────────────────────────────────── */

export function useAuthority() {
  /* 身份 */
  const identity = ref<AuthUser | null>(null)
  const identityLoading = ref(false)
  const identityFailure = ref<AuthorityFailure | null>(null)

  const roles = computed<string[]>(() => {
    const raw = identity.value?.roles
    return Array.isArray(raw) ? raw.map((r) => String(r)) : []
  })
  const isAdmin = computed(() => roles.value.includes('admin'))
  /** 契约：admin 通过**所有**角色门，所以这里要把 admin 也算成 approver。 */
  const isApprover = computed(() => roles.value.includes('approver') || isAdmin.value)

  /* 规则注册表 */
  const rules = ref<AuthorityRule[]>([])
  const rulesLoading = ref(false)
  const rulesFailure = ref<AuthorityFailure | null>(null)

  /* 候选 */
  const question = ref('')
  const candidates = ref<AuthorityCandidate[]>([])
  const candidatesLoading = ref(false)
  const candidatesFailure = ref<AuthorityFailure | null>(null)
  /** 是否已经查过一次：用来区分「还没查」与「查了但没有候选」 */
  const candidatesQueried = ref(false)

  /* 登记 */
  const selected = ref<AuthorityCandidate | null>(null)
  const draft = ref<AuthorityDraft>(emptyDraft())
  const registering = ref(false)
  const registerFailure = ref<AuthorityFailure | null>(null)
  const registerNotice = ref('')

  /* 事件 */
  const actingRuleId = ref<string | null>(null)
  const actionFailure = ref<AuthorityFailure | null>(null)
  const actionNotice = ref('')
  const reasons = ref<Record<string, string>>({})

  const loading = computed(
    () => rulesLoading.value || candidatesLoading.value || identityLoading.value,
  )
  const hasInvalidRule = computed(() => rules.value.some((r) => r.payload_valid === false))

  async function loadIdentity(): Promise<void> {
    identityLoading.value = true
    try {
      const res = await me()
      identity.value = (res?.user ?? null) as AuthUser | null
      identityFailure.value = null
    } catch (e) {
      identity.value = null
      identityFailure.value = toFailure(e, '读取当前身份失败')
    } finally {
      identityLoading.value = false
    }
  }

  async function loadRules(): Promise<void> {
    rulesLoading.value = true
    try {
      const res = await listAuthorityRules()
      rules.value = Array.isArray(res?.rules) ? res.rules : []
      rulesFailure.value = null
    } catch (e) {
      rules.value = []
      rulesFailure.value = toFailure(e, '规则注册表加载失败')
    } finally {
      rulesLoading.value = false
    }
  }

  async function loadCandidates(nextQuestion?: string): Promise<void> {
    if (typeof nextQuestion === 'string') question.value = nextQuestion
    const q = question.value.trim()
    if (!q) {
      // 没有问题时连「查不到候选」都不能说：清掉上一轮结果，避免把旧候选当成本次查询的产物。
      candidates.value = []
      candidatesQueried.value = false
      candidatesFailure.value = localFailure('VALIDATION_ERROR', '请先输入问题（1..2000 字）')
      return
    }
    candidatesLoading.value = true
    try {
      const res = await authorityCandidates(q)
      candidates.value = Array.isArray(res?.candidates) ? res.candidates : []
      candidatesFailure.value = null
    } catch (e) {
      candidates.value = []
      candidatesFailure.value = toFailure(e, '候选查询失败')
    } finally {
      candidatesQueried.value = true
      candidatesLoading.value = false
    }
  }

  /**
   * 选中一个候选 → 用候选上的建议值重填登记表。
   * 每次都换新的 request_id：换候选就是换规则，沿用旧 id 只会撞 AUTHORITY_REQUEST_ID_REUSED。
   */
  function selectCandidate(candidate: AuthorityCandidate): void {
    selected.value = candidate
    draft.value = draftFromCandidate(candidate, question.value.trim())
    registerFailure.value = null
    registerNotice.value = ''
  }

  function clearSelection(): void {
    selected.value = null
    draft.value = emptyDraft(question.value.trim())
    registerFailure.value = null
  }

  /** 写操作成功后：规则列表必须重拉（响应里没有 decision）；候选也要重查（适用性可能已变）。 */
  async function reloadAfterMutation(): Promise<void> {
    await loadRules()
    if (candidatesQueried.value) await loadCandidates()
  }

  async function submitRegister(): Promise<boolean> {
    registerNotice.value = ''
    const candidate = selected.value
    if (!candidate) {
      registerFailure.value = localFailure('VALIDATION_ERROR', '请先在候选面板里选中一条候选')
      return false
    }
    // 硬门槛：eligible=false 表示报告侧事实没有「已认证的确认复核 + 独立审批」，
    // 服务端会以 409 APPROVED_FIELD_REQUIRED 拒绝。这里连请求都不发。
    if (candidate.eligible === false) {
      registerFailure.value = localFailure(
        'APPROVED_FIELD_REQUIRED',
        '报告字段尚未独立审批，请先到字段复核处理。',
      )
      return false
    }
    const invalid = validateDraft(draft.value, candidate.produced_at)
    if (invalid) {
      registerFailure.value = invalid
      return false
    }
    const d = draft.value
    const body: AuthorityRuleInput = {
      request_id: d.request_id,
      question: d.question.trim(),
      measurement_id: d.measurement_id.trim(),
      candidate_sha256: d.candidate_sha256.trim(),
      selected_source: d.selected_source === 'report' ? 'report' : 'production',
      valid_from: d.valid_from.trim(),
      valid_to: d.valid_to.trim(),
      basis: {
        document_id: d.document_id.trim(),
        document_version: Number(d.document_version),
        source_sha256: d.source_sha256.trim(),
        page_no: Number(d.page_no),
      },
      rationale: d.rationale.trim(),
    }
    registering.value = true
    try {
      const res = await registerAuthorityRule(body)
      const ruleId = res?.rule?.rule_id ?? ''
      // 登记响应**没有** decision/event：这里只能说「已登记，待审批」。
      registerNotice.value = `已登记规则 ${ruleId}（当前状态为待审批；登记响应不含审批结论，已重新拉取注册表）。`
      clearSelection()
      await reloadAfterMutation()
      return true
    } catch (e) {
      const failure = toFailure(e, '规则登记失败')
      // 候选上下文/资格变了：表单里预填的 candidate_sha256、依据页与有效期都已作废，
      // 再提交一次必然还是同一个错。清空表单（这是诚实做法），错误码照原样显示。
      if (
        failure.code === 'AUTHORITY_CONTEXT_CHANGED' ||
        failure.code === 'APPROVED_FIELD_REQUIRED'
      ) {
        clearSelection()
        failure.message = `${failure.message}（候选上下文/资格已变，登记表已清空，请重新查询候选）`
      }
      registerFailure.value = failure
      if (RECOVERABLE_CODES.has(failure.code)) await reloadAfterMutation()
      return false
    } finally {
      registering.value = false
    }
  }

  /**
   * 批准 / 驳回 / 撤销。
   * previous_event_id 必须等于**链尾** event_id（来自刚拉取的列表）；不等于就 409
   * AUTHORITY_STATE_CHANGED —— 那种情况重新拉列表再让人决定，不自动重试。
   */
  async function decide(
    rule: AuthorityRule,
    decision: ApprovalDecision,
    reason: string,
  ): Promise<boolean> {
    const text = (reason || '').trim()
    actionFailure.value = null
    actionNotice.value = ''
    if (!isApprover.value) {
      actionFailure.value = localFailure(
        'PERMISSION_DENIED',
        ERROR_TEXT.PERMISSION_DENIED,
      )
      return false
    }
    if (rule.creator_user_id && rule.creator_user_id === identity.value?.user_id) {
      actionFailure.value = localFailure(
        'AUTHORITY_CREATOR_CANNOT_APPROVE',
        ERROR_TEXT.AUTHORITY_CREATOR_CANNOT_APPROVE,
      )
      return false
    }
    if (!text) {
      actionFailure.value = localFailure('AUTHORITY_REASON_REQUIRED', ERROR_TEXT.AUTHORITY_REASON_REQUIRED)
      return false
    }
    if (decision === 'revoked' && ruleState(rule) !== 'approved') {
      actionFailure.value = localFailure(
        'NO_AUTHORITY_APPROVAL_TO_REVOKE',
        ERROR_TEXT.NO_AUTHORITY_APPROVAL_TO_REVOKE,
      )
      return false
    }
    actingRuleId.value = rule.rule_id
    try {
      await submitAuthorityEvent(rule.rule_id, {
        // 每次动作一个新的幂等键：动作内容（理由/结论）变了就不该复用旧 id。
        request_id: newRequestId('authority-evt'),
        previous_event_id: rule.event_id ?? null,
        decision,
        reason: text,
      })
      const label = decision === 'approved' ? '已批准' : decision === 'rejected' ? '已驳回' : '已撤销'
      actionNotice.value = `规则 ${rule.rule_id} ${label}（事件已追加；规则本身不可修改，只追加事件）。`
      reasons.value[rule.rule_id] = ''
      await reloadAfterMutation()
      return true
    } catch (e) {
      const failure = toFailure(e, '审批动作失败')
      actionFailure.value = failure
      if (RECOVERABLE_CODES.has(failure.code)) await reloadAfterMutation()
      return false
    } finally {
      actingRuleId.value = null
    }
  }

  async function bootstrap(): Promise<void> {
    await loadIdentity()
    await loadRules()
  }

  async function refreshAll(): Promise<void> {
    await loadIdentity()
    await loadRules()
    if (candidatesQueried.value) await loadCandidates()
  }

  return {
    // 身份
    identity,
    roles,
    isAdmin,
    isApprover,
    identityLoading,
    identityFailure,
    // 规则
    rules,
    rulesLoading,
    rulesFailure,
    hasInvalidRule,
    // 候选
    question,
    candidates,
    candidatesLoading,
    candidatesFailure,
    candidatesQueried,
    // 登记
    selected,
    draft,
    registering,
    registerFailure,
    registerNotice,
    // 事件
    actingRuleId,
    actionFailure,
    actionNotice,
    reasons,
    // 视图辅助
    loading,
    // 动作
    loadIdentity,
    loadRules,
    loadCandidates,
    selectCandidate,
    clearSelection,
    submitRegister,
    decide,
    bootstrap,
    refreshAll,
  }
}
