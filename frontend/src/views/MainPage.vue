<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import ChatPanel from '../components/chat/ChatPanel.vue'
import ConstellationGraph from '../components/stargraph/ConstellationGraph.vue'
import NodeDetail from '../components/stargraph/NodeDetail.vue'
import { streamChat, getEvidence } from '../api/client'
import { useModelAccess } from '../composables/useModelAccess'
import type { ChatMessage, ChatPayload, EvidenceGraph, EvidenceNode } from '../api/types'

const route = useRoute()
const router = useRouter()

const messages = ref<ChatMessage[]>([])
const running = ref(false)
const graph = reactive<EvidenceGraph>({ nodes: [], edges: [], claims: [] })
const selectedNode = ref<EvidenceNode | null>(null)
/** 服务健康来自 /health，与「模型可用」是两件事（见 useModelAccess） */
const { serviceState: healthState, bootstrap: bootstrapModelAccess } = useModelAccess()

/**
 * 主区两个视图：对话 / 证据轨迹。
 *
 * 布局是「侧边栏 + 主区」两栏，主区顶部切换，选中者占满整个主区宽度
 * （1440 视口下约 1200px，1600 下约 1360px），不再是原来那个 490px 的常驻右栏。
 *
 * 两个视图都用 v-show 而不是 v-if：ConstellationGraph / NodeDetail / ChatPanel
 * 都只有一份实例，DOM 与组件状态都不重建。切走再切回来，正在跑的运行、
 * 已产出的证据图、选中的节点、滚动位置全都还在（v-if 会卸载聊天面板，
 * 正在流式写入的消息会跟着一起没）。
 */
type MainView = 'chat' | 'graph'
const activeView = ref<MainView>('chat')
/** 对话侧有新证据产出时的提示点：只提示，不强制把用户从对话里拽走 */
const evidenceHint = ref(false)

/** 从对话切到证据轨迹时清掉提示点 */
watch(activeView, (v) => {
  if (v === 'graph') evidenceHint.value = false
})

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
  evidenceHint.value = false
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
  evidenceHint.value = false

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
    // 工业核验：摘要里的「详见核验表」指的就是这个结果，必须落到消息上才会渲染
    if (final.industrial_result) assistant.industrialResult = final.industrial_result
    assistant.status = final.status === 'FAILED_WITH_EVIDENCE' ? 'error' : 'done'

    // 拉取证据图，点亮证据轨迹
    try {
      const ev = await getEvidence(final.run_id)
      graph.nodes = ev.nodes ?? []
      graph.edges = ev.edges ?? []
      graph.claims = ev.claims ?? []
      // 证据刚到位时如果用户还在对话侧，给提示点（不自动切换，免得打断阅读）
      if (graph.nodes.length) evidenceHint.value = true
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

/**
 * 画布尺寸现在是兜底值：ConstellationGraph 以容器实测宽度为准重算画布，
 * 这里只给出首帧容器宽度未知时的期望尺寸。旧版为 490px 侧栏调过的小画布
 * （300×272 / radiusScale 0.46）连同那个 prop 一起不再需要。
 */
const GRAPH_W = 900
const GRAPH_H = 600

const healthText = computed(() =>
  healthState.value === 'ok' ? '服务在线' : healthState.value === 'down' ? '服务离线' : '检测中',
)

let uidSeq = 0
const uid = () => `m-${Date.now()}-${uidSeq++}`

onMounted(async () => {
  // 服务健康 + 模型配置 + 读回上次连通性检测（三件事各自独立）
  void bootstrapModelAccess()

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
    <SideNav @new-chat="newChat" />

    <div class="center">
      <!-- 顶栏：左侧标题，中间「对话 / 证据轨迹」切换，右侧服务状态与入口 -->
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">问数与问答</span>
          <span class="tb-note">自然语言 → SQL / 检索 → 验证 → 带证据的答案</span>
        </div>

        <div class="viewtabs" role="tablist" aria-label="主视图切换">
          <button
            class="viewtab"
            type="button"
            role="tab"
            data-testid="tab-chat"
            :aria-selected="activeView === 'chat'"
            :class="{ 'is-active': activeView === 'chat' }"
            @click="activeView = 'chat'"
          >
            对话
          </button>
          <button
            class="viewtab"
            type="button"
            role="tab"
            data-testid="tab-graph"
            :aria-selected="activeView === 'graph'"
            :class="{ 'is-active': activeView === 'graph' }"
            @click="activeView = 'graph'"
          >
            证据轨迹
            <span v-if="graph.nodes.length" class="tab-count">{{ graph.nodes.length }}</span>
            <span v-if="evidenceHint && activeView !== 'graph'" class="tab-dot" aria-label="有新证据"></span>
          </button>
        </div>

        <div class="tb-right">
          <span class="pill" :class="healthState">
            <span class="dot"></span>{{ healthText }}
          </span>
          <button class="ghost-btn" type="button" @click="router.push('/')">了解原理</button>
        </div>
      </header>

      <!-- 对话视图：不用 v-if 卸载（卸载会丢正在流式写入的消息），只切显示。
           v-show 不够用 —— .view 上的 display:flex 会盖掉 v-show 的 inline display:none，
           两个视图会同时留在布局里（实测截图里对话被挤到证据图下面），所以显式加 .is-hidden。 -->
      <section
        class="view view-chat"
        :class="{ 'is-hidden': activeView !== 'chat' }"
        data-testid="view-chat"
      >
        <ChatPanel :messages="messages" :running="running" @send="send" />
      </section>

      <!-- 证据轨迹视图 -->
      <section
        class="view view-graph"
        :class="{ 'is-hidden': activeView !== 'graph' }"
        data-testid="view-graph"
      >
        <div class="graph-head">
          <div class="gh-titles">
            <span class="eyebrow">Evidence Graph</span>
            <h2>推理星链</h2>
          </div>
          <span class="count">{{ graph.nodes.length }} 节点</span>
          <span class="gh-note">点任意卡片看它的 SQL、检索片段、原始数据或推导关系</span>
        </div>

        <div class="graph-body" :class="{ 'has-detail': !!selectedNode }">
          <div class="graph-wrap">
            <ConstellationGraph
              :nodes="graph.nodes"
              :edges="graph.edges"
              :width="GRAPH_W"
              :height="GRAPH_H"
              @select="selectNode"
            />
            <div v-if="running" class="overlay">
              <span class="pulse-dot"></span> 正在生成证据…
            </div>
            <div v-else-if="graph.nodes.length === 0" class="overlay static">
              <p>提问后，这里会点亮一张证据星图：SQL、检索片段、原始数据与答案主张。点任意卡片看细节。</p>
            </div>
          </div>

          <aside class="detail-col">
            <div class="detail-head">
              <span class="dh-title">节点详情</span>
              <button v-if="selectedNode" class="dh-clear" type="button" @click="selectNode(null)">收起 ✕</button>
            </div>
            <div class="detail">
              <NodeDetail :node="selectedNode" />
            </div>
          </aside>
        </div>
      </section>
    </div>
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

/* ── 主区：顶栏 + 视图（对话 / 证据轨迹 二选一，占满整宽） ── */
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
  gap: 16px;
  padding: 14px 28px;
  border-bottom: 1px solid var(--color-line);
}
.tb-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
  flex: 1;
}
.tb-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
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
  white-space: nowrap;
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
  white-space: nowrap;
  transition: background 0.15s, border-color 0.15s;
}
.ghost-btn:hover {
  background: var(--color-ink);
  border-color: rgba(15, 23, 42, 0.2);
}

