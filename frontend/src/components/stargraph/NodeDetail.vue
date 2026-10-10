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

const payload = computed(() => props.node?.payload ?? {})
const label = computed(() => (props.node ? TYPE_LABEL[props.node.node_type] ?? props.node.node_type : ''))

function rows() {
  const p = payload.value
  const cols: string[] = p.columns ?? []
  const data: any[][] = p.rows ?? []
  return { cols, data }
}
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
        <template v-if="payload.source_id"><dt>数据源</dt><dd>{{ payload.source_id }}</dd></template>
        <template v-if="payload.prompt_version"><dt>提示词</dt><dd>{{ payload.prompt_version }}</dd></template>
        <template v-if="payload.sql_hash"><dt>SQL 哈希</dt><dd class="mono">{{ payload.sql_hash }}</dd></template>
      </dl>
    </template>

    <!-- 结果行 -->
    <template v-else-if="node.node_type === 'SQLRow'">
      <div class="tablewrap">
        <table v-if="rows().cols.length">
          <thead>
            <tr><th v-for="c in rows().cols" :key="c">{{ c }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in rows().data" :key="i">
              <td v-for="(c, j) in r" :key="j">{{ c }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">无列信息</p>
      </div>
      <dl class="kv">
        <template v-if="payload.row_count != null"><dt>行数</dt><dd>{{ payload.row_count }}</dd></template>
      </dl>
    </template>

    <!-- 文档 -->
    <template v-else-if="node.node_type === 'Document'">
      <p class="text">{{ payload.title }}</p>
      <dl class="kv">
        <template v-if="payload.document_id"><dt>文档 ID</dt><dd class="mono">{{ payload.document_id }}</dd></template>
        <template v-if="payload.document_version"><dt>版本</dt><dd>{{ payload.document_version }}</dd></template>
        <template v-if="payload.origin"><dt>来源</dt><dd>{{ payload.origin }}</dd></template>
      </dl>
      <a v-if="payload.source_url" :href="payload.source_url" target="_blank" rel="noopener" class="link">打开原文件 ↗</a>
    </template>

    <!-- 检索片段 -->
    <template v-else-if="node.node_type === 'Chunk'">
      <p class="text">{{ payload.text || payload.excerpt || '（无正文摘要）' }}</p>
      <dl class="kv">
        <template v-if="payload.title"><dt>所属文档</dt><dd>{{ payload.title }}</dd></template>
        <template v-if="payload.page_no != null"><dt>页码</dt><dd>{{ payload.page_no }}</dd></template>
        <template v-if="payload.used_in_answer != null"><dt>是否被引用</dt><dd>{{ payload.used_in_answer ? '是' : '否' }}</dd></template>
      </dl>
    </template>

    <!-- 字段 -->
    <template v-else-if="node.node_type === 'DocumentFact'">
      <dl class="kv">
        <template v-for="(v, k) in ['value', 'unit', 'entity', 'header', 'metric', 'measured_at', 'method']" :key="k">
          <template v-if="payload[k] != null"><dt>{{ k }}</dt><dd>{{ payload[k] }}</dd></template>
        </template>
      </dl>
    </template>

    <!-- 核验 -->
    <template v-else-if="node.node_type === 'VerificationCheck'">
      <dl class="kv">
        <template v-for="(v, k) in ['measurement_id', 'part_id', 'batch_id', 'operation_no', 'metric', 'value', 'unit', 'method', 'measured_at', 'compliance', 'agreement']" :key="k">
          <template v-if="payload[k] != null"><dt>{{ k }}</dt><dd>{{ payload[k] }}</dd></template>
        </template>
      </dl>
    </template>

    <!-- 冲突 -->
    <template v-else-if="node.node_type === 'ConflictRecord'">
      <dl class="kv">
        <template v-if="payload.conflict_type"><dt>冲突类型</dt><dd>{{ payload.conflict_type }}</dd></template>
        <template v-if="payload.metric"><dt>指标</dt><dd>{{ payload.metric }}</dd></template>
        <template v-if="payload.resolution_status"><dt>状态</dt><dd>{{ payload.resolution_status }}</dd></template>
      </dl>
      <pre v-if="payload.normalized_values" class="code">{{ JSON.stringify(payload.normalized_values, null, 2) }}</pre>
      <p v-if="payload.reason" class="text">{{ payload.reason }}</p>
    </template>

    <!-- 权威规则 -->
    <template v-else-if="node.node_type === 'AuthorityRule'">
      <dl class="kv">
        <template v-if="payload.rule_id"><dt>规则</dt><dd class="mono">{{ payload.rule_id }}</dd></template>
        <template v-if="payload.rule_version"><dt>版本</dt><dd>{{ payload.rule_version }}</dd></template>
      </dl>
    </template>

    <!-- 兜底：原始 JSON -->
    <template v-else>
      <pre class="code">{{ JSON.stringify(payload, null, 2) }}</pre>
    </template>
  </div>

  <div v-else class="empty">
    <div class="dot breathe">✦</div>
    <p>点击左侧星链图里的星点，查看它的 SQL、检索片段、原始数据或推导关系。</p>
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
  color: #7dd3fc;
  letter-spacing: 0.04em;
}
.ref {
  font-size: 11px;
  color: #64748b;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.text {
  font-size: 13.5px;
  line-height: 1.7;
  color: #dbe4f0;
  white-space: pre-wrap;
  word-break: break-word;
}
.code {
  background: rgba(2, 6, 16, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: #a5f3fc;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.kv {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 6px 12px;
  margin: 10px 0;
  font-size: 12.5px;
}
.kv dt {
  color: #64748b;
}
.kv dd {
  color: #dbe4f0;
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
  border: 1px solid rgba(148, 163, 184, 0.16);
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
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
  white-space: nowrap;
}
th {
  color: #94a3b8;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.02);
}
td {
  color: #dbe4f0;
}
tr:last-child td {
  border-bottom: none;
}
.link {
  color: #7dd3fc;
  font-size: 12.5px;
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
.empty {
  text-align: center;
  padding: 40px 16px;
  color: #64748b;
  font-size: 13px;
  line-height: 1.7;
}
.empty .dot {
  font-size: 22px;
  color: #7dd3fc;
  display: inline-block;
  margin-bottom: 10px;
}
.muted {
  color: #64748b;
  font-size: 12.5px;
}
</style>
