<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ChatMessage, TraceEvent } from '../../api/types'

const props = defineProps<{
  messages: ChatMessage[]
  running: boolean
}>()

const emit = defineEmits<{
  (e: 'send', payload: { question: string; mode: string }): void
}>()

const input = ref('')
const mode = ref('auto')
const textarea = ref<HTMLTextAreaElement | null>(null)
const scrollBox = ref<HTMLElement | null>(null)

const MODES = [
  { value: 'auto', label: '自动判断' },
  { value: 'retail', label: '零售问数' },
  { value: 'chinook', label: 'Chinook 音乐库' },
  { value: 'rag', label: '文档问答' },
  { value: 'industrial', label: '工业温度核验' },
  { value: 'industrial_sql', label: '工业问数（模型）' },
]

/** 建议问题：沿用原有的示例问题，只补一个前置小图标。 */
const CHIPS: { label: string; icon: string; question: string; mode: string }[] = [
  { label: '销售额前三产品', icon: '▤', question: '2025年销售额最高的前三个产品是什么？', mode: 'retail' },
  { label: 'VIP 客户数', icon: '◉', question: 'VIP客户有多少人？', mode: 'retail' },
  { label: '各大区销售额', icon: '◫', question: '2024年每个大区的销售额分别是多少？', mode: 'retail' },
  { label: '退款条件', icon: '▢', question: '七天无理由退款的条件是什么？', mode: 'rag' },
  { label: '耳机续航', icon: '⌕', question: '无线降噪耳机Pro的续航时间是多少？', mode: 'rag' },
  {
    label: '工业温度核验',
    icon: '⌖',
    question: '2026年9月A102工序30哪些批次温度超限？生产记录和质检报告是否一致？',
    mode: 'industrial',
  },
  { label: '工业批次数', icon: '⚙', question: '2026年9月A102工序30有多少个已测量批次？', mode: 'industrial_sql' },
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

const isEmpty = computed(() => props.messages.length === 0)

function submit() {
  const q = input.value.trim()
  if (!q || props.running) return
  emit('send', { question: q, mode: mode.value })
  input.value = ''
  nextTick(() => {
    if (textarea.value) textarea.value.style.height = 'auto'
  })
}

function askChip(c: { question: string; mode: string }) {
  if (props.running) return
  mode.value = c.mode
  emit('send', { question: c.question, mode: c.mode })
}

/** 输入框随内容长高，最多 180px，超过后内部滚动 */
function autoGrow() {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`
}

/** 新消息 / 流式增量都跟着滚到底，否则答案会停在看不见的地方 */
function scrollToEnd() {
  const el = scrollBox.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => [props.messages.length, props.messages[props.messages.length - 1]?.content?.length ?? 0, props.running],
  () => nextTick(scrollToEnd),
)

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
    <div class="stage" :class="{ 'is-empty': isEmpty }">
      <!-- 空态：巨大的品牌字 -->
      <div v-if="isEmpty" class="hero">
        <h1 class="brand-word">Data<span class="accent">Mind</span></h1>
        <p class="brand-sub">可解释 · 可溯源的精准问数智能体</p>
      </div>

      <!-- 消息列表 -->
      <div v-if="!isEmpty" ref="scrollBox" class="messages">
        <div class="messages-inner">
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
      </div>

      <!-- 输入区：大圆角输入框 + 建议胶囊 -->
      <div class="composer-wrap">
        <div class="composer">
          <div class="composer-row">
            <a
              class="round plus"
              href="/#files"
              title="上传并入库：在「我的文件」里上传 DXF / PDF / Office 文件"
              aria-label="上传并入库"
            >＋</a>

            <textarea
              ref="textarea"
              v-model="input"
              rows="1"
              placeholder="输入你的问题，例如：2025 年销售额最高的前三个产品是什么？"
              @input="autoGrow"
              @keydown.enter.exact.prevent="submit"
            ></textarea>

            <label class="scope" title="问数范围：决定查库、查文档还是两者联合">
              <select v-model="mode" aria-label="问数范围">
                <option v-for="m in MODES" :key="m.value" :value="m.value">{{ m.label }}</option>
              </select>
            </label>

            <button
              class="round send"
              type="button"
              :disabled="running || !input.trim()"
              :title="running ? '正在运行…' : '发送（Enter）'"
              aria-label="发送"
              @click="submit"
            >
              <span v-if="running" class="pulse-dot"></span>
              <span v-else>↑</span>
            </button>
          </div>
        </div>

        <div v-if="isEmpty" class="chips">
          <button
            v-for="c in CHIPS"
            :key="c.label"
            class="chip"
            type="button"
            :disabled="running"
            @click="askChip(c)"
          >
            <span class="chip-ico" aria-hidden="true">{{ c.icon }}</span>{{ c.label }}
          </button>
        </div>

        <p v-if="isEmpty" class="tip">Enter 发送 · Ctrl K 新对话 · 答案右侧会点亮可点击的证据星链</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--color-void);
}
.stage {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 28px;
}
/* 空态：品牌字 + 输入框 + 建议胶囊整体居中 */
.stage.is-empty {
  justify-content: center;
  padding-bottom: 6vh;
}

/* ── 空态品牌字 ── */
.hero {
  text-align: center;
  margin-bottom: 26px;
}
.brand-word {
  margin: 0;
  font-size: clamp(3rem, 7.2vw, 5.6rem);
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.045em;
  color: var(--color-text);
}
.brand-word .accent {
  color: var(--color-accent);
}
.brand-sub {
  margin: 16px 0 0;
  font-size: 14.5px;
  color: var(--color-muted);
}

/* ── 输入框 ── */
.composer-wrap {
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
  flex-shrink: 0;
  padding-bottom: 18px;
}
.composer {
  border: 1px solid var(--color-line);
  border-radius: 24px;
  background: var(--color-panel);
  padding: 10px 12px 10px 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 10px 28px rgba(15, 23, 42, 0.05);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.composer:focus-within {
  border-color: rgba(37, 99, 235, 0.45);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 10px 28px rgba(37, 99, 235, 0.10);
}
.composer-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
.composer textarea {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  resize: none;
  color: var(--color-text);
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  min-height: 34px;
  max-height: 180px;
  padding: 7px 2px;
}
.composer textarea::placeholder {
  color: #9aa3af;
}
.round {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.plus {
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  line-height: 1;
}
.plus:hover {
  background: var(--color-ink);
  color: var(--color-text);
}
.scope {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}
.scope select {
  appearance: none;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  background: var(--color-ink);
  color: var(--color-body);
  font-family: inherit;
  font-size: 12.5px;
  padding: 8px 12px;
  max-width: 150px;
  cursor: pointer;
}
.scope select:hover {
  color: var(--color-text);
  border-color: rgba(15, 23, 42, 0.2);
}
.scope select:focus {
  outline: none;
  border-color: rgba(37, 99, 235, 0.45);
}
.send {
  border: none;
  background: var(--color-accent);
  color: #ffffff;
  font-weight: 600;
}
.send:hover:not(:disabled) {
  background: #1d4ed8;
}
.send:disabled {
  background: rgba(15, 23, 42, 0.12);
  color: #ffffff;
  cursor: default;
}

/* ── 建议胶囊 ── */
.chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 16px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  color: var(--color-body);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.chip:hover:not(:disabled) {
  background: var(--color-panel);
  border-color: rgba(15, 23, 42, 0.2);
  color: var(--color-text);
}
.chip:disabled {
  opacity: 0.5;
  cursor: default;
}
.chip-ico {
  color: var(--color-accent);
  font-size: 12px;
}
.tip {
  margin: 14px 0 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--color-muted);
}

/* ── 消息列表 ── */
.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 0 10px;
}
.messages-inner {
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 22px;
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
  min-width: 0;
}
.user-bubble {
  background: var(--color-ink);
  border: 1px solid var(--color-line);
  color: var(--color-text);
  padding: 10px 15px;
  border-radius: 16px 16px 4px 16px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  color: #ffffff;
  background: linear-gradient(135deg, #2563eb, #6366f1);
}
.assistant .bubble {
  flex: 1;
  padding-top: 2px;
  font-size: 14px;
}
.thinking {
  font-size: 12.5px;
  color: var(--color-body);
  background: var(--color-ink);
  border: 1px solid var(--color-line);
  border-radius: 12px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.thinking summary {
  cursor: pointer;
  list-style: none;
  color: var(--color-accent);
  user-select: none;
}
.thinking summary::-webkit-details-marker {
  display: none;
}
.thinking ol {
  margin: 8px 0 2px;
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
  background: rgba(22, 163, 74, 0.12);
  color: #16a34a;
}
.mark.failed {
  background: rgba(225, 29, 72, 0.12);
  color: #e11d48;
}
.mark.warning {
  background: rgba(217, 119, 6, 0.12);
  color: #d97706;
}
.label {
  color: var(--color-text);
}
.agent {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-muted);
}
.thinking-line {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-accent);
  font-size: 13px;
  margin-bottom: 8px;
}
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.1); }
}
.answer {
  color: var(--color-text);
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}
.result-table {
  margin-top: 10px;
}
.table-caption {
  font-size: 12px;
  color: var(--color-muted);
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
.citations {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cite-title {
  font-size: 12px;
  color: var(--color-muted);
}
.cite {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--color-ink);
  border: 1px solid var(--color-line);
  border-radius: 10px;
}
.cite-idx {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  background: rgba(37, 99, 235, 0.1);
  color: var(--color-accent);
  font-size: 11px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.cite-body {
  min-width: 0;
}
.cite-doc {
  font-size: 12.5px;
  color: var(--color-text);
  font-weight: 600;
}
.cite-text {
  font-size: 12px;
  color: var(--color-body);
  line-height: 1.6;
  margin: 3px 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cite-meta {
  font-size: 11px;
  color: var(--color-muted);
}
.meta {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  color: var(--color-muted);
}
.st.ok {
  color: #16a34a;
}
.st.err {
  color: #e11d48;
}
.runid {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--color-muted);
}

@media (max-width: 900px) {
  .stage {
    padding: 0 16px;
  }
  .brand-word {
    font-size: clamp(2.4rem, 12vw, 3.4rem);
  }
  .scope select {
    max-width: 104px;
  }
}
</style>
