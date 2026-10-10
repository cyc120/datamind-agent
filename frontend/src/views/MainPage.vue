<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import ChatPanel from '../components/chat/ChatPanel.vue'
import ConstellationGraph from '../components/stargraph/ConstellationGraph.vue'
import NodeDetail from '../components/stargraph/NodeDetail.vue'
import { health, streamChat, getEvidence } from '../api/client'
import type { ChatMessage, ChatPayload, EvidenceGraph, EvidenceNode } from '../api/types'

const route = useRoute()
const router = useRouter()

const messages = ref<ChatMessage[]>([])
const running = ref(false)
const graph = reactive<EvidenceGraph>({ nodes: [], edges: [], claims: [] })
const selectedNode = ref<EvidenceNode | null>(null)
const healthState = ref<'checking' | 'ok' | 'down'>('checking')
const modelState = ref<'checking' | 'ok' | 'down'>('checking')
/** 证据面板：默认展开，可收成一条窄轨，把宽度还给对话 */
const collapsed = ref(false)

/**
 * 星链图的内部坐标系直接按面板内容宽度给定（面板 340 - 左右内边距）。
 * 组件的 SVG 是 width:100% + viewBox 缩放，若沿用旧版 660×520 的画布，
 * 在窄面板里会被压到约 0.45 倍，11px 的星点标签实际只剩 5px。
 * 半径系数取 0.46：0.62 会让孤立证据点（外层半径取满）越出 viewBox 被裁掉，
 * 实测 8 个证据点的检索图在 0.62 下有 5 个被裁、0.46 下 0 个。
 */
const GRAPH_W = 300
const GRAPH_H = 272
const GRAPH_SCALE = 0.46

const healthText = computed(() =>
  healthState.value === 'ok' ? '服务在线' : healthState.value === 'down' ? '服务离线' : '检测中',
)

let uidSeq = 0
const uid = () => `m-${Date.now()}-${uidSeq++}`

function buildPayload(question: string, mode: string): ChatPayload {
  switch (mode) {
    case 'retail':
      return { question, mode: 'sql', scope: { database_sources: ['demo_retail'] } }
    case 'chinook':
      return { question, mode: 'sql', scope: { database_sources: ['chinook'] } }
    case 'industrial_sql':
      return { question, mode: 'sql', scope: { database_sources: ['demo_industrial'] } }
    case 'rag':
      return { question, mode: 'rag', scope: { document_collections: ['demo_kb'] } }
    case 'industrial':
      return {
        question,
        mode: 'industrial',
        scope: { database_sources: ['demo_industrial'], document_collections: ['industrial_demo'] },
      }
    case 'auto':
    default:
      return { question, mode: 'auto' }
  }
}

/** 新对话：清空消息与证据图（运行中不清，避免把正在写的回答丢掉） */
function newChat() {
  if (running.value) return
  messages.value = []
  graph.nodes = []
  graph.edges = []
  graph.claims = []
  selectedNode.value = null
}

function selectNode(node: EvidenceNode | null) {
  selectedNode.value = node
}

async function send(payload: { question: string; mode: string }) {
  if (running.value || !payload.question.trim()) return
  const question = payload.question.trim()

  messages.value.push({ id: uid(), role: 'user', content: question })
  const assistant = reactive<ChatMessage>({
    id: uid(),
    role: 'assistant',
    content: '',
    status: 'running',
    thinking: [],
  })
  messages.value.push(assistant)

  graph.nodes = []
  graph.edges = []
  graph.claims = []
  selectedNode.value = null
  running.value = true

  try {
    const final = await streamChat(buildPayload(question, payload.mode), (ev) => {
      assistant.thinking!.push(ev)
    })

    assistant.runId = final.run_id
    assistant.content = final.answer
    assistant.runStatus = final.status
    assistant.confidence = final.confidence_level
    assistant.error = final.error_code ?? undefined
    if (final.sql_result) assistant.sqlResult = final.sql_result
    if (final.rag_result?.citations) assistant.citations = final.rag_result.citations
    assistant.status = final.status === 'FAILED_WITH_EVIDENCE' ? 'error' : 'done'

    // 拉取证据图，点亮右侧星链
    try {
      const ev = await getEvidence(final.run_id)
      graph.nodes = ev.nodes ?? []
      graph.edges = ev.edges ?? []
      graph.claims = ev.claims ?? []
    } catch {
      /* 证据图不可用不阻断对话 */
    }
  } catch (e: any) {
    assistant.status = 'error'
    assistant.content = e?.message || '请求失败，请确认后端服务已启动。'
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  try {
    const h = await health()
    healthState.value = h.status === 'ok' ? 'ok' : 'down'
    // 健康接口带 capabilities；模型状态这里只做粗略判断
    modelState.value = 'ok'
  } catch {
    healthState.value = 'down'
    modelState.value = 'down'
  }

  // 深链提问：#/app?q=…&mode=retail —— 与旧版控制台的 hash 深链同一套用法
  const q = route.query.q
  const m = route.query.mode
  if (typeof q === 'string' && q.trim()) {
    await send({ question: q, mode: typeof m === 'string' ? m : 'auto' })
  }
})
</script>

