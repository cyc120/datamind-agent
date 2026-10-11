<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { EvidenceNode, EvidenceEdge, NodeType } from '../../api/types'

const props = withDefaults(
  defineProps<{
    nodes: EvidenceNode[]
    edges: EvidenceEdge[]
    interactive?: boolean
    width?: number
    height?: number
  }>(),
  { interactive: true, width: 660, height: 430 },
)

const emit = defineEmits<{ (e: 'select', node: EvidenceNode | null): void }>()

/* ============================================================================
 * 推理星链：卡片式节点 + 贝塞尔连线的分层流程图（参考 Dify 工作流画布的观感）
 *
 * 旧版是「圆点 + 文字标签 + 直线」的径向星座图，问题有两个：
 *   1. 圆点上的信息量几乎为零，看不出这条证据是什么；
 *   2. 径向布局在窄栏（主界面右栏约 490px）里会把同层节点甩到画布外，
 *      48 个证据点（工业核验那条问题实测 48 节点 / 66 边）必然重叠。
 *
 * 现在改成有向分层图：
 *   · 分层 = 以 Claim（答案主张）为根的 BFS 深度，边方向 src → dst 视作「依赖」；
 *   · 同层节点按前一层邻居的重心（barycenter）排序，三遍来回，减少连线交叉；
 *   · 同层节点在带内网格换行排列（不是甩成一个圆环），行内居中；
 *   · 每个节点是一张卡片：彩色类型徽标 + 标题 + 2–4 行键值；
 *   · 连线是三次贝塞尔（竖直出 / 竖直入），端点带小圆点 handle；
 *   · 背景是很淡的点阵网格。
 *
 * 组件通过 width/height 传的是「期望画布尺寸」，实际画布按容器实测宽度重算：
 * 卡片宽度不是硬编码的，节点多的时候列数会被撑到 3–4 列，行高不变、整体变高，
 * 由 .sg-shell 负责纵向滚动 —— 48 张卡片也不会重叠或裁切。
 * ========================================================================== */

const TYPE_STYLE: Record<NodeType, { color: string; label: string; icon: string }> = {
  // 浅色主题：600 级深色，白底上对比度足够；与落地页图例的取值保持一致。
  Claim: { color: '#d97706', label: '答案主张', icon: '✦' },
  SQLQuery: { color: '#0284c7', label: 'SQL 查询', icon: '⌗' },
  SQLRow: { color: '#0891b2', label: '结果行', icon: '▦' },
  Document: { color: '#7c3aed', label: '文档', icon: '▤' },
  Chunk: { color: '#4f46e5', label: '检索片段', icon: '¶' },
  DocumentFact: { color: '#0d9488', label: '字段', icon: '⌗' },
  VerificationCheck: { color: '#16a34a', label: '核验', icon: '✓' },
  ConflictRecord: { color: '#e11d48', label: '冲突', icon: '⚡' },
  AuthorityRule: { color: '#ca8a04', label: '权威规则', icon: '§' },
}

const EDGE_STYLE: Record<string, { dash: string; color: string }> = {
  derived_from: { dash: '', color: 'rgba(100,116,139,0.42)' },
  supports: { dash: '4 3', color: 'rgba(2,132,199,0.50)' },
  retrieved_from: { dash: '1 5', color: 'rgba(79,70,229,0.48)' },
  resolved_by: { dash: '2 2', color: 'rgba(202,138,4,0.55)' },
}

/* ── 字段中文名：与 NodeDetail 的 FIELD_LABEL 取同一套叫法，避免同一字段两处两个名 ── */
const FIELD_LABEL: Record<string, string> = {
  batch_id: '批次',
  part_id: '零件',
  measurement_id: '测量',
  operation_no: '工序',
  metric: '指标',
  value: '实测',
  unit: '单位',
  normalized_value: '归一',
  normalized_unit: '归一单位',
  report_value: '报告',
  report_unit: '报告单位',
  compliance: '合规',
  agreement: '一致性',
  measured_at: '测量时间',
  produced_at: '生产时间',
  method: '方法',
  quality_method: '质检方法',
  quality_id: '质检',
  report_id: '报告',
  recorded_revision: '记录版本',
  applicable_revision: '适用版本',
  raw_value: '原文',
  raw_unit: '原文单位',
  raw_header: '表头',
  entity_name: '实体',
  entity_key: '实体',
  fact_kind: '字段类型',
  rule_id: '规则',
  rule_version: '规则版本',
  conflict_type: '冲突类型',
  resolution_status: '状态',
  left_value: '左值',
  right_value: '右值',
  page_start: '起始页',
  page_end: '结束页',
  document_version: '版本',
  retrieval_score: '检索分',
}

