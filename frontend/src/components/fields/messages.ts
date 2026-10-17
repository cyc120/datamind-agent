/**
 * 字段复核 —— 文案与错误解释的单一来源。
 *
 * WHY 集中在纯模块里：
 *   本屏的每一次写操作（复核 / 审批）都可能以 409 的「版本或状态已经动了」结束。
 *   这类失败**必须**被讲成「数据动了，请重新核对」，绝不能静默重试、也不能把
 *   409 说成「提交失败」让用户以为是网络问题而反复点提交（那只会制造重复记录）。
 *   把判定放这里，视图层就不会各自发明一套说法。
 */

import type { ApprovalDecision, FactDecision, FactExtractionQuality } from '../../api/types'

/* ── 正式审批状态 ──────────────────────────────────────────────────────── */

/**
 * formal_approval_status 的中文展示。
 * stale 这一条是本屏最重要的状态：审批有效的前提是它绑定的那条复核仍是当前链尾；
 * 一旦版本/复核变了，旧审批不再是「已批准」，而是「复核已变化，审批失效」——
 * 新的复核不能绕过它，必须走一次**全新的**独立审批。
 */
export const FORMAL_APPROVAL_TEXT: Record<string, string> = {
  not_approved: '未审批',
  approved: '已批准',
  rejected: '已驳回',
  stale: '复核已变化，审批失效',
}

export function formalApprovalText(status?: string | null): string {
  if (!status) return '未审批'
  return FORMAL_APPROVAL_TEXT[status] ?? status
}

/** stale / rejected 都要用警示色，不能让它们看起来和 已批准 一样。 */
export function formalApprovalTone(status?: string | null): 'ok' | 'warn' | 'bad' | 'muted' {
  if (status === 'approved') return 'ok'
  if (status === 'stale') return 'warn'
  if (status === 'rejected') return 'bad'
  return 'muted'
}

export const REVIEW_DECISION_TEXT: Record<string, string> = {
  confirmed: '确认抽取忠实',
  rejected: '驳回',
  needs_review: '需复核',
}

export const APPROVAL_DECISION_TEXT: Record<string, string> = {
  approved: '批准',
  rejected: '驳回',
  revoked: '撤销',
}

export function reviewDecisionText(decision?: string | null): string {
  if (!decision) return '—'
  return REVIEW_DECISION_TEXT[decision] ?? decision
}

export function approvalDecisionText(decision?: string | null): string {
  if (!decision) return '—'
  return APPROVAL_DECISION_TEXT[decision] ?? decision
}

/* ── 检索可用性（审批 ≠ 可检索）────────────────────────────────────────── */

/**
 * index_state 的中文说法。
 * 注意别和旁边那个 searchable 徽标写成同一句话：两个徽标挨着显示，
 * 若都写「检索器读得到」用户会以为是重复渲染（曾经就是这样）。
 * 所以这里只说「有没有建立索引」，searchable 那个徽标才说「读得到读不到」。
 */
export const INDEX_STATE_TEXT: Record<string, string> = {
  indexed: '已建立索引',
  no_chunks: '没有文本块，无法建索引',
  not_approved: '未通过复核，暂不建索引',
}

export function indexStateText(state?: string | null): string {
  if (!state) return '未知'
  return INDEX_STATE_TEXT[state] ?? state
}

/** 不可检索的原因码（后端原样下发）；未知码原样显示，不翻译成一句含糊的话。 */
export const INDEX_NOTE_TEXT: Record<string, string> = {
  UPLOAD_NOT_APPROVED: '上传尚未通过复核',
  UPLOAD_HAS_NO_CHUNKS: '上传没有生成文本块',
  UPLOAD_HAS_NO_CONTRACT_BLOCKS: '上传没有生成契约块',
}

export function indexNoteText(note?: string | null): string | null {
  if (!note) return null
  return INDEX_NOTE_TEXT[note] ?? note
}

/* ── 抽取质量 ─────────────────────────────────────────────────────────── */

/**
 * `quality.unresolved` 的条数。
 * 后端目前是**数组**（每条一个未确认的单元格/表头），但也允许是数字；两种都认，
 * 认不出来就返回 null（而不是 0）——「不知道有没有未确认项」和「一处都没有」是
 * 两件不同的事，后者才允许说「没有」。
 */
