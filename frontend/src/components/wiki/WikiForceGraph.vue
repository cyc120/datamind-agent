<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import AnimatedList from '../bits/AnimatedList/AnimatedList.vue'
import { WIKI_PAGE_LIMITS, listWikiEntries, wikiSpaceGraph } from '../../api/client'
import type { WikiEntry, WikiGraphNode, WikiGraphRelation } from '../../api/types'
import WikiGraphSettings from './WikiGraphSettings.vue'
import {
  DEFAULT_APPEARANCE,
  DEFAULT_COLOR_STATE,
  DEFAULT_FILTERS,
  DEFAULT_FORCE_PARAMS,
  ENTRY_TYPE_OPTIONS,
  FORCE_LIMITS,
  ForceLayout,
  REDUCED_MOTION_ITERATIONS,
  clampForceParams,
} from './forceLayout'
import type {
  ForceParams,
  LayoutNode,
  WikiAppearanceState,
  WikiColorState,
  WikiFilterState,
} from './forceLayout'

/* ============================================================================
 * Wiki 力导向关系图（契约 §8.2 语义 / §8.3 交互 / §9 视觉基线与布局边界）
 *
 * 为什么是 SVG 而不是 canvas：本图的上限被冻结在 100 节点 / 300 关系（§9.3），
 * 这个量级下 SVG 足够流畅，而且「图外可聚焦的词条目录 + 图内可 Tab 的节点」这条
 * 无障碍要求（§8.3 末段）在 SVG 上是原生的（`<g tabindex="0">` 就是可聚焦元素），
 * canvas 得自己重写一套 hit-test 与焦点管理。
 *
 * 五条硬边界落在哪里（都能在本文件里指出具体代码）：
 *   1. **不阻塞交互**：布局只在 requestAnimationFrame 回调里跑 `layout.tick(4)`
 *      （一帧 4 次迭代，100 节点 / 300 关系上约 0.2ms），没有同步长循环；
 *      低运动模式才一次算完（`runToEnd()`），用的还是同一份 300 次 / 5 秒预算。
 *   2. **收敛 / 暂停 / 隐藏 / 离开就停**：`layout.tick()` 返回 0 就退出循环；
 *      `visibilitychange`、`onBeforeUnmount`、暂停开关都调用 `stopSimulation()`。
 *   3. **只在三种情形重排**：节点关系集合变化（`sync`）、布局参数变化（`setParams`）、
 *      用户点「重新排布 / 恢复布局 / 按新力度重排」（`reheat`）。缩放、平移、选中、
 *      打开详情**不碰** layout —— 视图变换是独立的 `view` 对象，布局坐标是
 *      **与画布无关的虚拟空间**（容器 resize 也不需要重排）。
 *   4. **圆点大小只由「已加载连接数」决定**（`degreesOf` 在 forceLayout 里按并集重算，
 *      原因见那里的注释），有上下限；孤立节点保底半径，仍可见、可点击。
 *   5. **语义红线**：本组件不持有任何"全库"口径的数字；标题一律用服务端下发的
 *      `scope_description`（"当前授权范围图（不是全库全图）"）。
 * ========================================================================== */

const props = withDefaults(
  defineProps<{
    /** 空间 id。留空时先请求一次目录，用响应里的 `meta.space_id` 自举
     *  （第一版 space_id 与 user_id 一一对应，前端不需要猜、也不需要自己存）。 */
    spaceId?: string
    /** 画布高度（CSS 值）；宽度由容器决定 */
    height?: string
  }>(),
  { spaceId: '', height: '560px' },
)

const emit = defineEmits<{
  /** 选中的词条变化（null = 取消选中） */
  (e: 'select', entry: WikiEntry | null): void
  /** 用户要求打开详情（**不**改变当前视野与选中状态） */
  (e: 'open-entry', entryId: string): void
}>()

/** 选中项可被父组件双向绑定（深链 `/#/wiki/:entryId` 由页面把 id 传进来）。 */
const selectedEntryId = defineModel<string | null>('selectedEntryId', { default: null })

/* 四组设置：允许父组件绑定（用于持久化）；不绑定时就是组件内的本地状态。 */
const filters = defineModel<WikiFilterState>('filters', { default: () => ({ ...DEFAULT_FILTERS }) })
const colors = defineModel<WikiColorState>('colors', { default: () => ({ ...DEFAULT_COLOR_STATE }) })
const appearance = defineModel<WikiAppearanceState>('appearance', {
  default: () => ({ ...DEFAULT_APPEARANCE }),
})
const forces = defineModel<ForceParams>('forces', { default: () => ({ ...DEFAULT_FORCE_PARAMS }) })

/* ── 服务端数据（只有"当前授权范围图"这一份口径，没有第二个数据源）────────── */
const nodes = ref<WikiGraphNode[]>([])
const relations = ref<WikiGraphRelation[]>([])
const loaded = ref({ nodes: 0, relations: 0 })
const limits = ref({ nodes: FORCE_LIMITS.maxNodes, relations: FORCE_LIMITS.maxRelations })
const limitReached = ref<{ nodes: number; relations: number } | null>(null)
const truncatedReason = ref<string | null>(null)
const scopeDescription = ref('')
const warnings = ref<string[]>([])
const authorizedScope = ref({ nodes_considered: 0, nodes_visible: 0 })
const activeLayer = ref<string>('entries')
const hasMore = ref(false)
const nextCursor = ref<string | null>(null)
const asOf = ref('')
const spaceIdResolved = ref(props.spaceId)

const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const errorCode = ref('')

/* ── 布局与视图 ───────────────────────────────────────────────────────────── */
const layout = shallowRef<ForceLayout | null>(null)
/** 每帧替换的一份坐标快照：让 Vue 用扁平响应式追踪坐标，而不是把 Vue 反应式塞进 ForceLayout。 */
const frame = ref<LayoutNode[]>([])
const view = ref({ x: 0, y: 0, k: 1 })
/** 用户是否自己动过视图：动过之后自动适应就不再抢镜头。 */
const userAdjustedView = ref(false)
const canvas = ref<HTMLElement | null>(null)
const svgEl = ref<SVGSVGElement | null>(null)
const size = ref({ w: 900, h: 560 })
const dragging = ref<{ id: string; dx: number; dy: number } | null>(null)
const panning = ref<{ x: number; y: number; ox: number; oy: number } | null>(null)
const systemReducedMotion = ref(false)
const stopReason = ref<string | null>(null)
const iterationsUsed = ref(0)
/** 本次布局**实际**拿到的迭代预算（低运动模式下是 120 而不是 300，文案必须照实说）。 */
const iterationBudget = ref<number>(FORCE_LIMITS.maxIterations)
const durationBudgetMs = ref<number>(FORCE_LIMITS.maxDurationMs)

let rafId: number | null = null
let resizeObserver: ResizeObserver | null = null
let motionQuery: MediaQueryList | null = null
let fetchToken = 0
let debounceTimer: number | null = null

const lowMotion = computed(() => appearance.value.lowMotion || systemReducedMotion.value)

/* ── 节点/关系的读取助手（`node_kind` 是判别键，三种载荷只有一个存在）──────── */

function nodeIdOf(node: WikiGraphNode | undefined | null): string {
  if (!node) return ''
  return String(node.node_id ?? node.entry?.entry_id ?? node.run?.run_id ?? node.evidence_ref?.evidence_ref_id ?? '')
}

function isEntryNode(node: WikiGraphNode | undefined | null): boolean {
  return !!node && node.node_kind === 'entry' && !!node.entry
}

/** 标题：优先词条标题；非词条图层退回该图层自己的可读字段，最后才是裸 id。 */
function nodeTitle(node: WikiGraphNode | undefined | null): string {
  if (!node) return '(未知节点)'
  if (node.entry?.title) return String(node.entry.title)
  if (node.run?.question) return String(node.run.question)
  if (node.evidence_ref?.source_ref) return String(node.evidence_ref.source_ref)
  if (node.entry?.entry_id) return String(node.entry.entry_id)
  return String(node.node_id ?? '(无标题)')
}

const links = computed(() =>
  relations.value.map((relation) => ({
    id: String(relation.relation_id),
    source: String(relation.src),
    target: String(relation.dst),
  })),
)

const nodeIndex = computed(() => {
  const map = new Map<string, WikiGraphNode>()
  for (const node of nodes.value) map.set(nodeIdOf(node), node)
  return map
})

/** 坐标索引：每帧建一次，代替在模板里对每条线做一次 `find`。 */
const positionById = computed(() => {
  const map = new Map<string, LayoutNode>()
  for (const node of frame.value) map.set(node.id, node)
  return map
})

function nodeAt(id: string): WikiGraphNode | undefined {
  return nodeIndex.value.get(id)
}

const selectedNode = computed<WikiGraphNode | null>(() =>
  selectedEntryId.value ? (nodeAt(selectedEntryId.value) ?? null) : null,
)

const selectedEntry = computed<WikiEntry | null>(() => selectedNode.value?.entry ?? null)