type Row = { k: string; v: string; mono?: boolean }

function fmtValue(v: any): string {
  if (v == null || v === '') return ''
  if (Array.isArray(v)) return v.map((x) => (x && typeof x === 'object' ? '…' : String(x))).join(' / ')
  if (typeof v === 'object') {
    const parts = Object.entries(v)
      .filter(([, x]) => x != null && x !== '')
      .map(([k, x]) => `${k}=${x}`)
    return parts.join(' ')
  }
  return String(v)
}

function withUnit(value: any, unit: any): string {
  const v = fmtValue(value)
  if (!v) return ''
  const u = fmtValue(unit)
  return u ? `${v} ${u}` : v
}

function pageRange(p: any): string {
  const s = p.page_start ?? p.page_end
  if (s == null) return ''
  const e = p.page_end ?? s
  return e !== s ? `第 ${s}–${e} 页` : `第 ${s} 页`
}

/** 标签值：把常见的机器枚举翻成中文，未知值原样返回 */
const LABEL_VALUE: Record<string, Record<string, string>> = {
  compliance: { within_range: '范围内', out_of_range: '超限', unknown: '未知', insufficient: '证据不足' },
  agreement: {
    consistent: '一致',
    resolved_equivalent: '等价',
    needs_review: '待复核',
    insufficient_evidence: '证据不足',
    unresolved: '未决',
  },
  resolution_status: {
    resolved_by_authority: '按权威规则裁定',
    needs_review: '待复核',
    unresolved: '未决',
  },
  fact_kind: { requirement: '工艺要求', measurement: '质检测量' },
}
function labelValue(k: string, v: any): string {
  const s = fmtValue(v)
  if (!s) return ''
  return LABEL_VALUE[k]?.[s] ?? s
}

function truncate(s: string, n: number): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

/** 中文正文用比例字体，ID / 数字 / 哈希用等宽字体 */
const TEXT_KEYS = new Set([
  '主张',
  'SQL',
  '标题',
  '摘录',
  'raw_header',
  'metric',
  'method',
  '来源',
  'source',
  'entity_name',
])

/**
 * 卡片正文：只挑最能说明这条证据的 2–4 行。
 *
 * 字段名一律照抄真实 payload（NodeDetail 里那份实测结论），特别注意：
 *   SQLRow 的 payload 本身就是一行（扁平键，没有 columns / rows）；
 *   DocumentFact 没有 value / unit / entity / header，是 raw_value / raw_unit / entity_name / raw_header；
 *   Document 工业侧给 source_sha256、RAG 侧给 origin + document_sha256，两边都要认；
 *   ConflictRecord 的双方原值在 normalized_values 里，left/right_fact_refs 只是引用 id。
 * 上面这些键一个都不存在时，兜底取最先出现的 2 个标量字段，绝不留一张空卡片。
 */
