<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { submitBindingApproval } from '../../api/client'
import type {
  ApprovalDecision,
  DimensionApprovalInput,
  DimensionApprovalPayload,
  DimensionAuditRecord,
} from '../../api/types'
import { REASON_TEXT, apiErrorText, approvalDecisionText, newRequestId, shortHash } from './shared'

/**
 * 使用审批表单（ApprovalRequest）。
 *
 * 三条后端事实决定了这个表单的形状，全都在这里如实说明、不允许「先让用户试一下」：
 *
 *   1. 角色门槛是 **approver**（admin 越过所有角色门槛）。没有该角色时表单直接禁用，
 *      而不是提交后再显示 403 PERMISSION_DENIED。
 *   2. 审批人必须独立：登记人（binding.registrar_user_id）与该绑定引用的关联复核的
 *      复核人（reviewer_user_id）都会被 403 DIMENSION_INDEPENDENT_APPROVER_REQUIRED
 *      拒绝。前者能从绑定载荷直接读到；后者要读关联复核记录才知道 —— 读不到时本组件
 *      **不会**声称「你不是复核人」，只会提示「无法从本页证明」，并把 403 的解释留好。
 *   3. 撤销（revoked）只有在链尾结论是 approved 时才是合法请求；否则后端 409
 *      DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE。所以「撤销」这个选项只在链尾为
 *      approved 时出现，而不是先给一个必然失败的选项。
 *
 * 另外：审批只放开「能不能用这条绑定做对照」这道闸门，不构成制造授权，也**不**会
 * 改写结论里 authentication_verified / formal_drawing_approval /
 * product_quality_conclusion 三个 false。
 */

const props = defineProps<{
  bindingId: string
  bindingVersion: number
  bindingSha256: string
  /** 绑定载荷里的登记人：与当前身份相同就必须禁用（独立性硬约束） */
  registrarUserId: string
  /** 已按顺序排好的审批链（getBinding().approvals，项是 {payload, sha256} 包裹） */
  approvals: DimensionAuditRecord<DimensionApprovalPayload>[]
  identity: { user_id: string; roles: string[] } | null
  /** 该绑定引用的关联复核的复核人；reviewerKnown=false 表示本页没能读到 */
  reviewerUserId: string | null
  reviewerKnown: boolean
}>()

const emit = defineEmits<{
  (e: 'submitted', approval: DimensionAuditRecord<DimensionApprovalPayload>): void
}>()

const decision = ref<ApprovalDecision>('approved')
const reason = ref('')
const submitting = ref(false)
const error = ref('')
const okText = ref('')

/** 链尾（items 是 {payload, sha256} 包裹，这里读 item.payload） */
const latest = computed<DimensionApprovalPayload | null>(() => {
  const rows = props.approvals ?? []
  return rows.length ? rows[rows.length - 1].payload : null
})

const previousApprovalId = computed<string | null>(() => latest.value?.approval_id ?? null)

const roles = computed<string[]>(() => (props.identity?.roles ?? []).filter((r) => typeof r === 'string'))
const hasRole = (role: string) => roles.value.includes(role) || roles.value.includes('admin')

const isRegistrar = computed(
  () => !!props.identity && props.identity.user_id === props.registrarUserId,
)
const isReviewer = computed(
  () => !!props.identity && props.reviewerKnown && !!props.reviewerUserId
    && props.identity.user_id === props.reviewerUserId,
)

/** 撤销只有在链尾结论为 approved 时才可能成功 */
const canRevoke = computed(() => latest.value?.decision === 'approved')

const decisions = computed<ApprovalDecision[]>(() =>
  canRevoke.value ? ['approved', 'rejected', 'revoked'] : ['approved', 'rejected'],
)

/** 链尾一旦不再是 approved，就把已经选中的「撤销」收回，避免提交一个必然 409 的请求 */
watch(canRevoke, (allowed) => {
  if (!allowed && decision.value === 'revoked') decision.value = 'approved'
})

/** 禁用原因（按后端拒绝顺序：角色 → 独立性）。空串 = 可以提交。 */
const blockedReason = computed<string>(() => {
  if (!props.identity) return '未取得本地身份：审批需要 approver（或 admin）身份，请先登录。'
  if (!hasRole('approver')) {
    return `审批需要 approver 角色（admin 越过全部角色门槛）。当前角色：${roles.value.join('、') || '无'}。`
  }
  if (isRegistrar.value) {
    return `DIMENSION_INDEPENDENT_APPROVER_REQUIRED：${REASON_TEXT.DIMENSION_INDEPENDENT_APPROVER_REQUIRED}`
  }
  if (isReviewer.value) {
    return `DIMENSION_INDEPENDENT_APPROVER_REQUIRED：${REASON_TEXT.DIMENSION_INDEPENDENT_APPROVER_REQUIRED}`
  }
  return ''
})

const disabled = computed(() => !!blockedReason.value)