/* ── 视图切换 ──
   命名注意：类名一律带 view/tab 前缀，避开 Tailwind 生成的 @layer utilities
   （`.collapse` 已经坑过一次：visibility:collapse 让按钮永远看不见）。 */
.viewtabs {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  background: var(--color-ink);
}
.viewtab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: none;
  background: transparent;
  color: var(--color-body);
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1;
  padding: 7px 15px;
  border-radius: 999px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
}
.viewtab:hover {
  color: var(--color-text);
}
.viewtab.is-active {
  background: var(--color-panel);
  color: var(--color-text);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.10);
}
.tab-count {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--color-accent);
  background: rgba(37, 99, 235, 0.10);
  border-radius: 999px;
  padding: 2px 7px;
}
/* 有新证据时的提示点：只提示，不自动切换视图 */
.tab-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-accent);
}

.view {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* 非活动视图彻底移出布局（组件实例仍在，状态不丢）。
   !important 是刻意的：这一条必须压过 .view 的 display:flex，不能有歧义。 */
.view.is-hidden {
  display: none !important;
}

/* ── 证据轨迹视图 ── */
.graph-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px 10px;
  border-bottom: 1px solid var(--color-line);
}
.gh-titles {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.eyebrow {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-muted);
}
.graph-head h2 {
  margin: 0;
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
.gh-note {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 证据轨迹正文：宽屏「图 + 详情」两列，未选中节点时图占满整宽。
   ≤1180px 改成上下排：1000 视口下两列会把图挤到 ~350px，卡片放不下、
   字号掉到最小档（实测 48 节点在那里会横向溢出 50px）。
   上下排之后图拿整宽、详情在图下方，两边都能读。 */
.graph-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  overflow: hidden;
}
.graph-body.has-detail {
  grid-template-columns: minmax(0, 1fr) 372px;
}
@media (max-width: 1180px) {
  .graph-body.has-detail {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) minmax(200px, 42%);
  }
  .detail-col {
    border-left: none;
    border-top: 1px solid var(--color-line);
  }
}
.graph-wrap {
  position: relative;
  min-width: 0;
  min-height: 0;
  padding: 14px 18px 16px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
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
  max-width: 320px;
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
.detail-col {
  display: none;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  border-left: 1px solid var(--color-line);
  background: var(--color-panel);
}
.graph-body.has-detail .detail-col {
  display: flex;
}
.detail-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px 8px;
}
.dh-title {
  flex: 1;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-muted);
}
.dh-clear {
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  border-radius: 999px;
  padding: 3px 10px;
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}
.dh-clear:hover {
  background: var(--color-ink);
  color: var(--color-text);
}
.detail {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 16px 16px;
}

/* ── 窄屏（≤1100px）：侧边栏仍在，主区变窄 ──
   没有抽屉、没有遮罩、没有浮动按钮：切换式在两个视图之间切，
   本身就适配窄屏，不需要第二套入口。
   （证据轨迹在图 + 详情两列的断点在上面的 1180px 处。） */
@media (max-width: 1100px) {
  .topbar {
    padding: 12px 16px;
  }
  .tb-note {
    display: none;
  }
  .gh-note {
    display: none;
  }
  .graph-wrap {
    padding: 12px;
  }
}
</style>
