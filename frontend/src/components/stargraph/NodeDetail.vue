<script setup lang="ts">
import { computed } from 'vue'
import type { EvidenceNode } from '../../api/types'

const props = defineProps<{ node: EvidenceNode | null }>()

const TYPE_LABEL: Record<string, string> = {
  Claim: '答案主张',
  SQLQuery: 'SQL 查询',
  SQLRow: '查询结果行',
  Document: '文档',
  Chunk: '检索片段',
  DocumentFact: '字段',
  VerificationCheck: '核验',
  ConflictRecord: '冲突',
  AuthorityRule: '权威规则',
}

const payload = computed<Record<string, any>>(() => props.node?.payload ?? {})
const label = computed(() => (props.node ? TYPE_LABEL[props.node.node_type] ?? props.node.node_type : ''))

/**
 * 字段名一律取自真实 payload，别按语义猜：
 *
 * 工业证据（datamind/agent.py::_record_industrial_evidence）
 *   SQLQuery        sql / bound_params / sql_hash / source_snapshot_id
 *   SQLRow          扁平一行：part_id,batch_id,measurement_id,operation_no,metric,value,unit,
 *                   measured_at,method,quality_id,report_id —— 没有 columns / rows
 *   Document        document_id / document_version / source_sha256 / title / source_url
 *   DocumentFact    raw_value / raw_unit / raw_header / entity_name / fact_kind /
 *                   normalized_value / normalized_unit / conditions / valid_from / valid_to /
 *                   source_revision / parser_version / approval_status / evidence_refs
 *                   （原来的 value / unit / entity / header 一个都不存在）
 *   VerificationCheck 整行 row：compliance / agreement / value / unit / normalized_value /
 *                   report_value / report_unit / recorded_revision / applicable_revision /
 *                   issues / rules / quality_id / quality_method
 *   ConflictRecord  conflict_type / left_fact_refs / right_fact_refs / normalized_values /
 *                   rule_id / resolution_status / comparison_context / reason
 *   AuthorityRule   rule_id / rule_version / registration.* / creator_label / approver_label
 *
 * 检索证据（agent.py::_record_document_evidence，payload 来自 retrieval citation）
 *   Document        document_id / title / document_version / document_sha256 / source_url /
 *                   origin / quality_detail —— 注意这里是 origin，工业那侧是 source_sha256
 *   Chunk           quote / title / page_start / page_end / section_path / used_in_answer /
 *                   retrieval_score / source_url / page_url —— 没有 text / excerpt / page_no
 */

/** SQLRow 的 payload 就是一行结果，直接按键值渲染；顺序按后端 SQL 的列序 */
const SQL_ROW_ORDER = [
  'batch_id',
  'part_id',
  'produced_at',
  'recorded_revision',
  'measurement_id',
  'operation_no',
  'metric',
  'value',
  'unit',
  'measured_at',
  'method',
  'quality_id',
  'report_id',
  'quality_method',
]
const sqlRowFields = computed(() => {
  const p = payload.value
  const known = SQL_ROW_ORDER.filter((k) => p[k] != null)
  const rest = Object.keys(p).filter((k) => !SQL_ROW_ORDER.includes(k) && p[k] != null)
  return [...known, ...rest]
})