function buildRows(n: EvidenceNode, dense: boolean): Row[] {
  const p: Record<string, any> = (n.payload ?? {}) as Record<string, any>
  const out: Row[] = []
  const at = (k: string, v: any, unit?: any) => {
    const text = unit !== undefined ? withUnit(v, unit) : labelValue(k, v)
    if (text) out.push({ k, v: text, mono: !TEXT_KEYS.has(k) })
  }
  switch (n.node_type) {
    case 'Claim': {
      const t = p.text ?? p.claim ?? p.statement
      if (t) at('主张', truncate(String(t), dense ? 70 : 116))
      break
    }
    case 'SQLQuery': {
      if (p.sql) at('SQL', truncate(String(p.sql), dense ? 58 : 120))
      const bp = p.bound_params
      if (bp) at('绑定参数', Array.isArray(bp) ? bp.join(' · ') : bp)
      break
    }
    case 'SQLRow': {
      // payload 本身就是一行结果（扁平键），没有 columns / rows
      at('measurement_id', p.measurement_id)
      at('value', p.value, p.unit)
      if (out.length < 3) at('metric', p.metric)
      if (out.length < 3) at('batch_id', p.batch_id)
      break
    }
    case 'Document': {
      if (p.title) at('标题', truncate(String(p.title), dense ? 22 : 40))
      at('source', p.origin ?? (p.source_sha256 ? '工业入库' : ''))
      const h = p.source_sha256 ?? p.document_sha256
      if (h) out.push({ k: '哈希', v: `${String(h).slice(0, 8)}…`, mono: true })
      break
    }
    case 'Chunk': {
      at('title', p.title)
      if (p.quote) at('摘录', truncate(String(p.quote), dense ? 60 : 108))
      const pg = pageRange(p)
      if (pg) at('page', pg)
      break
    }
    case 'DocumentFact': {
      at('entity_name', p.entity_name)
      if (p.raw_value != null) at('raw_value', p.raw_value, p.raw_unit)
      if (out.length < 3) at('metric', p.metric)
      if (out.length < 3) at('fact_kind', p.fact_kind)
      if (out.length < 3) at('method', p.method)
      break
    }
    case 'VerificationCheck': {
      at('compliance', p.compliance)
      at('agreement', p.agreement)
      at('measurement_id', p.measurement_id)
      if (p.value != null) at('value', p.value, p.unit)
      else if (p.normalized_value != null) at('normalized_value', p.normalized_value, p.normalized_unit)
      if (out.length < 3) at('report_value', p.report_value, p.report_unit)
      break
    }
    case 'ConflictRecord': {
      at('conflict_type', p.conflict_type)
      at('metric', p.metric)
      const nv = p.normalized_values
      if (nv && typeof nv === 'object') {
        for (const [k, v] of Object.entries(nv)) {
          if (out.length >= 4) break
          if (v != null && v !== '') out.push({ k, v: fmtValue(v), mono: true })
        }
      }
      if (out.length < 3) at('resolution_status', p.resolution_status)
      if (out.length < 3) at('entity_key', p.entity_key)
      break
    }
    case 'AuthorityRule': {
      at('rule_id', p.rule_id)
      at('rule_version', p.rule_version)
      at('source', p.registration?.selected_source)
      if (out.length < 3) at('approval_status', p.approval_status)
      break
    }
  }
  if (!out.length) {
    // 兜底：payload 里最先出现的两个标量字段，保证卡片永远有内容
    for (const [k, v] of Object.entries(p)) {
      if (out.length >= 2) break
      if (v == null || typeof v === 'object') continue
      out.push({ k, v: truncate(String(v), 40), mono: !TEXT_KEYS.has(k) })
    }
  }
  return out.slice(0, dense ? 2 : 3)
}

/* ── 密度与尺寸 ──
 * 少节点 → 卡片大、行多、留白足；多节点 → 卡片紧凑、只留 2 行，靠列数与纵向滚动换可读性。
 * 卡宽不是拍脑袋的常量：先由节点数定列数，再由容器实测宽度反推卡宽（minClamp～maxCard）。
 */
const maxRows = computed(() => (props.nodes.length <= 8 ? 3 : 2))

/** 旧版按较短边铺开的径向半径系数，分层布局不再使用；width/height 只作为
 *  首帧容器宽度未知时的兜底期望尺寸，实际画布按容器实测宽度重算。 */
void props.width
void props.height

const shellEl = ref<HTMLElement | null>(null)
const shellW = ref(0)
let ro: ResizeObserver | null = null

onMounted(() => {
  const el = shellEl.value
  if (!el) return
  shellW.value = el.clientWidth || 0
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      const w = el.clientWidth || 0
      // 只在宽度真的变了才写，避免与滚动条互相触发
      if (Math.abs(w - shellW.value) > 1) shellW.value = w
    })
    ro.observe(el)
  }
})
onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
})

interface Placed {
  id: string
  x: number
  y: number
  w: number
  h: number
  layer: number
}
interface SgEdge {
  key: string
  d: string
  sx: number
  sy: number
  tx: number
  ty: number
  color: string
  dash: string
  tx_id: string
}

