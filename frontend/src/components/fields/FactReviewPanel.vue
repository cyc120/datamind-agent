<script setup lang="ts">
/**
 * 单条事实的复核 / 审批面板：历史链 + 两个写入表单。
 *
 * 三条不许含糊的规则在这里落地：
 *   ① 复核只针对**提取忠实度**（原文这段字有没有被正确抄成这条事实）；
 *      它不覆盖源文件自己写的审批声明，也不裁定两个来源谁更权威。
 *   ② `reviewer_user_id` 为 null 的复核是**历史未认证**记录：它没有可追溯的人，
 *      不能作为审批依据，所以面板**不提供**审批按钮（服务端也会以
 *      409 CONFIRMED_AUTHENTICATED_REVIEW_REQUIRED 拒绝）。
 *   ③ 审批 ≠ 可检索：审批只解开这一条事实的复核闸门；文档能否被检索器读到由
 *      文档自己的 index_state / searchable 决定，与审批结果无关。
 *
 * 写入请求体严格按后端 pydantic 模型（extra=forbid）：多一个键就是 422。
 * 版本四元组（document_version / source_sha256 / parser_version / payload_sha256）
 * 原样来自 facts 响应，绝不在这里「顺手补一个当前值」。
 */

import { computed, onMounted, ref, watch } from 'vue'
import {
  getFactApprovals,
  getFactReviews,
  me,
  submitFactApproval,
  submitFactReview,
} from '../../api/client'
import type {
  ApprovalDecision,
  DocumentFact,
  FactApprovalInput,
  FactApprovalRecord,
  FactDecision,
  FactFieldState,
  FactReviewInput,
  FactReviewRecord,
} from '../../api/types'
import {
  APPROVAL_DECISIONS,
  APPROVAL_DECISION_TEXT,
  REVIEW_DECISIONS,
  REVIEW_DECISION_TEXT,
  describeError,
  formalApprovalText,
  formalApprovalTone,
  newRequestId,
} from './messages'

const props = defineProps<{
  documentId: string
  fact: DocumentFact
}>()

const emit = defineEmits<{
  /** 提交成功后的字段投影；null = 版本已移动，调用方必须重新加载事实 */
  (e: 'updated', state: FactFieldState | null): void
  /** 版本/状态已变化：调用方应重新加载事实 */
  (e: 'stale', message: string): void
}>()

/* ── 历史链 ───────────────────────────────────────────────────────────── */

const reviews = ref<FactReviewRecord[]>([])
const approvals = ref<FactApprovalRecord[]>([])
const reviewsError = ref('')
const approvalsError = ref('')
const historiesLoading = ref(false)
/** 服务端自带的语义说明：原样显示，不改写成中文（避免把后端口径翻译走样）。 */
const reviewsSemantics = ref('')
const approvalsSemantics = ref('')

async function loadHistories(): Promise<void> {
  historiesLoading.value = true
  reviewsError.value = ''
  approvalsError.value = ''
  const factId = props.fact.fact_id
  // 两个历史分别判定成败：一个读不到，不该把另一个也显示成「加载失败」。
  const [reviewResult, approvalResult] = await Promise.allSettled([
    getFactReviews(props.documentId, factId),
    getFactApprovals(props.documentId, factId),
  ])
  if (props.fact.fact_id !== factId) return

  if (reviewResult.status === 'fulfilled') {
    const body = reviewResult.value
    reviews.value = Array.isArray(body?.reviews) ? body.reviews : []
    reviewsSemantics.value = body?.semantics || ''
  } else {
    reviews.value = []
    reviewsError.value = describeError(reviewResult.reason, '复核历史加载失败').text
  }
  if (approvalResult.status === 'fulfilled') {
    const body = approvalResult.value
    approvals.value = Array.isArray(body?.approvals) ? body.approvals : []
    approvalsSemantics.value = body?.semantics || ''
  } else {
    approvals.value = []
    approvalsError.value = describeError(approvalResult.reason, '审批历史加载失败').text
  }
  historiesLoading.value = false
}

