<script setup lang="ts">
/**
 * 工业核验表：把 industrial_result.rows 渲染成「一条 measurement 一行」的核验表。
 *
 * 字段名全部对着真实响应核过（2026-09 A102 工序 30，10 行 / 9 批次）：
 *   rows[].value + unit            数据库实测值
 *   rows[].normalized_value/unit   归一值（UNKNOWN_UNIT 时为 null）
 *   rows[].report_value/report_unit 质检报告侧原值
 *   rows[].compliance              核验结论（out_of_range 等）
 *   rows[].agreement               来源一致性（unresolved 等）
 *   rows[].issues[].left_value / right_value  冲突双方原值 —— 两者都必须显示
 *   rows[].rules[].rule_id         本行套用的规则
 *   summary.out_of_range / conflicts / needs_review  概览条与筛选依据
 */
import { computed, ref } from 'vue'
import type { IndustrialResult, IndustrialRow, IndustrialIssue } from '../../api/types'

const props = defineProps<{ result: IndustrialResult }>()

const rows = computed<IndustrialRow[]>(() => props.result?.rows ?? [])
const summary = computed(() => props.result?.summary)

/* ── 结论用词：后端是机器码，表里给中文，机器码留 title 便于对照 ── */
const COMPLIANCE_LABEL: Record<string, string> = {
  out_of_range: '超限',
  within_range: '范围内',
  needs_review: '待复核',
  insufficient_evidence: '证据不足',
}
const AGREEMENT_LABEL: Record<string, string> = {
  consistent: '一致',
  resolved_equivalent: '等价',
  resolved_by_authority: '已裁决',
  unresolved: '未消解',
  needs_review: '待复核',
  insufficient_evidence: '证据不足',
}
const ISSUE_LABEL: Record<string, string> = {
  value_conflict: '值冲突',
  version_overlap: '版本重叠',
  version_difference: '版本差异',
  entity_alignment: '实体对齐',
  measurement_context: '测量上下文',
  requirement_context: '要求上下文',
  unit_or_value: '单位/取值',
  unit_equivalence: '单位等价',
  report_gap: '报告缺口',
}

/** 状态色：与全站一致的红/橙/绿/蓝语义 */
function statusTone(v: string): string {
  if (v === 'out_of_range' || v === 'unresolved' || v === 'failed') return 'bad'
  if (v === 'needs_review' || v === 'insufficient_evidence' || v === 'warning') return 'warn'
  if (v === 'resolved_by_authority') return 'info'
  return 'ok'
}
const complianceText = (v: string) => COMPLIANCE_LABEL[v] ?? v
const agreementText = (v: string) => AGREEMENT_LABEL[v] ?? v
const issueText = (v: string) => ISSUE_LABEL[v] ?? v
const statusText = (v: string) => (v === 'resolved_by_authority' ? '已裁决' : AGREEMENT_LABEL[v] ?? v)

/** 「值 + 单位」拼成一个不可拆的片段 */
function withUnit(value: unknown, unit: unknown): string {
  if (value == null || value === '') return '—'
  return unit ? `${value} ${unit}` : String(value)
}

/** 冲突双方原值：issue 自带 left/right 就用它；没有则退回「生产 ↔ 报告」两侧原值 */
function issuePair(issue: IndustrialIssue, row: IndustrialRow): string {
  const left = issue.left_value
  const right = issue.right_value
  if (left != null || right != null) {
    return `左 ${withUnit(left, row.unit)} ↔ 右 ${withUnit(right, row.report_unit ?? row.unit)}`
  }
  if (row.report_value != null) {
    return `生产 ${withUnit(row.value, row.unit)} ↔ 报告 ${withUnit(row.report_value, row.report_unit)}`
  }
  return ''
}

/* ── 概览条：点某一类只看那一类，再点一次恢复全部 ── */
type FilterKey = 'all' | 'out_of_range' | 'conflicts' | 'needs_review' | 'authority_resolved'
const filter = ref<FilterKey>('all')

