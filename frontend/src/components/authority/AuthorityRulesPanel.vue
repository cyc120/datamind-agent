<script setup lang="ts">
import { computed } from 'vue'

import { basisHref, ruleState, RULE_STATE_TEXT, sourceText } from './useAuthority'
import type { AuthorityRuleState } from './useAuthority'
import type { ApprovalDecision, AuthorityRule } from '../../api/types'

/**
 * 已登记规则列表 + 批准/驳回/撤销。
 *
 * 三条硬规则在这里落地：
 *
 *  1. **payload_valid === false 是硬拒绝**：那一行只渲染
 *     「登记完整性校验失败：<rule_id>」，不展示载荷细节、不给任何启用按钮。
 *     原因不只是「这条可疑」：服务端 resolve() 只要发现注册表里有任何一条已批准但
 *     payload_valid=false 的规则，就把整个裁决 fail closed（AUTHORITY_REGISTRY_INVALID）。
 *     把坏载荷当正常规则展示，会让人以为它只是「另一条规则」。
 *
 *  2. **状态来自链尾事件**，服务端没有 status 列：没有事件 = 待审批。列表里的
 *     decision/actor/reason 都是 LEFT JOIN 出来的「最新事件」。
 *
 *  3. 批准不是「一定生效」：同一时刻若有多条适用规则，运行时以
 *     AUTHORITY_RULE_OVERLAP fail closed，**没有优先级**。所以按钮旁边的话术是
 *     「批准并启用」，不是「冲突已解决」。
 */

const props = defineProps<{
  rules: AuthorityRule[]
  isApprover: boolean
  currentUserId: string
  actingRuleId: string | null
}>()

/**
 * 每行的审批理由输入由页面持有（动作成功后由 useAuthority 清空，避免同一条理由被重复提交）。
 * 用 defineModel 而不是直接改 props 的嵌套字段：写入走模型 ref，语义更清楚。
 */
const reasons = defineModel<Record<string, string>>('reasons', { required: true })

const emit = defineEmits<{
  (e: 'act', payload: { rule: AuthorityRule; decision: ApprovalDecision; reason: string }): void
}>()

interface RuleRow {
  rule: AuthorityRule
  /** payload_valid === false：整行只显示失败说明 */
  invalid: boolean
  payloadKnown: boolean
  state: AuthorityRuleState
  stateLabel: string
  scopeSummary: string
  selectedSourceText: string
  windowText: string
  basisText: string
  basisLink: string
  creatorText: string
  createdText: string
  latestEventText: string
  blocker: string
  transitionHint: string
  canApprove: boolean
  canReject: boolean
  canRevoke: boolean
  acting: boolean
}

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn()
  } catch {
    return fallback
  }
}

function scopeSummary(rule: AuthorityRule): string {
  return safe(() => {
    const s = rule.payload?.context?.scope
    if (!s) return '（载荷里没有 scope）'
    return [
      ['零件', s.part_id],
      ['工序', s.operation_no],
      ['批次', s.batch_id],
      ['测量', s.measurement_id],
      ['指标', s.metric],
      ['方法', s.method],
    ]
      .map(([k, v]) => `${k} ${v || '—'}`)
      .join(' · ')
  }, '（载荷不可读）')
}

function basisText(rule: AuthorityRule): string {
  return safe(() => {
    const b = rule.payload?.basis
    if (!b) return '（载荷里没有 basis）'
    const sha = typeof b.source_sha256 === 'string' ? b.source_sha256 : ''
    return `${b.document_id} · v${b.document_version} · 第 ${b.page_no} 页 · sha256 ${sha.slice(0, 12)}…`
  }, '（载荷不可读）')
}

function creatorText(rule: AuthorityRule): string {
  const label = rule.creator_label || '（无显示名）'
  const roles = Array.isArray(rule.creator_roles) && rule.creator_roles.length
    ? `［${rule.creator_roles.join('、')}］`
    : '［无角色记录］'
  return `${label}［${rule.creator_user_id}］${roles}`
}