const FIELD_LABEL: Record<string, string> = {
  part_id: '零件',
  batch_id: '批次',
  measurement_id: '测量 ID',
  operation_no: '工序',
  metric: '指标',
  value: '实测值',
  unit: '单位',
  normalized_value: '归一值',
  normalized_unit: '归一单位',
  report_value: '报告值',
  report_unit: '报告单位',
  measured_at: '测量时间',
  produced_at: '生产时间',
  method: '方法',
  quality_method: '质检方法',
  quality_id: '质检 ID',
  report_id: '报告 ID',
  recorded_revision: '记录版本',
  applicable_revision: '适用版本',
  compliance: '合规结论',
  agreement: '来源一致性',
  raw_value: '原文值',
  raw_unit: '原文单位',
  raw_header: '原文表头',
  entity_id: '实体 ID',
  entity_name: '实体名',
  entity_type: '实体类型',
  fact_kind: '字段类型',
  source_revision: '来源修订',
  valid_from: '生效起',
  valid_to: '生效止',
  parser_version: '解析器',
  approval_status: '审批状态',
  formal_approval_status: '正式审批',
  verification_status: '核验状态',
  extraction_method: '抽取方式',
  provenance: '数据来源',
  operation_no_alt: '工序',
  page_start: '起始页',
  page_end: '结束页',
  document_version: '版本',
  document_id: '文档 ID',
  retrieval_score: '检索分',
}
const fieldLabel = (k: string) => FIELD_LABEL[k] ?? k