<template>
  <div class="mainpage">
    <SideNav :health="healthState" :model="modelState" @new-chat="newChat" />

    <div class="center">
      <!-- 顶栏：左侧标题，右侧与 Kimi 的「Log in / Download」同位的一对控件 -->
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">问数与问答</span>
          <span class="tb-note">自然语言 → SQL / 检索 → 验证 → 带证据的答案</span>
        </div>
        <div class="tb-right">
          <span class="pill" :class="healthState">
            <span class="dot"></span>{{ healthText }}
          </span>
          <button class="ghost-btn" type="button" @click="router.push('/')">了解原理</button>
        </div>
      </header>

      <ChatPanel :messages="messages" :running="running" @send="send" />
    </div>

    <aside class="right" :class="{ 'is-collapsed': collapsed }">
      <!-- 收起后只剩一条窄轨 -->
      <button v-if="collapsed" class="rail" type="button" title="展开证据面板" @click="collapsed = false">
        <span class="rail-icon" aria-hidden="true">✦</span>
        <span class="rail-text">证据面板</span>
        <span class="rail-badge">{{ graph.nodes.length }}</span>
      </button>

      <template v-else>
        <div class="right-head">
          <div class="rh-titles">
            <span class="eyebrow">Evidence Graph</span>
            <h2>推理星链</h2>
          </div>
          <span class="count">{{ graph.nodes.length }} 节点</span>
          <button class="collapse" type="button" title="收起证据面板" @click="collapsed = true">›</button>
        </div>

        <div class="graph-wrap">
          <ConstellationGraph
            :nodes="graph.nodes"
            :edges="graph.edges"
            :width="GRAPH_W"
            :height="GRAPH_H"
            :radius-scale="GRAPH_SCALE"
            @select="selectNode"
          />
          <div v-if="running" class="overlay">
            <span class="pulse-dot"></span> 正在生成证据…
          </div>
          <div v-else-if="graph.nodes.length === 0" class="overlay static">
            <p>提问后，这里会点亮一张证据星图：SQL、检索片段、原始数据与答案主张。点任意星点看细节。</p>
          </div>
        </div>

        <div class="detail">
          <NodeDetail :node="selectedNode" />
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.mainpage {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-void);
}

/* ── 中间：顶栏 + 对话 ── */
.center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 28px;
  border-bottom: 1px solid var(--color-line);
}
.tb-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}
.tb-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--color-text);
}
.tb-note {
  font-size: 12.5px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tb-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  color: var(--color-body);
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  border-radius: 999px;
  padding: 6px 12px;
}
.pill .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cbd5e1;
}
.pill.ok .dot {
  background: #16a34a;
}
.pill.down .dot {
  background: #e11d48;
}
.pill.checking .dot {
  background: #d97706;
  animation: pill-breathe 1.6s ease-in-out infinite;
}
@keyframes pill-breathe {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 1; }
}
.ghost-btn {
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-text);
  border-radius: 999px;
  padding: 7px 14px;
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ghost-btn:hover {
  background: var(--color-ink);
  border-color: rgba(15, 23, 42, 0.2);
}

/* ── 右侧证据面板：更窄、可折叠 ── */
.right {
  /* 原来写死 340px，星链图舒展不开（中间对话区反而空得慌）。
     改为按视口比例伸缩，1440px 宽时约 490px。 */
  width: clamp(380px, 34vw, 560px);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-left: 1px solid var(--color-line);
  background: var(--color-panel);
  transition: width 0.18s ease;
}
.right.is-collapsed {
  width: 56px;
}
.rail {
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 16px 0;
  border: none;
  background: var(--color-ink);
  color: var(--color-body);
  font-family: inherit;
  cursor: pointer;
}
.rail:hover {
  background: rgba(15, 23, 42, 0.06);
  color: var(--color-text);
}
.rail-icon {
  color: var(--color-accent);
  font-size: 14px;
}
.rail-text {
  writing-mode: vertical-rl;
  font-size: 12.5px;
  letter-spacing: 0.16em;
}
.rail-badge {
  font-size: 11px;
  color: var(--color-muted);
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 1px 6px;
}
.right-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 12px 10px 14px;
  flex-shrink: 0;
}
.rh-titles {
  flex: 1;
  min-width: 0;
}
.eyebrow {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-muted);
}
.right-head h2 {
  margin: 2px 0 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.count {
  font-size: 11px;
  color: var(--color-muted);
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 3px 9px;
  white-space: nowrap;
}
.collapse {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}
.collapse:hover {
  background: var(--color-ink);
  color: var(--color-text);
}
.graph-wrap {
  position: relative;
  flex-shrink: 0;
  padding: 0 14px;
}
.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-accent);
  font-size: 13px;
  pointer-events: none;
}
.overlay.static p {
  max-width: 230px;
  text-align: center;
  color: var(--color-muted);
  line-height: 1.7;
  margin: 0;
  font-size: 12.5px;
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.1); }
}
.detail {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 14px;
  border-top: 1px solid var(--color-line);
}

@media (max-width: 1100px) {
  .right {
    display: none;
  }
}
</style>