/** 分层 + 层内重心排序。派生的两类边反向，环按 DFS 打断。 */
function layerize(nodes: EvidenceNode[], edges: EvidenceEdge[]) {
  const ids = new Set(nodes.map((n) => n.evidence_id))
  const key = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`)
  const seen = new Set<string>()
  // [边原本的 src, dst, edge_type]；同一对节点只保留第一条
  const kept: [string, string, string][] = []
  for (const e of edges) {
    const s = e.src_evidence_id
    const t = e.dst_evidence_id
    if (!ids.has(s) || !ids.has(t) || s === t) continue
    const k = key(s, t)
    if (seen.has(k)) continue
    seen.add(k)
    kept.push([s, t, e.edge_type ?? ''])
  }

  /**
   * 流程方向 = 证据的产生方向（上游 → 下游）。
   * 边的方向是「src 由 dst 派生而来」（真实图里 ConflictRecord --derived_from--> VerificationCheck、
   * DocumentFact --derived_from--> Document），恰好和流程方向相反：直接按边方向分层会把
   * 冲突记录、字段顶到最上面（实测 48 节点工业图：L0 的 15 个节点里混进了 5 个冲突）。
   * 所以 derived_from / retrieved_from 这两类「派生」边反向，supports 等语义正向保留。
   */
  const orient = ([s, t, etype]: [string, string, string]): [string, string] =>
    etype === 'supports' || etype === 'resolved_by' ? [s, t] : [t, s]

  const succ = new Map<string, string[]>()
  ids.forEach((id) => succ.set(id, []))
  for (const e of kept) {
    const [a, b] = orient(e)
    succ.get(a)!.push(b)
  }

  // 环打断：DFS 里遇到仍在栈上的回边，就把它当作反向依赖（避免 longest-path 死循环）
  const mark = new Map<string, number>()
  const broken = new Set<string>()
  const walk = (start: string) => {
    const stack: Array<[string, number]> = [[start, 0]]
    mark.set(start, 1)
    while (stack.length) {
      const top = stack[stack.length - 1]
      const list = succ.get(top[0])!
      if (top[1] >= list.length) {
        mark.set(top[0], 2)
        stack.pop()
        continue
      }
      const nb = list[top[1]++]
      const m = mark.get(nb) ?? 0
      if (m === 1) {
        broken.add(`${top[0]}>${nb}`)
        continue
      }
      if (m === 0) {
        mark.set(nb, 1)
        stack.push([nb, 0])
      }
    }
  }
  for (const n of nodes) if ((mark.get(n.evidence_id) ?? 0) === 0) walk(n.evidence_id)

  const into = new Map<string, string[]>()
  const outof = new Map<string, string[]>()
  ids.forEach((id) => {
    into.set(id, [])
    outof.set(id, [])
  })
  for (const e of kept) {
    let [a, b] = orient(e)
    // 回边反向（上面 DFS 用的就是 orient 之后的图，所以这里要按 orient 后的方向查）
    if (broken.has(`${a}>${b}`)) {
      const tmp = a
      a = b
      b = tmp
    }
    into.get(b)!.push(a)
    outof.get(a)!.push(b)
  }

  // longest-path 分层（Kahn）：层号 = 从根出发的最长依赖链
  const indeg = new Map<string, number>()
  ids.forEach((id) => indeg.set(id, into.get(id)!.length))
  const layerOf = new Map<string, number>()
  ids.forEach((id) => layerOf.set(id, 0))
  const queue = nodes.map((n) => n.evidence_id).filter((id) => (indeg.get(id) ?? 0) === 0)
  for (let i = 0; i < queue.length; i++) {
    const cur = queue[i]
    for (const nb of outof.get(cur)!) {
      layerOf.set(nb, Math.max(layerOf.get(nb) ?? 0, (layerOf.get(cur) ?? 0) + 1))
      const d = (indeg.get(nb) ?? 1) - 1
      indeg.set(nb, d)
      if (d === 0) queue.push(nb)
    }
  }

  /**
   * 源头节点（SQLQuery / Document）下压到最后一层。
   *
   * 它们是「派生」链的最底端（SQLRow ← SQLQuery、DocumentFact ← Document），
   * 在 Kahn 里没有前驱、本来会被留在第 0 层，于是 SQL 和文档跟答案主张挤在同一行、
   * 而「结果行 / 字段」被推到它们下面，读起来是倒的。
   * 实测 48 节点工业图：不压时 L0 混着 10 个主张 + 1 个 SQLQuery + 1 个文档；
   * 压到底后变成 L0 主张 10 → L1 结果行 10 → L2 字段 11 →（核验 10）→ L4 源头 2，
   * 层数与 4 层相同，但方向与语义、与真实 payload 的派生关系一致。
   */
  const SOURCE_TYPES = new Set(['SQLQuery', 'Document'])
  const maxLayerSoFar = nodes.reduce((m, n) => Math.max(m, layerOf.get(n.evidence_id) ?? 0), 0)
  for (const n of nodes) {
    if (SOURCE_TYPES.has(n.node_type)) layerOf.set(n.evidence_id, maxLayerSoFar)
  }

  const layers = new Map<number, EvidenceNode[]>()
  for (const n of nodes) {
    const l = layerOf.get(n.evidence_id) ?? 0
    if (!layers.has(l)) layers.set(l, [])
    layers.get(l)!.push(n)
  }
  const order = [...layers.keys()].sort((a, b) => a - b)
  const indexIn = (l: number) => {
    const m = new Map<string, number>()
    ;(layers.get(l) ?? []).forEach((n, i) => m.set(n.evidence_id, i))
    return m
  }
  // 重心排序：三遍「下行 / 上行」，让相邻两层的连线段尽量不交叉
  for (let pass = 0; pass < 3; pass++) {
    const down = pass % 2 === 0
    const seq = down ? order : [...order].reverse()
    for (const l of seq) {
      const group = layers.get(l)!
      if (group.length < 2) continue
      const ref = indexIn(down ? l - 1 : l + 1)
      if (!ref.size) continue
      const score = new Map<string, number>()
      group.forEach((n, i) => {
        const nbs = down ? into.get(n.evidence_id)! : outof.get(n.evidence_id)!
        const vals = nbs.map((x) => ref.get(x)).filter((x): x is number => x !== undefined)
        const bary = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : i
        score.set(n.evidence_id, bary)
      })
      group.sort((a, b) => (score.get(a.evidence_id)! - score.get(b.evidence_id)!))
    }
  }
  return { layers, order }
}

const layout = computed(() => {
  const nodes = props.nodes
  if (!nodes.length) {
    return {
      placed: [] as Placed[],
      edges: [] as SgEdge[],
      near: new Map<string, Set<string>>(),
      cw: 0,
      ch: 0,
      rows: maxRows.value,
    }
  }
  // 实测宽度优先；首帧拿到 0 时退回调用方给的期望宽（无头截图第一帧就可能是这种情况）
  const avail = Math.max(shellW.value || props.width || 520, 260)
  const rowsPerCard = maxRows.value
  const dense = nodes.length > 8
  const total = nodes.length

  // 列数：先按节点数给个舒服的列数，再被容器宽度压回去（卡宽不小于 138px）
  const baseCols = total <= 4 ? total : total <= 9 ? 3 : total <= 20 ? 3 : total <= 40 ? 4 : 5
  const gapX = dense ? 12 : 16
  const gapY = dense ? 14 : 20
  // 画布左右内边距。必须 ≤ gapX，否则「列数按容器宽算、画布再加两侧内边距」会
  // 让画布比容器内容盒宽出 2*padX——实测就是这样多出 20px，
  // 让十几张卡越过了容器右边（卡行居中所以看不出来，但 scrollWidth 是实打实超了）。
  const padX = dense ? 10 : 12
  const padY = dense ? 12 : 16
  const usable = Math.max(180, avail - padX * 2)
  const maxCols = Math.max(1, Math.min(baseCols, Math.floor((usable + gapX) / (138 + gapX))))

  const { layers, order } = layerize(nodes, props.edges)
  const rowCount = (l: number) => Math.ceil((layers.get(l)?.length ?? 0) / maxCols)

  // 卡宽：全局统一（各层同宽），至少 138，最多 244；由「减去内边距后的可用宽」反推，
  // 保证 maxCols 张卡 + 列间距 + 两侧内边距一定塞得进 avail。
  const cw = Math.min(244, Math.max(138, Math.floor((usable - (maxCols - 1) * gapX) / maxCols)))
  const contentW = () => maxCols * cw + (maxCols - 1) * gapX

  // 卡高必须和 CSS 里的 --sg-pad-y/--sg-head/--sg-row/--sg-rowgap 完全一致：
  // 2*(6px padding + 1px border) + 15px 标题行 + N*18px 键值行 + (N-1)*3px 行距
  const CARD_BOX = 14
  const HEAD_H = 15
  const ROW_H = 18
  const ROW_GAP = 3
  const cardH = (n: EvidenceNode) => {
    const rows = buildRows(n, dense).length
    return CARD_BOX + HEAD_H + rows * ROW_H + Math.max(0, rows - 1) * ROW_GAP
  }
  const bandH = new Map<number, number>()
  for (const l of order) {
    const group = layers.get(l)!
    let h = 0
    const rc = rowCount(l)
    for (let r = 0; r < rc; r++) {
      const slice = group.slice(r * maxCols, (r + 1) * maxCols)
      h = Math.max(h, ...slice.map(cardH))
    }
    bandH.set(l, h)
  }

  const gapBand = dense ? 30 : 42
  // 画布宽度不再额外加内边距（padX 已经在 usable 里扣过），只在容器比内容宽时补足，
  // 这样画布永远不会超过容器的内容盒宽度。
  const cwTotal = Math.max(contentW() + padX * 2, avail)

  // 自下而上算每层的起始 y，末尾就不会剩一段算不准的空白
  const bandTop = new Map<number, number>()
  let acc = padY
  for (const l of order) {
    bandTop.set(l, acc)
    acc += bandH.get(l)! * rowCount(l) + (rowCount(l) - 1) * gapY + gapBand
  }
  const ch = acc - gapBand + padY

  const placed: Placed[] = []
  const byId = new Map<string, Placed>()
  // 列位置用一条全局栅格：各行都从同一列起点排，竖着的「车道」才不会随行内节点数左右漂移
  const colStart = Math.max(0, (cwTotal - contentW()) / 2)
  for (const l of order) {
    const group = layers.get(l)!
    const band = bandH.get(l) ?? 60
    const top = bandTop.get(l) ?? 0
    group.forEach((n, i) => {
      const col = i % maxCols
      const row = Math.floor(i / maxCols)
      const x = colStart + col * (cw + gapX)
      const p: Placed = { id: n.evidence_id, x, y: top + row * (band + gapY), w: cw, h: cardH(n), layer: l }
      placed.push(p)
      byId.set(p.id, p)
    })
  }
  const list: SgEdge[] = []
  const ekey = new Set<string>()
  const near = new Map<string, Set<string>>()
  const link = (a: string, b: string) => {
    if (!near.has(a)) near.set(a, new Set())
    near.get(a)!.add(b)
  }
  for (const e of props.edges) {
    const s = byId.get(e.src_evidence_id)
    const t = byId.get(e.dst_evidence_id)
    if (!s || !t) continue
    // 同一对节点只画一条线：路径完全相同，重复画只会让线更深
    const k = e.src_evidence_id < e.dst_evidence_id
      ? `${e.src_evidence_id}|${e.dst_evidence_id}`
      : `${e.dst_evidence_id}|${e.src_evidence_id}`
    if (ekey.has(k)) continue
    ekey.add(k)
    link(e.src_evidence_id, e.dst_evidence_id)
    link(e.dst_evidence_id, e.src_evidence_id)
    // 端点选在卡片朝向对方的那条边上，曲线自然从边缘出发、到边缘结束。
    // 只比较竖直位置会把同层（左右相邻）的边判成「目标在上」而向上绕一大圈，
    // 所以先看水平重叠：重叠再用上下，不重叠一律水平出入。
    const xOverlap = Math.min(s.x + s.w, t.x + t.w) - Math.max(s.x, t.x) > 4
    const goingDown = t.y > s.y + s.h / 2
    let sx: number
    let sy: number
    let c1x: number
    let c1y: number
    let tx: number
    let ty: number
    let c2x: number
    let c2y: number
    if (xOverlap) {
      sx = s.x + s.w / 2
      sy = goingDown ? s.y + s.h : s.y
      tx = t.x + t.w / 2
      ty = goingDown ? t.y : t.y + t.h
      const dy = Math.max(18, Math.abs(ty - sy) * 0.5)
      c1x = sx
      c1y = sy + (goingDown ? dy : -dy)
      c2x = tx
      c2y = ty + (goingDown ? -dy : dy)
    } else {
      const tRight = t.x + t.w / 2 > s.x + s.w / 2
      sx = tRight ? s.x + s.w : s.x
      sy = s.y + s.h / 2
      tx = tRight ? t.x : t.x + t.w
      ty = t.y + t.h / 2
      const dx = Math.max(22, Math.abs(tx - sx) * 0.5)
      c1x = sx + (tRight ? dx : -dx)
      c1y = sy
      c2x = tx + (tRight ? -dx : dx)
      c2y = ty
    }
    const st = EDGE_STYLE[e.edge_type] ?? { dash: '', color: 'rgba(100,116,139,0.42)' }
    list.push({
      key: k,
      d: `M ${sx} ${sy} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${tx} ${ty}`,
      sx,
      sy,
      tx,
      ty,
      color: st.color,
      dash: st.dash,
      tx_id: e.dst_evidence_id,
    })
  }
  return { placed, edges: list, near, cw: cwTotal, ch, rows: rowsPerCard }
})

const nodeById = computed(() => {
  const m = new Map<string, EvidenceNode>()
  props.nodes.forEach((n) => m.set(n.evidence_id, n))
  return m
})
/** 点阵节距跟着卡宽走：卡片小的时候点也密一点，视觉密度才稳 */
const dotGap = computed(() => ((layout.value.placed[0]?.w ?? 200) < 190 ? 14 : 18))

const selectedId = ref<string | null>(null)
const hoveredId = ref<string | null>(null)

function styleOf(n: EvidenceNode | undefined) {
  return (n && TYPE_STYLE[n.node_type]) || TYPE_STYLE.Claim
}
function cardStyle(p: Placed) {
  return {
    left: `${p.x}px`,
    top: `${p.y}px`,
    width: `${p.w}px`,
    height: `${p.h}px`,
  }
}
function rowsOf(n: EvidenceNode): Row[] {
  return buildRows(n, layout.value.rows < 3)
}
/** 悬停某张卡时，与之无连线关系的卡淡出，链路更清楚 */
function isConnected(id: string): boolean {
  const h = hoveredId.value
  if (!h || h === id) return true
  return layout.value.near.get(h)?.has(id) ?? false
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

// 同一页可能同时挂两个实例（落地页演示图 + 主界面实时图），defs 里的 pattern id 必须唯一
let sgSeq = 0
const dotId = `sg-dots-${(sgSeq = (sgSeq + 1) % 1000)}-${Math.random().toString(36).slice(2, 7)}`
</script>

<template>
  <div ref="shellEl" class="sg-shell">
    <div class="sg-canvas" :style="{ width: layout.cw + 'px', height: layout.ch + 'px' }">
      <!-- 点阵网格：很淡的圆点，铺满整块画布 -->
      <svg class="sg-grid" :width="layout.cw" :height="layout.ch" aria-hidden="true">
        <defs>
          <pattern :id="dotId" :width="dotGap" :height="dotGap" patternUnits="userSpaceOnUse">
            <circle :cx="dotGap * 0.3" :cy="dotGap * 0.3" r="1.3" fill="rgba(15,23,42,0.26)" />
          </pattern>
        </defs>
        <rect x="0" y="0" :width="layout.cw" :height="layout.ch" :fill="`url(#${dotId})`" />
      </svg>

      <!-- 连线：三次贝塞尔 + 端点小圆点 -->
      <svg class="sg-line" :width="layout.cw" :height="layout.ch" aria-hidden="true">
        <g v-for="e in layout.edges" :key="e.key">
          <path :d="e.d" fill="none" :stroke="e.color" :stroke-dasharray="e.dash" stroke-width="1.4" stroke-linecap="round" />
          <circle :cx="e.sx" :cy="e.sy" r="2.4" :fill="e.color" />
          <circle :cx="e.tx" :cy="e.ty" r="2.4" :fill="e.color" />
        </g>
      </svg>

      <!-- 卡片节点 -->
      <div
        v-for="p in layout.placed"
        :key="p.id"
        class="sg-card"
        :class="{
          'is-selected': selectedId === p.id,
          'is-dim': !!hoveredId && hoveredId !== p.id && !isConnected(p.id),
        }"
        :style="cardStyle(p)"
        :data-evidence-id="p.id"
        :role="interactive ? 'button' : undefined"
        :tabindex="interactive ? 0 : undefined"
        @click="onSelect(p.id)"
        @keydown.enter.prevent="onSelect(p.id)"
        @mouseenter="hoveredId = p.id"
        @mouseleave="hoveredId = null"
      >
        <div class="sg-head">
          <span class="sg-badge" :style="{ background: styleOf(nodeById.get(p.id)).color }" aria-hidden="true">
            {{ styleOf(nodeById.get(p.id)).icon }}
          </span>
          <span class="sg-title">{{ styleOf(nodeById.get(p.id)).label }}</span>
          <span v-if="p.w >= 190" class="sg-ref">{{ nodeById.get(p.id)?.ref_key }}</span>
        </div>
        <dl class="sg-rows">
          <div v-for="(r, i) in rowsOf(nodeById.get(p.id)!)" :key="i" class="sg-row">
            <dt>{{ r.k }}</dt>
            <dd :class="{ 'is-mono': r.mono !== false }" :title="r.v">{{ r.v }}</dd>          </div>
        </dl>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 滚动容器：主界面主区的证据视图高度受限，48 张卡片比容器高，靠它纵向滚动；
   落地页没有高度约束时它就是自然高度。
   scrollbar-gutter: stable 是关键：滚动条出现/消失会改变 clientWidth，
   而卡宽正是由 clientWidth 反推的——不留槽就会「变宽→出滚动条→变窄→…」抖动。
   overflow-x: clip（不是 hidden）把横向滚动彻底关掉：hidden 仍然是一个可滚动的
   溢出容器，差半个滚动条宽度就会多出一条横向滚动条，clip 不会。
   卡行在画布里居中，左右各有 padX 内边距，差十几像素也不会被裁。 */
.sg-shell {
  width: 100%;
  max-width: 100%;
  overflow-x: clip;
  overflow-y: auto;
  scrollbar-gutter: stable;
  overscroll-behavior: contain;
  border-radius: 12px;
  background: #ffffff;
}
.sg-canvas {
  position: relative;
}
.sg-grid,
.sg-line {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
}
.sg-grid {
  z-index: 0;
}
.sg-line {
  z-index: 1;
  overflow: visible;
}

/* ── 卡片盒模型：JS 里的 cardH() 必须和这里的每个数字对齐 ──
   （之前 cardH 估的是 32 + 行数*17 + 4，实际渲染 36 / 53 / 70，带高对不上，
   行数一多就会在层里留下肉眼可见的缝。现在卡高全部由这几个常量决定，
   并且卡片写死 height + overflow:hidden，估算值就是渲染值。） */
.sg-card {
  --sg-pad-y: 6px;
  --sg-pad-x: 8px;
  --sg-head: 15px;
  --sg-row: 18px;
  --sg-rowgap: 3px;
  position: absolute;
  z-index: 2;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: var(--sg-rowgap);
  padding: var(--sg-pad-y) var(--sg-pad-x);
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid rgba(15, 23, 42, 0.12);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  overflow: hidden;
  cursor: pointer;
  animation: sg-card-in 0.34s ease-out backwards;
  transition: box-shadow 0.15s ease, border-color 0.15s ease, opacity 0.15s ease;
}
.sg-card:hover {
  border-color: rgba(37, 99, 235, 0.45);
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.10);
}
.sg-card.is-selected {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.22), 0 4px 14px rgba(15, 23, 42, 0.10);
}
.sg-card.is-dim {
  opacity: 0.42;
}
/* 入场只淡入，不用 opacity:0 做 backwards 填充的终点——
   动画一旦不播放（无头渲染 / 降低动效）卡片会永久不可见，旧版踩过这个坑。 */