/** 选中节点的 1 跳邻域（只由**已加载**的关系算出，不推测任何隐藏邻居）。 */
const neighborIds = computed(() => {
  const set = new Set<string>()
  const focus = selectedEntryId.value
  if (!focus) return set
  for (const relation of relations.value) {
    if (relation.src === focus && relation.dst) set.add(String(relation.dst))
    if (relation.dst === focus && relation.src) set.add(String(relation.src))
  }
  set.delete(focus)
  return set
})

/** 与选中节点相连的关系（用于"关系类型、方向与依据"清单）。 */
const selectedRelations = computed(() => {
  const focus = selectedEntryId.value
  if (!focus) return []
  return relations.value.filter((relation) => relation.src === focus || relation.dst === focus)
})

/* ── 颜色（契约 §9.2：初始深浅灰；启用后按类型或分类着色，且必须有图例与文字说明）── */

/** 词条类型 → 颜色。**只为分组服务**，与置信度/权威/审核结果无关。 */
const TYPE_COLORS: Record<string, string> = {
  business_entity: '#2563eb',
  metric: '#16a34a',
  norm: '#d97706',
  conclusion: '#7c3aed',
}
/** 分类着色的调色板：按分类名排序后取模，保证同一分类在同一份数据里颜色稳定。 */
const CATEGORY_PALETTE = [
  '#2563eb',
  '#16a34a',
  '#d97706',
  '#7c3aed',
  '#0891b2',
  '#e11d48',
  '#4f46e5',
  '#0d9488',
]
const NODE_KIND_COLORS: Record<string, string> = {
  entry: '#2563eb',
  evidence_ref: '#0891b2',
  run: '#7c3aed',
}
const NO_CATEGORY_KEY = '未分类'

/** 分类名 → 颜色：按已加载集合里的分类名**排序**取模（不是按出现顺序，避免翻页后变色）。 */
const categoryColorMap = computed(() => {
  const names = new Set<string>()
  for (const node of nodes.value) {
    const category = node.entry?.category
    if (category) names.add(String(category))
  }
  const map = new Map<string, string>()
  ;[...names]
    .sort()
    .forEach((name, index) => map.set(name, CATEGORY_PALETTE[index % CATEGORY_PALETTE.length]))
  return map
})

interface LegendItem {
  key: string
  label: string
  color: string
  count: number
}

function colorForNode(node: WikiGraphNode | undefined): string {
  if (!node) return '#9ca3af'
  if (!colors.value.enabled) {
    // 初始：**深浅蓝阶**（用户要求：都蓝色但深深浅浅，比原来的灰阶好看）。
    // 按"已加载连接数"分四档：连得越多越深、孤立节点最浅。
    //
    // 这仍然**不是语义色**（不表示好坏、不代表结论），只是让结构在视觉上分层；
    // 状态依旧靠文字标识（契约 §9.2：状态标识是文字、不是颜色）。
    // 最浅一档取 blue-200 而不是更浅 —— 节点只有几像素，浅到看不见就白画了。
    const degree = positionById.value.get(nodeIdOf(node))?.degree ?? 0
    if (degree === 0) return '#bfdbfe'
    if (degree <= 2) return '#93c5fd'
    if (degree <= 5) return '#60a5fa'
    return '#3b82f6'
  }
  if (colors.value.by === 'type' && isEntryNode(node)) {
    return TYPE_COLORS[String(node.entry?.entry_type)] ?? '#9ca3af'
  }
  if (colors.value.by === 'category' && isEntryNode(node)) {
    const category = node.entry?.category ? String(node.entry.category) : NO_CATEGORY_KEY
    return category === NO_CATEGORY_KEY
      ? '#9ca3af'
      : (categoryColorMap.value.get(category) ?? '#9ca3af')
  }
  return NODE_KIND_COLORS[String(node.node_kind)] ?? '#9ca3af'
}

/** 描边：灰色模式下是"深灰描边"，启用颜色组后压暗一点，保证白底上仍有轮廓。 */
function strokeForNode(): string {
  return colors.value.enabled ? 'rgba(15,23,42,0.38)' : '#5b6472'
}