const summaryItems = computed(() => {
  const s = summary.value
  return [
    { key: 'all' as FilterKey, label: '批次', value: s?.batch_count ?? new Set(rows.value.map((r) => r.batch_id)).size },
    { key: 'all' as FilterKey, label: '测量', value: s?.measurement_count ?? rows.value.length },
    { key: 'out_of_range' as FilterKey, label: '超限', value: s?.out_of_range?.length ?? 0 },
    { key: 'conflicts' as FilterKey, label: '冲突', value: s?.conflicts?.length ?? 0 },
    { key: 'authority_resolved' as FilterKey, label: '已裁决', value: s?.authority_resolved?.length ?? 0 },
    { key: 'needs_review' as FilterKey, label: '待复核', value: s?.needs_review?.length ?? 0 },
  ]
})

function toggleFilter(key: FilterKey) {
  if (key === 'all') {
    filter.value = 'all'
    return
  }
  filter.value = filter.value === key ? 'all' : key
}

const filteredRows = computed<IndustrialRow[]>(() => {
  const s = summary.value
  const ids = (list?: string[]) => new Set(list ?? [])
  switch (filter.value) {
    case 'out_of_range':
      return rows.value.filter(
        (r) => r.compliance === 'out_of_range' || ids(s?.out_of_range).has(r.measurement_id),
      )
    case 'conflicts':
      return rows.value.filter(
        (r) => r.agreement === 'unresolved' || ids(s?.conflicts).has(r.measurement_id),
      )
    case 'authority_resolved':
      return rows.value.filter(
        (r) => r.agreement === 'resolved_by_authority' || ids(s?.authority_resolved).has(r.measurement_id),
      )
    case 'needs_review':
      return rows.value.filter((r) => ids(s?.needs_review).has(r.measurement_id))
    default:
      return rows.value
  }
})

/* ── 可复现性证据：范围 / 规则版本 / 权威版本 / 数据集版本 ── */
const scope = computed(() => props.result?.scope ?? {})
const scopeLine = computed(() => {
  const s: any = scope.value
  const parts: string[] = []
  if (s.part_id) parts.push(`零件 ${s.part_id}`)
  if (s.operation_no) parts.push(`工序 ${s.operation_no}`)
  if (s.month) parts.push(s.month)
  if (s.metric) parts.push(s.metric)
  return parts.join(' · ')
})
const scopeRange = computed(() => {
  const s: any = scope.value
  return s.start && s.end ? `统计区间 ${s.start} ~ ${s.end}` : ''
})
const dataset = computed(() => props.result?.dataset ?? {})
const datasetVersion = computed(() => (dataset.value as any).dataset_version ?? '')
const datasetTip = computed(() => {
  const d: any = dataset.value
  const bits = [d.provenance, d.seed_sha256 && `seed_sha256 ${d.seed_sha256}`, d.pdf_sha256 && `pdf_sha256 ${d.pdf_sha256}`]
  return bits.filter(Boolean).join('\n')
})
const ruleLine = computed(() => {
  const r = props.result
  return [
    r?.rule_version && `规则版本 ${r.rule_version}`,
    r?.authority_version && `权威版本 ${r.authority_version}`,
    datasetVersion.value && `数据集 ${datasetVersion.value}`,
    r?.partial ? '结论不完整（部分测量待复核/存在冲突）' : '',
  ].filter(Boolean) as string[]
})
</script>

