<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EvidenceNode, EvidenceEdge, NodeType } from '../../api/types'

const props = withDefaults(
  defineProps<{
    nodes: EvidenceNode[]
    edges: EvidenceEdge[]
    interactive?: boolean
    width?: number
    height?: number
  }>(),
  { interactive: true, width: 660, height: 540 },
)

const emit = defineEmits<{ (e: 'select', node: EvidenceNode | null): void }>()

const selectedId = ref<string | null>(null)
const hoveredId = ref<string | null>(null)

const TYPE_STYLE: Record<NodeType, { color: string; short: string; label: string; size: number }> = {
  // 浅色主题：原先的浅粉彩色（#fcd34d / #38bdf8 …）是为深底选的，在白底上对比度不足，
  // 这里统一换成同色系的 600 级深色版本，图例与此保持一致。
  Claim: { color: '#d97706', short: '答案', label: '答案主张', size: 9 },
  SQLQuery: { color: '#0284c7', short: 'SQL', label: 'SQL 查询', size: 6.5 },
  SQLRow: { color: '#0891b2', short: '结果', label: '结果行', size: 5.5 },
  Document: { color: '#7c3aed', short: '文档', label: '文档', size: 6.5 },
  Chunk: { color: '#4f46e5', short: '片段', label: '检索片段', size: 5.5 },
  DocumentFact: { color: '#0d9488', short: '字段', label: '字段', size: 5.5 },
  VerificationCheck: { color: '#16a34a', short: '核验', label: '核验', size: 6 },
  ConflictRecord: { color: '#e11d48', short: '冲突', label: '冲突', size: 7 },
  AuthorityRule: { color: '#ca8a04', short: '规则', label: '权威规则', size: 6 },
}

const EDGE_STYLE: Record<string, { dash: string; color: string }> = {
  derived_from: { dash: '', color: 'rgba(100,116,139,0.50)' },
  supports: { dash: '4 3', color: 'rgba(2,132,199,0.55)' },
  retrieved_from: { dash: '1 5', color: 'rgba(79,70,229,0.50)' },
  resolved_by: { dash: '2 2', color: 'rgba(202,138,4,0.60)' },
}

interface Placed {
  id: string
  x: number
  y: number
  layer: number
}

// 径向 BFS 分层：从 Claim（答案主张）出发，证据逐层向外铺开，像星图一样。
function layoutGraph(nodes: EvidenceNode[], edges: EvidenceEdge[], w: number, h: number): Placed[] {
  const ids = new Set(nodes.map((n) => n.evidence_id))
  const adj = new Map<string, string[]>()
  ids.forEach((id) => adj.set(id, []))
  for (const e of edges) {
    if (ids.has(e.src_evidence_id) && ids.has(e.dst_evidence_id)) {
      adj.get(e.src_evidence_id)!.push(e.dst_evidence_id)
      adj.get(e.dst_evidence_id)!.push(e.src_evidence_id)
    }
  }

  const layerOf = new Map<string, number>()
  const visited = new Set<string>()
  const roots = nodes.filter((n) => n.node_type === 'Claim').map((n) => n.evidence_id)
  const queue: string[] = roots.length ? [...roots] : [nodes[0]?.evidence_id].filter(Boolean)
  queue.forEach((id) => {
    visited.add(id)
    layerOf.set(id, 0)
  })
  while (queue.length) {
    const cur = queue.shift()!
    for (const nb of adj.get(cur) || []) {
      if (!visited.has(nb)) {
        visited.add(nb)
        layerOf.set(nb, (layerOf.get(cur) || 0) + 1)
        queue.push(nb)
      }
    }
  }
  nodes.forEach((n) => {
    if (!visited.has(n.evidence_id)) {
      visited.add(n.evidence_id)
      layerOf.set(n.evidence_id, 99)
    }
  })

  const cx = w / 2
  const cy = h / 2
  const layers = new Map<number, EvidenceNode[]>()
  nodes.forEach((n) => {
    const l = layerOf.get(n.evidence_id) ?? 0
    if (!layers.has(l)) layers.set(l, [])
    layers.get(l)!.push(n)
  })
  const realLayers = [...layers.keys()].filter((l) => l < 99)
  const maxLayer = Math.max(...realLayers, 0)

  const placed: Placed[] = []
  layers.forEach((layerNodes, l) => {
    const ll = l >= 99 ? maxLayer + 1 : l
    // 半径以外接正方形为基准：原来按 min(w,h) 且只用到 0.46 的外圈，
    // 在横向画布上会让星座缩在中间一小块。这里按对角线方向铺开并逐层接近边缘。
    const radius = ll === 0 ? 0 : Math.min(w, h) * 0.62 * (ll / (maxLayer + 1))
    const count = layerNodes.length
    layerNodes.forEach((n, i) => {
      // 同层多节点时错开角度，避免重叠
      const ang = count > 1 ? (i / count) * Math.PI * 2 - Math.PI / 2 : 0
      const jitterX = count > 1 ? 0 : (i - (count - 1) / 2) * 42
      const x = cx + Math.cos(ang) * radius + jitterX
      const y = cy + Math.sin(ang) * radius
      placed.push({ id: n.evidence_id, x, y, layer: ll })
    })
  })
  return placed
}