/** 图例：**只统计当前已加载集合**，并把这个口径写在每一项上。 */
const legend = computed<LegendItem[]>(() => {
  if (!colors.value.enabled) return []
  const counts = new Map<string, number>()
  const labels = new Map<string, string>()
  const colorOf = new Map<string, string>()
  for (const node of nodes.value) {
    let key: string
    let label: string
    if (colors.value.by === 'type' && isEntryNode(node)) {
      key = node.entry?.entry_type ? String(node.entry.entry_type) : '未标注类型'
      label =
        ENTRY_TYPE_OPTIONS.find((option) => option.value === key)?.label ??
        (key === '未标注类型' ? '未标注类型的词条' : key)
    } else if (colors.value.by === 'category' && isEntryNode(node)) {
      const category = node.entry?.category ? String(node.entry.category) : NO_CATEGORY_KEY
      key = category === NO_CATEGORY_KEY ? NO_CATEGORY_KEY : `category:${category}`
      label = category === NO_CATEGORY_KEY ? '未分类的词条' : `分类：${category}`
    } else {
      key = `kind:${node.node_kind}`
      label = `${nodeKindLabel(String(node.node_kind))}（非词条节点）`
    }
    counts.set(key, (counts.get(key) ?? 0) + 1)
    labels.set(key, label)
    colorOf.set(key, colorForNode(node))
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
    .map(([key, count]) => ({
      key,
      label: labels.get(key) ?? key,
      color: colorOf.get(key) ?? '#64748b',
      count,
    }))
})

const legendNote = computed(() => {
  if (!colors.value.enabled) return ''
  return colors.value.by === 'type'
    ? '按词条类型着色：颜色只区分类型，不表示重要程度、置信度或权威。未标注类型的词条与其它图层的节点统一用灰色。'
    : '按分类着色：颜色只区分目录分类，不表示重要程度、置信度或权威。没有分类的词条用灰色。'
})

/* ── 状态标识：**不靠颜色**（契约 §9.2 最后两行）────────────────────────── */

interface StatusMark {
  glyph: string
  text: string
}

/** 只读三套状态里的**文字**，不做任何合并，也不把 warned 读成 verified。 */
function statusMarks(node: WikiGraphNode | undefined | null): StatusMark[] {
  const entry = node?.entry
  if (!entry) return []
  const marks: StatusMark[] = []
  const status = entry.status ?? ({} as WikiEntry['status'])
  if (status.assertion === 'conflicted') marks.push({ glyph: '⚠', text: '存在未裁决冲突' })
  else if (status.assertion === 'insufficient') marks.push({ glyph: '?', text: '证据不足' })
  else if (status.assertion === 'unverified') marks.push({ glyph: '○', text: '未核验' })
  else if (status.assertion === 'verified') marks.push({ glyph: '✓', text: '已核验' })
  if (status.publication === 'draft') marks.push({ glyph: '✎', text: '草稿' })
  else if (status.publication === 'in_review') marks.push({ glyph: '✎', text: '待复核' })
  else if (status.publication === 'archived') marks.push({ glyph: '⌫', text: '已归档' })
  if (status.source_validity === 'stale') marks.push({ glyph: '↻', text: '来源已换版，待重新核验' })
  else if (status.source_validity === 'unavailable') marks.push({ glyph: '⊘', text: '来源定位不可用' })
  return marks
}

const UNKNOWN_STATUS = '（字段缺失，不猜测）'

function assertionText(node: WikiGraphNode | undefined | null): string {
  const value = node?.entry?.status?.assertion
  if (!value) return UNKNOWN_STATUS
  const map: Record<string, string> = {
    verified: '已验证',
    unverified: '未验证',
    insufficient: '证据不足',
    conflicted: '有冲突',
  }
  return map[String(value)] ?? String(value)
}

function publicationText(node: WikiGraphNode | undefined | null): string {
  const value = node?.entry?.status?.publication
  if (!value) return UNKNOWN_STATUS
  const map: Record<string, string> = {
    draft: '草稿',
    in_review: '待复核',
    published: '已发布',
    archived: '已归档',
  }
  return map[String(value)] ?? String(value)
}

function validityText(node: WikiGraphNode | undefined | null): string {
  const value = node?.entry?.status?.source_validity
  if (!value) return UNKNOWN_STATUS
  const map: Record<string, string> = { current: '当前', stale: '已换版', unavailable: '不可用' }
  return map[String(value)] ?? String(value)
}

function entryTypeText(node: WikiGraphNode | undefined | null): string {
  const value = node?.entry?.entry_type
  if (!value) return ''
  return ENTRY_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? String(value)
}

function nodeKindLabel(kind: string): string {
  const map: Record<string, string> = { entry: '词条', evidence_ref: '来源证据', run: '运行' }
  return map[kind] ?? `未知节点类型（${kind}）`
}

/** 节点在**已加载**集合里的连接数（渲染与文案都用它，绝不显示"全库度数"）。 */
function loadedDegree(id: string): number {
  return positionById.value.get(id)?.degree ?? 0
}

/* ── 渲染模型：每帧算一次，模板不再做查找 ─────────────────────────────── */

/**
 * 标签的按需显示：**屏幕空间贪心避让 + 数量预算**（契约 §9.2「缩放后按可用空间减少
 * 非选中标签；选中节点与邻域标题优先显示」）。
 *
 * 做法：把每个候选标签当成一个屏幕坐标下的矩形，按优先级依次尝试放置，与已放置的
 * 矩形相交就跳过。优先级 = 选中节点 → 它的一跳邻域 → 其余按"已加载连接数"降序。
 * 预算与缩放同向：缩得越小，可用屏幕面积越小，标签越少。
 *
 * 两个刻意的例外：
 *   · **选中节点与邻域不做避让判定**（它们的矩形仍然登记，所以别人不会压上来）：
 *     契约要求它们"优先显示"，被自己的邻域挤掉就违背了这条。
 *   · 完全落在画布外的标签直接不画（画布 overflow:hidden，画了也看不见）。
 */
const LABEL_CHAR_WIDTH = 11.5
const LABEL_HEIGHT = 16

function computeLabelIds(): Set<string> {
  const ids = new Set<string>()
  const priority: string[] = []
  if (selectedEntryId.value) {
    priority.push(selectedEntryId.value)
    for (const id of neighborIds.value) {
      if (priority.length >= 41) break
      priority.push(id)
    }
  }
  if (!appearance.value.showLabels) {
    // 关掉"显示标题"时仍然保留**选中节点**的标题：否则选中了却看不出选的是谁。
    return selectedEntryId.value ? new Set([selectedEntryId.value]) : new Set<string>()
  }

  const k = Math.max(MIN_SCALE, view.value.k)
  const inverse = 1 / k
  const placed: Array<{ x0: number; x1: number; y0: number; y1: number }> = []
  const positionByIdLocal = positionById.value
  const collides = (rect: { x0: number; x1: number; y0: number; y1: number }): boolean =>
    placed.some(
      (other) =>
        !(rect.x1 < other.x0 || rect.x0 > other.x1 || rect.y1 < other.y0 || rect.y0 > other.y1),
    )
  const rectFor = (id: string) => {
    const node = positionByIdLocal.get(id)
    const data = nodeAt(id)
    if (!node) return null
    const screenX = node.x * k + view.value.x
    const screenY = node.y * k + view.value.y
    const text = nodeTitle(data)
    const width = Math.min(240, Math.max(26, text.length * LABEL_CHAR_WIDTH))
    const top = screenY + node.radius + 4
    return { x0: screenX - width / 2, x1: screenX + width / 2, y0: top, y1: top + LABEL_HEIGHT }
  }

  // 1) 选中节点与邻域：无条件放置，但登记矩形
  for (const id of priority) {
    const rect = rectFor(id)
    if (!rect) continue
    placed.push(rect)
    ids.add(id)
  }

  // 2) 其余节点：先过预算，再过避让
  const budget = Math.max(6, Math.min(60, Math.round(60 * k)))
  const rest = frame.value
    .filter((node) => !ids.has(node.id))
    .sort((a, b) => b.degree - a.degree || (a.id < b.id ? -1 : 1))
  for (const node of rest) {
    if (ids.size >= Math.max(budget, priority.length)) break
    const rect = rectFor(node.id)
    if (!rect) continue
    // 完全在画布外（含被右上角面板压住的区域不做判断：面板是浮层，用户可折叠）就不画
    if (rect.y1 < 0 || rect.y0 > size.value.h || rect.x1 < 0 || rect.x0 > size.value.w) continue
    if (collides(rect)) continue
    placed.push(rect)
    ids.add(node.id)
  }
  return ids
}

interface RenderNode {
  id: string
  x: number
  y: number
  radius: number
  labelOffset: number
  degree: number
  title: string
  fill: string
  stroke: string
  marks: StatusMark[]
  markText: string
  showLabel: boolean
  selected: boolean
  neighbor: boolean
  dim: boolean
  aria: string
}

const renderNodes = computed<RenderNode[]>(() => {
  const labelIds = computeLabelIds()
  const focus = selectedEntryId.value
  const neighbors = neighborIds.value
  // 圆点半径在**屏幕空间**恒定：`r = node.radius / k`。理由是这个半径承载的是
  // "已加载连接数"这一个语义（契约 §9.2），它不该随着用户缩放而变成另一种读数；
  // 而且屏幕恒定才能保证缩到很小的时候孤立节点仍然看得见、点得中。
  const inverseScale = 1 / Math.max(MIN_SCALE, view.value.k)
  return frame.value.map((node) => {
    const data = nodeAt(node.id)
    const marks = statusMarks(data)
    const title = nodeTitle(data)
    const radius = node.radius * inverseScale
    return {
      id: node.id,
      x: node.x,
      y: node.y,
      radius,
      labelOffset: radius + 11 * inverseScale,
      degree: node.degree,
      title,
      fill: colorForNode(data),
      stroke: strokeForNode(),
      marks,
      markText: marks.map((mark) => mark.glyph).join(''),
      showLabel: labelIds.has(node.id),
      selected: node.id === focus,
      neighbor: neighbors.has(node.id),
      dim: !!focus && node.id !== focus && !neighbors.has(node.id),
      aria: `${title}：已加载连接数 ${node.degree}${
        marks.length ? `；状态标识 ${marks.map((mark) => mark.text).join('、')}` : ''
      }`,
    }
  })
})

/** 关系类型 -> 中文。键与 wiki 词表（WIKI_DOMAIN.md 3.2 的 21 种）一一对应；未收录的类型一律显示原样代码，不猜。 */
const RELATION_TYPE_ZH: Record<string, string> = {
  restates: '同一主张',
  supersedes: '取代',
  supports: '支持',
  conflicts: '冲突',
  contradicts: '冲突',
  derived_from: '由…推导',
  retrieved_from: '取自',
  verified_by: '经…核验',
  repaired_from: '修复自',
  resolved_by: '经…裁决',
  entity_match: '同一实体',
  version_scope: '版本范围',
  unit_equivalent: '单位等价',
  time_overlap: '时间重叠',
  maps_to: '映射到',
  measures: '度量',
  applies_to: '适用于',
  scoped_by: '范围由…限定',
  threshold_of: '阈值属于',
  about: '关于',
  justified_by: '依据',
}

function relationTypeLabel(relation: WikiGraphRelation): string {
  const code = relation.relation_type ? String(relation.relation_type) : ''
  if (!code) return ''
  return RELATION_TYPE_ZH[code] ?? code
}

interface RenderLink {
  id: string
  x1: number
  y1: number
  x2: number
  y2: number
  unsupported: boolean
  emphasized: boolean
  dim: boolean
  arrow: boolean
  /** 连线中点要显示的关系类型中文名；空串表示这条边不标。 */
  label: string
}

/** 边足够少时把关系类型全标出来；边多时只标与选中项相连的（见 renderLinks）。 */
const showAllLinkLabels = computed(() => relations.value.length <= 20)

const renderLinks = computed<RenderLink[]>(() => {
  const positions = positionById.value
  const focus = selectedEntryId.value
  const out: RenderLink[] = []
  for (const relation of relations.value) {
    const src = positions.get(String(relation.src))
    const dst = positions.get(String(relation.dst))
    // 两端都必须在**已加载**节点集合里（服务端也不会给半条边，这里是第二道闸）。
    if (!src || !dst) continue
    const incident = !!focus && (relation.src === focus || relation.dst === focus)
    out.push({
      id: String(relation.relation_id),
      x1: src.x,
      y1: src.y,
      x2: dst.x,
      y2: dst.y,
      unsupported: relation.supported === false,
      emphasized: incident,
      dim: !!focus && !incident,
      // 方向只在**选中节点**的连线上画箭头：300 条边全带箭头会变成噪声。
      arrow: incident && relation.direction !== 'undirected',
      // 同理，关系类型的文字标签也不是每条都标：边少（<=20）时全标；
      // 边多时只标与选中节点相连的那几条，否则整张图会被标签糊住。
      label: showAllLinkLabels.value || incident ? relationTypeLabel(relation) : '',
    })
  }
  return out
})

/* ── 拉数（永远只取「当前授权范围图」这一份口径）──────────────────────────── */

function resetGraphState(): void {
  nodes.value = []
  relations.value = []
  loaded.value = { nodes: 0, relations: 0 }
  limitReached.value = null
  truncatedReason.value = null
  hasMore.value = false
  nextCursor.value = null
  warnings.value = []
  layout.value = null
  frame.value = []
  stopSimulation()
}

/**
 * 取一批图数据。
 * `cursor=null` 表示**新集合的第一批**：图层或筛选变化时服务端会忽略旧游标并重置，
 * 客户端**必须**同时清空已加载集合，否则"继续加载绕过上限"会在客户端侧发生（契约 §4 语义 4）。
 */
async function fetchGraph(cursor: string | null): Promise<void> {
  if (!spaceIdResolved.value) return
  const token = ++fetchToken
  if (cursor == null) {
    loading.value = true
    error.value = ''
    errorCode.value = ''
  } else {
    loadingMore.value = true
  }
  try {
    const response = await wikiSpaceGraph(spaceIdResolved.value, {
      layer: filters.value.layer,
      q: filters.value.q,
      type: filters.value.type,
      category: filters.value.category,
      status: filters.value.status,
      limit: WIKI_PAGE_LIMITS.graph.default,
      cursor,
    })
    if (token !== fetchToken) return
    const data = response.data
    activeLayer.value = String(data.layer ?? filters.value.layer)
    scopeDescription.value = String(data.scope_description ?? '')
    loaded.value = {
      nodes: Number(data.loaded?.nodes ?? 0),
      relations: Number(data.loaded?.relations ?? 0),
    }
    limits.value = {
      nodes: Number(data.limits?.nodes ?? FORCE_LIMITS.maxNodes),
      relations: Number(data.limits?.relations ?? FORCE_LIMITS.maxRelations),
    }
    limitReached.value = data.limit_reached ?? null
    truncatedReason.value = data.truncated_reason ?? null
    hasMore.value = !!response.paging?.has_more
    nextCursor.value = response.paging?.next_cursor ?? null
    warnings.value = Array.isArray(response.warnings) ? [...response.warnings] : []
    authorizedScope.value = {
      nodes_considered: Number(response.meta?.authorized_scope?.nodes_considered ?? 0),
      nodes_visible: Number(response.meta?.authorized_scope?.nodes_visible ?? 0),
    }
    asOf.value = String(response.meta?.as_of ?? '')
    spaceIdResolved.value = String(response.meta?.space_id ?? spaceIdResolved.value)

    // 增量合并：服务端只回**增量节点**与本批**新完成**的关系，所以按 id 去重合并。
    const nodeMap = new Map(nodes.value.map((node) => [nodeIdOf(node), node]))
    for (const node of data.nodes ?? []) nodeMap.set(nodeIdOf(node), node)
    nodes.value = [...nodeMap.values()]
    const relationMap = new Map(relations.value.map((rel) => [String(rel.relation_id), rel]))
    for (const relation of data.relations ?? []) relationMap.set(String(relation.relation_id), relation)
    relations.value = [...relationMap.values()]

    // 选中项若已不在新的授权集合里，**明确**取消选中并让文案说明，而不是留一个悬空 id。
    if (selectedEntryId.value && !nodeMap.has(selectedEntryId.value)) {
      selectedEntryId.value = null
      emit('select', null)
    }
    syncLayout()
  } catch (err: any) {
    if (token !== fetchToken) return
    errorCode.value = String(err?.errorCode ?? 'WIKI_LOAD_FAILED')
    error.value = String(err?.message ?? err)
    if (cursor == null) resetGraphState()
  } finally {
    if (token === fetchToken) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

/** 自举 space_id：第一版 space_id 与 user_id 一一对应，目录接口的 meta 会告诉我们。 */
async function bootstrapSpaceId(): Promise<boolean> {
  if (spaceIdResolved.value) return true
  try {
    const response = await listWikiEntries({ limit: 1 })
    spaceIdResolved.value = String(response.meta?.space_id ?? '')
    return !!spaceIdResolved.value
  } catch (err: any) {
    errorCode.value = String(err?.errorCode ?? 'WIKI_LOAD_FAILED')
    error.value = String(err?.message ?? err)
    return false
  }
}

async function reload(): Promise<void> {
  resetGraphState()
  if (!(await bootstrapSpaceId())) return
  await fetchGraph(null)
}

async function loadMore(): Promise<void> {
  if (!hasMore.value || !nextCursor.value || limitReached.value || loadingMore.value) return
  await fetchGraph(nextCursor.value)
}

/* ── 布局驱动（唯一允许重排的三个入口都在这里）────────────────────────── */

function syncLayout(): void {
  const ids = nodes.value.map((node) => nodeIdOf(node))
  const current = layout.value
  if (current && current.nodes.length) {
    // 集合变化：保留仍在集合里的节点坐标（否则每次翻页用户都会丢失方位感）。
    current.sync(ids, links.value)
  } else {
    layout.value = new ForceLayout(
      ids,
      links.value,
      forces.value,
      lowMotion.value ? { maxIterations: REDUCED_MOTION_ITERATIONS } : {},
    )
  }
  if (layout.value) {
    iterationBudget.value = layout.value.budget.maxIterations
    durationBudgetMs.value = layout.value.budget.maxDurationMs
  }
  frame.value = layout.value ? layout.value.nodes.map((node) => ({ ...node })) : []
  maybeAutoFit()
  startSimulation()
}

function startSimulation(): void {
  const current = layout.value
  if (!current) return
  if (appearance.value.paused) {
    current.stop()
    stopReason.value = current.stopReason
    return
  }
  if (lowMotion.value) {
    // 低运动：一次算完并静态呈现（仍然受 300 次 / 5 秒上限约束），不做逐帧动画。
    stopLoop()
    current.runToEnd()
    frame.value = current.nodes.map((node) => ({ ...node }))
    iterationsUsed.value = current.iterations
    stopReason.value = current.stopReason
    maybeAutoFit()
    return
  }
  startLoop()
}

function startLoop(): void {
  if (rafId != null) return
  const step = () => {
    rafId = null
    const current = layout.value
    if (!current) return
    if (current.stopped) {
      stopReason.value = current.stopReason
      return
    }
    // 每帧最多 4 次迭代：100 节点 / 300 关系上一次迭代约 0.05ms，所以一帧约 0.2ms，
    // 选择、拖拽、详情交互都不会被挡住（契约 §9.3 第 5 条）。
    const done = current.tick(4)
    iterationsUsed.value = current.iterations
    frame.value = current.nodes.map((node) => ({ ...node }))
    if (done === 0 || current.stopped) {
      stopReason.value = current.stopReason
      maybeAutoFit()
      return
    }
    rafId = requestAnimationFrame(step)
  }
  rafId = requestAnimationFrame(step)
}

function stopLoop(): void {
  if (rafId != null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
}

/** 停止模拟：收敛后自然退出、暂停、页面隐藏、离开视图、组件卸载都走它。 */
function stopSimulation(): void {
  stopLoop()
  const current = layout.value
  if (current && !current.stopped) {
    current.stop()
    stopReason.value = current.stopReason
  }
}

/* ── 视图变换（缩放 / 平移；**不**触发任何重算）────────────────────────── */

const MIN_SCALE = 0.15
const MAX_SCALE = 4

function clampScale(value: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value))
}

/**
 * 「恢复视图」：把当前布局重新装进画布。只改视图变换，**不**重排。
 *
 * 内边距是**不对称**的：右上角常驻设置面板，如果按整块画布居中，图会有一部分被面板压住。
 * 所以把可用矩形扣掉面板宽度再居中。窄画布下按比例收窄，避免可用宽度变成 0。
 */
const VIEW_PADDING = { left: 56, top: 56, bottom: 56, right: 300 }

function availableRect(): { x: number; y: number; w: number; h: number } {
  const right = Math.min(VIEW_PADDING.right, Math.round(size.value.w * 0.34))
  return {
    x: VIEW_PADDING.left,
    y: VIEW_PADDING.top,
    w: Math.max(80, size.value.w - VIEW_PADDING.left - right),
    h: Math.max(80, size.value.h - VIEW_PADDING.top - VIEW_PADDING.bottom),
  }
}

function viewCenter(): { x: number; y: number } {
  const rect = availableRect()
  return { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 }
}

function fitView(): void {
  const current = layout.value
  const rect = availableRect()
  if (!current || !current.nodes.length) {
    const center = viewCenter()
    view.value = { x: center.x, y: center.y, k: 1 }
    userAdjustedView.value = false
    return
  }
  const box = current.bounds()
  const width = Math.max(1, box.maxX - box.minX)
  const height = Math.max(1, box.maxY - box.minY)
  const k = clampScale(Math.min(1.6, rect.w / width, rect.h / height))
  const centerX = (box.minX + box.maxX) / 2
  const centerY = (box.minY + box.maxY) / 2
  view.value = { x: rect.x + rect.w / 2 - centerX * k, y: rect.y + rect.h / 2 - centerY * k, k }
  userAdjustedView.value = false
}

/** 自动适应只在用户没自己动过视图时发生（否则就是抢镜头）。 */
function maybeAutoFit(): void {
  if (!userAdjustedView.value) fitView()
}

function zoomBy(factor: number): void {
  const k = clampScale(view.value.k * factor)
  const center = viewCenter()
  view.value = {
    x: center.x - (center.x - view.value.x) * (k / view.value.k),
    y: center.y - (center.y - view.value.y) * (k / view.value.k),
    k,
  }
  userAdjustedView.value = true
}

/** 恢复视图 + 明确要求重新排布（按钮上写明两件事都做，避免用户以为只是回到中心）。 */
function resetAndRelayout(): void {
  const current = layout.value
  if (!current) return
  current.clearFixed()
  current.setParams(clampForceParams(forces.value))
  current.reheat()
  startSimulation()
}

function onWheel(event: WheelEvent): void {
  // 缩放**不**重排：只改 view 的 k 与偏移。
  event.preventDefault()
  const element = svgEl.value
  if (!element) return
  const rect = element.getBoundingClientRect()
  const factor = Math.exp(-event.deltaY * 0.0015)
  const next = clampScale(view.value.k * factor)
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  view.value = {
    x: px - (px - view.value.x) * (next / view.value.k),
    y: py - (py - view.value.y) * (next / view.value.k),
    k: next,
  }
  userAdjustedView.value = true
}

function toLayoutPoint(clientX: number, clientY: number): { x: number; y: number } {
  const rect = svgEl.value?.getBoundingClientRect()
  const px = rect ? clientX - rect.left : clientX
  const py = rect ? clientY - rect.top : clientY
  return { x: (px - view.value.x) / view.value.k, y: (py - view.value.y) / view.value.k }
}

function onBackgroundPointerDown(event: PointerEvent): void {
  if (dragging.value) return
  panning.value = { x: event.clientX, y: event.clientY, ox: view.value.x, oy: view.value.y }
  const target = event.currentTarget as Element | null
  target?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  const drag = dragging.value
  if (drag) {
    // 拖拽**只**改显示位置：不重排、不改关系、不重新取数（契约 §8.3）。
    const point = toLayoutPoint(event.clientX, event.clientY)
    layout.value?.moveNode(drag.id, point.x - drag.dx, point.y - drag.dy)
    frame.value = layout.value ? layout.value.nodes.map((node) => ({ ...node })) : []
    return
  }
  const pan = panning.value
  if (pan) {
    view.value = {
      ...view.value,
      x: pan.ox + (event.clientX - pan.x),
      y: pan.oy + (event.clientY - pan.y),
    }
    userAdjustedView.value = true
  }
}

function onPointerUp(): void {
  if (dragging.value) layout.value?.setFixed(dragging.value.id, true)
  dragging.value = null
  panning.value = null
}

function onNodePointerDown(id: string, event: PointerEvent): void {
  event.stopPropagation()
  if (event.button !== 0) return
  const point = toLayoutPoint(event.clientX, event.clientY)
  const position = layout.value?.nodeById(id)
  dragging.value = {
    id,
    dx: position ? point.x - position.x : 0,
    dy: position ? point.y - position.y : 0,
  }
  const target = event.currentTarget as Element | null
  target?.setPointerCapture?.(event.pointerId)
}

function selectById(id: string | null): void {
  selectedEntryId.value = id
  emit('select', id ? (nodeAt(id)?.entry ?? null) : null)
}

/* ── 关系文案（类型 / 方向 / 依据）────────────────────────────────────── */

function relationLabel(relation: WikiGraphRelation): string {
  // 服务端 `label` 对未支持类型给的是"未支持关系类型"——**不要**改写成最近的已知类型。
  return String(relation.label || relation.relation_type || '（无类型字段）')
}

function relationDirectionText(relation: WikiGraphRelation, focus: string | null): string {
  const src = nodeIndex.value.get(String(relation.src))
  const dst = nodeIndex.value.get(String(relation.dst))
  const srcTitle = src ? nodeTitle(src) : String(relation.src)
  const dstTitle = dst ? nodeTitle(dst) : String(relation.dst)
  const directed = relation.direction !== 'undirected'
  const head = directed
    ? `${srcTitle} → ${dstTitle}`
    : `${srcTitle} — ${dstTitle}（无向）`
  if (!focus) return head
  if (relation.src === focus) return `出边：${head}`
  if (relation.dst === focus) return `入边：${head}`
  return head
}

/** 依据（`basis`）：只展示**存在**的键，缺失就不写，不替它补默认值。 */
function relationBasisText(relation: WikiGraphRelation): string {
  const basis = relation.basis
  if (!basis || typeof basis !== 'object') return '（服务端未给依据字段）'
  const parts = Object.entries(basis)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(
      ([key, value]) =>
        `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`,
    )
  return parts.length ? parts.join(' · ') : '（依据字段存在但为空）'
}

function relationNote(relation: WikiGraphRelation): string {
  const notes: string[] = [
    `类型代码 ${relation.relation_type}；来源 ${relation.origin ?? '（未给）'}/${
      relation.raw_type ?? '（未给）'
    }`,
  ]
  if (relation.supported === false) {
    notes.push(`该关系类型未被支持：${relation.unsupported_reason ?? '未给原因'}（不改写成已知类型）`)
  }
  if (relation.rule_version) notes.push(`规则版本 ${relation.rule_version}`)
  if (relation.basis_note) notes.push(String(relation.basis_note))
  if (relation.provenance_incomplete?.applies) notes.push('该关系的出处记录不完整（服务端已声明）')
  return notes.join(' · ')
}

/* ── 提示语：每一条都对应一个真实的服务端字段，没有一句是推测 ───────────── */

const WARNING_TEXT: Record<string, string> = {
  AUTHORIZATION_FILTERED:
    '部分对象因权限被过滤：本次考虑的对象数与实际可见数不一致。“看不到”不等于“不存在”。',
  CURSOR_RESET_ON_FILTER_CHANGE: '筛选条件变化，服务端已忽略旧游标并重新返回第一批。',
  WIKI_GRAPH_LIMIT_REACHED: `已达到显示上限（${FORCE_LIMITS.maxNodes} 节点 / ${FORCE_LIMITS.maxRelations} 关系），请缩小范围。`,
}

const warningTexts = computed(() =>
  warnings.value.map((code) => WARNING_TEXT[code] ?? `服务端提示：${code}`),
)

const scopeLine = computed(
  () =>
    scopeDescription.value ||
    '当前授权范围图（不是全库全图）：只包含本次授权、当前图层、已加载的节点与关系。',
)

const loadedLine = computed(
  () =>
    `已加载 ${loaded.value.nodes} / 上限 ${limits.value.nodes} 个节点、` +
    `${loaded.value.relations} / 上限 ${limits.value.relations} 条关系` +
    '（口径：当前授权 + 当前图层 + 已加载）',
)

/** 分页 / 截断**分别**标明（契约 §5.2 第 4 条：不得混成一句）。 */
const pagingText = computed(() => {
  if (limitReached.value) {
    return `已截断：达到上限 ${limitReached.value.nodes} 节点 / ${limitReached.value.relations} 关系，不能再通过继续加载绕过${
      truncatedReason.value ? `（原因码 ${truncatedReason.value}）` : ''
    }。`
  }
  if (truncatedReason.value) return `已截断（原因码 ${truncatedReason.value}）。`
  if (hasMore.value) return '尚有后续：本页只是当前授权范围图的一部分，可以继续加载。'
  return '本页已到当前授权范围图的末尾（这不是“全库只有这些”的结论）。'
})

const demoDataLine = computed(() => {
  const flagged = nodes.value.filter((node) => node.entry?.demo_data)
  if (!flagged.length) return ''
  return `${flagged.length} 个已加载词条标记为 demo_data=true：本库数据是合成演示数据，不代表真实工厂数据、真实审批或独立准确率结论。`
})

/** 非词条图层的节点形状，契约只冻结了 `node_kind`/`node_id` + 各自载荷，这里如实说明。 */
const nonEntryLayerNote = computed(() =>
  filters.value.layer === 'entries'
    ? ''
    : '非「词条关系」图层：节点没有词条的三套状态，标题取自该图层自身的可读字段（运行问题 / 证据来源），卡片里不会伪造词条状态。',
)

const catalog = computed(() =>
  nodes.value
    .map((node) => ({ id: nodeIdOf(node), node, title: nodeTitle(node) }))
    .sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN')),
)

const statusText = computed(() => {
  if (loading.value) return '正在加载当前授权范围图…'
  if (error.value) return ''
  if (stopReason.value === 'converged') return `布局已收敛（${iterationsUsed.value} 次迭代），已停止计算。`
  if (stopReason.value === 'iterations') {
    return `布局达到迭代上限 ${iterationBudget.value} 次，已停止计算。`
  }
  if (stopReason.value === 'duration') {
    return `布局达到运行时长上限 ${durationBudgetMs.value / 1000} 秒，已停止计算。`
  }
  if (stopReason.value === 'stopped') return '布局已停止（暂停或页面不在前台）。'
  return ''
})

/* ── 生命周期与监听 ───────────────────────────────────────────────────────── */

function onVisibilityChange(): void {
  if (document.hidden) {
    // 页面隐藏 → 立即停止模拟（契约 §9.3 第 1 条）。
    stopSimulation()
    stopReason.value = 'stopped'
  }
}

function onMotionChange(event: MediaQueryListEvent | MediaQueryList): void {
  systemReducedMotion.value = event.matches
}

function measure(): void {
  const element = canvas.value
  if (!element) return
  const rect = element.getBoundingClientRect()
  if (rect.width > 40 && rect.height > 40) {
    size.value = { w: Math.round(rect.width), h: Math.round(rect.height) }
  }
}

onMounted(async () => {
  measure()
  resizeObserver = new ResizeObserver(() => measure())
  if (canvas.value) resizeObserver.observe(canvas.value)

  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  onMotionChange(motionQuery)
  motionQuery.addEventListener?.('change', onMotionChange)

  document.addEventListener('visibilitychange', onVisibilityChange)
  await reload()
})

onBeforeUnmount(() => {
  stopSimulation()
  resizeObserver?.disconnect()
  resizeObserver = null
  motionQuery?.removeEventListener?.('change', onMotionChange)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (debounceTimer != null) window.clearTimeout(debounceTimer)
})

/** 父组件换了空间 id → 重新取数。 */
watch(
  () => props.spaceId,
  (next) => {
    if (next === spaceIdResolved.value) return
    spaceIdResolved.value = next
    void reload()
  },
)

/** 图层变化 → 重新取得授权数据并**重置已加载集合**（契约 §9.2 / §4 语义 4）。 */
watch(
  () => filters.value.layer,
  () => {
    void reload()
  },
)

/**
 * 其它筛选变化 → 同样是一次新集合；关键词与分类做 320ms 去抖，避免每次按键都打服务端。
 *
 * ⚠ 依赖必须按**值**比较。筛选状态现在是**整体替换**模型对象（见 WikiGraphSettings 的
 * `patchFilters`：`filters.value = { ...filters.value, q }`），任何一项变化都会让这个
 * watcher 重新求值；而 `() => [a, b, c]` 这种数组 getter 每次都返回**新数组**，
 * `watch` 按引用比较就会一律判定"变了"，结果是换图层时上面的 watch 立刻取一次数、
 * 这里 320ms 后再取一次（同一份筛选打两次请求）。用 computed 字符串指纹把它变成
 * 按值比较：只有这四项真的变了才去抖取数。
 */
const otherFiltersKey = computed(() =>
  JSON.stringify([filters.value.type, filters.value.status, filters.value.category, filters.value.q]),
)

watch(otherFiltersKey, () => {
  if (debounceTimer != null) window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    debounceTimer = null
    void reload()
  }, 320)
})