async function submit(): Promise<void> {
  error.value = ''
  okText.value = ''
  if (disabled.value) {
    error.value = blockedReason.value
    return
  }
  const text = reason.value.trim()
  if (!text) {
    // 后端 clean(p,'reason') 会以 DIMENSION_REASON_REQUIRED 拒绝；本地先给同一句中文。
    error.value = `DIMENSION_REASON_REQUIRED：${REASON_TEXT.DIMENSION_REASON_REQUIRED}`
    return
  }
  const body: DimensionApprovalInput = {
    request_id: newRequestId(),
    binding_version: props.bindingVersion,
    binding_sha256: props.bindingSha256,
    // required-but-nullable：链尾为空时必须**显式**传 null，省略会 422
    previous_approval_id: previousApprovalId.value,
    decision: decision.value,
    reason: text,
  }
  submitting.value = true
  try {
    const res = await submitBindingApproval(props.bindingId, body)
    okText.value = `已记录审批：${approvalDecisionText(res.approval.payload.decision)} · ${res.approval.payload.approval_id}`
    reason.value = ''
    emit('submitted', res.approval)
  } catch (e: any) {
    error.value = apiErrorText(e, '审批提交失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="dima-card" data-testid="dim-approval-form">
    <h3 class="dima-title">使用审批</h3>
    <p class="dima-note">
      审批只决定「这条绑定能不能用于对照」这一道闸门。它不构成制造授权，
      也不会把对照结论里那三个 false 改成 true。
    </p>

    <div class="dima-ref" data-testid="dim-approval-target">
      <div class="dima-ref-row">
        <span class="dima-key">binding_version</span>
        <span class="dima-val">{{ bindingVersion }}</span>
      </div>
      <div class="dima-ref-row">
        <span class="dima-key">binding_sha256</span>
        <code class="dima-val" :title="bindingSha256">{{ shortHash(bindingSha256) }}</code>
      </div>
      <div class="dima-ref-row">
        <span class="dima-key">previous_approval_id</span>
        <code class="dima-val" data-testid="dim-approval-previous">
          {{ previousApprovalId ?? '空值（链尾为空，这里必须显式传空）' }}
        </code>
      </div>
      <div class="dima-ref-row">
        <span class="dima-key">链尾结论</span>
        <span class="dima-val" data-testid="dim-approval-latest">
          {{ latest ? approvalDecisionText(latest.decision) : '（还没有审批）' }}
        </span>
      </div>
    </div>

    <p v-if="blockedReason" class="dima-blocked" data-testid="dim-approval-blocked">{{ blockedReason }}</p>
    <p v-else-if="!reviewerKnown" class="dima-warn" data-testid="dim-approval-reviewer-unknown">
      本页没能读到这条绑定引用的关联复核记录，因此<b>无法证明</b>你不是该复核的复核人。
      如果你正是，服务端会以 403 DIMENSION_INDEPENDENT_APPROVER_REQUIRED 拒绝。
    </p>

    <div class="dima-fields">
      <label class="dima-field">
        <span class="dima-label">审批结论</span>
        <select v-model="decision" class="dima-input" :disabled="disabled" data-testid="dim-approval-decision">
          <option v-for="d in decisions" :key="d" :value="d">{{ approvalDecisionText(d) }}</option>
        </select>
      </label>
      <label class="dima-field">
        <span class="dima-label">审批理由（必填，服务端存原文）</span>
        <textarea
          v-model="reason"
          class="dima-input dima-textarea"
          rows="3"
          :disabled="disabled"
          placeholder="说明依据：为什么同意 / 否决 / 撤销"
          data-testid="dim-approval-reason"
        />
      </label>
    </div>

    <button
      class="dima-submit"
      type="button"
      :disabled="disabled || submitting"
      data-testid="dim-approval-submit"
      @click="submit"
    >
      {{ submitting ? '提交中…' : '提交审批' }}
    </button>

    <p v-if="!canRevoke" class="dima-hint" data-testid="dim-approval-revoke-hint">
      当前链尾不是「同意使用」，所以不提供「撤销」：撤销需要一条仍在生效的审批，
      否则服务端会以 409 DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE 拒绝。
    </p>

    <p v-if="error" class="dima-error" data-testid="dim-approval-error">{{ error }}</p>
    <p v-if="okText" class="dima-ok" data-testid="dim-approval-ok">{{ okText }}</p>
  </section>
</template>

<style scoped>
/* 类名统一 dima- 前缀（理由同 RunsPage 顶部注释：Tailwind v4 @layer utilities 撞名）。 */
.dima-card {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
}
/* 面板标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.dima-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dima-note {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dima-ref {
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
}
.dima-ref-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 4px;
}
.dima-key {
  flex-shrink: 0;
  width: 160px;
  font-size: 13px;
  color: var(--color-muted);
}
.dima-val {
  font-size: 13.5px;
  color: var(--color-text);
  word-break: break-all;
}
.dima-blocked {
  margin: 10px 0 0;
  padding: 8px 10px;
  border: 1px solid rgba(225, 29, 72, 0.3);
  border-radius: 12px;
  background: rgba(225, 29, 72, 0.06);
  font-size: 13.5px;
  line-height: 1.7;
  color: #b91c1c;
}
.dima-warn {
  margin: 10px 0 0;
  padding: 8px 10px;
  border: 1px solid rgba(217, 119, 6, 0.3);
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.06);
  font-size: 13.5px;
  line-height: 1.7;
  color: #b45309;
}
.dima-fields {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.dima-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.dima-label {
  font-size: 13px;
  color: var(--color-muted);
}
.dima-input {
  font: inherit;
  font-size: 14px;
  padding: 7px 9px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
}
.dima-input:focus {
  outline: none;
  border-color: var(--color-accent);
}
.dima-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.dima-textarea {
  resize: vertical;
  line-height: 1.6;
}
/* 提交按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.dima-submit {
  margin-top: 12px;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 7px 18px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.dima-submit:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.dima-submit:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.dima-hint {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dima-error {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #e11d48;
}
.dima-ok {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #16a34a;
}
</style>