/* ── 身份（只用于展示与提示；写入身份完全由服务端决定）────────────────── */

const identityText = ref('')
const identityUserId = ref('')
const identityError = ref('')

async function loadIdentity(): Promise<void> {
  try {
    const body = await me()
    const user = body?.user
    if (!user) {
      identityText.value = '匿名（写操作会被服务端以 401 LOGIN_REQUIRED 拒绝）'
      identityUserId.value = ''
      return
    }
    const roles = Array.isArray(user.roles) ? user.roles.join('/') : ''
    identityText.value = roles ? `${user.display_name}（${roles}）` : user.display_name
    identityUserId.value = user.user_id || ''
  } catch (e: any) {
    // 读不到身份不影响历史展示；如实说明「没读到」，不假装成匿名也不假装成已登录。
    identityText.value = '身份读取失败'
    identityError.value = describeError(e, '当前身份读取失败').text
  }
}

/* ── 表单状态 ─────────────────────────────────────────────────────────── */

const reviewDecision = ref<FactDecision>('confirmed')
const reviewReason = ref('')
const reviewBusy = ref(false)
const reviewError = ref('')
const reviewNotice = ref('')
const reviewRequestId = ref(newRequestId())

const approvalDecision = ref<ApprovalDecision>('approved')
const approvalReason = ref('')
const approvalBusy = ref(false)
const approvalError = ref('')
const approvalNotice = ref('')
const approvalRequestId = ref(newRequestId())

/** 当前生效的复核 / 审批（来自 facts 投影，不是历史数组的最后一条）。 */
const latestReview = computed<FactReviewRecord | null>(() => props.fact.latest_review ?? null)
const latestApproval = computed<FactApprovalRecord | null>(() => props.fact.latest_approval ?? null)

/** 规则②：reviewer_user_id 为空 = 历史未认证记录。 */
function isLegacyReview(record: FactReviewRecord | null | undefined): boolean {
  return !!record && !record.reviewer_user_id
}

/**
 * 能否提供审批按钮。服务端只接受「最新一条复核 = confirmed 且实名」作为绑定对象，
 * 所以这三种情况一律不提供按钮（而不是让用户点了才看到 409）。
 */
const approvalBlockedReason = computed<string>(() => {
  const review = latestReview.value
  if (!review) {
    return '还没有任何人工复核：审批必须绑定一条已确认（confirmed）的实名复核，因此当前不提供审批按钮。'
  }
  if (isLegacyReview(review)) {
    return '最新一条复核是历史未认证记录（reviewer_user_id 为空）：它没有可追溯的复核人，不能作为审批依据，因此不提供审批按钮。历史未认证记录不补身份。'
  }
  if (review.decision !== 'confirmed') {
    return `最新一条复核的结论是「${REVIEW_DECISION_TEXT[review.decision] ?? review.decision}」：只有 confirmed 的复核才能被审批，因此当前不提供审批按钮。`
  }
  return ''
})
const canApprove = computed(() => !approvalBlockedReason.value)

/** 服务端会以 409 REVIEWER_CANNOT_APPROVE_OWN_REVIEW 拒绝自己批自己：先提示，不隐藏按钮。 */
const selfApprovalWarning = computed(
  () =>
    !!identityUserId.value &&
    !!latestReview.value?.reviewer_user_id &&
    identityUserId.value === latestReview.value.reviewer_user_id,
)

/** 撤销只对「当前生效的批准」有效；否则服务端 409 NO_ACTIVE_APPROVAL_TO_REVOKE。 */
const revokeHasTarget = computed(() => latestApproval.value?.decision === 'approved')

/* ── 版本/载荷变化：重读历史、换提交标识，但保留用户已写的理由 ─────────── */