export function unresolvedCount(quality: FactExtractionQuality | null | undefined): number | null {
  const raw = (quality as Record<string, unknown> | null | undefined)?.unresolved
  if (Array.isArray(raw)) return raw.length
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw
  return null
}

/** 抽取口径说明（后端原文，例如「仅显式扫描单元格；实体 ID/批次/有效期未知」）。 */
export function extractionScope(quality: FactExtractionQuality | null | undefined): string {
  const scope = (quality as Record<string, unknown> | null | undefined)?.scope
  return typeof scope === 'string' ? scope : ''
}

/** 抽取侧的审批口径声明（industrial 事实会带 approval_semantics）。 */
export function approvalSemantics(quality: FactExtractionQuality | null | undefined): string {
  const text = (quality as Record<string, unknown> | null | undefined)?.approval_semantics
  return typeof text === 'string' ? text : ''
}

/* ── 错误解释 ─────────────────────────────────────────────────────────── */

export interface FieldErrorInfo {
  /** 后端 error_code；422（pydantic）没有码时为 VALIDATION_ERROR */
  code: string
  status: number | null
  /** 直接可显示的中文（含错误码本身，便于和服务端日志对照） */
  text: string
  /** true = 版本/状态已经动了：必须重新加载并重新核对，绝不能静默重试 */
  recoverable: boolean
}

/** 版本/状态类 409：都表示「你手上的快照已经过期」，处理方式只有重新加载。 */
const RECOVERABLE_CODES = new Set<string>([
  'FACT_UNAVAILABLE_OR_STALE',
  'FACT_VERSION_MISMATCH',
  'SOURCE_VERSION_MISMATCH',
  'REVIEW_STATE_CHANGED',
  'APPROVAL_STATE_CHANGED',
  'REVIEW_REQUEST_ID_REUSED',
  'APPROVAL_REQUEST_ID_REUSED',
  // 文档选择类：声称的是某一个版本，而服务端那行已经变了 → 顺着「重读」处理
  'DOCUMENT_SELECTION_CHANGED',
])

/** 每个码一句准话：说清「什么动了」，而不是笼统的「提交失败」。 */
const CODE_TEXT: Record<string, string> = {
  FACT_UNAVAILABLE_OR_STALE:
    '该事实在当前版本中已不可用或已过期（FACT_UNAVAILABLE_OR_STALE）：版本可能已更新，已重新加载。',
  FACT_VERSION_MISMATCH:
    '事实载荷与服务器当前版本不一致（FACT_VERSION_MISMATCH）：版本的某一项已移动，已重新加载。',
  SOURCE_VERSION_MISMATCH:
    '源文件哈希与服务器当前版本不一致（SOURCE_VERSION_MISMATCH）：版本已移动，已重新加载。',
  REVIEW_STATE_CHANGED:
    '复核链尾已变化（REVIEW_STATE_CHANGED）：在你提交前已有人写入复核，已重新加载，请基于最新链尾重新提交。',
  APPROVAL_STATE_CHANGED:
    '审批链尾已变化（APPROVAL_STATE_CHANGED）：在你提交前已有新的审批，已重新加载，请基于最新链尾重新提交。',
  REVIEW_REQUEST_ID_REUSED:
    '本次提交标识已经用于另一份复核内容（REVIEW_REQUEST_ID_REUSED）：未做自动重试，已重新加载，请重新提交。',
  APPROVAL_REQUEST_ID_REUSED:
    '本次提交标识已经用于另一份审批内容（APPROVAL_REQUEST_ID_REUSED）：未做自动重试，已重新加载，请重新提交。',
  DOCUMENT_SELECTION_CHANGED:
    '文档版本与所选快照不一致（DOCUMENT_SELECTION_CHANGED）：已重新加载，请重新核对。',
  CONFIRMED_AUTHENTICATED_REVIEW_REQUIRED:
    '需要一条已确认的实名复核才能审批（CONFIRMED_AUTHENTICATED_REVIEW_REQUIRED）：历史未认证记录不能作为审批依据。',
  REVIEWER_CANNOT_APPROVE_OWN_REVIEW:
    '不得审批自己提交的复核（REVIEWER_CANNOT_APPROVE_OWN_REVIEW）。',
  NO_ACTIVE_APPROVAL_TO_REVOKE:
    '当前没有可撤销的生效审批（NO_ACTIVE_APPROVAL_TO_REVOKE）。',
  REVIEW_REASON_REQUIRED: '复核理由不能为空（REVIEW_REASON_REQUIRED）。',
  APPROVAL_REASON_REQUIRED: '审批理由不能为空（APPROVAL_REASON_REQUIRED）。',
  REVIEWER_IDENTITY_SERVER_OWNED:
    '复核人身份由服务端写入，客户端不得指定（REVIEWER_IDENTITY_SERVER_OWNED）。',
  DOCUMENT_NOT_FOUND: '文档不存在或当前身份读不到（DOCUMENT_NOT_FOUND）。',
  SOURCE_FILE_UNAVAILABLE: '源文件不可用，无法校验定位（SOURCE_FILE_UNAVAILABLE）。',
  COLLECTION_NOT_ALLOWED: '该 collection 不在允许清单内（COLLECTION_NOT_ALLOWED）。',
  LOGIN_REQUIRED: '需要登录（LOGIN_REQUIRED）。',
  SESSION_INVALID: '会话已失效（SESSION_INVALID）：请重新登录。',
  PERMISSION_DENIED: '权限不足（PERMISSION_DENIED）：当前身份没有复核 / 审批所需的角色。',
  CSRF_REQUIRED: '缺少或失效的 CSRF 令牌（CSRF_REQUIRED）：请刷新页面后重试。',
  ORIGIN_NOT_ALLOWED: '请求来源不被允许（ORIGIN_NOT_ALLOWED）：只能从同源页面发起。',
  VALIDATION_ERROR:
    '请求未通过服务端校验（FastAPI 422，没有 error_code）：字段缺失或格式不符，请检查后重试。',
}