<template>
  <section v-if="rows.length" class="verify">
    <div class="v-head">
      <h3 class="v-title">工业核验表</h3>
      <span class="v-count">{{ filteredRows.length }} / {{ rows.length }} 条测量</span>
      <span v-if="result.model_used === false" class="v-chip" title="本次核验由规则与 SQL 完成，未调用大模型">规则核验 · 未用模型</span>
    </div>

    <!-- 结果可复现的证据：范围 + 三个版本号 -->
    <p class="v-meta">
      <span v-if="scopeLine" class="v-scope" :title="scopeRange">{{ scopeLine }}</span>
      <span v-for="line in ruleLine" :key="line" class="v-ver" :title="line.startsWith('数据集') ? datasetTip : line">{{ line }}</span>
    </p>

    <!-- 概览条：点击某一类只看该类，再点一次回到全部 -->
    <div class="v-summary">
      <button
        v-for="(it, i) in summaryItems"
        :key="i"
        class="v-stat"
        type="button"
        :class="[{ active: filter !== 'all' && filter === it.key }, it.key === 'out_of_range' ? 'bad' : it.key === 'conflicts' ? 'bad' : it.key === 'needs_review' ? 'warn' : '']"
        :disabled="it.key === 'all'"
        :title="it.key === 'all' ? '全部测量' : `只看「${it.label}」的行`"
        @click="toggleFilter(it.key)"
      >
        <b>{{ it.value }}</b><span>{{ it.label }}</span>
      </button>
      <button v-if="filter !== 'all'" class="v-reset" type="button" @click="filter = 'all'">清除筛选</button>
    </div>

    <div class="v-wrap">
      <table class="v-table">
        <thead>
          <tr>
            <th>批次</th>
            <th>测量ID</th>
            <th>修订版</th>
            <th>实测值</th>
            <th>归一值</th>
            <th>合规</th>
            <th>一致性</th>
            <th>问题</th>
            <th>规则</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in filteredRows" :key="r.measurement_id" :class="[statusTone(r.compliance), statusTone(r.agreement)]">
            <td class="c-batch">{{ r.batch_id }}</td>
            <td class="c-mid mono">{{ r.measurement_id }}</td>
            <td class="c-rev">
              <span :title="`记录版本 ${r.recorded_revision ?? '—'} / 适用版本 ${r.applicable_revision ?? '—'}`">
                {{ r.applicable_revision ?? r.recorded_revision ?? '—' }}
              </span>
            </td>
            <td class="c-val"><b>{{ r.value }}</b><i>{{ r.unit }}</i></td>
            <td class="c-val">
              <template v-if="r.normalized_value != null"><b>{{ r.normalized_value }}</b><i>{{ r.normalized_unit }}</i></template>
              <span v-else class="c-na" title="单位未识别或不可比较，归一值未生成">—</span>
            </td>
            <td>
              <span class="tag" :class="statusTone(r.compliance)" :title="r.compliance">{{ complianceText(r.compliance) }}</span>
            </td>
            <td>
              <span class="tag" :class="statusTone(r.agreement)" :title="r.agreement">{{ agreementText(r.agreement) }}</span>
            </td>
            <td class="c-issues">
              <p v-if="!r.issues.length" class="c-na">无</p>
              <div v-for="(is, k) in r.issues" :key="k" class="issue">
                <span class="issue-kind" :class="statusTone(is.status)">{{ issueText(is.kind) }}</span>
                <!-- 冲突并陈：left_value / right_value 双方都在，不做取舍 -->
                <span v-if="issuePair(is, r)" class="issue-pair">{{ issuePair(is, r) }}</span>
                <span v-if="is.reason" class="issue-reason" :title="is.reason">{{ is.reason }}</span>
              </div>
            </td>
            <td class="c-rules">
              <p v-if="!r.rules.length" class="c-na">无</p>
              <span
                v-for="(rl, k) in r.rules"
                :key="k"
                class="rule mono"
                :class="rl.passed === true ? 'ok' : rl.passed === false ? 'bad' : ''"
                :title="[rl.rule_id, rl.rule_version, rl.expression, rl.conversion, rl.left_value != null ? `left ${rl.left_value}` : '', rl.right_value != null ? `right ${rl.right_value}` : ''].filter(Boolean).join('\n')"
              >{{ rl.rule_id }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!filteredRows.length" class="v-empty">该筛选下没有测量记录。</p>
    </div>

    <ul class="v-legend">
      <li>「左 / 右」是冲突双方的原值：两方都保留，不做取舍。</li>
      <li>没有左右值的问题行，显示「生产 ↔ 报告」两侧的原值。</li>
      <li>「规则」列的小字是本行实际套用的规则编号，鼠标悬停可看判据表达式。</li>
      <li>结论标签悬停可以看到服务端返回的原始取值。</li>
    </ul>
  </section>
</template>

<style scoped>
.verify {
  margin-top: 14px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 12px 12px 10px;
}
.v-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* 面板标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.v-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.v-count {
  font-size: 12.5px;
  color: var(--color-muted);
}
/* 右上角计数标签：蓝底白字加粗椭圆（原来是灰底描边） */
.v-chip {
  margin-left: auto;
  font-size: 12.5px;
  font-weight: 600;
  color: #ffffff;
  background: var(--color-accent);
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 2px 9px;
  white-space: nowrap;
}
.v-meta {
  margin: 8px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-text);
}
.v-scope {
  color: var(--color-body);
  font-weight: 600;
}
.v-ver {
  border: 1px solid var(--color-line);
  border-radius: 4px;
  padding: 0 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  cursor: help;
}