function factKey(fact: DocumentFact): string {
  return [
    props.documentId,
    fact.fact_id,
    fact.document_version,
    fact.source_sha256,
    fact.parser_version,
    fact.payload_sha256,
  ].join('|')
}

let currentKey = ''

watch(
  () => factKey(props.fact),
  (next) => {
    const previousFactId = currentKey.split('|')[1] ?? ''
    const nextFactId = next.split('|')[1] ?? ''
    currentKey = next
    // 换的是另一条事实 → 理由不能跟着搬过去；只是同一事实版本移动 → 保留已写内容。
    if (previousFactId && previousFactId !== nextFactId) {
      reviewReason.value = ''
      approvalReason.value = ''
    }
    // 每次进入新的版本/载荷都换一个提交标识：旧标识可能已经落库。
    reviewRequestId.value = newRequestId()
    approvalRequestId.value = newRequestId()
    reviewError.value = ''
    reviewNotice.value = ''
    approvalError.value = ''
    approvalNotice.value = ''
    void loadHistories()
  },
  { immediate: true },
)

onMounted(() => {
  void loadIdentity()
})

/* ── 提交 ─────────────────────────────────────────────────────────────── */

/**
 * 提交后的统一收尾。
 * `field_state` 为 null 是后端的明确信号：这条事实在当前版本里已经读不到了
 * → 版本移动，必须让父组件重新加载，绝不能拿旧投影继续显示「已确认/已批准」。
 */
async function afterWrite(state: FactFieldState | null, what: string): Promise<void> {
  if (state === null) {
    emit(
      'stale',
      `${what}已提交，但服务端返回的 field_state 为 null：该事实的版本已移动，已重新加载，请重新核对。`,
    )
  } else {
    emit('updated', state)
  }
  await loadHistories()
}

async function submitReview(): Promise<void> {
  const reason = reviewReason.value.trim()
  if (!reason) {
    reviewError.value = '请填写复核理由（不能为空）。'
    return
  }
  reviewBusy.value = true
  reviewError.value = ''
  reviewNotice.value = ''
  try {
    // 请求体只带后端模型声明的键：reviewer_label 由服务端写入（传错值 422
    // REVIEWER_IDENTITY_SERVER_OWNED），reviewer_user_id 更不许出现（extra=forbid → 422）。
    const body: FactReviewInput = {
      request_id: reviewRequestId.value,
      document_version: props.fact.document_version,
      source_sha256: props.fact.source_sha256,
      parser_version: props.fact.parser_version,
      payload_sha256: props.fact.payload_sha256,
      previous_review_id: latestReview.value?.review_id ?? null,
      decision: reviewDecision.value,
      reason,
    }
    const result = await submitFactReview(props.documentId, props.fact.fact_id, body)
    reviewNotice.value = `复核已记录：${result?.review?.review_id ?? ''}`
    reviewReason.value = ''
    reviewRequestId.value = newRequestId()
    await afterWrite(result?.field_state ?? null, '复核')
  } catch (e) {
    const info = describeError(e, '提交复核失败')
    reviewError.value = info.text
    if (info.recoverable) {
      // 版本/状态已变：换标识、重读历史，并请父组件重载事实；不做静默重试。
      reviewRequestId.value = newRequestId()
      emit('stale', info.text)
      await loadHistories()
    }
  } finally {
    reviewBusy.value = false
  }
}