/**
 * 把任意异常翻成一个可显示的中文 + 是否属于「版本/状态已变」。
 * 只认 client.ts 放进 Error.errorCode 的码；认不出就回落原始 message，绝不吞掉。
 */
export function describeError(e: unknown, fallback: string): FieldErrorInfo {
  const err = e as { errorCode?: unknown; status?: unknown; message?: unknown } | null
  const code = typeof err?.errorCode === 'string' && err.errorCode ? err.errorCode : ''
  const status = typeof err?.status === 'number' ? err.status : null
  const raw = typeof err?.message === 'string' ? err.message : ''
  const recoverable = !!code && RECOVERABLE_CODES.has(code)

  if (code && CODE_TEXT[code]) {
    // apiError 把 detail.details 拼在码后面（`CODE {json}`）：有额外细节就附上，不隐藏证据；
    // 只有码本身时就不再重复一遍（否则每句话后面都拖一个同义的括号）。
    const details = raw.startsWith(code) ? raw.slice(code.length).trim() : raw
    return {
      code,
      status,
      text: details ? `${CODE_TEXT[code]}（服务端原文：${details}）` : CODE_TEXT[code],
      recoverable,
    }
  }
  if (!code && status === 422) {
    return {
      code: 'VALIDATION_ERROR',
      status,
      text: `${CODE_TEXT.VALIDATION_ERROR}${raw ? `（${raw}）` : ''}`,
      recoverable: false,
    }
  }
  if (code) {
    return { code, status, text: `${fallback}（${code}${raw && raw !== code ? `：${raw}` : ''}）`, recoverable }
  }
  return { code: '', status, text: raw ? `${fallback}：${raw}` : fallback, recoverable: false }
}

/* ── 提交标识 ─────────────────────────────────────────────────────────── */

/**
 * 幂等用的 request_id：后端要求 `^[A-Za-z0-9_-]+$` 且 <= 64。
 * 同一次表单填写必须复用同一个 id：若第一次提交其实已落库、只是响应丢了，
 * 用同一个 id 重试会让服务端返回**已有的**那条记录（幂等），而不是写入第二条。
 * 内容变了却复用同一个 id，服务端会以 *_REQUEST_ID_REUSED 拒绝——那正是我们要的
 * 信号（它说明我们手上的内容与已落库的不同），所以这里从不「悄悄换一个 id 重试」。
 */
export function newRequestId(): string {
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  let hex = ''
  for (let i = 0; i < bytes.length; i += 1) hex += bytes[i].toString(16).padStart(2, '0')
  return `req-${hex}`
}

/** 表单里的决策选项（顺序即推荐顺序：先确认、再退回、最后不确定）。 */
export const REVIEW_DECISIONS: FactDecision[] = ['confirmed', 'rejected', 'needs_review']
export const APPROVAL_DECISIONS: ApprovalDecision[] = ['approved', 'rejected', 'revoked']