const layout = computed(() => layoutGraph(props.nodes, props.edges, props.width, props.height))

const nodeById = computed(() => {
  const m = new Map<string, EvidenceNode>()
  props.nodes.forEach((n) => m.set(n.evidence_id, n))
  return m
})

const placedMap = computed(() => {
  const m = new Map<string, Placed>()
  layout.value.forEach((p) => m.set(p.id, p))
  return m
})

const edgeLines = computed(() =>
  props.edges
    .filter((e) => placedMap.value.has(e.src_evidence_id) && placedMap.value.has(e.dst_evidence_id))
    .map((e) => ({
      id: e.src_evidence_id + e.dst_evidence_id + (e.edge_type || ''),
      type: e.edge_type,
      x1: placedMap.value.get(e.src_evidence_id)!.x,
      y1: placedMap.value.get(e.src_evidence_id)!.y,
      x2: placedMap.value.get(e.dst_evidence_id)!.x,
      y2: placedMap.value.get(e.dst_evidence_id)!.y,
    })),
)

function styleOf(n: EvidenceNode) {
  return TYPE_STYLE[n.node_type] ?? TYPE_STYLE.Claim
}

function onSelect(id: string) {
  if (!props.interactive) return
  if (selectedId.value === id) {
    selectedId.value = null
    emit('select', null)
  } else {
    selectedId.value = id
    emit('select', nodeById.value.get(id) ?? null)
  }
}
</script>

<template>
  <svg
    class="constellation"
    :viewBox="`0 0 ${width} ${height}`"
    :width="width"
    :height="height"
    role="img"
    aria-label="推理证据星链图"
  >
    <defs>
      <radialGradient id="starGlow" cx="50%" cy="50%" r="50%">
        <!-- 浅色主题：原来的白色光晕在白底上完全不可见，改为极淡的深色晕圈 -->
        <stop offset="0%" stop-color="#0b1220" stop-opacity="0.14" />
        <stop offset="100%" stop-color="#0b1220" stop-opacity="0" />
      </radialGradient>
    </defs>

    <!-- 连线 -->
    <g>
      <line
        v-for="e in edgeLines"
        :key="'e' + e.id"
        :x1="e.x1"
        :y1="e.y1"
        :x2="e.x2"
        :y2="e.y2"
        :stroke="EDGE_STYLE[e.type]?.color ?? 'rgba(148,163,184,0.4)'"
        :stroke-dasharray="EDGE_STYLE[e.type]?.dash ?? ''"
        stroke-width="1.2"
      />
    </g>

    <!-- 星点 -->
    <g
      v-for="p in layout"
      :key="p.id"
      class="node"
      :style="{ animationDelay: p.layer * 130 + 'ms' }"
      @click="onSelect(p.id)"
      @mouseenter="hoveredId = p.id"
      @mouseleave="hoveredId = null"
    >
      <circle
        :cx="p.x"
        :cy="p.y"
        :r="styleOf(nodeById.get(p.id)!).size + 8"
        fill="url(#starGlow)"
        :opacity="hoveredId === p.id || selectedId === p.id ? 0.9 : 0.28"
      />
      <circle
        :cx="p.x"
        :cy="p.y"
        :r="styleOf(nodeById.get(p.id)!).size"
        :fill="styleOf(nodeById.get(p.id)!).color"
        :stroke="selectedId === p.id ? '#0b1220' : 'transparent'"
        stroke-width="1.5"
      />
      <text
        :x="p.x"
        :y="p.y + styleOf(nodeById.get(p.id)!).size + 16"
        text-anchor="middle"
        :fill="hoveredId === p.id ? '#0b1220' : 'rgba(71,85,105,0.85)'"
        font-size="11"
        :style="{ transition: 'fill .2s' }"
      >
        {{ styleOf(nodeById.get(p.id)!).short }}
      </text>
    </g>
  </svg>
</template>

<style scoped>
.constellation {
  width: 100%;
  height: auto;
  display: block;
}
.node {
  cursor: pointer;
  animation: nodeIn 0.7s cubic-bezier(0.2, 0.85, 0.3, 1) backwards;
}
.node circle {
  transition: r 0.15s ease;
}
.node:hover :deep(circle:last-of-type) {
  filter: drop-shadow(0 0 6px currentColor);
}
/* 入场只做缩放，不做透明度。
   原来 from 里带 opacity: 0，配合 backwards 填充与逐层延迟，意味着每个节点在它那段
   延迟结束前都是不可见的——一旦动画因任何原因不播放（无头渲染、降低动效、渲染异常），
   节点会永久消失，只剩连线。缩放入场既能表达「星点依次点亮」，又保证任何时刻都可见。 */
@keyframes nodeIn {
  from {
    transform: scale(0.2);
    transform-origin: center;
    transform-box: fill-box;
  }
  to {
    transform: scale(1);
    transform-origin: center;
    transform-box: fill-box;
  }
}

/* 降低动效偏好时不做入场，直接呈现终态。写在 scoped 块内才能与上面的 .node 规则
   同特异性并靠顺序生效——放全局会被 scoped 选择器压掉。 */
@media (prefers-reduced-motion: reduce) {
  .node {
    animation: none;
  }
  .node circle {
    transition: none;
  }
}
</style>
