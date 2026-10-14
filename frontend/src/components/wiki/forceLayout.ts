/* ══════════════════════════════════════════════════════════════════════════
 * Wiki 知识图谱的力导向布局：**自研，不引 d3 / 不用 matter-js**（契约 §9.4 已拍板）。
 *
 * 本文件是**纯逻辑**：不碰 DOM、不 import Vue、不 import 任何依赖，所以它能被
 *   1) 组件按帧驱动（`tick(4)`，不阻塞选择/拖拽/详情），
 *   2) node 直接运行（`node --experimental-strip-types`）做纯逻辑测试。
 *
 * 三条必须守住的边界（契约 §9.3，冻结值，**不允许调用方放宽**）：
 *   · 单次显示节点 ≤ 100、关系 ≤ 300（超出由服务端截断；这里再兜一层）；
 *   · 迭代 ≤ 300 次**或**运行 ≤ 5 秒，**先到即停**；
 *   · 收敛 / 暂停 / 页面隐藏 / 离开图视图 → 停止模拟；只有节点关系集合变化、
 *     布局参数变化、用户明确恢复时才重新排布（缩放/平移/打开详情**不**重排）。
 *
 * 数值上的两个刻意选择：
 *   · 坐标系是**与画布无关的虚拟空间**，原点在质心。缩放、平移、容器 resize 都只动
 *     视图变换，不触碰这里的坐标 —— 这样"缩放/平移不触发全图重新计算"是结构上成立的，
 *     而不是靠约定。
 *   · 初始位置由**节点 id 排序后**的种子伪随机数生成（黄金角螺旋 + 抖动），因此
 *     同一份输入永远得到同一份布局（契约 §9.4 把"确定性"列为自研的代价之一）。
 * ══════════════════════════════════════════════════════════════════════════ */

/** 契约 §9.3 的四条硬边界。`as const` 让测试可以逐字断言，避免有人悄悄改数值。 */
export const FORCE_LIMITS = {
  /** 单次显示节点上限 */
  maxNodes: 100,
  /** 单次显示关系上限 */
  maxRelations: 300,
  /** 迭代次数上限 */
  maxIterations: 300,
  /** 运行时长上限（毫秒） */
  maxDurationMs: 5000,
} as const

/** 低运动偏好（`prefers-reduced-motion: reduce`）下的短时排布预算：一次算完，之后不再动。 */
export const REDUCED_MOTION_ITERATIONS = 120

/** 圆点半径的硬上下限（契约 §9.2「有上下限的缩放」）。 */
export const RADIUS_BOUNDS = { min: 3, max: 11 } as const
/** 倍率缩放下仍然成立的绝对下界：孤立节点**始终可见可点击**（契约 §9.2）。 */
export const MIN_VISIBLE_RADIUS = 2.2
/** 半径参考度的下限：本次集合里度数普遍很小时（例如全是孤立节点）不至于人人顶格。 */
export const RADIUS_REFERENCE_FLOOR = 4

export interface ForceLink {
  /** 关系 id（服务端 `relation_id`） */
  id: string
  /** 起点节点 id（服务端 `relation.src`） */
  source: string
  /** 终点节点 id（服务端 `relation.dst`） */
  target: string
}

export interface LayoutNode {
  id: string
  /** 当前**授权且已加载**的连接数；圆点大小只由它决定（契约 §9.2） */
  degree: number
  x: number
  y: number
  vx: number
  vy: number
  /** 半径由 degree 推出，已夹在 RADIUS_BOUNDS 内 */
  radius: number
  /** 被用户拖拽固定：拖拽只改显示位置，不改变任何知识关系 */
  fixed: boolean
}

export type ForceStopReason = 'converged' | 'iterations' | 'duration' | 'stopped' | null

/* ── 力度参数（契约 §8.3 的第四组设置）──────────────────────────────────────
   四个标量，各自有界、可恢复默认值。它们**只改变布局**，不改变任何关系或审核结论：
   本文件里没有任何一处会写回节点/关系数据，这是结构上的保证。 */