/** 力度变化 → 布局参数变了，这是契约允许重排的三种情形之一。 */
watch(
  () => ({ ...forces.value }),
  (next) => {
    const current = layout.value
    if (!current) return
    current.setParams(clampForceParams(next))
    current.reheat()
    startSimulation()
  },
  { deep: true },
)

/** 低运动开关：从"逐帧"切到"一次算完"，或反过来。 */
watch(lowMotion, (enabled) => {
  const current = layout.value
  if (!current) return
  stopSimulation()
  if (enabled) {
    current.runToEnd()
    frame.value = current.nodes.map((node) => ({ ...node }))
    iterationsUsed.value = current.iterations
    stopReason.value = current.stopReason
    maybeAutoFit()
  } else if (!appearance.value.paused) {
    startSimulation()
  }
})

/** 暂停 / 恢复。恢复 = 用户明确要求继续，所以允许续算（不是回到初值重排）。 */
watch(
  () => appearance.value.paused,
  (paused) => {
    const current = layout.value
    if (!current) return
    if (paused) {
      stopSimulation()
      stopReason.value = 'stopped'
    } else {
      current.reheat()
      startSimulation()
    }
  },
)

defineExpose({
  /** 供页面在"从详情回到图"时显式恢复视图（只改视图，不重排） */
  fitView,
  relayout: resetAndRelayout,
  selectedEntryId,
})
</script>

