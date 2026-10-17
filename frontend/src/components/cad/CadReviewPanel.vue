<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  CadAssociationState,
  CadFeatureGate,
  DrawingFeature,
  DrawingFeatureReview,
  DrawingVersion,
  FactDecision,
} from '../../api/types'
import CadErrorNote from './CadErrorNote.vue'
import CadGateSummary from './CadGateSummary.vue'
import { decisionText, shortHash } from './cadCopy'
import type { CadErrorView } from './cadCopy'

/**
 * 「人工关联复核」—— 人做出的决定只在这里出现。
 *
 * ★ 本屏最重要的区分 ★
 * 本区**只**读 `review_gate` 与 `association_reviews`（那是叠加了人工复核的门槛和决策），
 * 绝不去改写、也不去重新解释 `features[].association_status`（那是机器冻结的判定）。
 * 计划被复核的记录写入 features[] 之外的审计链，所以刷新后 features 表**不会**变 ——
 * 那不是失败，是本设计要证明的事情。
 *
 * 另外两件必须照实说的话：
 *   1. 复核只确认「这条标注对应哪个候选圆」；原文、单位、几何一律不变，
 *      也不代表图纸审批或生产合格；
 *   2. 复核不跨版本继承：切到新版本时 latest_review 必为 null，
 *      review_gate 会以 CAD_ASSOCIATION_NOT_REVIEWED 排除该要素。
 */
const props = defineProps<{
  version: DrawingVersion | null
  currentVersion: number | null
  readOnlyVersion: boolean
  feature: DrawingFeature | null
  associationState: CadAssociationState | null
  associationReviews: CadAssociationState[]
  reviewGate: CadFeatureGate | null
  reviews: DrawingFeatureReview[]
  /** 服务端给出的链尾 review_id（= 提交时会带上的 previous_review_id，所见即所发） */
  previousReviewId: string | null
  historyLoading: boolean
  historyError: CadErrorView | null
  reviewSemantics: string
  submitting: boolean
  submitError: CadErrorView | null
  submitNotice: string
  canReview: boolean
  rolesText: string
  identityLoaded: boolean
  anonymous: boolean
  /** 每次成功提交后自增：用来清空表单，避免把上一次的内容当成新决定 */
  formKey: number
}>()

const emit = defineEmits<{
  (e: 'submit', payload: { decision: FactDecision; selectedTarget: string | null; reason: string }): void
  (e: 'reload-history'): void
  (e: 'select-feature', featureId: string): void
}>()

const decision = ref<FactDecision>('confirmed')
const target = ref('')
const reason = ref('')

/** 换要素或提交成功后重置表单：残留的上一次决定会让人以为已经复核过了。 */
watch(
  () => [props.feature?.feature_id ?? '', props.formKey],
  () => {
    decision.value = 'confirmed'
    target.value = ''
    reason.value = ''
  },
)

/** 非 confirmed 的决策按契约**不能**带 selected_target，本地就把选择清掉，免得提交时才炸。 */
watch(decision, (next) => {
  if (next !== 'confirmed') target.value = ''
})

const candidates = computed<string[]>(() =>
  Array.isArray(props.feature?.target_candidates) ? props.feature!.target_candidates : [],
)

const reasonError = computed(() => {
  const text = reason.value.trim()
  if (!text) return '复核原因不能为空（服务端会答 422 CAD_REVIEW_REASON_REQUIRED）。'
  if (text.length > 1000) return `复核原因最长 1000 字，当前 ${text.length} 字。`
  return ''
})

const targetError = computed(() => {
  if (decision.value !== 'confirmed') return ''
  if (!target.value) return 'decision=confirmed 必须从候选圆里选一个 handle。'
  if (!candidates.value.includes(target.value))
    return '选中的 handle 不在当前要素的 target_candidates 里（服务端会答 422 CAD_REVIEW_TARGET_INVALID）。'
  return ''
})