export interface ForceParams {
  /** 排斥力：节点间的库仑式斥力系数（Fruchterman–Reingold 的 k²/d） */
  repulsion: number
  /** 连接拉力：弹簧系数，作用于 d²/k（越大越快被拉回理想长度） */
  linkStrength: number
  /** 连接长度：弹簧的自然长度 k（布局单位，不是像素） */
  linkDistance: number
  /** 向中心聚合的程度：指向原点的向心力系数 */
  centerGravity: number
}

export const FORCE_PARAM_BOUNDS = {
  repulsion: { min: 0.2, max: 3, step: 0.05 },
  linkStrength: { min: 0.02, max: 1, step: 0.02 },
  linkDistance: { min: 20, max: 200, step: 2 },
  centerGravity: { min: 0, max: 0.3, step: 0.01 },
} as const

export const DEFAULT_FORCE_PARAMS: ForceParams = {
  repulsion: 1,
  linkStrength: 0.12,
  linkDistance: 40,
  centerGravity: 0.06,
}

/** 落在区间内；NaN/Infinity 一律回落到默认值（不静默变成 0）。 */
function finiteOr(value: unknown, fallback: number): number {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : fallback
}

function clampTo(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function clampForceParams(input: Partial<ForceParams> | null | undefined): ForceParams {
  const source = input ?? {}
  const out = {} as ForceParams
  for (const key of Object.keys(FORCE_PARAM_BOUNDS) as (keyof ForceParams)[]) {
    const bounds = FORCE_PARAM_BOUNDS[key]
    out[key] = clampTo(finiteOr(source[key], DEFAULT_FORCE_PARAMS[key]), bounds.min, bounds.max)
  }
  return out
}

export function isDefaultForceParams(params: Partial<ForceParams> | null | undefined): boolean {
  const clamped = clampForceParams(params)
  return (Object.keys(DEFAULT_FORCE_PARAMS) as (keyof ForceParams)[])
    .every((key) => Math.abs(clamped[key] - DEFAULT_FORCE_PARAMS[key]) < 1e-9)
}

/* ── 外观 / 颜色 / 筛选：类型、默认值与边界（纯数据，供两个组件共用一份真相）──
   放在这里而不是各自组件里，是为了让"默认值 / 边界"只有一处定义，
   测试也不必去解析 .vue 文件。 */

/** 契约 §5.2 的三个图层。 */
export type WikiLayer = 'entries' | 'evidence' | 'runs'

export interface WikiFilterState {
  layer: WikiLayer
  /** 词条类型（空串 = 不限） */
  type: string
  /** 分类（空串 = 不限） */
  category: string
  /** **发布流程**状态（空串 = 不限）；核验状态与来源有效性是另外两套，不在这个筛选项里 */
  status: string
  /** 关键词（空串 = 不限） */
  q: string
}

export const DEFAULT_FILTERS: WikiFilterState = {
  layer: 'entries',
  type: '',
  category: '',
  status: '',
  q: '',
}

/** 契约 §5.1 的四个词条类型与四个发布状态（顺序即下拉框顺序）。 */
export const ENTRY_TYPE_OPTIONS = [
  { value: 'business_entity', label: '业务实体' },
  { value: 'metric', label: '指标' },
  { value: 'norm', label: '规范' },
  { value: 'conclusion', label: '结论' },
] as const

export const PUBLICATION_STATUS_OPTIONS = [
  { value: 'draft', label: '草稿' },
  { value: 'in_review', label: '待复核' },
  { value: 'published', label: '已发布' },
  { value: 'archived', label: '已归档' },
] as const

export const LAYER_OPTIONS = [
  { value: 'entries', label: '词条关系' },
  { value: 'evidence', label: '来源证据' },
  { value: 'runs', label: '运行轨迹' },
] as const

export interface WikiColorState {
  /** 是否启用颜色组；关闭 = 初始的深浅灰（契约 §9.2） */
  enabled: boolean
  /** 按词条类型或按分类着色 */
  by: 'type' | 'category'
}

export const DEFAULT_COLOR_STATE: WikiColorState = { enabled: false, by: 'type' }

export interface WikiAppearanceState {
  /** 标题显示 */
  showLabels: boolean
  /** 圆点尺寸缩放（在 RADIUS_BOUNDS 之上的整体倍率） */
  dotScale: number
  /** 细线透明度 */
  linkOpacity: number
  /** 低运动模式（也由系统 prefers-reduced-motion 自动置位） */
  lowMotion: boolean
  /** 暂停布局：暂停后节点不再持续移动（契约 §9.3 第 6 条） */
  paused: boolean
}

export const APPEARANCE_BOUNDS = {
  dotScale: { min: 0.6, max: 1.8, step: 0.1 },
  linkOpacity: { min: 0.08, max: 0.9, step: 0.02 },
} as const

export const DEFAULT_APPEARANCE: WikiAppearanceState = {
  showLabels: true,
  dotScale: 1,
  // 低对比度细线（契约 §9.1）：0.4 在白底上是"看得见但不抢戏"的一档。
  linkOpacity: 0.4,
  lowMotion: false,
  paused: false,
}

/** 圆点尺寸缩放：先夹边界，再用于 `dotRadius`（后者还会再夹一次绝对上下限）。 */
export function clampDotScale(value: unknown): number {
  return clampTo(
    finiteOr(value, DEFAULT_APPEARANCE.dotScale),
    APPEARANCE_BOUNDS.dotScale.min,
    APPEARANCE_BOUNDS.dotScale.max,
  )
}

export function clampLinkOpacity(value: unknown): number {
  return clampTo(
    finiteOr(value, DEFAULT_APPEARANCE.linkOpacity),
    APPEARANCE_BOUNDS.linkOpacity.min,
    APPEARANCE_BOUNDS.linkOpacity.max,
  )
}

/* ── 连接数与圆点半径 ─────────────────────────────────────────────────────── */

/**
 * 每个节点在**传入的关系集合**里的连接数。
 *
 * 为什么前端还要自己数一遍：服务端在**每一批**里给的 `degree` 是按"截至那一批的已加载
 * 关系"算的（`wiki_query.graph_batch` → `degrees_of(ids_after, relations_after)`），
 * 第一批节点拿到的是小集合上的度数，翻到第二批之后它已经偏小。图里同时显示两批节点时，
 * 只有对"并集关系"重算才等于契约 §4 语义 7 的口径：**当前授权 + 当前图层 + 已加载**。
 * 两端只要有一端不在节点集合里就一律不计数（服务端也不会返回半条边）。
 */
export function degreesOf(
  nodeIds: readonly string[],
  links: readonly ForceLink[],
): Map<string, number> {
  const degrees = new Map<string, number>()
  for (const id of nodeIds) degrees.set(id, 0)
  for (const link of links) {
    if (degrees.has(link.source)) degrees.set(link.source, (degrees.get(link.source) ?? 0) + 1)
    if (degrees.has(link.target)) degrees.set(link.target, (degrees.get(link.target) ?? 0) + 1)
  }
  return degrees
}

/**
 * 连接数 → 半径。**只**由连接数决定，与置信度/权威/语义得分无关（契约 §9.2）。
 *
 * 用 `sqrt(degree / reference)`：视觉上"面积 ∝ 连接数"，比线性更不容易让高度数节点
 * 吃掉整个画布。`degree = 0`（孤立节点）得到下限半径 —— 孤立节点**仍然可见可点击**。
 * 结果恒被夹在 `[MIN_VISIBLE_RADIUS, RADIUS_BOUNDS.max * 1.5]` 内，
 * 所以无论 `dotScale` 取到什么值，"有上下限"都成立。
 */
export function dotRadius(degree: number, reference: number, scale = 1): number {
  const d = Math.max(0, finiteOr(degree, 0))
  const ref = Math.max(RADIUS_REFERENCE_FLOOR, finiteOr(reference, RADIUS_REFERENCE_FLOOR))
  const ratio = Math.min(1, Math.sqrt(d / ref))
  const base = RADIUS_BOUNDS.min + (RADIUS_BOUNDS.max - RADIUS_BOUNDS.min) * ratio
  const bound = RADIUS_BOUNDS.max * 1.5
  return clampTo(base * clampDotScale(scale), MIN_VISIBLE_RADIUS, bound)
}

/* ── 确定性初始位置 ───────────────────────────────────────────────────────── */

/** 32 位字符串哈希（FNV-1a）：只用来做种子，不承担任何安全用途。 */
function hashString(text: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** 种子伪随机数（mulberry32）：同一 seed 永远同一串数，因此布局可复现。 */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 由节点 id 集合导出种子：**先排序**，所以与调用方传入的顺序无关。 */
export function layoutSeed(nodeIds: readonly string[]): number {
  const sorted = [...nodeIds].map(String).sort()
  return hashString(`${sorted.length}\u0001${sorted.join('\u0002')}`)
}

/* ── 模拟器 ───────────────────────────────────────────────────────────────── */

export interface ForceLayoutOptions {
  /** 迭代上限；**不得超过** `FORCE_LIMITS.maxIterations` */
  maxIterations?: number
  /** 时长上限（毫秒）；**不得超过** `FORCE_LIMITS.maxDurationMs` */
  maxDurationMs?: number
  /** 判定收敛：单次迭代里最大的合力位移小于它即算力平衡，停止 */
  convergeEpsilon?: number
  /** 时间源（测试注入假时钟用）；默认 performance.now，缺失时退化成 Date.now */
  now?: () => number
}

function defaultNow(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now()
}

function clampBudget(value: unknown, hardMax: number, fallback: number): number {
  const n = finiteOr(value, fallback)
  if (n <= 0) return 0
  return Math.min(hardMax, n)
}

function uniqueSorted(nodeIds: readonly string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of nodeIds) {
    const id = String(raw)
    if (seen.has(id)) continue
    seen.add(id)
    out.push(id)
  }
  out.sort()
  return out
}

/**
 * 有界力导向模拟器。
 *
 * 组件按这个顺序用它：
 *   const layout = new ForceLayout(ids, links, params, options)
 *   requestAnimationFrame 循环里：`if (layout.tick(4) === 0) 停掉循环`
 *   页面隐藏 / 组件卸载：`layout.stop()`
 *   用户点"暂停"：`layout.stop()`（坐标原样保留）；点"恢复布局"：`layout.reheat()` 再开循环
 *   节点/关系集合变化：`layout.sync(ids, links)`（保留已有坐标，再按同一条预算续算）
 *   力度参数变化：`layout.setParams(params); layout.reheat()`
 *   拖拽：`layout.moveNode(id, x, y)` —— **不**触发任何重排
 */
export class ForceLayout {
  readonly nodes: LayoutNode[] = []

  private links: ForceLink[] = []
  private params: ForceParams
  private readonly maxIterations: number
  private readonly maxDurationMs: number
  private readonly convergeEpsilon: number
  private readonly clock: () => number
  private stepLimitValue: number

  private iterationCount = 0
  private running = true
  private reason: ForceStopReason = null
  private startedAt = 0
  private durationMs = 0

  /** 因上限被丢掉的输入规模，供上层如实显示"本图只画了前 N 个" */
  readonly droppedNodes: number
  readonly droppedLinks: number

  constructor(
    nodeIds: readonly string[],
    links: readonly ForceLink[],
    params: Partial<ForceParams> = {},
    options: ForceLayoutOptions = {},
  ) {
    this.params = clampForceParams(params)
    this.maxIterations = clampBudget(
      options.maxIterations,
      FORCE_LIMITS.maxIterations,
      FORCE_LIMITS.maxIterations,
    )
    this.maxDurationMs = clampBudget(
      options.maxDurationMs,
      FORCE_LIMITS.maxDurationMs,
      FORCE_LIMITS.maxDurationMs,
    )
    this.convergeEpsilon = Math.max(0.01, finiteOr(options.convergeEpsilon, 0.5))
    this.clock = options.now ?? defaultNow
    this.stepLimitValue = Math.max(24, this.params.linkDistance)

    const unique = uniqueSorted(nodeIds)
    this.droppedNodes = Math.max(0, unique.length - FORCE_LIMITS.maxNodes)
    const keptIds = unique.slice(0, FORCE_LIMITS.maxNodes)
    const kept = new Set(keptIds)

    // 关系按 id 排序后再算：浮点加法不满足结合律，排过序才能保证
    // 「同一组关系、无论调用方以什么顺序传入」得到逐位相同的坐标。
    this.links = links
      .filter((link) => kept.has(link.source) && kept.has(link.target))
      .slice(0, FORCE_LIMITS.maxRelations)
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    this.droppedLinks = Math.max(0, links.length - this.links.length)

    this.nodes.push(...this.seedNodes(keptIds, new Map()))
    this.resetClock()
  }

  /** 确定性初值：黄金角螺旋（各向同性、没有"全挤在一侧"的病态初值）+ 确定性抖动。 */
  private seedNodes(keptIds: readonly string[], previous: Map<string, LayoutNode>): LayoutNode[] {
    const degrees = degreesOf(keptIds, this.links)
    const reference = Math.max(1, ...keptIds.map((id) => degrees.get(id) ?? 0))
    const random = mulberry32(layoutSeed(keptIds))
    const golden = Math.PI * (3 - Math.sqrt(5))
    const spread = Math.max(28, this.params.linkDistance * 0.9)
    const out: LayoutNode[] = []
    keptIds.forEach((id, index) => {
      const degree = degrees.get(id) ?? 0
      const radius = dotRadius(degree, reference)
      const old = previous.get(id)
      if (old) {
        // 已有坐标原样保留：翻页/续算不该让用户的方位感归零。
        old.degree = degree
        old.radius = radius
        out.push(old)
        return
      }
      const angle = index * golden
      const distance = spread * Math.sqrt(index + 0.35)
      const jitter = (random() - 0.5) * spread * 0.35
      out.push({
        id,
        degree,
        x: Math.cos(angle) * distance + jitter,
        y: Math.sin(angle) * distance + jitter,
        vx: 0,
        vy: 0,
        radius,
        fixed: false,
      })
    })
    return out
  }

  get iterations(): number {
    return this.iterationCount
  }

  get elapsedMs(): number {
    return this.durationMs
  }

  get stopped(): boolean {
    return !this.running
  }

  get stopReason(): ForceStopReason {
    return this.reason
  }

  get converged(): boolean {
    return this.reason === 'converged'
  }

  /** 上限的只读快照（测试与界面说明共用它，避免两处各写一份数字）。 */
  get budget(): {
    maxIterations: number
    maxDurationMs: number
    remainingIterations: number
    remainingMs: number
  } {
    return {
      maxIterations: this.maxIterations,
      maxDurationMs: this.maxDurationMs,
      remainingIterations: Math.max(0, this.maxIterations - this.iterationCount),
      remainingMs: Math.max(0, this.maxDurationMs - this.durationMs),
    }
  }

  get forceParams(): ForceParams {
    return { ...this.params }
  }

  get linkList(): readonly ForceLink[] {
    return this.links
  }

  /** 本片段内被截断的输入（>0 时界面必须说明"只画了前 N 个"）。 */
  get truncatedInput(): { nodes: number; links: number } {
    return { nodes: this.droppedNodes, links: this.droppedLinks }
  }

  private resetClock(): void {
    this.startedAt = this.clock()
    this.durationMs = 0
  }

  /**
   * 跑一批迭代（组件每帧调它，所以它必须"小步快跑"）。
   * 返回本批**实际**执行的迭代数；0 表示已经停了，循环该退出。
   */
  tick(batch = 4): number {
    if (!this.running) return 0
    const wanted = Math.max(1, Math.floor(finiteOr(batch, 1)))
    let done = 0
    for (let i = 0; i < wanted; i += 1) {
      if (this.iterationCount >= this.maxIterations) {
        this.finish('iterations')
        break
      }
      this.durationMs = Math.max(0, this.clock() - this.startedAt)
      if (this.durationMs >= this.maxDurationMs) {
        this.finish('duration')
        break
      }
      const displacement = this.iterate()
      this.iterationCount += 1
      done += 1
      if (displacement < this.convergeEpsilon) {
        this.finish('converged')
        break
      }
    }
    // 刚好跑满最后一次（迭代到顶）时也要把状态落成"已停"。
    if (this.iterationCount >= this.maxIterations && this.running) this.finish('iterations')
    return done
  }

  /**
   * 一次算完（低运动模式的静态排布）。**同样**受 300 次 / 5 秒约束：
   * 低运动不是"可以多算"，只是"不逐帧动"。
   */
  runToEnd(): number {
    let total = 0
    while (this.running) {
      const done = this.tick(16)
      total += done
      if (done === 0) break
    }
    return total
  }

  /** 停止模拟（暂停 / 页面隐藏 / 离开图视图 / 卸载）。坐标原样保留，不是重置。 */
  stop(): void {
    if (!this.running) return
    this.running = false
    if (this.reason == null) this.reason = 'stopped'
  }

  /**
   * 用户**明确**要求恢复布局（或力度参数变化后重新排布）。
   * 重新给一次完整预算（300 次 / 5 秒），但保留当前坐标作为热启动，所以画面不会跳回初值。
   */
  reheat(): void {
    this.iterationCount = 0
    this.running = true
    this.reason = null
    this.resetClock()
  }

  /** 布局参数变化（契约允许重排的三种情形之一）。返回夹过边界的参数。 */
  setParams(params: Partial<ForceParams>): ForceParams {
    this.params = clampForceParams({ ...this.params, ...params })
    this.stepLimitValue = Math.max(24, this.params.linkDistance)
    return { ...this.params }
  }

  /** 拖拽专用：只改显示位置，**不**重排、**不**改任何关系。 */
  moveNode(id: string, x: number, y: number): void {
    const node = this.nodeById(id)
    if (!node) return
    node.x = finiteOr(x, node.x)
    node.y = finiteOr(y, node.y)
    node.vx = 0
    node.vy = 0
  }

  /** 拖拽结束后钉住该节点，避免后续续算把它拉回去。 */
  setFixed(id: string, fixed: boolean): void {
    const node = this.nodeById(id)
    if (!node) return
    node.fixed = fixed
    if (fixed) {
      node.vx = 0
      node.vy = 0
    }
  }

  clearFixed(): void {
    for (const node of this.nodes) node.fixed = false
  }

  nodeById(id: string): LayoutNode | undefined {
    return this.nodes.find((node) => node.id === id)
  }

  /**
   * 节点/关系集合变化（契约允许重排的三种情形之一）。
   * **保留仍在集合里的节点坐标**，只给新节点安排初值；被移除的节点直接删掉。
   */
  sync(nodeIds: readonly string[], links: readonly ForceLink[]): void {
    const previous = new Map(this.nodes.map((node) => [node.id, node]))
    const unique = uniqueSorted(nodeIds)
    const keptIds = unique.slice(0, FORCE_LIMITS.maxNodes)
    const kept = new Set(keptIds)
    this.links = links
      .filter((link) => kept.has(link.source) && kept.has(link.target))
      .slice(0, FORCE_LIMITS.maxRelations)
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))

    const next = this.seedNodes(keptIds, previous)
    this.nodes.length = 0
    this.nodes.push(...next)
    this.iterationCount = 0
    this.reason = null
    this.resetClock()
  }

  /** 当前布局的外接盒（组件据此做一次性"适应视图"；之后缩放平移都不再重算布局）。 */
  bounds(): { minX: number; minY: number; maxX: number; maxY: number } {
    if (!this.nodes.length) return { minX: 0, minY: 0, maxX: 0, maxY: 0 }
    let minX = Number.POSITIVE_INFINITY
    let minY = Number.POSITIVE_INFINITY
    let maxX = Number.NEGATIVE_INFINITY
    let maxY = Number.NEGATIVE_INFINITY
    for (const node of this.nodes) {
      minX = Math.min(minX, node.x - node.radius)
      minY = Math.min(minY, node.y - node.radius)
      maxX = Math.max(maxX, node.x + node.radius)
      maxY = Math.max(maxY, node.y + node.radius)
    }
    return { minX, minY, maxX, maxY }
  }

  private finish(reason: Exclude<ForceStopReason, null>): void {
    this.running = false
    this.reason = reason
  }

  /**
   * 一次迭代 = 三种力 + 位移上限（温度）。返回本轮**未夹之前**的最大合力位移，
   * 它就是收敛判据：力平衡时它趋近 0。
   *
   *   斥力（所有节点对，O(n²)）：f = repulsion · k² / d，d 有下限，防除零炸掉；
   *   拉力（每条关系）：f = linkStrength · d² / k（FR 原式：长程拉得动、短程不推挤）；
   *   向心（每个节点）：f = centerGravity · |p|，指向原点。
   *
   * 位移夹在 `温度 = stepLimit · alpha` 内，alpha 随迭代线性降到 0.005。
   */
  private iterate(): number {
    const nodes = this.nodes
    const count = nodes.length
    const params = this.params
    const k = params.linkDistance
    const kSquared = k * k
    const minDistance = Math.max(1, k * 0.08)
    const alpha = Math.max(0.005, 1 - this.iterationCount / this.maxIterations)
    const limit = this.stepLimitValue * alpha
    const dx = new Float64Array(count)
    const dy = new Float64Array(count)

    for (let i = 0; i < count; i += 1) {
      const a = nodes[i]
      for (let j = i + 1; j < count; j += 1) {
        const b = nodes[j]
        let vx = b.x - a.x
        let vy = b.y - a.y
        let distanceSquared = vx * vx + vy * vy
        if (distanceSquared < minDistance * minDistance) {
          // 数值不稳定（两点几乎重合）：给一个确定性的微小偏移，而不是除以零。
          const angle = ((i * 31 + j * 17) % 360) * (Math.PI / 180)
          vx = Math.cos(angle) * minDistance
          vy = Math.sin(angle) * minDistance
          distanceSquared = minDistance * minDistance
        }
        const distance = Math.sqrt(distanceSquared)
        const force = (params.repulsion * kSquared) / distance
        const ux = vx / distance
        const uy = vy / distance
        dx[i] -= ux * force
        dy[i] -= uy * force
        dx[j] += ux * force
        dy[j] += uy * force
      }
    }

    const index = new Map<string, number>()
    for (let i = 0; i < count; i += 1) index.set(nodes[i].id, i)

    for (const link of this.links) {
      const a = index.get(link.source)
      const b = index.get(link.target)
      if (a == null || b == null) continue
      const na = nodes[a]
      const nb = nodes[b]
      const vx = nb.x - na.x
      const vy = nb.y - na.y
      const distance = Math.max(minDistance, Math.sqrt(vx * vx + vy * vy))
      const force = (params.linkStrength * distance * distance) / k
      const ux = vx / distance
      const uy = vy / distance
      dx[a] += ux * force
      dy[a] += uy * force
      dx[b] -= ux * force
      dy[b] -= uy * force
    }

    if (params.centerGravity > 0) {
      // 向心力随节点数放大（1 + n/25）：斥力是 O(n²) 累加的，固定系数会让大图整体膨胀、
      // 小图缩成一团。放大之后"向中心聚合"这个旋钮的**方向**不变（越大越聚），
      // 但同一档位下的屏幕密度在不同规模上大致一致，孤立节点也不会被甩到视野之外。
      const gravity = params.centerGravity * (1 + count / 25)
      for (let i = 0; i < count; i += 1) {
        dx[i] -= nodes[i].x * gravity
        dy[i] -= nodes[i].y * gravity
      }
    }

    let maxDisplacement = 0
    for (let i = 0; i < count; i += 1) {
      const node = nodes[i]
      if (node.fixed) {
        node.vx = 0
        node.vy = 0
        continue
      }
      const magnitude = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i])
      if (magnitude > maxDisplacement) maxDisplacement = magnitude
      if (magnitude === 0) {
        node.vx = 0
        node.vy = 0
        continue
      }
      const capped = Math.min(magnitude, limit)
      node.vx = (dx[i] / magnitude) * capped
      node.vy = (dy[i] / magnitude) * capped
      node.x += node.vx
      node.y += node.vy
    }
    return maxDisplacement
  }
}