@keyframes sg-card-in {
  from {
    opacity: 0.2;
  }
  to {
    opacity: 1;
  }
}
.sg-head {
  flex-shrink: 0;
  height: var(--sg-head);
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}
.sg-badge {
  flex-shrink: 0;
  width: 14px;
  height: 14px;
  border-radius: 4px;
  color: #ffffff;
  font-size: 9px;
  line-height: 14px;
  text-align: center;
}
.sg-title {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sg-ref {
  flex-shrink: 0;
  max-width: 92px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 9px;
  color: var(--color-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sg-rows {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sg-rowgap);
  min-width: 0;
}
.sg-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  height: var(--sg-row);
  font-size: 9.5px;
  line-height: calc(var(--sg-row) - 2px);
  overflow: hidden;
}
.sg-row dt {
  flex-shrink: 0;
  max-width: 62px;
  color: var(--color-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sg-row dd {
  flex: 1;
  min-width: 0;
  margin: 0;
  text-align: right;
  color: var(--color-body);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 值里既有数字/哈希/枚举（等宽更整齐）也有中文正文（等宽会很怪），
   所以只有明确标记为 mono 的行才用等宽字体。 */
.sg-row dd.is-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

@media (prefers-reduced-motion: reduce) {
  .sg-card {
    animation: none;
    transition: none;
  }
}
</style>