/** 提交前必须能凑齐 CAS 整组指纹；缺一项都不许提交（不许盲填）。 */
const tupleMissing = computed<string[]>(() => {
  const missing: string[] = []
  if (!props.feature) missing.push('尚未选择要素')
  if (!props.version) missing.push('版本信息未加载')
  if (!props.associationState?.feature_sha256) missing.push('缺少服务端给出的 feature_sha256')
  if (props.historyLoading) missing.push('复核历史仍在加载（previous_review_id 未知）')
  else if (props.historyError) missing.push('复核历史加载失败（previous_review_id 未知）')
  return missing
})

const blockedReasons = computed<string[]>(() => {
  const list = [...tupleMissing.value]
  if (props.readOnlyVersion) list.push('当前是历史版本（只读），复核提交会 409 CAD_REVIEW_VERSION_CHANGED')
  if (props.anonymous) list.push('当前匿名：写操作会被 401 LOGIN_REQUIRED 拒绝')
  else if (props.identityLoaded && !props.canReview)
    list.push(`关联复核需要 REVIEWER 角色（ADMIN 可过所有闸门）；当前角色：${props.rolesText || '无'}`)
  if (reasonError.value) list.push(reasonError.value)
  if (targetError.value) list.push(targetError.value)
  return list
})

const canSubmit = computed(() => blockedReasons.value.length === 0 && !props.submitting)

function submit(): void {
  if (!canSubmit.value) return
  emit('submit', {
    decision: decision.value,
    selectedTarget: decision.value === 'confirmed' ? target.value : null,
    reason: reason.value.trim(),
  })
}

/** 服务端会 CAS 校验的整组指纹（feature_sha256 来自 association_reviews，不来自 features）。 */
const tuple = computed(() => [
  ['document_version', props.version ? String(props.version.document_version) : '—'],
  ['source_sha256', shortHash(props.version?.source_sha256, 16)],
  ['parser_version', props.version?.parser_version ?? '—'],
  ['bundle_sha256', shortHash(props.version?.bundle_sha256, 16)],
  ['feature_sha256', shortHash(props.associationState?.feature_sha256, 16)],
  ['previous_review_id', props.previousReviewId ?? 'null（本版本该要素尚无复核）'],
] as Array<[string, string]>)
</script>