/* ── 概览条 ── */
.v-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 9px 0 8px;
}
.v-stat {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  border-radius: 8px;
  padding: 4px 9px;
  font-family: inherit;
  font-size: 12.5px;
  color: var(--color-body);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.v-stat b {
  font-size: 14.5px;
  color: var(--color-text);
}
.v-stat:hover:not(:disabled) {
  border-color: rgba(15, 23, 42, 0.24);
  background: var(--color-panel);
}
.v-stat:disabled {
  cursor: default;
}
.v-stat.bad b {
  color: #e11d48;
}
.v-stat.warn b {
  color: #d97706;
}
.v-stat.active {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.08);
  color: var(--color-accent);
}
.v-stat.active b {
  color: var(--color-accent);
}
.v-reset {
  margin-left: auto;
  border: none;
  background: none;
  padding: 0;
  font-family: inherit;
  font-size: 12.5px;
  color: var(--color-accent);
  cursor: pointer;
}
.v-reset:hover {
  text-decoration: underline;
}

/* ── 表体：固定布局 + 容器内横向滚动，任何宽度下都不撑破对话框 ── */
.v-wrap {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--color-line);
  border-radius: 12px;
}
.v-table {
  width: 100%;
  min-width: 660px;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 13px;
}
.v-table th {
  text-align: left;
  font-weight: 600;
  color: var(--color-muted);
  background: var(--color-ink);
  padding: 6px 7px;
  border-bottom: 1px solid var(--color-line);
  white-space: nowrap;
}
.v-table td {
  vertical-align: top;
  padding: 6px 7px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-text);
  word-break: break-word;
}
.v-table tr:last-child td {
  border-bottom: none;
}
.v-table th:nth-child(1) { width: 6.5%; }
.v-table th:nth-child(2) { width: 8.5%; }
.v-table th:nth-child(3) { width: 9%; }
.v-table th:nth-child(4) { width: 9%; }
.v-table th:nth-child(5) { width: 9%; }
.v-table th:nth-child(6) { width: 8.5%; }
.v-table th:nth-child(7) { width: 9%; }
.v-table th:nth-child(8) { width: 16.5%; }
.v-table th:nth-child(9) { width: 24%; }

/* 超限/冲突行整行可辨：左侧色条 + 极淡底色 */
.v-table tr.bad td {
  background: rgba(225, 29, 72, 0.05);
}
.v-table tr.bad td:first-child {
  box-shadow: inset 2px 0 0 #e11d48;
}
.v-table tr.warn td {
  background: rgba(217, 119, 6, 0.05);
}
.v-table tr.warn td:first-child {
  box-shadow: inset 2px 0 0 #d97706;
}