/** 值渲染：数组/对象不直接丢进模板，转成可读文本 */
function fmt(v: any): string {
  if (v == null) return '—'
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(' / ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** 「值 + 单位」：核验/字段节点里值都带单位，分开显示容易看漏 */
function withUnit(value: any, unit: any): string {
  if (value == null || value === '') return '—'
  return unit ? `${value} ${unit}` : String(value)
}

/** Chunk：真实字段是 quote，页码是 page_start / page_end，没有 text / excerpt / page_no */
const chunkQuote = computed(() => {
  const p = payload.value
  return p.quote || p.excerpt || p.text || '（无正文摘要）'
})
const chunkPage = computed(() => {
  const p = payload.value
  const start = p.page_start ?? p.page_end ?? p.page_no
  if (start == null) return ''
  const end = p.page_end ?? start
  return end !== start ? `第 ${start}–${end} 页` : `第 ${start} 页`
})
const chunkSections = computed(() => {
  const p = payload.value.section_path
  return Array.isArray(p) ? p.filter((s: any) => typeof s === 'string' && s.trim()).join(' › ') : ''
})

/** Document：RAG 侧给 origin，工业侧给 source_sha256，两边都要认 */
const docOrigin = computed(() => payload.value.origin ?? '')
const docSha = computed(() => payload.value.source_sha256 ?? payload.value.document_sha256 ?? '')

/** ConflictRecord：双方原值来自 normalized_values；left/right 是 fact 引用（只有 id） */
const conflictValues = computed(() => {
  const nv = payload.value.normalized_values
  if (!nv || typeof nv !== 'object') return []
  return Object.entries(nv)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => ({ key: k, value: fmt(v) }))
})
const conflictRefs = computed(() => {
  const p = payload.value
  const refs = (list: any[]) =>
    Array.isArray(list)
      ? list.map((r) => (r && typeof r === 'object' ? r.fact_id ?? r.measurement_id ?? JSON.stringify(r) : String(r)))
      : []
  return { left: refs(p.left_fact_refs), right: refs(p.right_fact_refs) }
})

/** DocumentFact：左侧证据块（bbox/页码）是「原文可定位」的凭据 */
const factRefs = computed(() => {
  const refs = payload.value.evidence_refs
  return Array.isArray(refs)
    ? refs.map((r: any) => ({
        role: r.role ?? '',
        field: r.field ?? '',
        text: r.text ?? r.field_value ?? '',
        page: r.page_no ?? null,
      }))
    : []
})

/** 核验行的问题：left/right 双方都列出来 */
const checkIssues = computed(() => {
  const list = payload.value.issues
  return Array.isArray(list) ? list : []
})
</script>

<template>
  <div v-if="node" class="node-detail">
    <div class="head">
      <span class="type">{{ label }}</span>
      <span class="ref">{{ node.ref_key }}</span>
    </div>

    <!-- 答案主张 -->
    <template v-if="node.node_type === 'Claim'">
      <p class="text">{{ payload.text }}</p>
    </template>

    <!-- SQL -->
    <template v-else-if="node.node_type === 'SQLQuery'">
      <pre class="code">{{ payload.sql }}</pre>
      <dl class="kv">
        <template v-if="payload.source_snapshot_id"><dt>快照</dt><dd class="mono">{{ payload.source_snapshot_id }}</dd></template>
        <template v-if="payload.sql_hash"><dt>SQL 哈希</dt><dd class="mono">{{ payload.sql_hash }}</dd></template>
        <template v-if="payload.bound_params"><dt>绑定参数</dt><dd class="mono">{{ fmt(payload.bound_params) }}</dd></template>
      </dl>
    </template>

    <!-- 结果行：payload 就是一行结果（扁平字段），没有 columns / rows -->
    <template v-else-if="node.node_type === 'SQLRow'">
      <dl class="kv">
        <template v-for="k in sqlRowFields" :key="k">
          <dt>{{ fieldLabel(k) }}</dt>
          <dd :class="{ mono: k.endsWith('_id') || k.endsWith('_at') }">{{ fmt(payload[k]) }}</dd>
        </template>
      </dl>
    </template>

    <!-- 文档 -->
    <template v-else-if="node.node_type === 'Document'">
      <p class="text">{{ payload.title }}</p>
      <dl class="kv">
        <template v-if="payload.document_id"><dt>文档 ID</dt><dd class="mono">{{ payload.document_id }}</dd></template>
        <template v-if="payload.document_version"><dt>版本</dt><dd>{{ payload.document_version }}</dd></template>
        <template v-if="docOrigin"><dt>来源类型</dt><dd>{{ docOrigin }}</dd></template>
        <template v-if="docSha"><dt>内容哈希</dt><dd class="mono">{{ docSha }}</dd></template>
      </dl>
      <a v-if="payload.source_url" :href="payload.source_url" target="_blank" rel="noopener" class="link">打开原文件 ↗</a>
    </template>

    <!-- 检索片段：正文在 quote，页码在 page_start / page_end -->
    <template v-else-if="node.node_type === 'Chunk'">
      <p class="text">{{ chunkQuote }}</p>
      <dl class="kv">
        <template v-if="payload.title"><dt>所属文档</dt><dd>{{ payload.title }}</dd></template>
        <template v-if="chunkPage"><dt>页码</dt><dd>{{ chunkPage }}</dd></template>
        <template v-if="chunkSections"><dt>章节</dt><dd>{{ chunkSections }}</dd></template>
        <template v-if="payload.retrieval_score != null"><dt>检索分</dt><dd>{{ payload.retrieval_score }}</dd></template>
        <template v-if="payload.used_in_answer != null"><dt>是否被引用</dt><dd>{{ payload.used_in_answer ? '是' : '否' }}</dd></template>
      </dl>
      <div class="links">
        <a v-if="payload.page_url" :href="payload.page_url" target="_blank" rel="noopener" class="link">查看原页 ↗</a>
        <a v-if="payload.source_url" :href="payload.source_url" target="_blank" rel="noopener" class="link">原文件 ↗</a>
      </div>
    </template>

    <!-- 字段：原文值在 raw_value / raw_unit，实体名在 entity_name，表头在 raw_header -->
    <template v-else-if="node.node_type === 'DocumentFact'">
      <dl class="kv">
        <template v-if="payload.fact_kind"><dt>字段类型</dt><dd>{{ payload.fact_kind === 'requirement' ? '工艺要求' : payload.fact_kind === 'measurement' ? '质检测量' : payload.fact_kind }}</dd></template>
        <template v-if="payload.entity_name"><dt>实体</dt><dd>{{ payload.entity_name }}<span v-if="payload.entity_type" class="sub">（{{ payload.entity_type }}）</span></dd></template>
        <template v-if="payload.operation_no"><dt>工序</dt><dd>{{ payload.operation_no }}</dd></template>
        <template v-if="payload.batch_id"><dt>批次</dt><dd>{{ payload.batch_id }}</dd></template>
        <template v-if="payload.raw_value"><dt>原文值</dt><dd><b>{{ withUnit(payload.raw_value, payload.raw_unit) }}</b></dd></template>
        <template v-if="payload.normalized_value != null"><dt>归一值</dt><dd>{{ withUnit(payload.normalized_value, payload.normalized_unit) }}</dd></template>
        <template v-if="payload.metric"><dt>指标</dt><dd>{{ payload.metric }}<span v-if="payload.raw_header && payload.raw_header !== payload.metric" class="sub">（表头 {{ payload.raw_header }}）</span></dd></template>
        <template v-if="payload.method"><dt>方法</dt><dd>{{ payload.method }}</dd></template>
        <template v-if="payload.measured_at"><dt>测量时间</dt><dd class="mono">{{ payload.measured_at }}</dd></template>
        <template v-if="payload.report_id"><dt>报告 ID</dt><dd class="mono">{{ payload.report_id }}</dd></template>
        <template v-if="payload.valid_from || payload.valid_to"><dt>有效期</dt><dd class="mono">{{ payload.valid_from ?? '—' }} ~ {{ payload.valid_to ?? '—' }}</dd></template>
        <template v-if="payload.approval_status"><dt>审批状态</dt><dd>{{ payload.approval_status }}</dd></template>
        <template v-if="payload.provenance"><dt>数据来源</dt><dd>{{ payload.provenance }}</dd></template>
        <template v-if="payload.source_url"><dt>出处</dt><dd><a :href="payload.source_url" target="_blank" rel="noopener" class="link">原文件 ↗</a></dd></template>
      </dl>
      <div v-if="factRefs.length" class="refs">
        <div class="refs-title">原文定位 · {{ factRefs.length }} 块</div>
        <div v-for="(rf, i) in factRefs" :key="i" class="ref">
          <span class="ref-role">{{ rf.role || rf.field }}</span>
          <span class="ref-text">{{ rf.text }}</span>
          <span v-if="rf.page != null" class="ref-page">P{{ rf.page }}</span>
        </div>
      </div>
    </template>

    <!-- 核验：整行 row，结论 + 双方原值 + 问题 + 规则 -->
    <template v-else-if="node.node_type === 'VerificationCheck'">
      <dl class="kv">
        <template v-if="payload.compliance"><dt>合规结论</dt><dd><span class="pill" :class="payload.compliance === 'out_of_range' ? 'bad' : payload.compliance === 'within_range' ? 'ok' : 'warn'">{{ payload.compliance }}</span></dd></template>
        <template v-if="payload.agreement"><dt>来源一致性</dt><dd><span class="pill" :class="payload.agreement === 'unresolved' ? 'bad' : payload.agreement === 'consistent' || payload.agreement === 'resolved_equivalent' ? 'ok' : 'warn'">{{ payload.agreement }}</span></dd></template>
        <template v-if="payload.batch_id"><dt>批次</dt><dd>{{ payload.batch_id }}</dd></template>
        <template v-if="payload.value != null"><dt>实测值</dt><dd><b>{{ withUnit(payload.value, payload.unit) }}</b></dd></template>
        <template v-if="payload.normalized_value != null"><dt>归一值</dt><dd>{{ withUnit(payload.normalized_value, payload.normalized_unit) }}</dd></template>
        <template v-if="payload.report_value != null"><dt>报告值</dt><dd>{{ withUnit(payload.report_value, payload.report_unit) }}</dd></template>
        <template v-if="payload.recorded_revision || payload.applicable_revision"><dt>修订版</dt><dd>记录 {{ payload.recorded_revision ?? '—' }} / 适用 {{ payload.applicable_revision ?? '—' }}</dd></template>
        <template v-if="payload.measured_at"><dt>测量时间</dt><dd class="mono">{{ payload.measured_at }}</dd></template>
        <template v-if="payload.method"><dt>方法</dt><dd>{{ payload.method }}</dd></template>
        <template v-if="payload.report_id"><dt>报告 ID</dt><dd class="mono">{{ payload.report_id }}</dd></template>
      </dl>
      <div v-if="checkIssues.length" class="issues">
        <div class="refs-title">问题 · {{ checkIssues.length }}</div>
        <div v-for="(is, i) in checkIssues" :key="i" class="ref">
          <span class="ref-role">{{ is.kind }}</span>
          <span class="ref-text">{{ is.reason || is.status }}</span>
          <span v-if="is.left_value != null || is.right_value != null" class="ref-pair">左 {{ is.left_value ?? '—' }} ↔ 右 {{ is.right_value ?? '—' }}</span>
        </div>
      </div>
      <div v-if="Array.isArray(payload.rules) && payload.rules.length" class="refs">
        <div class="refs-title">套用规则 · {{ payload.rules.length }}</div>
        <div v-for="(rl, i) in payload.rules" :key="i" class="ref">
          <span class="ref-role">{{ rl.rule_id }}</span>
          <span class="ref-text">{{ rl.expression || rl.conversion || rl.rule_version }}</span>
        </div>
      </div>
    </template>

    <!-- 冲突 -->
    <template v-else-if="node.node_type === 'ConflictRecord'">
      <dl class="kv">
        <template v-if="payload.conflict_type"><dt>冲突类型</dt><dd>{{ payload.conflict_type }}</dd></template>
        <template v-if="payload.metric"><dt>指标</dt><dd>{{ payload.metric }}</dd></template>
        <template v-if="payload.resolution_status"><dt>状态</dt><dd><span class="pill" :class="payload.resolution_status === 'resolved_by_authority' ? 'ok' : payload.resolution_status === 'needs_review' ? 'warn' : 'bad'">{{ payload.resolution_status }}</span></dd></template>
        <template v-if="payload.rule_id"><dt>规则</dt><dd class="mono">{{ payload.rule_id }}</dd></template>
        <template v-if="payload.rule_version"><dt>规则版本</dt><dd>{{ payload.rule_version }}</dd></template>
        <template v-if="payload.entity_key"><dt>实体</dt><dd class="mono">{{ fmt(payload.entity_key) }}</dd></template>
        <template v-if="payload.comparison_context"><dt>比对上下文</dt><dd class="mono">{{ fmt(payload.comparison_context) }}</dd></template>
      </dl>
      <div v-if="conflictValues.length" class="refs">
        <div class="refs-title">双方原值（都保留）</div>
        <div v-for="v in conflictValues" :key="v.key" class="ref">
          <span class="ref-role">{{ v.key }}</span>
          <span class="ref-text">{{ v.value }}</span>
        </div>
      </div>
      <dl v-if="conflictRefs.left.length || conflictRefs.right.length" class="kv">
        <template v-if="conflictRefs.left.length"><dt>左引用</dt><dd class="mono">{{ conflictRefs.left.join(' / ') }}</dd></template>
        <template v-if="conflictRefs.right.length"><dt>右引用</dt><dd class="mono">{{ conflictRefs.right.join(' / ') }}</dd></template>
        <template v-if="payload.selected_fact_ref"><dt>采信</dt><dd class="mono">{{ fmt(payload.selected_fact_ref) }}</dd></template>
      </dl>
      <p v-if="payload.reason" class="text">{{ payload.reason }}</p>
    </template>

    <!-- 权威规则 -->
    <template v-else-if="node.node_type === 'AuthorityRule'">
      <dl class="kv">
        <template v-if="payload.rule_id"><dt>规则</dt><dd class="mono">{{ payload.rule_id }}</dd></template>
        <template v-if="payload.rule_version"><dt>版本</dt><dd>{{ payload.rule_version }}</dd></template>
        <template v-if="payload.registration?.selected_source"><dt>采信来源</dt><dd>{{ payload.registration.selected_source === 'production' ? '生产记录' : '质检报告' }}</dd></template>
        <template v-if="payload.registration?.valid_from || payload.registration?.valid_to"><dt>有效期</dt><dd class="mono">{{ payload.registration?.valid_from ?? '—' }} ~ {{ payload.registration?.valid_to ?? '—' }}</dd></template>
        <template v-if="payload.creator_label"><dt>登记人</dt><dd>{{ payload.creator_label }}</dd></template>
        <template v-if="payload.approver_label"><dt>批准人</dt><dd>{{ payload.approver_label }}</dd></template>
        <template v-if="payload.approval_reason"><dt>批准理由</dt><dd>{{ payload.approval_reason }}</dd></template>
        <template v-if="payload.event_id"><dt>事件 ID</dt><dd class="mono">{{ payload.event_id }}</dd></template>
      </dl>
      <p v-if="payload.registration?.rationale" class="text">{{ payload.registration.rationale }}</p>
    </template>

    <!-- 兜底：原始 JSON -->
    <template v-else>
      <pre class="code">{{ JSON.stringify(payload, null, 2) }}</pre>
    </template>
  </div>

  <div v-else class="empty">
    <div class="dot breathe">✦</div>
    <!-- 措辞不写方位：宽屏时星链在左边，窄屏抽屉里它在上面，两者共用这一份文案 -->
    <p>点击星链图里的星点，查看它的 SQL、检索片段、原始数据或推导关系。</p>
  </div>
</template>

<style scoped>
.node-detail {
  padding: 4px 2px;
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.type {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-accent);
  letter-spacing: 0.04em;
}
.ref {
  font-size: 11px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.text {
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.code {
  background: var(--color-ink);
  border: 1px solid var(--color-line);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--color-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.kv {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 6px 10px;
  margin: 10px 0;
  font-size: 12.5px;
}
.kv dt {
  color: var(--color-muted);
}
.kv dd {
  color: var(--color-text);
  margin: 0;
  word-break: break-word;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
}
.tablewrap {
  overflow-x: auto;
  margin: 8px 0;
  border: 1px solid var(--color-line);
  border-radius: 10px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
th,
td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--color-line);
  white-space: nowrap;
}
th {
  color: var(--color-muted);
  font-weight: 600;
  background: var(--color-ink);
}
td {
  color: var(--color-text);
}
tr:last-child td {
  border-bottom: none;
}
.link {
  color: var(--color-accent);
  font-size: 12.5px;
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
.links {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
}
.sub {
  color: var(--color-muted);
  font-size: 11.5px;
}
.pill {
  display: inline-block;
  font-size: 11px;
  line-height: 1.6;
  border-radius: 4px;
  padding: 0 6px;
}
.pill.ok {
  color: #15803d;
  background: rgba(22, 163, 74, 0.10);
}
.pill.bad {
  color: #be123c;
  background: rgba(225, 29, 72, 0.12);
  font-weight: 600;
}
.pill.warn {
  color: #b45309;
  background: rgba(217, 119, 6, 0.12);
}
.refs,
.issues {
  margin: 10px 0;
}
.refs-title {
  font-size: 11px;
  color: var(--color-muted);
  margin-bottom: 5px;
}
.ref {
  display: flex;
  align-items: baseline;
  gap: 7px;
  padding: 5px 0;
  border-top: 1px solid var(--color-line);
  font-size: 11.5px;
}
.ref-role {
  flex-shrink: 0;
  color: var(--color-body);
  background: var(--color-ink);
  border-radius: 4px;
  padding: 0 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10.5px;
}
.ref-text {
  flex: 1;
  min-width: 0;
  color: var(--color-text);
  word-break: break-word;
}
.ref-pair {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--color-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10.5px;
}
.ref-page {
  flex-shrink: 0;
  color: var(--color-muted);
  font-size: 10.5px;
}
.empty {
  text-align: center;
  padding: 34px 12px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.7;
}
.empty .dot {
  font-size: 20px;
  color: var(--color-accent);
  display: inline-block;
  margin-bottom: 10px;
}
.muted {
  color: var(--color-muted);
  font-size: 12.5px;
}
</style>