<template>
  <section class="cad-section" data-testid="cad-review-panel">
    <h2 class="cad-sec-title">人工关联复核</h2>

    <p class="cad-hint cad-emph" data-testid="cad-review-note">
      本区只呈现 <code>review_gate</code>（叠加人工复核后的门槛）与 <code>association_reviews</code>
      （人工决策）。它与「机器提取」是两份数据：复核不会改写 <code>features[].association_status</code>，
      也不会改变原文、单位或几何。
    </p>
    <p class="cad-hint cad-honesty" data-testid="cad-honesty-review">
      复核只确认标注对应哪个候选圆；原文、单位和几何保持不变，不代表图纸审批或生产合格。
    </p>

    <CadGateSummary :gate="reviewGate" caption="使用门槛（review_gate，叠加人工复核）" testid="cad-review-gate" />

    <p v-if="reviewSemantics" class="cad-semantics" data-testid="cad-review-semantics">
      服务端原文（review_semantics）：<code>{{ reviewSemantics }}</code>
    </p>

    <p v-if="readOnlyVersion" class="cad-warn" data-testid="cad-readonly-version">
      正在读版本 v{{ version?.document_version ?? '—' }}，当前版本是 v{{ currentVersion ?? '—' }}：
      历史版本**可以读、但只读**。复核记录的 CAS 整组指纹只认当前版本链尾，对历史版本提交会以
      409 <code>CAD_REVIEW_VERSION_CHANGED</code> 失败 —— 所以本区对历史版本禁用提交，不假装能写。
    </p>

    <!-- 关联复核快照：每条要素的人工决策 -->
    <h3 class="cad-sub">各要素的人工复核状态（association_reviews）</h3>
    <p v-if="!associationReviews.length" class="cad-hint" data-testid="cad-association-empty">
      服务端没有返回任何 association_reviews 条目 —— 缺少该快照就无法安全提交复核（拿不到 feature_sha256）。
    </p>
    <div v-else class="cad-tablewrap">
      <table class="cad-table">
        <thead>
          <tr>
            <th>要素</th>
            <th>feature_sha256</th>
            <th>历史条数</th>
            <th>最新人工决定</th>
            <th>选定目标</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="s in associationReviews"
            :key="s.feature_id"
            data-testid="cad-association-row"
            :class="{ 'cad-is-selected': feature && feature.feature_id === s.feature_id }"
            @click="emit('select-feature', s.feature_id)"
          >
            <td><code class="cad-code">{{ s.feature_id }}</code></td>
            <td :title="s.feature_sha256"><code class="cad-code">{{ shortHash(s.feature_sha256, 12) }}</code></td>
            <td>{{ s.history_count }}</td>
            <td>
              <template v-if="s.latest_review">
                {{ decisionText(s.latest_review.decision) }}
                <span class="cad-meta">{{ s.latest_review.reviewer_label }} · {{ s.latest_review.created_at }}</span>
              </template>
              <span v-else class="cad-none">未复核（latest_review = null）</span>
            </td>
            <td><code class="cad-code">{{ s.latest_review?.selected_target ?? '—' }}</code></td>
            <td class="cad-reason-cell">{{ s.latest_review?.reason ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p
      v-if="associationReviews.length && associationReviews.every((s) => !s.latest_review)"
      class="cad-hint"
      data-testid="cad-no-review-note"
    >
      本版本**尚未有任何人工复核**：所有 latest_review 都是 null，review_gate 对被排除要素给出的原因是
      <code>CAD_ASSOCIATION_NOT_REVIEWED</code>。新版本不继承上一版本的复核结论 —— 每次登记新版本，
      复核都要在这个版本上重新做。
    </p>

    <!-- 选中要素的复核历史 + 提交表单 -->
    <h3 class="cad-sub">单条要素的复核</h3>
    <p v-if="!feature" class="cad-hint" data-testid="cad-review-none-selected">
      还没有选中要素。请在上方「机器提取（原文/几何）」表里点一行（或在「人工复核」表里点一行），
      这里才会显示它的复核历史与提交表单。
    </p>

    <template v-else>
      <div class="cad-feature-head" data-testid="cad-review-feature">
        <div>
          <code class="cad-code">{{ feature.feature_id }}</code>
          <span class="cad-meta">
            标注原文 “{{ feature.annotation_raw }}” · 单位 {{ feature.unit }} ·
            名义值 {{ feature.nominal_value ?? '—' }} ·
            下/上 {{ feature.tolerance_lower ?? '—' }} / {{ feature.tolerance_upper ?? '—' }}
          </span>
        </div>
        <button class="cad-mini" type="button" data-testid="cad-reload-history" :disabled="historyLoading" @click="emit('reload-history')">
          {{ historyLoading ? '读取中…' : '重新读取复核历史' }}
        </button>
      </div>
      <p class="cad-hint">
        机器关联状态（冻结，仅供参考，不是本区结论）：{{ feature.association_status }}；
        机器选定目标：<code class="cad-code">{{ feature.selected_target ?? '—' }}</code>。
        人工结论以 <code>association_reviews</code> 为准。
      </p>

      <CadErrorNote :error="historyError" testid="cad-history-error" />
      <p v-if="historyLoading" class="cad-hint">正在读取复核历史…</p>
      <p v-else-if="!historyError && !reviews.length" class="cad-hint" data-testid="cad-history-empty">
        该要素在本版本**从未**被人工复核（latest_review = null）：所以 previous_review_id 会发 null，
        review_gate 以 CAD_ASSOCIATION_NOT_REVIEWED 排除它。
      </p>
      <ul v-else-if="reviews.length" class="cad-history" data-testid="cad-history-list">
        <li v-for="(r, i) in reviews" :key="r.review_id">
          <span class="cad-hist-idx">#{{ i + 1 }}</span>
          <span class="cad-hist-decision">{{ decisionText(r.decision) }}</span>
          <span class="cad-meta">
            目标 <code class="cad-code">{{ r.selected_target ?? '—' }}</code>
            · {{ r.reviewer_label }}（{{ (r.reviewer_roles || []).join('/') || '—' }}）
            · {{ r.created_at }}
            · review_id <code class="cad-code">{{ r.review_id }}</code>
            · previous <code class="cad-code">{{ r.previous_review_id ?? 'null' }}</code>
          </span>
          <span class="cad-hist-reason">{{ r.reason }}</span>
        </li>
      </ul>

      <form class="cad-form" data-testid="cad-review-form" @submit.prevent="submit">
        <h4>提交关联复核（DrawingAssociationReviewRequest）</h4>

        <fieldset class="cad-field">
          <legend>decision</legend>
          <label><input v-model="decision" type="radio" value="confirmed" data-testid="cad-decision-confirmed" /> confirmed（确认关联）</label>
          <label><input v-model="decision" type="radio" value="rejected" data-testid="cad-decision-rejected" /> rejected（驳回）</label>
          <label><input v-model="decision" type="radio" value="needs_review" data-testid="cad-decision-needs-review" /> needs_review（仍需复核）</label>
        </fieldset>

        <fieldset class="cad-field">
          <legend>selected_target</legend>
          <p v-if="decision !== 'confirmed'" class="cad-hint" data-testid="cad-target-absent">
            当前决策不是 confirmed：按契约 <code>selected_target</code> 必须是 null（带上会被 422
            CAD_REVIEW_TARGET_UNEXPECTED），本页发送 null。
          </p>
          <template v-else>
            <p v-if="!candidates.length" class="cad-hint" data-testid="cad-target-empty">
              该要素的 <code>target_candidates</code> 为空：无法做 confirmed，只能 rejected 或 needs_review。
            </p>
            <label v-for="h in candidates" :key="h">
              <input v-model="target" type="radio" :value="h" data-testid="cad-target-option" />
              <code class="cad-code">{{ h }}</code>
            </label>
          </template>
        </fieldset>

        <label class="cad-textarea">
          <span>reason（必填，非空，≤1000 字）</span>
          <textarea
            v-model="reason"
            rows="3"
            maxlength="1000"
            data-testid="cad-review-reason"
            placeholder="写清楚为什么这样关联：依据哪个候选圆的几何/引注，以及你排除了什么。"
          ></textarea>
        </label>

        <details class="cad-tuple" data-testid="cad-cas-tuple">
          <summary>本次提交会带上的版本指纹（服务端 CAS 校验整组，任一项过期即 409）</summary>
          <dl>
            <template v-for="[key, value] in tuple" :key="key">
              <dt>{{ key }}</dt>
              <dd><code class="cad-code">{{ value }}</code></dd>
            </template>
          </dl>
        </details>

        <CadErrorNote :error="submitError" testid="cad-review-submit-error" />
        <p v-if="submitNotice" class="cad-ok" data-testid="cad-review-notice">{{ submitNotice }}</p>

        <div class="cad-blocked" v-if="blockedReasons.length" data-testid="cad-review-blocked">
          <p>现在不能提交，原因如下（不做假提交）：</p>
          <ul>
            <li v-for="(b, i) in blockedReasons" :key="i">{{ b }}</li>
          </ul>
        </div>

        <button class="cad-submit" type="submit" :disabled="!canSubmit" data-testid="cad-review-submit">
          {{ submitting ? '提交中…' : '提交复核' }}
        </button>
        <span class="cad-meta cad-role">
          当前身份：{{ anonymous ? '匿名' : rolesText || '—' }}；
          本动作要求 REVIEWER（ADMIN 可过所有闸门，APPROVER 在这里会被 403 PERMISSION_DENIED）。
        </span>
      </form>
    </template>

    <p class="cad-hint cad-foot" data-testid="cad-review-conflict-note">
      门槛冲突（例如 <code>CAD_GEOMETRY_ANNOTATION_CONFLICT</code>）来自原文本身：
      关联复核**不能**把它批准掉，也不能让 inspection_gate 变成 ready。
    </p>
  </section>
</template>

<style scoped>
.cad-section {
  margin: 0 0 22px;
  padding: 14px 16px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
}
.cad-sec-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
/* 段内小标题与其它页统一：蓝色、15px、600（原来是 14px 灰黑） */
.cad-sub {
  margin: 16px 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.cad-hint {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--color-text);
}
.cad-emph {
  color: var(--color-body);
}
.cad-honesty {
  color: #b45309;
}
.cad-hint code,
.cad-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 5px;
  padding: 1px 5px;
  color: var(--color-text);
}
.cad-semantics {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--color-text);
}
.cad-warn {
  margin: 0 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(217, 119, 6, 0.35);
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.07);
  font-size: 13.5px;
  line-height: 1.75;
  color: #b45309;
}
.cad-tablewrap {
  overflow-x: auto;
  border: 1px solid var(--color-line);
  border-radius: 12px;
}
.cad-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 14px;
  min-width: 720px;
}
.cad-table th,
.cad-table td {
  text-align: left;
  vertical-align: top;
  padding: 7px 10px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-body);
}
.cad-table th {
  color: var(--color-muted);
  font-weight: 500;
  background: var(--color-ink);
  white-space: nowrap;
}
.cad-table tbody tr.cad-is-selected {
  background: rgba(37, 99, 235, 0.08);
}
.cad-table tbody tr[data-testid='cad-association-row'] {
  cursor: pointer;
}
.cad-table tr:last-child td {
  border-bottom: 0;
}
.cad-none {
  color: var(--color-text);
  font-style: italic;
}
.cad-reason-cell {
  max-width: 260px;
  white-space: normal;
}
.cad-meta {
  display: inline-block;
  margin-left: 6px;
  font-size: 12.5px;
  color: var(--color-text);
  line-height: 1.6;
}
.cad-feature-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
  margin-bottom: 8px;
}
/* 「逐条复核」小按钮：与其它按钮统一成蓝底白字加粗椭圆（原来是白底细描边） */
.cad-mini {
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.cad-mini:disabled {
  opacity: 0.6;
  cursor: default;
}
.cad-history {
  list-style: none;
  margin: 0 0 12px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cad-history li {
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-body);
}
.cad-hist-idx {
  font-size: 12.5px;
  color: var(--color-muted);
  margin-right: 6px;
}
.cad-hist-decision {
  font-weight: 600;
  color: var(--color-text);
}
.cad-hist-reason {
  display: block;
  margin-top: 3px;
  white-space: pre-wrap;
}
.cad-form {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  padding: 12px 14px;
  background: var(--color-ink);
}
/* 表单小标题与其它页统一：蓝色、15px、600（原来是 14px 灰黑） */
.cad-form h4 {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.cad-field {
  border: 0;
  padding: 0;
  margin: 0 0 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
}
.cad-field legend {
  font-size: 13px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  margin-bottom: 4px;
}
.cad-field label {
  font-size: 14px;
  color: var(--color-body);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}
.cad-textarea {
  display: block;
  margin-bottom: 12px;
}
.cad-textarea span {
  display: block;
  font-size: 13px;
  color: var(--color-muted);
  margin-bottom: 4px;
}
.cad-textarea textarea {
  width: 100%;
  font: inherit;
  font-size: 14px;
  line-height: 1.7;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
  resize: vertical;
  box-sizing: border-box;
}
.cad-tuple {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--color-text);
}
.cad-tuple summary {
  cursor: pointer;
}
.cad-tuple dl {
  margin: 6px 0 0;
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 12px;
}
.cad-tuple dt {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.cad-tuple dd {
  margin: 0;
  word-break: break-all;
}
.cad-blocked {
  margin: 0 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(217, 119, 6, 0.35);
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.07);
  font-size: 13.5px;
  color: #b45309;
}
.cad-blocked p {
  margin: 0 0 4px;
}
.cad-blocked ul {
  margin: 0;
  padding-left: 18px;
  line-height: 1.7;
}
/* 提交按钮本来就是蓝底，补齐加粗以与「模型接入」页的按钮等价 */
.cad-submit {
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 8px 20px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
}
.cad-submit:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.cad-submit:disabled {
  opacity: 0.5;
  cursor: default;
  background: var(--color-panel);
  color: var(--color-muted);
  border-color: var(--color-line);
}
.cad-role {
  margin-left: 10px;
}
.cad-ok {
  margin: 0 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(22, 163, 74, 0.35);
  border-radius: 12px;
  background: rgba(22, 163, 74, 0.07);
  font-size: 13.5px;
  color: #15803d;
  line-height: 1.7;
}
.cad-foot {
  margin-top: 12px;
}
</style>