<template>
  <div class="wg-shell">
    <!-- ── 图区：右上角是紧凑的可折叠设置面板 ─────────────────────────────── -->
    <div ref="canvas" class="wg-canvas" :style="{ height: props.height }">
      <svg
        ref="svgEl"
        class="wg-svg"
        :viewBox="`0 0 ${size.w} ${size.h}`"
        :width="size.w"
        :height="size.h"
        role="group"
        :aria-label="`当前授权范围图：${loaded.nodes} 个已加载节点、${loaded.relations} 条已加载关系。可以用 Tab 键在图外的词条目录里选择词条。`"
        @wheel="onWheel"
        @pointerdown="onBackgroundPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
      >
        <defs>
          <marker
            id="wg-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#334155" />
          </marker>
        </defs>

        <g :transform="`translate(${view.x},${view.y}) scale(${view.k})`">
          <!-- 细线：只画**已保存且允许访问**的关系；未支持的类型用虚线 -->
          <line
            v-for="link in renderLinks"
            :key="link.id"
            class="wg-link"
            :x1="link.x1"
            :y1="link.y1"
            :x2="link.x2"
            :y2="link.y2"
            vector-effect="non-scaling-stroke"
            :stroke="link.unsupported ? '#b45309' : '#64748b'"
            :stroke-dasharray="link.unsupported ? '3 3' : undefined"
            :stroke-width="link.emphasized ? 1.6 : 1"
            :stroke-opacity="link.dim ? Math.min(appearance.linkOpacity, 0.12) : appearance.linkOpacity"
            :marker-end="link.arrow ? 'url(#wg-arrow)' : undefined"
          />
          <!-- 关系类型标签：画在连线中点。只在小图或与选中项相连的边上出现，否则满屏文字。 -->
          <text
            v-for="link in renderLinks.filter((l) => l.label)"
            :key="`label-${link.id}`"
            class="wg-link-label"
            :x="(link.x1 + link.x2) / 2"
            :y="(link.y1 + link.y2) / 2"
            :font-size="10 / view.k"
            text-anchor="middle"
            :opacity="link.dim ? 0.25 : 1"
          >
            {{ link.label }}
          </text>

          <!-- 节点：圆点 + 标题；孤立节点（连接数 0）同样可点、可聚焦 -->
          <g
            v-for="node in renderNodes"
            :key="node.id"
            class="wg-node"
            :class="{
              'wg-node-dim': node.dim,
              'wg-node-selected': node.selected,
              'wg-node-neighbor': node.neighbor,
            }"
            tabindex="0"
            role="button"
            :aria-label="node.aria"
            @pointerdown="onNodePointerDown(node.id, $event)"
            @keydown.enter.prevent="selectById(node.id)"
            @keydown.space.prevent="selectById(node.id)"
            @click="selectById(node.id)"
          >
            <!-- 透明的点击热区：屏幕尺寸恒定，所以缩得很小时小圆点也点得中 -->
            <circle class="wg-hit" :cx="node.x" :cy="node.y" :r="Math.max(node.radius, 11 / view.k)" />
            <circle
              class="wg-dot"
              :cx="node.x"
              :cy="node.y"
              :r="node.radius"
              :fill="node.fill"
              :stroke="node.stroke"
              stroke-width="1"
              vector-effect="non-scaling-stroke"
            />
            <text
              v-if="node.showLabel"
              class="wg-label"
              :x="node.x"
              :y="node.y + node.labelOffset"
              text-anchor="middle"
              :font-size="11 / view.k"
            >
              {{ node.title }}
            </text>
            <!-- 状态标识是**文字**，不是颜色（契约 §9.2） -->
            <text
              v-if="node.markText"
              class="wg-mark"
              :x="node.x + node.radius * 0.95"
              :y="node.y - node.radius * 0.75"
              :font-size="10 / view.k"
            >
              {{ node.markText }}
            </text>
          </g>
        </g>
      </svg>

      <!-- 右上角：紧凑的可折叠设置面板（四组） -->
      <div class="wg-settings">
        <WikiGraphSettings
          v-model:filters="filters"
          v-model:colors="colors"
          v-model:appearance="appearance"
          v-model:forces="forces"
          :active-layer="activeLayer"
          :limit-reached="!!limitReached"
          :busy="loading || loadingMore"
          :legend="legend"
          :legend-note="legendNote"
          :system-reduced-motion="systemReducedMotion"
          @relayout="resetAndRelayout"
          @toggle-pause="appearance = { ...appearance, paused: !appearance.paused }"
          @reset-colors="colors = { ...DEFAULT_COLOR_STATE }"
        />
      </div>

      <!-- 图外（键盘可达）的视图控制：不拖拽也能缩放与恢复 -->
      <div class="wg-viewbar" role="group" aria-label="视图控制">
        <button type="button" class="wg-vbtn" @click="zoomBy(1.25)">放大</button>
        <button type="button" class="wg-vbtn" @click="zoomBy(0.8)">缩小</button>
        <button type="button" class="wg-vbtn" @click="fitView()">恢复视图</button>
        <span class="wg-vzoom" aria-live="polite">缩放 {{ view.k.toFixed(2) }}×</span>
      </div>

      <!-- 空态 / 加载 / 出错：三种情况分开说，绝不用"没有数据"盖住失败 -->
      <div v-if="loading" class="wg-overlay">正在加载当前授权范围图…</div>
      <div v-else-if="error" class="wg-overlay wg-overlay-error" role="alert">
        <p class="wg-overlay-title">加载失败（{{ errorCode || 'WIKI_LOAD_FAILED' }}）</p>
        <p class="wg-overlay-note">{{ error }}</p>
        <p class="wg-overlay-note">
          这不是“没有数据”：读取失败与空集合是两件事，所以这里不显示空态文案。
        </p>
        <button type="button" class="wg-vbtn" @click="reload()">重新加载</button>
      </div>
      <div v-else-if="!nodes.length" class="wg-overlay">
        <p class="wg-overlay-title">当前授权范围内没有可显示的节点</p>
        <p class="wg-overlay-note">
          服务端本次考虑 {{ authorizedScope.nodes_considered }} 个对象、返回
          {{ authorizedScope.nodes_visible }} 个。图是空的<strong>不等于</strong>知识库是空的：
          可能是筛选条件过窄、当前图层没有数据，或这些对象不在本次授权范围内
          （受权限过滤时服务端会给出 AUTHORIZATION_FILTERED 提示）。
        </p>
        <p class="wg-overlay-note">
          圆点大小只表示当前授权且已加载的连接数，位置只用于排布——两者都不代表任何结论。
        </p>
      </div>
    </div>

    <!-- ── 图外可聚焦的词条目录：无需拖拽、无需辨色也能选词条 ─────────────── -->
    <div class="wg-panels">
      <section class="wg-card" aria-labelledby="wg-catalog-title">
        <h3 id="wg-catalog-title" class="wg-card-title">
          词条目录<span class="wg-card-note">可聚焦，Enter 选择；不依赖拖拽或辨色</span>
        </h3>
        <p v-if="catalog.length" class="wg-card-note">
          目录按标题排序，共 {{ catalog.length }} 个<strong>已加载</strong>节点。每条依次是：词条类型、
          三套状态、已加载连接数，以及状态标识（文字，不是颜色）。
        </p>
        <!-- 词条目录每条去框改橙条后，再逐条淡入（Vue Bits 的 AnimatedList）。 -->
        <AnimatedList v-if="catalog.length" tag="ul" class="wg-catalog">
          <li v-for="item in catalog" :key="item.id">
            <button
              type="button"
              class="wg-catalog-btn"
              :class="{ 'wg-catalog-btn-active': item.id === selectedEntryId }"
              :aria-current="item.id === selectedEntryId ? 'true' : undefined"
              @click="selectById(item.id)"
            >
              <span class="wg-catalog-title">{{ item.title }}</span>
              <span class="wg-catalog-meta">
                <template v-if="isEntryNode(item.node)">
                  {{ entryTypeText(item.node) || '未标注类型' }} · 发布
                  {{ publicationText(item.node) }} · 核验 {{ assertionText(item.node) }} · 来源
                  {{ validityText(item.node) }}
                </template>
                <template v-else>{{ nodeKindLabel(String(item.node.node_kind)) }}</template>
                · 已加载连接 {{ loadedDegree(item.id) }}
              </span>
              <span v-if="statusMarks(item.node).length" class="wg-catalog-marks">
                {{ statusMarks(item.node).map((mark) => `${mark.glyph} ${mark.text}`).join('；') }}
              </span>
            </button>
          </li>
        </AnimatedList>
        <p v-else class="wg-card-note">当前授权范围内没有已加载的节点，目录为空。</p>
      </section>

      <section class="wg-card" aria-labelledby="wg-selection-title">
        <h3 id="wg-selection-title" class="wg-card-title">
          选中的词条<span class="wg-card-note">打开详情不会改变当前视野与选中状态</span>
        </h3>
        <template v-if="selectedNode">
          <p class="wg-sel-title">{{ nodeTitle(selectedNode) }}</p>
          <p class="wg-card-note">{{ selectedNode.node_id }}</p>
          <template v-if="selectedEntry">
            <dl class="wg-kv">
              <dt>词条类型</dt>
              <dd>{{ entryTypeText(selectedNode) || '未标注类型' }}</dd>
              <dt>分类</dt>
              <dd>{{ selectedEntry.category || '（未分类）' }}</dd>
              <dt>发布流程状态</dt>
              <dd>{{ publicationText(selectedNode) }}</dd>
              <dt>核验状态</dt>
              <dd>{{ assertionText(selectedNode) }}</dd>
              <dt>来源有效性</dt>
              <dd>{{ validityText(selectedNode) }}</dd>
              <dt>已加载连接数</dt>
              <dd>{{ loadedDegree(selectedNode.node_id) }}（当前授权 + 当前图层 + 已加载）</dd>
              <dt>证据引用数</dt>
              <dd>{{ selectedEntry.evidence_ref_count }}</dd>
              <dt>词条自报的可见关系数</dt>
              <dd>
                {{ selectedEntry.relation_counts?.visible ?? '（字段缺失）' }}
                （口径 {{ selectedEntry.relation_counts?.scope ?? '（缺失）' }}；
                该词条层面是否被截断：{{
                  selectedEntry.relation_counts?.truncated == null
                    ? '（字段缺失）'
                    : selectedEntry.relation_counts.truncated
                      ? '是'
                      : '否'
                }}）
              </dd>
              <dt>版本数</dt>
              <dd>{{ selectedEntry.revision_count }}</dd>
              <dt>演示数据</dt>
              <dd>{{ selectedEntry.demo_data ? '是（demo_data=true）' : '否' }}</dd>
            </dl>
            <p v-if="statusMarks(selectedNode).length" class="wg-card-note">
              状态标识：{{
                statusMarks(selectedNode)
                  .map((mark) => `${mark.glyph} ${mark.text}`)
                  .join('；')
              }}
            </p>
            <p class="wg-card-note">
              三套状态来自服务端三个独立字段，<strong>不合并</strong>成一个“总状态”；
              上面每一项都能在详情与溯源里逐条核对。
            </p>
            <div class="wg-actions">
              <button type="button" class="wg-vbtn" @click="emit('open-entry', selectedNode.node_id)">
                打开详情
              </button>
              <a
                class="wg-vbtn"
                :href="`/v1/wiki/entries/${encodeURIComponent(selectedNode.node_id)}/export`"
              >
                导出 Markdown
              </a>
            </div>
          </template>
          <p v-else class="wg-card-note">
            该节点不是词条（node_kind={{ selectedNode.node_kind }}），没有词条的三套状态；
            这里只显示它在当前图里的已加载连接数。
          </p>

          <h4 class="wg-sub-title">关系（{{ selectedRelations.length }} 条，来自已加载集合）</h4>
          <ul v-if="selectedRelations.length" class="wg-relations">
            <li v-for="relation in selectedRelations" :key="relation.relation_id" class="wg-relation">
              <p class="wg-relation-head">
                {{ relationLabel(relation) }}
                <span class="wg-relation-dir">{{ relationDirectionText(relation, selectedEntryId) }}</span>
              </p>
              <p class="wg-relation-note">{{ relationNote(relation) }}</p>
              <p class="wg-relation-note">依据：{{ relationBasisText(relation) }}</p>
              <button
                type="button"
                class="wg-linkbtn"
                @click="selectById(relation.src === selectedEntryId ? relation.dst : relation.src)"
              >
                选中另一端
              </button>
            </li>
          </ul>
          <p v-else class="wg-card-note">
            这个节点在<strong>已加载集合</strong>里没有关系。它是一个孤立节点：仍然可见、可点击；
            也可能只是它的关系还没被加载（有后续时可以继续加载）。
          </p>
        </template>
        <p v-else class="wg-card-note">
          还没有选中词条。点击图上的圆点，或在上面的目录里选一个。选中后这里会列出关系类型、
          方向与依据。
        </p>
      </section>

      <section class="wg-card" aria-labelledby="wg-scope-title">
        <h3 id="wg-scope-title" class="wg-card-title">范围与边界</h3>
        <p class="wg-card-note wg-scope">{{ scopeLine }}</p>
        <p class="wg-card-note">{{ loadedLine }}</p>
        <p class="wg-card-note">{{ pagingText }}</p>
        <p v-if="nonEntryLayerNote" class="wg-card-note">{{ nonEntryLayerNote }}</p>
        <p v-if="demoDataLine" class="wg-card-note">{{ demoDataLine }}</p>
        <ul v-if="warningTexts.length" class="wg-warn-list">
          <li v-for="text in warningTexts" :key="text">{{ text }}</li>
        </ul>
        <p v-if="asOf" class="wg-card-note wg-meta">数据时刻（服务端 as_of）：{{ asOf }}</p>
        <p v-if="statusText" class="wg-card-note wg-meta">{{ statusText }}</p>
        <div v-if="hasMore && !limitReached" class="wg-actions">
          <button type="button" class="wg-vbtn" :disabled="loadingMore" @click="loadMore()">
            {{ loadingMore ? '加载中…' : '继续加载下一批' }}
          </button>
        </div>

        <h4 class="wg-sub-title">这些视觉编码各表示什么</h4>
        <ul class="wg-legend-notes">
          <li>
            <strong>圆点大小</strong>只表示当前授权且已加载的连接数（有上下限的缩放）；
            <strong>不</strong>表示置信度、权威或全库连接数。
          </li>
          <li>
            <strong>细线</strong>代表实际已保存且允许访问的关系；未支持的关系类型用虚线并在清单里写明。
          </li>
          <li>
            <strong>空间距离与布局位置</strong>只用于排布，<strong>不</strong>代表语义得分、可靠性或因果关系。
          </li>
          <li>
            <strong>颜色</strong>（启用颜色组后）只区分词条类型或分类；冲突与核验状态另用文字标识表达，不靠颜色。
          </li>
          <li><strong>力度设置</strong>只改变布局，不改变任何关系或审核结果。</li>
          <li>本图是<strong>当前授权范围图</strong>，不是全库全图；分页与截断状态分别标注在上面。</li>
        </ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* ⚠ 两条本仓库踩过的坑，这里都规避了：
   1. 类名全部 `wg-` 前缀（`wg-shell` / `wg-canvas` / `wg-dot` …）。Tailwind 工具类
      里没有 `wg-` 开头的，所以不会重演 `.collapse`（visibility:collapse）、
      `.table-caption`（display:table-caption）那类"工具类接管未声明属性"的事故。
   2. 不用 `.static` / `.collapse` / `.table-*` 这类与工具类同名的词。 */