async function submitApproval(): Promise<void> {
  const reason = approvalReason.value.trim()
  if (!reason) {
    approvalError.value = '请填写审批理由（不能为空）。'
    return
  }
  const review = latestReview.value
  if (!review || !canApprove.value) {
    approvalError.value = approvalBlockedReason.value
    return
  }
  approvalBusy.value = true
  approvalError.value = ''
  approvalNotice.value = ''
  try {
    // 没有「审批人标签」这种字段：approver_label / approver_user_id / approver_roles
    // 全部由服务端按当前身份写入；客户端多传一个键就是 422。
    const body: FactApprovalInput = {
      request_id: approvalRequestId.value,
      document_version: props.fact.document_version,
      source_sha256: props.fact.source_sha256,
      parser_version: props.fact.parser_version,
      payload_sha256: props.fact.payload_sha256,
      review_id: review.review_id,
      previous_approval_id: latestApproval.value?.approval_id ?? null,
      decision: approvalDecision.value,
      reason,
    }
    const result = await submitFactApproval(props.documentId, props.fact.fact_id, body)
    approvalNotice.value = `审批已记录：${result?.approval?.approval_id ?? ''}`
    approvalReason.value = ''
    approvalRequestId.value = newRequestId()
    await afterWrite(result?.field_state ?? null, '审批')
  } catch (e) {
    const info = describeError(e, '提交审批失败')
    approvalError.value = info.text
    if (info.recoverable) {
      approvalRequestId.value = newRequestId()
      emit('stale', info.text)
      await loadHistories()
    }
  } finally {
    approvalBusy.value = false
  }
}

/** 版本四元组的展示（谁绑了谁一眼可见）。 */
function tupleText(record: FactReviewRecord | FactApprovalRecord): string {
  const sha = (record.source_sha256 || '').slice(0, 12)
  const payload = (record.payload_sha256 || '').slice(0, 12)
  return `v${record.document_version} · sha ${sha}… · parser ${record.parser_version} · payload ${payload}…`
}

