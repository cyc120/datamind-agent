<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import SideNav from '../components/SideNav.vue'
import ChatPanel from '../components/chat/ChatPanel.vue'
import ConstellationGraph from '../components/stargraph/ConstellationGraph.vue'
import NodeDetail from '../components/stargraph/NodeDetail.vue'
import { health, streamChat, getEvidence } from '../api/client'
import type { ChatMessage, ChatPayload, EvidenceGraph, EvidenceNode } from '../api/types'

const messages = ref<ChatMessage[]>([])
const running = ref(false)
const graph = reactive<EvidenceGraph>({ nodes: [], edges: [], claims: [] })
const selectedNode = ref<EvidenceNode | null>(null)
const healthState = ref<'checking' | 'ok' | 'down'>('checking')
const modelState = ref<'checking' | 'ok' | 'down'>('checking')

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
})
</script>

<template>
  <div class="mainpage">
    <SideNav :health="healthState" :model="modelState" />

    <ChatPanel :messages="messages" :running="running" @send="send" />

    <aside class="right">
      <div class="right-head">
        <div>
          <div class="eyebrow">Evidence Graph</div>
          <h2>推理星链</h2>
        </div>
        <span class="count">{{ graph.nodes.length }} 节点</span>
      </div>

      <div class="graph-wrap">
        <ConstellationGraph
          :nodes="graph.nodes"
          :edges="graph.edges"
          :width="660"
          :height="520"
          @select="selectedNode = $event"
        />
        <div v-if="running" class="overlay">
          <span class="pulse-dot"></span> 正在生成证据…
        </div>
        <div v-else-if="graph.nodes.length === 0" class="overlay static">
          <p>提问后，这里会点亮一张证据星图：SQL、检索片段、原始数据与答案主张。</p>
        </div>
      </div>

      <div class="detail">
        <NodeDetail :node="selectedNode" />
      </div>
    </aside>
  </div>
</template>

<style scoped>
.mainpage {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background:
    radial-gradient(1200px 600px at 70% -10%, rgba(56, 189, 248, 0.08), transparent 60%),
    radial-gradient(1000px 600px at 20% 110%, rgba(129, 140, 248, 0.08), transparent 60%),
    var(--color-void);
}
.right {
  width: 400px;
  flex-shrink: 0;
  border-left: 1px solid var(--color-line);
  background: rgba(10, 14, 24, 0.7);
  backdrop-filter: blur(12px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.right-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 18px 10px;
}
.eyebrow {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #64748b;
}
.right-head h2 {
  margin: 3px 0 0;
  font-size: 17px;
  font-weight: 600;
  color: #eef4ff;
}
.count {
  font-size: 11px;
  color: #64748b;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 3px 10px;
}
.graph-wrap {
  position: relative;
  padding: 0 12px;
  flex-shrink: 0;
}
.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #7dd3fc;
  font-size: 13px;
  pointer-events: none;
}
.overlay.static p {
  max-width: 260px;
  text-align: center;
  color: #64748b;
  line-height: 1.7;
  margin: 0;
  font-size: 12.5px;
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #7dd3fc;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.1); }
}
.detail {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  border-top: 1px solid var(--color-line);
}

@media (max-width: 1100px) {
  .right {
    display: none;
  }
}
</style>