.wg-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
  background: var(--color-void);
  color: var(--color-text);
}

.wg-canvas {
  position: relative;
  width: 100%;
  background: #ffffff;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  overflow: hidden;
  touch-action: none;
  /* 设置面板的宽度只在这里写一次：面板自己的 width 用 var() 读它，
     空态覆盖层的右侧留白也用它 —— 两处同一个数才不会互相追着改。 */
  --wg-settings-width: 300px;
}

.wg-svg {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
  background: #ffffff;
}

.wg-svg:active {
  cursor: grabbing;
}

.wg-link {
  stroke-linecap: round;
}
/* 连线上的关系类型文字：压小、灰、不挡线。 */
.wg-link-label {
  fill: #64748b;
  pointer-events: none;
  user-select: none;
}

.wg-node {
  cursor: pointer;
}

.wg-node:focus-visible {
  outline: none;
}

.wg-node:focus-visible .wg-dot {
  stroke: var(--color-accent);
  stroke-width: 2.5;
}

.wg-dot {
  transition: fill 0.15s ease, stroke 0.15s ease;
}

.wg-node-dim {
  opacity: 0.38;
}

.wg-node-selected .wg-dot {
  stroke: var(--color-accent);
  stroke-width: 2.5;
}

.wg-node-neighbor .wg-dot {
  stroke: #1e293b;
  stroke-width: 1.6;
}