/** 登记时间的本地化显示（纯展示，不参与任何比较，所以按本机时区渲染即可）。 */
function stamp(iso?: string | null): string {
  if (!iso) return '（缺失）'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * 链尾事件摘要。列表投影里**没有**事件的 created_at（LEFT JOIN 只取 event_id/decision/
 * 操作人/理由），所以这里不编造事件时间，只给规则自身的登记时间。
 */
function latestEventText(rule: AuthorityRule): string {
  if (rule.decision == null) return '尚无事件：这条规则从未被启用/驳回/撤销'
  const label = RULE_STATE_TEXT[ruleState(rule)]
  const actor = rule.actor_label || rule.actor_user_id || '（操作人未知）'
  const reason = rule.activation_reason ? rule.activation_reason : '（理由为空）'
  return `${label} · 操作人 ${actor} · 理由：${reason}`
}

const rows = computed<RuleRow[]>(() =>
  props.rules.map((rule) => {
    const invalid = rule.payload_valid === false
    const payloadKnown = rule.payload_valid === true
    const state = ruleState(rule)
    const isCreator = !!rule.creator_user_id && rule.creator_user_id === props.currentUserId

    // 谁能操作：approver 角色；不能是自己的规则；载荷校验必须是「通过」而不是「没结论」。
    const blocker = !props.isApprover
      ? '需要 approver 角色（admin 通过所有角色门）；这个动作会被服务端以 403 PERMISSION_DENIED 拦住。'
      : isCreator
        ? '创建人不能启用/驳回/撤销自己登记的规则（409 AUTHORITY_CREATOR_CANNOT_APPROVE）；请换另一个审批账户操作。'
        : !payloadKnown
          ? '没有拿到载荷校验结论（payload_valid 缺失）：不能把这条当作有效依据，也不提供启用动作。'
          : ''

    const unknownState = state === 'unknown'
    const canApprove = !blocker && !unknownState && state !== 'approved'
    const canReject = !blocker && !unknownState && state === 'pending'
    const canRevoke = !blocker && !unknownState && state === 'approved'

    const transitionHint = unknownState
      ? '服务端返回了无法识别的链尾结论：本行不提供任何动作，也不替它解释。'
      : state === 'approved'
        ? '只能撤销：撤销需要先有一次已批准的记录（本次满足）；撤销只追加事件，不删除历史。'
        : state === 'rejected' || state === 'revoked'
          ? '可重新批准：服务端会重跑一次完整核验（候选是否仍未消解、报告字段是否仍已独立审批、依据是否仍是已发布 PDF），可能返回 AUTHORITY_CONTEXT_CHANGED / APPROVED_FIELD_REQUIRED / AUTHORITY_BASIS_INVALID。'
          : '批准会重跑一次完整核验；驳回只是记录结论，不改变任何一方的数值。'

    return {
      rule,
      invalid,
      payloadKnown,
      state,
      stateLabel: RULE_STATE_TEXT[state],
      scopeSummary: invalid ? '' : scopeSummary(rule),
      selectedSourceText: invalid ? '' : sourceText(rule.payload?.selected_source),
      windowText: invalid
        ? ''
        : `${rule.payload?.valid_from ?? '—'} → ${rule.payload?.valid_to ?? '—'}（左闭右开：valid_from ≤ produced_at < valid_to）`,
      basisText: invalid ? '' : basisText(rule),
      basisLink: invalid ? '' : basisHref(rule.payload?.basis),
      creatorText: invalid ? '' : creatorText(rule),
      createdText: invalid ? '' : stamp(rule.created_at),
      latestEventText: invalid ? '' : latestEventText(rule),
      blocker,
      transitionHint,
      canApprove,
      canReject,
      canRevoke,
      acting: props.actingRuleId === rule.rule_id,
    }
  }),
)

function act(rule: AuthorityRule, decision: ApprovalDecision): void {
  emit('act', { rule, decision, reason: reasons.value[rule.rule_id] ?? '' })
}
</script>

<template>
  <ul class="art-list" data-testid="authority-rules">
    <li v-for="row in rows" :key="row.rule.rule_id" class="art-item">
      <!-- payload_valid === false：只显示这一行失败说明，其余一律不渲染（硬拒绝）。 -->
      <p
        v-if="row.invalid"
        class="art-invalid"
        data-testid="authority-rule-invalid"
      >
        登记完整性校验失败：{{ row.rule.rule_id }}
      </p>

      <article v-else class="art-row" data-testid="authority-rule-row">
        <header class="art-head">
          <code class="art-id" data-testid="authority-rule-id">{{ row.rule.rule_id }}</code>
          <span
            class="art-state"
            :class="`is-${row.state}`"
            data-testid="authority-rule-state"
          >{{ row.stateLabel }}</span>
          <span
            class="art-payload"
            :class="row.payloadKnown ? 'is-ok' : 'is-warn'"
            data-testid="authority-rule-payload"
          >
            载荷校验 payload_valid：{{ row.payloadKnown ? '通过' : '未取得结论' }}
          </span>
        </header>

        <dl class="art-facts">
          <div class="art-fact">
            <dt>范围 scope</dt>
            <dd>{{ row.scopeSummary }}</dd>
          </div>
          <div class="art-fact">
            <dt>为准来源 selected_source</dt>
            <dd>{{ row.selectedSourceText }}</dd>
          </div>
          <div class="art-fact">
            <dt>有效期（原样带时区）</dt>
            <dd>{{ row.windowText }}</dd>
          </div>
          <div class="art-fact">
            <dt>依据 basis</dt>
            <dd>
              {{ row.basisText }}
              <a
                v-if="row.basisLink"
                class="art-link"
                :href="row.basisLink"
                target="_blank"
                rel="noopener"
                data-testid="authority-rule-basis-link"
              >查看依据页</a>
            </dd>
          </div>
          <div class="art-fact">
            <dt>登记人 creator</dt>
            <dd data-testid="authority-rule-creator">{{ row.creatorText }}</dd>
          </div>
          <div class="art-fact">
            <dt>登记理由 rationale</dt>
            <dd class="art-rationale" data-testid="authority-rule-rationale">
              {{ row.rule.payload?.rationale || '（载荷里没有理由）' }}
            </dd>
          </div>
          <div class="art-fact">
            <dt>登记时间</dt>
            <dd>{{ row.createdText }}</dd>
          </div>
          <div class="art-fact">
            <dt>链尾事件</dt>
            <dd data-testid="authority-rule-event">
              {{ row.latestEventText }}
              <code v-if="row.rule.event_id"> · event {{ row.rule.event_id }}</code>
            </dd>
          </div>
        </dl>

        <p v-if="row.blocker" class="art-blocker" data-testid="authority-rule-blocker">
          {{ row.blocker }}
        </p>

        <div class="art-actions">
          <input
            v-model="reasons[row.rule.rule_id]"
            class="art-reason"
            type="text"
            maxlength="1000"
            placeholder="审批理由（必填，1..1000 字）"
            :disabled="row.acting"
            data-testid="authority-reason"
          />
          <button
            class="art-btn"
            type="button"
            :disabled="!row.canApprove || row.acting"
            data-testid="authority-approve"
            @click="act(row.rule, 'approved')"
          >
            {{ row.acting ? '提交中…' : '批准并启用' }}
          </button>
          <button
            class="art-btn"
            type="button"
            :disabled="!row.canReject || row.acting"
            data-testid="authority-reject"
            @click="act(row.rule, 'rejected')"
          >
            驳回
          </button>
          <button
            class="art-btn"
            type="button"
            :disabled="!row.canRevoke || row.acting"
            data-testid="authority-revoke"
            @click="act(row.rule, 'revoked')"
          >
            撤销
          </button>
        </div>
        <p class="art-hint">{{ row.transitionHint }}</p>
      </article>
    </li>
  </ul>
</template>

<style scoped>
/* 类名统一 art- 前缀（table-caption / result-caption 撞名事故的教训）。 */
.art-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.art-item {
  margin: 0;
}
.art-invalid {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid rgba(225, 29, 72, 0.4);
  border-radius: 12px;
  background: rgba(225, 29, 72, 0.06);
  color: #e11d48;
  font-size: 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}
.art-row {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 13px 15px;
}
.art-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.art-id {
  font-size: 13px;
  color: var(--color-text);
  word-break: break-all;
}
/* 审批状态 / 载荷校验：判据类徽标，语义色（橙=待批、蓝=已批准、红=驳回/撤销、绿=校验通过）保留不动 */
.art-state,
.art-payload {
  font-size: 13px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
}
.art-state.is-pending {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
.art-state.is-approved {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.art-state.is-rejected,
.art-state.is-revoked,
.art-state.is-unknown {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: #e11d48;
}
.art-payload.is-ok {
  color: #16a34a;
}
.art-payload.is-warn {
  color: #d97706;
}
.art-facts {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 5px 18px;
}
.art-fact {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.art-fact dt {
  flex-shrink: 0;
  width: 132px;
  font-size: 13px;
  color: var(--color-muted);
}
.art-fact dd {
  margin: 0;
  min-width: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
  word-break: break-word;
}
.art-fact dd.art-rationale {
  white-space: pre-wrap;
  color: var(--color-text);
}
.art-link {
  margin-left: 6px;
  color: var(--color-accent);
}
.art-blocker {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: #d97706;
}
.art-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.art-reason {
  flex: 1;
  min-width: 220px;
  font: inherit;
  font-size: 13.5px;
  padding: 6px 9px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  color: var(--color-text);
}
/* 审批按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.art-btn {
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.art-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.art-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.art-hint {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
</style>
