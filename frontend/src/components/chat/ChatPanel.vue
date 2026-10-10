<script setup lang="ts">
import { ref } from 'vue'
import type { ChatMessage, TraceEvent } from '../../api/types'

defineProps<{
  messages: ChatMessage[]
  running: boolean
}>()

const emit = defineEmits<{
  (e: 'send', payload: { question: string; mode: string }): void
}>()

const input = ref('')
const mode = ref('auto')

const MODES = [
  { value: 'auto', label: '自动判断' },
  { value: 'retail', label: '零售问数' },
  { value: 'chinook', label: 'Chinook 音乐库' },
  { value: 'rag', label: '文档问答' },
  { value: 'industrial', label: '工业温度核验' },
  { value: 'industrial_sql', label: '工业问数（模型）' },
]

const CHIPS: { label: string; question: string; mode: string }[] = [
  { label: '销售额前三产品', question: '2025年销售额最高的前三个产品是什么？', mode: 'retail' },
  { label: 'VIP 客户数', question: 'VIP客户有多少人？', mode: 'retail' },
  { label: '各大区销售额', question: '2024年每个大区的销售额分别是多少？', mode: 'retail' },
  { label: '退款条件', question: '七天无理由退款的条件是什么？', mode: 'rag' },
  { label: '耳机续航', question: '无线降噪耳机Pro的续航时间是多少？', mode: 'rag' },
  { label: '工业温度核验', question: '2026年9月A102工序30哪些批次温度超限？生产记录和质检报告是否一致？', mode: 'industrial' },
  { label: '工业批次数', question: '2026年9月A102工序30有多少个已测量批次？', mode: 'industrial_sql' },
]

const EVENT_LABEL: Record<string, string> = {
  'run.started': '开始运行',
  'query.parsed': '理解问题',
  'plan.created': '生成执行计划',
  'tool.started': '调用工具',
  'retrieval.completed': '检索完成',
  'sql.validated': 'SQL 校验通过',
  'sql.executed': '查询执行完成',
  'verification.passed': '验证通过',
  'verification.failed': '验证未通过',
  'evidence.attached': '证据已关联',
  'answer.completed': '生成答案',
  'answer.abstained': '证据不足，拒绝作答',
  'answer.clarification': '需要澄清',
  'run.failed': '运行失败',
}

const TOOL_LABEL: Record<string, string> = {
  nl2sql: 'SQL 生成',
  'knowledge.retrieve': '文档检索',
  'industrial.compare': '工业跨源核验',
  postgresql: '只读数据库',
}

function eventLabel(ev: TraceEvent): string {
  const base = EVENT_LABEL[ev.event_type] ?? ev.event_type
  if (ev.tool_name && TOOL_LABEL[ev.tool_name]) return `${base} · ${TOOL_LABEL[ev.tool_name]}`
  return base
}

function submit() {
  const q = input.value.trim()
  if (!q) return
  emit('send', { question: q, mode: mode.value })
  input.value = ''
}

function statusText(m: ChatMessage): string {
  if (m.status === 'running') return '正在思考…'
  if (m.status === 'error') return '运行失败'
  if (m.runStatus === 'ABSTAINED') return '证据不足'
  if (m.runStatus === 'NEEDS_CLARIFICATION') return '需要澄清'
  return m.confidence ? `置信度 ${m.confidence}` : ''
}
</script>