.wg-hit {
  fill: transparent;
  pointer-events: all;
}

.wg-label {
  fill: var(--color-body);
  paint-order: stroke;
  stroke: #ffffff;
  stroke-width: 3px;
  stroke-linejoin: round;
  pointer-events: none;
  user-select: none;
}

.wg-node-selected .wg-label,
.wg-node-neighbor .wg-label {
  fill: var(--color-text);
  font-weight: 600;
}

.wg-mark {
  fill: #b45309;
  pointer-events: none;
  user-select: none;
}

.wg-settings {
  position: absolute;
  top: 10px;
  right: 10px;
  /* 同时给 top 与 bottom：面板的 max-height:100% 才有确定的参照高度，
     否则面板可能比画布更高、被 overflow:hidden 裁掉最后一组（力度）而无法展开。 */
  bottom: 10px;
  z-index: 3;
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  max-width: calc(100% - 20px);
  /* 容器不挡画布：只有面板本体接收指针事件，面板下方的空白仍然可以拖拽/框选。 */
  pointer-events: none;
}

.wg-settings > * {
  pointer-events: auto;
}

.wg-viewbar {
  position: absolute;
  bottom: 10px;
  left: 10px;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--color-line);
  border-radius: 8px;
}

.wg-vzoom {
  font-size: 12.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

/* 视图缩放按钮：与其它按钮统一成蓝底白字加粗椭圆（原来是白底细描边） */
.wg-vbtn {
  padding: 3px 10px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  text-decoration: none;
  cursor: pointer;
}

.wg-vbtn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}

