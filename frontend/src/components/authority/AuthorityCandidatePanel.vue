<script setup lang="ts">
import type { AuthorityCandidate } from '../../api/types'

/**
 * 单条未消解冲突候选。
 *
 * WHY 两侧原值必须并排显示：这条候选存在的前提就是生产数据与报告字段**不一致**。
 * 只显示「胜方」或只显示一个数，等于把冲突抹掉；权威规则的作用是记录「以哪一侧为准」，
 * 不是改写任何一侧的原始值。所以 production_value/unit 与 report_value/unit 一律原样并陈，
 * 一个都不换算、不四舍五入、不省略。
 *
 * eligible 的含义（决定登记按钮能不能点）：报告侧事实是否已经拿到
 * 「已认证身份的确认复核」**并且**「独立的正式审批」。两者缺一就不能登记。
 */

const props = defineProps<{
  candidate: AuthorityCandidate
  selected: boolean
  /** 登记请求在飞行中：全局禁用，避免重复选中/重复提交 */
  busy?: boolean
}>()

const emit = defineEmits<{ (e: 'select', candidate: AuthorityCandidate): void }>()

function raw(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '（缺失）'
  return String(value)
}

function unit(value: string | null | undefined): string {
  return value ? value : '（无单位）'
}

/** 原样显示时间串：它的时区偏移是 valid_from ≤ produced_at < valid_to 判定的组成部分。 */
function stamp(value: string | null | undefined): string {
  return value ? value : '（缺失）'
}

const scopeFields: Array<{ key: keyof AuthorityCandidate['context']['scope']; label: string }> = [
  { key: 'part_id', label: '零件' },
  { key: 'operation_no', label: '工序' },
  { key: 'batch_id', label: '批次' },
  { key: 'measurement_id', label: '测量 ID' },
  { key: 'metric', label: '指标' },
  { key: 'method', label: '方法' },
]
</script>

<template>
  <article
    class="acp-card"
    :class="{ 'is-selected': props.selected }"
    data-testid="authority-candidate"
  >
    <header class="acp-head">
      <code class="acp-id" data-testid="authority-candidate-id">
        {{ candidate.context.scope.measurement_id }}
      </code>
      <span
        class="acp-badge"
        :class="candidate.eligible === false ? 'is-blocked' : 'is-eligible'"
        data-testid="authority-candidate-eligible"
      >
        {{ candidate.eligible === false ? '报告字段尚未独立审批' : '报告字段已复核且已独立审批' }}
      </span>
    </header>

    <div class="acp-compare" data-testid="authority-candidate-values">
      <section class="acp-side acp-production" data-testid="authority-candidate-production">
        <h4 class="acp-side-title">生产数据（SQL 实测原值）</h4>
        <p class="acp-value">
          <span class="acp-number">{{ raw(candidate.production_value) }}</span>
          <span class="acp-unit">{{ unit(candidate.production_unit) }}</span>
        </p>
      </section>

      <div class="acp-gap" aria-hidden="true">对</div>

      <section class="acp-side acp-report" data-testid="authority-candidate-report">
        <h4 class="acp-side-title">报告字段（PDF 事实原值）</h4>
        <p class="acp-value">
          <span class="acp-number" :class="{ 'is-missing': candidate.report_value == null }">
            {{ raw(candidate.report_value) }}
          </span>
          <span class="acp-unit">{{ unit(candidate.report_unit) }}</span>
        </p>
      </section>
    </div>

    <p class="acp-note">两侧都是原值，未做单位换算或取舍：冲突到这里仍然未消解。</p>

    <dl class="acp-meta">
      <div class="acp-meta-row">
        <dt>生产记录时间 produced_at</dt>
        <dd><code data-testid="authority-candidate-produced-at">{{ stamp(candidate.produced_at) }}</code></dd>
      </div>
      <div v-for="f in scopeFields" :key="f.key" class="acp-meta-row">
        <dt>{{ f.label }}</dt>
        <dd><code>{{ candidate.context.scope[f.key] || '—' }}</code></dd>
      </div>
    </dl>

    <p
      v-if="candidate.eligible === false"
      class="acp-blocked"
      data-testid="authority-candidate-ineligible"
    >
      报告字段尚未独立审批，请先到字段复核处理。
    </p>

    <button
      class="acp-pick"
      type="button"
      :disabled="candidate.eligible === false || props.busy || props.selected"
      data-testid="authority-register-button"
      @click="emit('select', candidate)"
    >
      {{ props.selected ? '已选中（在下方填写登记表）' : '选中并登记规则' }}
    </button>
  </article>
</template>

<style scoped>
/* 类名统一 acp- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会出现
   「部分属性被工具类接管」的怪象（仓库里已出过两次事故）。 */
.acp-card {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
  margin-bottom: 12px;
}
.acp-card.is-selected {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.05);
}
.acp-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.acp-id {
  font-size: 14px;
  color: var(--color-text);
  word-break: break-all;
}
/* 徽标统一蓝底白字加粗椭圆（原来是白底描边） */
.acp-badge {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
}
.acp-badge.is-eligible {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.acp-badge.is-blocked {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
.acp-compare {
  display: flex;
  align-items: stretch;
  gap: 10px;
}
.acp-side {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  padding: 10px 12px;
  background: var(--color-ink);
}
.acp-production {
  border-left: 3px solid var(--color-accent);
}
.acp-report {
  border-left: 3px solid var(--color-accent-2);
}
/* 对比栏小标题：蓝色、15px、600（原来是 13px 灰字，属离群值） */
.acp-side-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.acp-value {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}
.acp-number {
  font-size: 18px;
  font-variant-numeric: tabular-nums;
  color: var(--color-text);
}
.acp-number.is-missing {
  font-size: 14.5px;
  color: #e11d48;
}
.acp-unit {
  font-size: 13.5px;
  color: var(--color-body);
}
.acp-gap {
  align-self: center;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--color-text);
}
.acp-note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--color-text);
}
.acp-meta {
  margin: 10px 0 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 4px 16px;
}
.acp-meta-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.acp-meta-row dt {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--color-muted);
}
.acp-meta-row dd {
  margin: 0;
  min-width: 0;
  font-size: 13px;
  color: var(--color-body);
  word-break: break-all;
}
.acp-blocked {
  margin: 12px 0 0;
  font-size: 14px;
  color: #d97706;
}
/* 「选为权威来源」：与其它按钮统一成蓝底白字加粗椭圆（原来是白底细描边） */
.acp-pick {
  margin-top: 12px;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 7px 16px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.acp-pick:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.acp-pick:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