<template>
  <section class="chat">
    <header class="chat-head">
      <div>
        <div class="eyebrow">DataMind Agent / 问数 · 问答</div>
        <h1>向数据提问</h1>
        <p>自然语言 → SQL / 检索 → 验证 → 带证据的答案</p>
      </div>
      <label class="mode">
        <span>范围</span>
        <select v-model="mode">
          <option v-for="m in MODES" :key="m.value" :value="m.value">{{ m.label }}</option>
        </select>
      </label>
    </header>

    <div class="chips">
      <button
        v-for="c in CHIPS"
        :key="c.label"
        class="chip"
        @click="emit('send', { question: c.question, mode: c.mode })"
      >
        {{ c.label }}
      </button>
    </div>

    <div ref="scroll" class="messages">
      <div v-if="messages.length === 0" class="empty">
        <div class="orb breathe">✦</div>
        <h2>问一句，看证据如何点亮</h2>
        <p>左侧会实时显示推理链路，右侧星链图会把 SQL、检索片段与原始数据串成一张可点击的证据网。</p>
      </div>

      <template v-for="m in messages" :key="m.id">
        <!-- 用户 -->
        <div v-if="m.role === 'user'" class="row user">
          <div class="bubble user-bubble">{{ m.content }}</div>
        </div>

        <!-- 助手 -->
        <div v-else class="row assistant">
          <div class="avatar">DM</div>
          <div class="bubble">
            <!-- 思考过程（可折叠） -->
            <details v-if="m.thinking && m.thinking.length" class="thinking" :open="m.status === 'running'">
              <summary>
                <span>{{ m.status === 'running' ? '正在思考…' : `思考过程 · ${m.thinking.length} 步` }}</span>
              </summary>
              <ol>
                <li v-for="ev in m.thinking" :key="ev.event_id">
                  <span class="mark" :class="ev.status">{{ ev.status === 'failed' ? '✕' : ev.status === 'warning' ? '!' : '✓' }}</span>
                  <span class="label">{{ eventLabel(ev) }}</span>
                  <span class="agent">{{ ev.agent_name }}</span>
                </li>
              </ol>
            </details>

            <div v-if="m.status === 'running' && !(m.thinking && m.thinking.length)" class="thinking-line">
              <span class="pulse-dot"></span> 正在思考…
            </div>

            <!-- 答案正文 -->
            <div v-if="m.content" class="answer">{{ m.content }}</div>

            <!-- 结果表 -->
            <div v-if="m.sqlResult && m.sqlResult.columns" class="result-table">
              <div class="table-caption">查询结果 · {{ m.sqlResult.row_count ?? m.sqlResult.rows?.length ?? 0 }} 行</div>
              <div class="twrap">
                <table>
                  <thead><tr><th v-for="c in m.sqlResult.columns" :key="c">{{ c }}</th></tr></thead>
                  <tbody>
                    <tr v-for="(r, i) in m.sqlResult.rows" :key="i">
                      <td v-for="(c, j) in r" :key="j">{{ c }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- 引用卡片 -->
            <div v-if="m.citations && m.citations.length" class="citations">
              <div class="cite-title">引用来源</div>
              <div v-for="(c, i) in m.citations" :key="i" class="cite">
                <span class="cite-idx">{{ i + 1 }}</span>
                <div class="cite-body">
                  <div class="cite-doc">{{ c.title || c.document_id }}</div>
                  <p class="cite-text">{{ c.text || c.excerpt || '' }}</p>
                  <div v-if="c.page_no != null" class="cite-meta">第 {{ c.page_no }} 页</div>
                </div>
              </div>
            </div>

            <div class="meta">
              <span :class="['st', m.status === 'error' ? 'err' : 'ok']">{{ statusText(m) }}</span>
              <span v-if="m.runId" class="runid">{{ m.runId }}</span>
            </div>
          </div>
        </div>
      </template>
    </div>

    <div class="inputbar">
      <textarea
        v-model="input"
        rows="1"
        placeholder="输入你的问题，例如：2025 年销售额最高的前三个产品是什么？"
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <button class="send" :disabled="running || !input.trim()" @click="submit">
        <span v-if="running">运行中</span>
        <span v-else>发送 ↑</span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.chat {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.chat-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px 12px;
  border-bottom: 1px solid var(--color-line);
}
.eyebrow {
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #64748b;
}
.chat-head h1 {
  margin: 4px 0 2px;
  font-size: 20px;
  font-weight: 600;
  color: #eef4ff;
}
.chat-head p {
  margin: 0;
  font-size: 12.5px;
  color: #64748b;
}
.mode {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: #64748b;
}
.mode select {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--color-line);
  color: #e5e9f0;
  border-radius: 10px;
  padding: 7px 10px;
  font-size: 13px;
}
.mode option {
  background: #0e1422;
  color: #e5e9f0;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 24px;
}
.chip {
  font-size: 12.5px;
  color: #a5b4fc;
  background: rgba(129, 140, 248, 0.08);
  border: 1px solid rgba(129, 140, 248, 0.22);
  border-radius: 999px;
  padding: 6px 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.chip:hover {
  background: rgba(129, 140, 248, 0.16);
  color: #c7d2fe;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.empty {
  margin: auto;
  text-align: center;
  max-width: 380px;
  color: #64748b;
}
.empty .orb {
  font-size: 30px;
  color: #7dd3fc;
  display: inline-block;
  margin-bottom: 12px;
}
.empty h2 {
  font-size: 16px;
  color: #cbd5e1;
  margin: 0 0 8px;
}
.empty p {
  font-size: 13px;
  line-height: 1.7;
  margin: 0;
}
.row {
  display: flex;
  gap: 10px;
}
.row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 100%;
}
.user-bubble {
  background: linear-gradient(135deg, rgba(56, 189, 248, 0.2), rgba(129, 140, 248, 0.18));
  border: 1px solid rgba(56, 189, 248, 0.28);
  color: #e0f2fe;
  padding: 10px 15px;
  border-radius: 16px 16px 4px 16px;
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
}
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  color: #04101d;
  background: linear-gradient(135deg, #7dd3fc, #818cf8);
}
.assistant .bubble {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--color-line);
  border-radius: 4px 16px 16px 16px;
  padding: 12px 16px;
  font-size: 14px;
}
.thinking {
  font-size: 12.5px;
  color: #94a3b8;
}
.thinking summary {
  cursor: pointer;
  list-style: none;
  color: #7dd3fc;
  margin-bottom: 6px;
  user-select: none;
}
.thinking summary::-webkit-details-marker {
  display: none;
}
.thinking ol {
  margin: 4px 0 8px;
  padding-left: 2px;
  list-style: none;
  border-left: 1px solid var(--color-line);
}
.thinking li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0 3px 12px;
}
.mark {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 9px;
  flex-shrink: 0;
}
.mark.succeeded {
  background: rgba(74, 222, 128, 0.15);
  color: #4ade80;
}
.mark.failed {
  background: rgba(251, 113, 133, 0.15);
  color: #fb7185;
}
.mark.warning {
  background: rgba(251, 191, 36, 0.15);
  color: #fbbf24;
}
.label {
  color: #cbd5e1;
}
.agent {
  margin-left: auto;
  font-size: 11px;
  color: #475569;
}
.thinking-line {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #7dd3fc;
  font-size: 13px;
  margin-bottom: 6px;
}
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7dd3fc;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.1); }
}
.answer {
  color: #e5e9f0;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
.result-table {
  margin-top: 10px;
}
.table-caption {
  font-size: 12px;
  color: #94a3b8;
  margin-bottom: 6px;
}
.twrap {
  overflow-x: auto;
  border: 1px solid var(--color-line);
  border-radius: 10px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
th, td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
  white-space: nowrap;
}
th { color: #94a3b8; background: rgba(255, 255, 255, 0.02); }
td { color: #dbe4f0; }
tr:last-child td { border-bottom: none; }
.citations {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cite-title {
  font-size: 12px;
  color: #94a3b8;
}
.cite {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: rgba(2, 6, 16, 0.4);
  border: 1px solid var(--color-line);
  border-radius: 10px;
}
.cite-idx {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  background: rgba(129, 140, 248, 0.2);
  color: #a5b4fc;
  font-size: 11px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.cite-doc {
  font-size: 12.5px;
  color: #cbd5e1;
  font-weight: 600;
}
.cite-text {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.6;
  margin: 3px 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cite-meta {
  font-size: 11px;
  color: #64748b;
}
.meta {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  color: #64748b;
}
.st.ok { color: #4ade80; }
.st.err { color: #fb7185; }
.runid {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #475569;
}
.inputbar {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  padding: 14px 24px 18px;
  border-top: 1px solid var(--color-line);
}
.inputbar textarea {
  flex: 1;
  resize: none;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--color-line);
  border-radius: 14px;
  color: #e5e9f0;
  padding: 12px 14px;
  font-size: 14px;
  line-height: 1.5;
  min-height: 46px;
  max-height: 160px;
  font-family: inherit;
}
.inputbar textarea:focus {
  outline: none;
  border-color: rgba(56, 189, 248, 0.5);
}
.send {
  background: linear-gradient(135deg, #38bdf8, #6366f1);
  color: #04101d;
  border: none;
  border-radius: 12px;
  padding: 12px 18px;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.send:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