.wg-vbtn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wg-vbtn:disabled {
  color: var(--color-muted);
  cursor: not-allowed;
}

.wg-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px 32px;
  /* 空态 / 失败 / 加载文字**不许被右上角浮层盖住**：浮层是绝对定位、层级更高（z-index 3），
     它占了右侧 --wg-settings-width，所以覆盖层在右侧让出同样的宽度再居中。
     实测：改前 1280×900 下正文右边缘 1005px 压到浮层左边缘 973px 底下 32px，
     1024×900 下 40% 的上限又不够、还差 10px —— 所以上限放到 50%（面板本身要 310px，
     半屏以内都还让得出来），超过半屏才停手，那时句子已经贴着浮层左边了。 */
  padding-right: min(calc(var(--wg-settings-width, 300px) + 30px), 50%);
  text-align: center;
  background: rgba(255, 255, 255, 0.94);
}

/* 覆盖层标题与其它页统一：蓝色、15px、600（出错态由下面一条改成红字，保留判据色） */
.wg-overlay-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wg-overlay-note {
  margin: 0;
  max-width: 62ch;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--color-body);
}

.wg-overlay-error .wg-overlay-title {
  color: #b91c1c;
}

/* 单栏：三张卡片依次整宽排列。
   原来三栏（或调过权重的 1 : 0.85 : 1.35）都太窄 —— 卡片里既有大段中文说明，
   又有原始技术字段（wev-xxx / body_sha256）和 markdown 表格原文，
   窄栏里每 4~5 个字就折一次行，整页像一堵碎字墙。这些内容本来就需要宽度。
   与尺寸页同样改成单栏。 */
.wg-panels {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}

.wg-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 18px;
  background: var(--color-panel);
  border: 1px solid var(--color-line);
  border-radius: 12px;
}

/* 卡片标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.wg-card-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wg-card-note {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.72;
  color: var(--color-text);
}
/* 次要信息（数据时刻、布局迭代次数等）：压小一档，不与主结论抢注意力。 */
.wg-meta {
  font-size: 12.5px;
  color: var(--color-body);
}

.wg-scope {
  color: var(--color-text);
  font-weight: 500;
}

/* 子标题与其它页统一：蓝色、15px、600（原来是 13.5px 灰黑，属离群值） */
.wg-sub-title {
  margin: 6px 0 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

/* 选中词条标题：蓝色、15px、600 */
.wg-sel-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wg-catalog {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 去框：词条目录每条原来是「浅灰底 + 圆角」的卡片，改成左侧橙色细条。橙色沿用本页「边界 / 需注意」的语义。 */
.wg-catalog-btn {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 6px 8px 6px 14px;
  border: 0;
  border-left: 2px solid #d97706;
  border-radius: 0;
  background: none;
  color: var(--color-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.wg-catalog-btn:hover {
  border-left-color: #b45309;
  background: rgba(217, 119, 6, 0.05);
}

.wg-catalog-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

/* 选中项：左条换成蓝色并加浅蓝底 —— 去框之后需要一个和默认橙条不同的记号。 */
.wg-catalog-btn-active {
  border-left-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.07);
}
/* 选中项底色变浅蓝之后，里面的橙色状态标识会显得脏 —— 跟着一起换蓝。 */
.wg-catalog-btn-active .wg-catalog-marks {
  color: var(--color-accent);
}

.wg-catalog-title {
  font-size: 14px;
  font-weight: 600;
}

.wg-catalog-meta {
  font-size: 12.5px;
  color: var(--color-body);
}

.wg-catalog-marks {
  font-size: 12.5px;
  color: #b45309;
}

.wg-kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 10px;
  margin: 0;
  font-size: 13px;
}

.wg-kv dt {
  color: var(--color-muted);
}

.wg-kv dd {
  margin: 0;
  color: var(--color-text);
}

.wg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.wg-relations {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 280px;
  overflow-y: auto;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wg-relation {
  padding: 6px 8px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
}

.wg-relation-head {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
}

.wg-relation-dir {
  display: block;
  font-size: 12.5px;
  font-weight: 400;
  color: var(--color-body);
}

.wg-relation-note {
  margin: 2px 0 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--color-text);
  word-break: break-word;
}

.wg-linkbtn {
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-accent);
  font-size: 12.5px;
  font-family: inherit;
  text-decoration: underline;
  cursor: pointer;
}

.wg-linkbtn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wg-warn-list {
  margin: 0;
  padding-left: 16px;
  font-size: 13px;
  line-height: 1.6;
  color: #b45309;
}

.wg-legend-notes {
  margin: 0;
  padding-left: 16px;
  font-size: 13px;
  line-height: 1.65;
  color: var(--color-body);
}

@media (prefers-reduced-motion: reduce) {
  .wg-dot {
    transition: none;
  }
}
</style>