.c-batch {
  font-weight: 600;
}
/* 批次号 / 测量 ID 是标识符，窄视口下也不能折行（折成两行会被误读成两个值） */
.v-table td.c-batch,
.v-table td.c-mid {
  white-space: nowrap;
  word-break: normal;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
}
.c-rev {
  font-size: 12.5px;
  color: var(--color-body);
}
/* 版本号 / 值 / 单位都是不可拆的整体。
   注意选择器必须压过上面的 `.v-table td`（0,1,1）——`word-break` 是继承属性，
   只写 `.c-val`（0,1,0）会被 td 的 break-word 盖掉，degC 就又被折成 de / gC。
   值和单位用 inline-block 包成原子盒：放不下时整体换行，绝不从中间断开。 */
.v-table td.c-rev {
  white-space: nowrap;
  word-break: normal;
}
.v-table td.c-val {
  word-break: normal;
  overflow-wrap: normal;
}
.v-table td.c-val b,
.v-table td.c-val i {
  display: inline-block;
  white-space: nowrap;
}
.v-table td.c-val b {
  color: var(--color-text);
}
.v-table td.c-val i {
  margin-left: 4px;
  font-style: normal;
  font-size: 12px;
  color: var(--color-muted);
}
.c-na {
  margin: 0;
  color: var(--color-muted);
}
.tag {
  display: inline-block;
  font-size: 12.5px;
  line-height: 1.6;
  border-radius: 4px;
  padding: 0 4px;
  /* 允许在标签内部换行：宁可标签占两行，也不能溢出到相邻列上压住内容 */
  white-space: normal;
}
.tag.ok {
  color: #15803d;
  background: rgba(22, 163, 74, 0.10);
}
.tag.bad {
  color: #be123c;
  background: rgba(225, 29, 72, 0.12);
  font-weight: 600;
}
/* 判据标签：橙底白字。原来是浅橙底 + 深橙字 #b45309，实心化后深橙压橙底读不出来；
   橙色本身作为「待复核」语义保留。 */
.tag.warn {
  color: #ffffff;
  font-weight: 600;
  background: #d97706;
}
.tag.info {
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.10);
}

/* ── 问题列：结论 + 冲突双方原值 + 原因 ── */
.issue {
  margin-bottom: 3px;
}
.issue:last-child {
  margin-bottom: 0;
}
.issue-kind {
  display: inline-block;
  font-size: 12px;
  border-radius: 4px;
  padding: 0 4px;
  color: var(--color-body);
  background: var(--color-ink);
}
.issue-kind.bad {
  color: #be123c;
  background: rgba(225, 29, 72, 0.12);
}
.issue-kind.warn {
  color: #ffffff; font-weight: 600;
  background: #d97706;
}
.issue-pair {
  display: block;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  /* 优先在空格处换行，别把「报告」拆成「报 / 告」；实在放不下才允许任意断 */
  word-break: keep-all;
  overflow-wrap: anywhere;
}
.issue-reason {
  display: block;
  font-size: 9.5px;
  line-height: 1.5;
  color: var(--color-text);
  word-break: break-all;
}

/* ── 规则列：rule_id 是审计标识，只能换行不能截断，字号压到 9.5px 换取整行可读 ── */
.c-rules .rule {
  display: block;
  font-size: 9.5px;
  line-height: 1.55;
  color: var(--color-body);
  word-break: break-all;
}
.c-rules .rule.ok {
  color: #15803d;
}
.c-rules .rule.bad {
  color: #be123c;
}
.v-empty {
  margin: 0;
  padding: 10px;
  font-size: 13px;
  color: var(--color-text);
}
.v-legend {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-text);
}
/* 说明按条列出：每行一条、前面一个蓝点（与 GovernancePage 的 .gov-note 同一套写法）。 */
.v-legend > li {
  position: relative;
  padding-left: 14px;
}
.v-legend > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.62em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
}
</style>