function whenText(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
</script>

<template>
  <section class="fp-panel" data-testid="fields-fact-detail">
    <!-- 当前生效状态：只认 facts 投影里的三个字段，不看源文件自己的声明 -->
    <div class="fp-current" data-testid="fields-current-state">
      <div class="fp-cur-row">
        <span class="fp-cur-key">最新复核</span>
        <span v-if="!latestReview" class="fp-cur-val fp-muted">没有人工复核记录</span>
        <span v-else class="fp-cur-val">
          {{ REVIEW_DECISION_TEXT[latestReview.decision] ?? latestReview.decision }}
          <span class="fp-cur-who">
            {{ latestReview.reviewer_user_id ? `· ${latestReview.reviewer_label || latestReview.reviewer_user_id}` : '· 历史未认证' }}
          </span>
        </span>
      </div>
      <div class="fp-cur-row">
        <span class="fp-cur-key">正式审批</span>
        <span class="fp-cur-val">
          <span class="fp-badge" :class="`tone-${formalApprovalTone(fact.formal_approval_status)}`">
            {{ formalApprovalText(fact.formal_approval_status) }}
          </span>
          <span v-if="latestApproval" class="fp-cur-who">
            · {{ latestApproval.approver_label || latestApproval.approver_user_id }}
          </span>
        </span>
      </div>
      <p class="fp-honesty" data-testid="fields-review-scope-note">
        复核只针对提取忠实度；不覆盖原文声明、不裁定来源权威。源文件自己写的审批状态见下方「源文件自述」，
        它不是本系统的审批结论。
      </p>
    </div>

    <div class="fp-cols">
      <!-- ── 复核历史 + 表单 ── -->
      <div class="fp-col">
        <h4 class="fp-h">复核历史</h4>
        <p v-if="historiesLoading" class="fp-hint">正在读取…</p>
        <p v-else-if="reviewsError" class="fp-error" data-testid="fields-reviews-error">{{ reviewsError }}</p>
        <p v-else-if="!reviews.length" class="fp-empty" data-testid="fields-reviews-empty">
          还没有复核记录。复核是追加式的：每提交一次都留一条，不会被覆盖。
        </p>
        <ul v-else class="fp-list" data-testid="fields-review-history">
          <li v-for="r in reviews" :key="r.review_id" class="fp-item">
            <div class="fp-item-head">
              <span class="fp-item-decision">{{ REVIEW_DECISION_TEXT[r.decision] ?? r.decision }}</span>
              <span v-if="r.review_id === latestReview?.review_id" class="fp-tag is-current">当前生效</span>
              <span v-else class="fp-tag">非当前链尾</span>
              <span v-if="isLegacyReview(r)" class="fp-tag is-legacy" data-testid="fields-legacy-review">
                历史未认证
              </span>
              <span class="fp-item-when">{{ whenText(r.created_at) }}</span>
            </div>
            <p class="fp-item-meta">
              <template v-if="r.reviewer_user_id">
                复核人 {{ r.reviewer_label || '—' }}（{{ r.reviewer_user_id }}）
              </template>
              <template v-else>
                历史未认证记录：没有复核人身份，历史未认证记录不补身份
              </template>
              <span v-if="r.reviewer_roles?.length"> · 角色 {{ r.reviewer_roles.join('/') }}</span>
            </p>
            <p class="fp-item-meta">{{ tupleText(r) }}</p>
            <p class="fp-item-meta">previous_review_id：{{ r.previous_review_id ?? 'null（链首）' }}</p>
            <p class="fp-item-reason">{{ r.reason }}</p>
          </li>
        </ul>
        <p v-if="reviewsSemantics" class="fp-semantics">服务端口径：{{ reviewsSemantics }}</p>

        <form class="fp-form" data-testid="fields-review-form" @submit.prevent="submitReview">
          <h5 class="fp-form-title">提交复核</h5>
          <p class="fp-identity" data-testid="fields-identity">
            当前身份：{{ identityText || '读取中…' }}
          </p>
          <p v-if="identityError" class="fp-error">{{ identityError }}</p>

          <div class="fp-decisions" role="radiogroup" aria-label="复核结论">
            <button
              v-for="d in REVIEW_DECISIONS"
              :key="d"
              type="button"
              class="fp-decision"
              :class="{ 'is-on': reviewDecision === d }"
              role="radio"
              :aria-checked="reviewDecision === d"
              :data-testid="`fields-review-decision-${d}`"
              @click="reviewDecision = d"
            >
              {{ REVIEW_DECISION_TEXT[d] }}
            </button>
          </div>

          <textarea
            v-model="reviewReason"
            class="fp-reason"
            rows="3"
            maxlength="1000"
            placeholder="复核理由（必填）：说明这次判断依据的是原文的哪一段、为什么忠实或不忠实"
            data-testid="fields-review-reason"
          ></textarea>
          <p class="fp-hint">
            只评价「原文这段字有没有被忠实提取成这条事实」。不要在这里裁定来源权威，也不要把它当成对原文
            结论的背书。
          </p>

          <p v-if="reviewError" class="fp-error" data-testid="fields-review-form-error">{{ reviewError }}</p>
          <p v-if="reviewNotice" class="fp-notice" data-testid="fields-review-form-notice">{{ reviewNotice }}</p>

          <button class="fp-submit" type="submit" :disabled="reviewBusy" data-testid="fields-review-submit">
            {{ reviewBusy ? '提交中…' : '提交复核' }}
          </button>
        </form>
      </div>

      <!-- ── 审批历史 + 表单 ── -->
      <div class="fp-col">
        <h4 class="fp-h">审批历史</h4>
        <p v-if="historiesLoading" class="fp-hint">正在读取…</p>
        <p v-else-if="approvalsError" class="fp-error" data-testid="fields-approvals-error">{{ approvalsError }}</p>
        <p v-else-if="!approvals.length" class="fp-empty" data-testid="fields-approvals-empty">
          还没有审批记录。审批必须绑定一条已确认的实名复核，并且审批人不能是提交该复核的人。
        </p>
        <ul v-else class="fp-list" data-testid="fields-approval-history">
          <li v-for="a in approvals" :key="a.approval_id" class="fp-item">
            <div class="fp-item-head">
              <span class="fp-item-decision">{{ APPROVAL_DECISION_TEXT[a.decision] ?? a.decision }}</span>
              <span v-if="a.approval_id === latestApproval?.approval_id" class="fp-tag is-current">当前生效</span>
              <span v-else class="fp-tag">非当前链尾</span>
              <span class="fp-item-when">{{ whenText(a.created_at) }}</span>
            </div>
            <p class="fp-item-meta">
              审批人 {{ a.approver_label || '—' }}（{{ a.approver_user_id }}）
              <span v-if="a.approver_roles?.length"> · 角色 {{ a.approver_roles.join('/') }}</span>
            </p>
            <p class="fp-item-meta">{{ tupleText(a) }}</p>
            <p class="fp-item-meta">review_id：{{ a.review_id }}</p>
            <p class="fp-item-meta">previous_approval_id：{{ a.previous_approval_id ?? 'null（链首）' }}</p>
            <p class="fp-item-reason">{{ a.reason }}</p>
          </li>
        </ul>
        <p v-if="approvalsSemantics" class="fp-semantics">服务端口径：{{ approvalsSemantics }}</p>

        <form
          v-if="canApprove"
          class="fp-form"
          data-testid="fields-approval-form"
          @submit.prevent="submitApproval"
        >
          <h5 class="fp-form-title">提交审批</h5>
          <p class="fp-identity">
            审批身份（服务端写入，客户端不可指定）：{{ identityText || '读取中…' }}
          </p>

          <p v-if="selfApprovalWarning" class="fp-warn" data-testid="fields-self-approval-warning">
            你正是这条复核的提交人：服务端会以 409 REVIEWER_CANNOT_APPROVE_OWN_REVIEW 拒绝自己批自己。
          </p>
          <p v-if="approvalDecision === 'revoked' && !revokeHasTarget" class="fp-warn">
            当前没有生效的批准可撤销：服务端会以 409 NO_ACTIVE_APPROVAL_TO_REVOKE 拒绝。
          </p>

          <div class="fp-decisions" role="radiogroup" aria-label="审批结论">
            <button
              v-for="d in APPROVAL_DECISIONS"
              :key="d"
              type="button"
              class="fp-decision"
              :class="{ 'is-on': approvalDecision === d }"
              role="radio"
              :aria-checked="approvalDecision === d"
              :data-testid="`fields-approval-decision-${d}`"
              @click="approvalDecision = d"
            >
              {{ APPROVAL_DECISION_TEXT[d] }}
            </button>
          </div>

          <textarea
            v-model="approvalReason"
            class="fp-reason"
            rows="3"
            maxlength="1000"
            placeholder="审批理由（必填）"
            data-testid="fields-approval-reason"
          ></textarea>
          <p class="fp-hint">
            审批只解开这一条事实的复核闸门：审批 ≠ 可检索。文档能否被检索器读到，由文档自己的
            index_state / searchable 决定（见左侧文档信息），与审批结果无关。
          </p>

          <p v-if="approvalError" class="fp-error" data-testid="fields-approval-form-error">{{ approvalError }}</p>
          <p v-if="approvalNotice" class="fp-notice" data-testid="fields-approval-form-notice">{{ approvalNotice }}</p>

          <button class="fp-submit" type="submit" :disabled="approvalBusy" data-testid="fields-approval-submit">
            {{ approvalBusy ? '提交中…' : '提交审批' }}
          </button>
        </form>

        <!-- 规则②：不满足绑定条件时**不提供**按钮，只说明为什么 -->
        <div v-else class="fp-blocked" data-testid="fields-approval-blocked">
          <h5 class="fp-form-title">不提供审批按钮</h5>
          <p class="fp-blocked-text">{{ approvalBlockedReason }}</p>
          <p class="fp-hint">
            审批 ≠ 可检索：即使审批通过，文档能否被检索到仍取决于它自己的 index_state / searchable。
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 类名统一 fp- 前缀（Tailwind v4 @layer utilities 撞名事故的预防措施）。 */
.fp-panel {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
  padding: 14px 16px 18px;
  margin: 8px 0 16px;
}
.fp-current {
  border-bottom: 1px solid var(--color-line);
  padding-bottom: 10px;
  margin-bottom: 12px;
}
.fp-cur-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 5px;
}
.fp-cur-key {
  flex-shrink: 0;
  width: 68px;
  font-size: 13.5px;
  color: var(--color-muted);
}
.fp-cur-val {
  font-size: 14px;
  color: var(--color-text);
}
.fp-cur-who {
  color: var(--color-muted);
}
.fp-muted {
  color: var(--color-muted);
}
/* 徽标统一蓝底白字加粗椭圆；tone-warn / tone-bad 是判据（橙/红），保留 */
.fp-badge {
  font-size: 13px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
}
.fp-badge.tone-ok {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.fp-badge.tone-warn {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
.fp-badge.tone-bad {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: #e11d48;
}
/* tone-muted 现在与基础样式等价（保留类名，避免动模板） */
.fp-badge.tone-muted {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.fp-honesty {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.fp-cols {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}
.fp-col {
  min-width: 0;
}
/* 两栏小标题与其它页统一：蓝色、15px、600（原来是 14px 灰黑） */
.fp-h {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.fp-list {
  list-style: none;
  margin: 0 0 10px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fp-item {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 8px 10px;
}
.fp-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.fp-item-decision {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
/* 标签统一蓝底白字加粗椭圆；is-current（绿）/ is-legacy（橙）是判据，保留 */
.fp-tag {
  font-size: 12.5px;
  font-weight: 600;
  color: #ffffff;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  padding: 1px 8px;
}
/* 「当前有效/历史记录」是判据（绿/橙），颜色保留；
   但基础样式现在是蓝底，必须自带浅底，否则会变成「蓝底绿字 / 蓝底橙字」。 */
.fp-tag.is-current {
  background: rgba(22, 163, 74, 0.10);
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.35);
}
.fp-tag.is-legacy {
  background: rgba(217, 119, 6, 0.10);
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
}
.fp-item-when {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
.fp-item-meta {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--color-text);
  word-break: break-all;
}
.fp-item-reason {
  margin: 6px 0 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-body);
  white-space: pre-wrap;
}
.fp-semantics {
  margin: 0 0 10px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-text);
  word-break: break-word;
}
.fp-form,
.fp-blocked {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: #ffffff;
  padding: 10px 12px 12px;
}
/* 表单标题与其它页统一：蓝色、15px、600（原来是 13.5px 灰黑，属离群值） */
.fp-form-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.fp-identity {
  margin: 0 0 8px;
  font-size: 12.5px;
  color: var(--color-text);
}
.fp-decisions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.fp-decision {
  font: inherit;
  font-size: 13.5px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.fp-decision.is-on {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: rgba(37, 99, 235, 0.08);
}
.fp-reason {
  width: 100%;
  box-sizing: border-box;
  font: inherit;
  font-size: 14px;
  line-height: 1.6;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
  resize: vertical;
}
.fp-reason:focus {
  outline: none;
  border-color: rgba(37, 99, 235, 0.45);
}
.fp-hint,
.fp-empty {
  margin: 6px 0 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.fp-error {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #e11d48;
}
.fp-notice {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #16a34a;
}
.fp-warn {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.7;
  color: #d97706;
}
.fp-blocked-text {
  margin: 0 0 6px;
  font-size: 14px;
  line-height: 1.75;
  color: var(--color-body);
}
/* 提交按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.fp-submit {
  margin-top: 10px;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 6px 18px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.fp-submit:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.fp-submit:disabled {
  opacity: 0.6;
  cursor: default;
}
@media (max-width: 900px) {
  .fp-cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
