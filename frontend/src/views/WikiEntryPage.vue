<script setup lang="ts">
/**
 * Wiki 词条深链页（`/#/wiki/:entryId`，契约 docs/WIKI_API.md §9.1 的路由冻结行）。
 *
 * 这一页是「词条正文的直接入口」（`WIKI_DOMAIN.md` §8 第 8 条：默认入口反转后，
 * `/#/wiki` 给关系图，正文由深链直接打开）。它同时承担契约里四件必须照实做的事：
 *
 *   1. **刷新后仍停在同一词条与同一版本**（§8.1）：版本写在 `?revision=wrev-…`，
 *      页面初始化时**读取地址栏**决定请求哪个版本，而不是先写死再补地址栏。
 *   2. **窄屏分步切换**（§7.2）：窄屏把"关系图"和"详情"拆成两步，同一时刻只挂一个；
 *      但"来源证据"与"运行轨迹"在窄屏**仍然必须可达** —— 它们不是被隐藏的图层，
 *      而是详情这一步里可聚焦的区块（`?step=` 不参与状态，避免多一份会漂移的真相）。
 *   3. **返回关系图并保留原选中词条**（§7.2 最小要求 2）：返回链接带 `?select=<id>`，
 *      由 WikiPage 注入回组件。用 `router.resolve` 生成真实 `href` 再 `@click.prevent`：
 *      中键/新标签页打开也能用，而左键不触发整页刷新。
 *   4. **失败与"没有数据"绝不互相冒充**（§8.2 纪律 1）：错误码逐条给出中文解释，
 *      读取失败不显示空态文案；`WIKI_LOAD_FAILED` 与"确实没有证据"是两句不同的话。
 *
 * ⚠ 为什么不复用 `api/client.ts`：本页需要的 #3（版本详情）与 #4（版本历史）两个读接口
 * 当时**没有**在前端封装里（client.ts 只封装了 #1/#2/#6/#7/#8），而本次改动被明确限定
 * 不许改 `client.ts` / `types.ts`。所以这里用一个 15 行的本地读内核：与 client.ts 的
 * `apiError` 同一套错误信封解析（`{"detail":{"error_code","message","hint"}}`，框架 422 是
 * 数组形态），并把 `{data, meta, paging, warnings}` 统一拆开 —— 注意 #3/#4 的后端实现
 * **没有**套信封，而 #7 套了，所以 `unwrap()` 两种形状都认（这不是猜，是两处实现的差异）。
 *
 * ⚠ 类名一律 `wk-` 前缀：Tailwind v4 的 @layer utilities 会生成同名工具类，
 * `.collapse`（visibility:collapse）与 `.table-caption`（display:table-caption）已经各坑过
 * 一次；`wk-` 不落在任何工具类的名字空间里，构建产物里也已核对无同名工具类。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import SideNav from '../components/SideNav.vue'
import WikiForceGraph from '../components/wiki/WikiForceGraph.vue'
import { renderMarkdown } from '../components/wiki/markdown'
import { wikiEntryExportUrl, wikiEntryGraph, wikiEntryProvenance } from '../api/client'
import type {
  WikiAssertion,
  WikiEntry,
  WikiEntryDetailData,
  WikiEvidenceRef,
  WikiGraphData,
  WikiGraphRelation,
  WikiProvenanceData,
  WikiReviewEvent,
  WikiRevision,
  WikiRevisionSummary,
  WikiRunSummary,
  WikiTraceEventSummary,
} from '../api/types'

const route = useRoute()
const router = useRouter()

/* ── 1) 本地读内核（见文件头：为什么不能走 client.ts）────────────────────── */

type WikiError = Error & { errorCode?: string; status?: number }

/** 拆信封：#3/#4 返回裸对象，#7 返回 `{data, meta, paging, warnings}`，两种都认。 */
interface WikiRead<T> {
  data: T
  meta: Record<string, any> | null
  paging: Record<string, any> | null
  warnings: string[]
}

function unwrap<T>(body: any): WikiRead<T> {
  const enveloped =
    body && typeof body === 'object' && 'data' in body &&
    ('meta' in body || 'paging' in body || 'warnings' in body)
  if (!enveloped) {
    return { data: body as T, meta: null, paging: null, warnings: [] }
  }
  return {
    data: body.data as T,
    meta: body.meta ?? null,
    paging: body.paging ?? null,
    warnings: Array.isArray(body.warnings) ? body.warnings.map(String) : [],
  }
}

/** 与 client.ts 的 `apiError` 同形：错误码优先取 `detail.error_code`，框架 422 折成一行。 */
async function toWikiError(res: Response, name: string): Promise<WikiError> {
  let message = ''
  let code = ''
  try {
    const body = await res.json()
    const detail = body?.detail
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      code = 'VALIDATION_ERROR'
      message = detail
        .map((item: any) => {
          const loc = Array.isArray(item?.loc) ? item.loc.filter((k: any) => k !== 'body').join('.') : ''
          const msg = typeof item?.msg === 'string' ? item.msg : JSON.stringify(item)
          return loc ? `${loc}: ${msg}` : msg
        })
        .join('; ')
    } else if (detail && typeof detail === 'object') {
      code = typeof detail.error_code === 'string' ? detail.error_code : ''
      const hint = typeof detail.hint === 'string' && detail.hint ? ` ${detail.hint}` : ''
      const extra = detail.details != null ? ` ${JSON.stringify(detail.details)}` : ''
      message = `${code || (typeof detail.message === 'string' ? detail.message : JSON.stringify(detail))}${hint}${extra}`
    }
  } catch {
    /* 空体 / 非 JSON：message 留空，下面回落到状态码 */
  }
  const err = new Error(message || `${name} HTTP ${res.status}`) as WikiError
  err.errorCode = code || `HTTP_${res.status}`
  err.status = res.status
  return err
}

async function readWiki<T>(path: string): Promise<WikiRead<T>> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw await toWikiError(res, path)
  return unwrap<T>(await res.json())
}

/* ── 2) 错误与原因码的**中文解释表** ───────────────────────────────────────
   覆盖面按契约 §8.1 的七类失败场景逐条对齐（加载失败 / 无权限 / 无证据 / 引用失效 /
   定位不可用 / 整理失败 / 版本冲突），另加本页会遇到的几个 404 与框架错误。
   `retry` 只标"重试有意义"的（读失败可重试）；角色门与版本冲突**不**建议盲目重试。 */

interface ErrorCopy {
  title: string
  note: string
  retry?: boolean
}

const ERROR_TEXT: Record<string, ErrorCopy> = {
  WIKI_ENTRY_NOT_FOUND: {
    title: '词条不存在，或当前身份无权读取它（WIKI_ENTRY_NOT_FOUND）',
    note: '服务端对"不存在"与"无权限读草稿 / 属于别人"返回完全相同的 404（存在性隐藏），所以这里不猜测是哪一种。',
  },
  WIKI_REVISION_NOT_FOUND: {
    title: '该版本不存在，或不属于这个词条（WIKI_REVISION_NOT_FOUND）',
    note: '地址栏里的 revision 可能来自另一个词条。可以改用「当前发布头」再读一次。',
  },
  WIKI_SPACE_NOT_FOUND: {
    title: '空间不存在，或不属于当前身份（WIKI_SPACE_NOT_FOUND）',
    note: '空间 id 来自服务端自举，界面上没有让人手填的入口。',
  },
  WIKI_PERMISSION_DENIED: {
    title: '无权限（WIKI_PERMISSION_DENIED · 403）',
    note: '角色门未通过。重试不会改变结果 —— 需要管理员授予相应角色。',
  },
  WIKI_SOURCE_FORBIDDEN: {
    title: '来源不可访问（WIKI_SOURCE_FORBIDDEN · 403）',
    note: '角色门通过了，但其中某个来源不在本次授权范围内。',
  },
  WIKI_RUN_FORBIDDEN: {
    title: '运行不可访问（WIKI_RUN_FORBIDDEN · 403）',
    note: '目标运行不属于当前身份。',
  },
  WIKI_NO_EVIDENCE: {
    title: '没有可展示的证据（WIKI_NO_EVIDENCE · 409）',
    note: '这是"确实没有"：目标运行没有可投影的证据。它与"读取失败"是两件事，所以这里说的是没有，而不是加载不出来。',
  },
  WIKI_REFERENCE_INVALID: {
    title: '引用失效（WIKI_REFERENCE_INVALID · 422）',
    note: '被引用的证据引用不存在或已被授权过滤。界面不把它当作"没有引用"，也不替它找一个近似的来源。',
  },
  WIKI_LOCATOR_UNAVAILABLE: {
    title: '定位不可用（WIKI_LOCATOR_UNAVAILABLE · 422）',
    note: '定位记录存在但不可解析（bbox 缺失 / 页号越界 / 记录已删）。摘要仍然展示，只是不给一个猜出来的页码。',
  },
  WIKI_BUILD_FAILED: {
    title: '整理失败（WIKI_BUILD_FAILED · 409）',
    note: '整理任务的失败详情在任务对象里（error_code / error_detail），问答本身不受影响。',
  },
  WIKI_VERSION_CONFLICT: {
    title: '版本冲突（WIKI_VERSION_CONFLICT · 409）',
    note: '地址栏里的版本与服务端的基线 / 正文摘要不一致。请重新拉取最新版本后再试（不是重试同一个请求）。',
  },
  WIKI_EXPORT_TOO_LARGE: {
    title: '导出超过 300 条证据引用（WIKI_EXPORT_TOO_LARGE · 422）',
    note: '导出不做"截断后声称完整"，所以直接拒绝：请按版本或范围缩小。',
  },
  WIKI_REVISION_TOO_LARGE: {
    title: '版本内容超上限（WIKI_REVISION_TOO_LARGE · 422）',
    note: '正文 / 主张 / 关系其中一项超上限，需要拆分。',
  },
  WIKI_LOAD_FAILED: {
    title: '加载失败（WIKI_LOAD_FAILED · 502）',
    note: '读取失败与"没有数据"是两件事：这里不显示空态文案。可以重试。',
    retry: true,
  },
  LOGIN_REQUIRED: {
    title: '未登录（LOGIN_REQUIRED · 401）',
    note: '/v1/wiki/* 不进匿名白名单，读取也需要身份。',
  },
  SESSION_INVALID: {
    title: '会话已失效（SESSION_INVALID · 401）',
    note: '需要重新登录后再访问。',
  },
  VALIDATION_ERROR: {
    title: '请求参数不合法（框架 422）',
    note: '这是 FastAPI 的请求校验结果，没有 WIKI_* 错误码。',
  },
}

function errorCopy(code: string): ErrorCopy {
  if (ERROR_TEXT[code]) return ERROR_TEXT[code]
  if (/^HTTP_5\d\d$/.test(code)) {
    return { title: `服务端错误（${code}）`, note: '这不是"没有数据"，是这次读取失败了。可以重试。', retry: true }
  }
  return {
    title: `读取失败（${code}）`,
    note: '服务端返回了未在契约 §8 里列出的错误码：原值保留，不翻译成更好听的说法。',
    retry: true,
  }
}

/** 提示键（`warnings`）的中文说明；未知原因码原样显示，不猜。 */
const WARNING_TEXT: Record<string, string> = {
  AUTHORIZATION_FILTERED:
    '部分对象因权限被过滤：本次考虑的对象数与实际可见数不一致。"看不到"不等于"不存在"。',
  CURSOR_RESET_ON_FILTER_CHANGE: '筛选或版本变化，服务端已忽略旧游标并重新返回第一批。',
  WIKI_GRAPH_LIMIT_REACHED: '已达到显示上限（100 节点 / 300 关系），请缩小范围。',
  WIKI_LOAD_FAILED: '轨迹部分读取失败（服务端给出 WIKI_LOAD_FAILED）——不显示为"无事件"。',
  WIKI_TRACE_EVENTS_UNAVAILABLE: '轨迹事件本轮不可用：这是读取失败，不是"确实没有事件"。',
  WIKI_NO_REVISION_FOR_PROVENANCE: '该词条还没有任何版本，所以没有可展开的来源（这是"确实没有"）。',
}

/* ── 3) 状态位的文字化（冲突与核验状态**不得只靠颜色**）────────────────── */

const STATUS_LABEL: Record<string, string> = {
  // 发布流程状态
  draft: '草稿',
  in_review: '待复核',
  published: '已发布',
  archived: '已归档',
  // 主张核验状态
  unverified: '未核验',
  verified: '已核验',
  insufficient: '证据不足',
  conflicted: '存在冲突',
  // 来源有效性
  current: '有效（current）',
  stale: '已过期（stale）',
  unavailable: '不可用（unavailable）',
  // 业务键的人工确认（`pending` 不等于"已确认"）
  pending: '待人工确认（pending）',
  confirmed: '已人工确认（confirmed）',
}

/** 四类词条（`WIKI_DOMAIN.md` §2）。与"状态"共用一张表会把类型显示成"未在词表中"，所以分开。 */
const ENTRY_TYPE_LABEL: Record<string, string> = {
  business_entity: '业务实体',
  metric: '指标',
  norm: '规范',
  conclusion: '分析结论',
}

/* 类型：只显示中文。原来会在后面补一个英文原值（分析结论（conclusion）），
   但另外两行（词条 ID、分类）都没有英文，三行放一起格式不统一，所以去掉。
   未收录的取值仍然原样露出并注明，不猜。 */
function entryTypeText(value: unknown): string {
  if (value == null || value === '') return '（字段缺失）'
  const key = String(value)
  return ENTRY_TYPE_LABEL[key] ? ENTRY_TYPE_LABEL[key] : `${key}（未在词表中：原值保留）`
}

function statusLabel(value: unknown, fallback = '（字段缺失）'): string {
  if (value == null || value === '') return fallback
  const key = String(value)
  return STATUS_LABEL[key] ? `${STATUS_LABEL[key]}` : `${key}（未在词表中：原值保留）`
}

function text(value: unknown, fallback = '（字段缺失，不猜测）'): string {
  if (value == null || value === '') return fallback
  return String(value)
}

/** 后端给的是带时区的 ISO 串，交给浏览器按本机时区渲染（与 RunsPage.whenText 同形）。 */
function whenText(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function shortHash(value?: string | null): string {
  if (!value) return ''
  const bare = String(value).replace(/^sha256:/, '')
  return bare.length > 16 ? `${bare.slice(0, 16)}…` : bare
}

function countKeys(value: unknown): number {
  return value && typeof value === 'object' ? Object.keys(value as object).length : 0
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/* ── 4) 路由参数与版本 ───────────────────────────────────────────────────── */

function firstParam(value: unknown): string {
  const raw = Array.isArray(value) ? value[0] : value
  return typeof raw === 'string' ? raw.trim() : ''
}

const entryId = computed(() => firstParam(route.params.entryId))
/** 地址栏里的版本就是**请求的**版本（刷新/分享都停在这一个版本上） */
const requestedRevisionId = computed(() => firstParam(route.query.revision))

/* ── 5) 数据 ─────────────────────────────────────────────────────────────── */

interface RevisionListData {
  entry_id?: string
  revisions?: WikiRevisionSummary[]
  limit?: number
  has_more?: boolean
  next_cursor?: string | null
  truncated?: boolean
}

const detail = ref<WikiEntryDetailData | null>(null)
const revisions = ref<WikiRevisionSummary[]>([])
const revisionPaging = ref<{ has_more: boolean; truncated: boolean } | null>(null)
const provenance = ref<WikiProvenanceData | null>(null)
const provenanceMeta = ref<{ warnings: string[]; truncated: boolean; hasMore: boolean } | null>(null)
const neighborhood = ref<WikiGraphData | null>(null)

const loading = ref(false)
const pageError = ref<{ code: string; message: string } | null>(null)
const partErrors = ref<{ revisions: WikiError | null; provenance: WikiError | null; neighborhood: WikiError | null }>({
  revisions: null,
  provenance: null,
  neighborhood: null,
})
/** 引用 [n] → 证据锚点的定位反馈（"定位不可用"必须说出来，不能默默什么都不发生） */
const locateNotice = ref('')

let loadToken = 0

const entry = computed<WikiEntry | null>(() => detail.value?.entry ?? null)
const revision = computed<WikiRevision | null>(() => detail.value?.revision ?? null)
/* 正文：`body_markdown` 是**服务端模板生成的 Markdown**，不是 HTML。
   以前这里直接把它塞进 <pre>，于是 `#` / `>` / 反引号 / 表格的 `|---|` 全裸着，
   还带着等宽字体——看着像代码而不是文档。现在交给 `components/wiki/markdown.ts`
   渲染：那个模块先把源文本**全部转义**再补标签，所以 `v-html` 拿到的是本仓库自己
   写出来的标签（为什么不用 marked 见该文件头部的 WHY）。 */
const bodyHtml = computed(() => renderMarkdown(revision.value?.body_markdown))
const headRevisionId = computed(() => entry.value?.head_revision_id ?? '')
/** 读的是不是历史版本：只看"请求的版本 vs 词条头"，**不**把当前状态贴到历史版本上（§5.5）。
 *  词条还没有发布头时（`head_revision_id` 为空）不能叫"历史版本" —— 那时只是"指定版本"，否则
 *  一个刚生成的草稿会被界面说成历史。 */
const readingHistory = computed(
  () =>
    !!requestedRevisionId.value &&
    !!headRevisionId.value &&
    requestedRevisionId.value !== headRevisionId.value,
)
/* 曾经有一个 readingPinnedRevision（"指定了版本但还没有发布头"）专门驱动「指定版本」标签。
   那个标签按用户要求已移除，这个 computed 随之没有使用者，一并删掉，避免留死代码。 */

async function load(): Promise<void> {
  const token = ++loadToken
  const id = entryId.value
  const rev = requestedRevisionId.value
  loading.value = true
  pageError.value = null
  partErrors.value = { revisions: null, provenance: null, neighborhood: null }
  provenanceMeta.value = null
  locateNotice.value = ''

  if (!id) {
    pageError.value = { code: 'WIKI_ENTRY_NOT_FOUND', message: '地址里没有词条 id。' }
    loading.value = false
    return
  }

  const detailPath =
    `/v1/wiki/entries/${encodeURIComponent(id)}` +
    (rev ? `?revision_id=${encodeURIComponent(rev)}` : '')

  // 四个请求各自独立：正文（必需）之外的三个失败**不影响**正文渲染，
  // 但它们各自必须显式报错 —— 把"读不到证据"显示成"没有证据"就是伪造结论。
  const [detailResult, revisionsResult, provenanceResult, neighborhoodResult] = await Promise.allSettled([
    readWiki<WikiEntryDetailData>(detailPath),
    readWiki<RevisionListData>(`/v1/wiki/entries/${encodeURIComponent(id)}/revisions?limit=50`),
    wikiEntryProvenance(id, { revision_id: rev || undefined, limit: 200 }),
    wikiEntryGraph(id, { depth: 1, limit: 50 }),
  ])

  if (token !== loadToken) return // 期间又导航了：丢弃这次结果

  if (detailResult.status === 'rejected') {
    const err = detailResult.reason as WikiError
    detail.value = null
    pageError.value = { code: String(err?.errorCode || 'HTTP_ERROR'), message: String(err?.message || err) }
  } else {
    detail.value = detailResult.value.data
    if (detailResult.value.warnings.length) {
      provenanceMeta.value = {
        warnings: detailResult.value.warnings,
        truncated: false,
        hasMore: false,
      }
    }
  }

  if (revisionsResult.status === 'rejected') {
    revisions.value = []
    revisionPaging.value = null
    partErrors.value.revisions = revisionsResult.reason as WikiError
  } else {
    const list = revisionsResult.value.data
    revisions.value = Array.isArray(list?.revisions) ? list.revisions : []
    revisionPaging.value = {
      has_more: !!list?.has_more,
      truncated: !!list?.truncated,
    }
  }

  if (provenanceResult.status === 'rejected') {
    provenance.value = null
    partErrors.value.provenance = provenanceResult.reason as WikiError
  } else {
    provenance.value = provenanceResult.value.data
    provenanceMeta.value = {
      warnings: provenanceResult.value.warnings,
      truncated: !!provenanceResult.value.paging?.truncated,
      hasMore: !!provenanceResult.value.paging?.has_more,
    }
  }

  if (neighborhoodResult.status === 'rejected') {
    neighborhood.value = null
    partErrors.value.neighborhood = neighborhoodResult.reason as WikiError
  } else {
    neighborhood.value = neighborhoodResult.value.data
  }

  loading.value = false
}

watch([entryId, requestedRevisionId], () => void load(), { immediate: true })

/* ── 6) 窄屏分步切换（契约 §7.2：关系图与详情分步，但来源/轨迹仍可达）────── */

const narrow = ref(false)
let mql: MediaQueryList | null = null
const step = ref<'entry' | 'graph'>('entry')
const bodyEl = ref<HTMLElement | null>(null)

function onNarrowChange(event: MediaQueryListEvent | MediaQueryList): void {
  narrow.value = event.matches
}

onMounted(() => {
  mql = window.matchMedia('(max-width: 1100px)')
  onNarrowChange(mql)
  mql.addEventListener?.('change', onNarrowChange)
})

onBeforeUnmount(() => {
  mql?.removeEventListener?.('change', onNarrowChange)
  mql = null
})

/** 窄屏时只挂一步；宽屏两步并排。关系图**离开就卸载**（契约 §9.3 第 1 条：离开即停算）。 */
const showGraph = computed(() => (narrow.value ? step.value === 'graph' : true))
const showDetail = computed(() => (narrow.value ? step.value === 'entry' : true))

/** 切换步骤时回到顶部，否则窄屏下会停在"上一步滚到一半"的位置 */
function goStep(next: 'entry' | 'graph'): void {
  step.value = next
  bodyEl.value?.scrollTo({ top: 0 })
}

/* ── 7) 组件内的导航 ─────────────────────────────────────────────────────── */

/** 图里选中的节点（进入关系图那一步时预置为当前词条；点别的节点只改选中，不跳转） */
const graphSelected = ref<string | null>(entryId.value)
watch(entryId, (value) => {
  graphSelected.value = value || null
})

const graphHref = computed(
  () => router.resolve({ path: '/wiki', query: { select: entryId.value } }).href,
)

/** 返回关系图（保留选中）：左键走 router（不整页刷新），中键仍可用 href 新开标签 */
function backToGraph(): void {
  void router.push({ path: '/wiki', query: { select: entryId.value } })
}

function openEntry(nextEntryId: string): void {
  if (!nextEntryId) return
  void router.push({ name: 'wiki-entry', params: { entryId: nextEntryId } })
}

/** 版本选择写进地址栏（push：后退键能退回上一个版本，符合"版本也是位置"的直觉） */
function selectRevision(value: string): void {
  const query: Record<string, string> = {}
  if (value) query.revision = value
  void router.push({ name: 'wiki-entry', params: { entryId: entryId.value }, query })
}

function onRevisionChange(event: Event): void {
  selectRevision(String((event.target as HTMLSelectElement).value || ''))
}

function backToChat(): void {
  void router.push('/app')
}

function reload(): void {
  void load()
}

/** 「引用 [n] → 证据」的定位：**不用** `<a href="#…">`（hash 路由下会跳到不存在的路由，
   实测会白屏），改用 scrollIntoView + focus。定位不到时给一句明确的话。 */
function focusEvidence(refId: string): void {
  if (!refId) return
  const el = document.getElementById(`wk-evidence-${refId}`)
  if (!el) {
    locateNotice.value = `引用 ${refId} 不在本次已加载的证据清单里（证据可能因上限被截断，或该引用已失效）。`
    return
  }
  locateNotice.value = ''
  el.scrollIntoView({ block: 'center' })
  ;(el as HTMLElement).focus({ preventScroll: true })
}

const exportHref = computed(() => wikiEntryExportUrl(entryId.value, requestedRevisionId.value || null))

/* ── 8) 证据与轨迹的读取助手（三套状态、三种 error_summary 形状，都不合并）── */

/** `summary.events_unavailable` 不在冻结的摘要类型里（服务层额外加的降级说明），这里显式取它。 */
const eventsUnavailable = computed(
  () => (provenance.value?.summary as Record<string, unknown> | undefined)?.events_unavailable === true,
)

const evidenceList = computed<WikiEvidenceRef[]>(() => provenance.value?.evidence_refs ?? [])

const provenanceSummary = computed(() => provenance.value?.summary ?? detail.value?.provenance_summary ?? null)

/** 证据清单里定位是否可用：`locator` 缺失就是缺失 —— 由服务端给码，界面不自己判"大概在哪一页" */
function locatorText(ref: WikiEvidenceRef): string {
  const loc = ref.locator
  if (!loc || typeof loc !== 'object') return '定位不可用（服务端未返回 locator；不猜测页 / 块 / bbox）'
  const parts: string[] = []
  const kind = (loc as any).locator_kind
  if (kind) parts.push(String(kind))
  const page = (loc as any).page_no
  if (page != null) parts.push(`第 ${page} 页`)
  const bbox = (loc as any).bbox
  if (Array.isArray(bbox) && bbox.length) parts.push(`bbox [${bbox.map((n: any) => Number(n).toFixed(1)).join(', ')}]`)
  const block = (loc as any).block_id
  if (block) parts.push(`块 ${block}`)
  const contract = (loc as any).contract_version
  if (contract) parts.push(String(contract))
  if (!parts.length) return '定位不可用（locator 存在但字段不全；原样显示而不补一个猜出来的位置）'
  return parts.join(' · ')
}

/** `error_summary` 的三种形状（§5.7 约束 2）不得合并成一句"无错误" */
function errorSummaryOf(run: WikiRunSummary): { kind: 'error' | 'none' | 'unavailable' | 'missing'; text: string } {
  const value = run.error_summary
  if (value == null) {
    return { kind: 'missing', text: '错误摘要字段缺失（服务端未给出；不显示为"无错误"）' }
  }
  if ((value as any).unavailable) {
    return { kind: 'unavailable', text: '错误摘要不可用（读取失败）—— 不得显示为"无错误"' }
  }
  if ((value as any).none === true && !(value as any).error_code) {
    return { kind: 'none', text: '本次运行无错误事件（服务端显式声明 none: true）' }
  }
  const bits = [
    (value as any).error_code,
    (value as any).event_type,
    (value as any).agent_name,
    (value as any).at,
    (value as any).detail,
  ]
    .filter((x: any) => x != null && x !== '')
    .map((x: any) => String(x))
  return { kind: 'error', text: bits.length ? bits.join(' · ') : prettyJson(value) }
}

function eventIsError(event: WikiTraceEventSummary): boolean {
  return event.status === 'failed' || !!event.error_code
}

/** 版本下拉里显示的一行：id 前缀 + 状态 + 时间（都是文字，不靠颜色） */
function revisionOptionText(row: WikiRevisionSummary): string {
  const when = whenText(row.created_at)
  return `${String(row.revision_id).slice(0, 12)}… · ${statusLabel(row.status)}${when ? ` · ${when}` : ''}`
}

/** 地址栏里的版本是否出现在可见版本历史里（不在时要说明白，而不是静默把下拉跳到第一项） */
const requestedRevisionInList = computed(
  () => !requestedRevisionId.value || revisions.value.some((row) => row.revision_id === requestedRevisionId.value),
)

const relationOtherEnd = (relation: WikiGraphRelation): string => {
  if (relation.src === entryId.value) return relation.dst
  if (relation.dst === entryId.value) return relation.src
  return `${relation.src} → ${relation.dst}`
}

const neighborEntries = computed(() => {
  const root = neighborhood.value?.root ?? entryId.value
  return (neighborhood.value?.nodes ?? [])
    .filter((node) => node.node_kind === 'entry' && node.node_id !== root && node.entry)
    .map((node) => ({ id: node.node_id, title: node.entry?.title || node.node_id }))
})

const neighborhoodLine = computed(() => {
  const data = neighborhood.value
  if (!data) return ''
  const loaded = data.loaded ?? { nodes: 0, relations: 0 }
  const limits = data.limits ?? { nodes: 0, relations: 0 }
  const paging = `已加载 ${loaded.nodes} / 上限 ${limits.nodes} 个节点、${loaded.relations} / 上限 ${limits.relations} 条关系（口径：当前授权 + 当前图层 + 已加载）`
  const truncation = data.truncated
    ? `已截断${data.truncated_reason ? `（原因码 ${data.truncated_reason}）` : ''}${data.unreached_neighbors != null ? `，当前授权范围内还有 ${data.unreached_neighbors} 个邻居未返回` : ''}`
    : '本次没有因上限而截断'
  return `${paging}；${truncation}`
})

function assertionValueText(assertion: WikiAssertion): string {
  const raw = assertion.raw_value == null ? '' : String(assertion.raw_value)
  const rawUnit = assertion.raw_unit ? ` ${assertion.raw_unit}` : ''
  const norm = assertion.normalized_value == null ? '' : String(assertion.normalized_value)
  const normUnit = assertion.normalized_unit ? ` ${assertion.normalized_unit}` : ''
  if (!raw && !norm) return '（数值字段缺失）'
  const head = raw ? `${raw}${rawUnit}` : '（原始值缺失）'
  const tail = norm ? `（归一 ${norm}${normUnit}）` : '（归一值缺失）'
  return `${head} ${tail}`
}

const reviewEvents = computed<WikiReviewEvent[]>(() => detail.value?.review_events ?? [])
const relations = computed<WikiGraphRelation[]>(() => detail.value?.relations ?? [])
const runs = computed<WikiRunSummary[]>(() => provenance.value?.runs ?? [])
const events = computed<WikiTraceEventSummary[]>(() => provenance.value?.events ?? [])
</script>

<template>
  <div class="wk-shell" data-testid="wiki-entry-page">
    <SideNav @new-chat="backToChat" />

    <div class="wk-center">
      <!-- ── 顶栏：返回关系图（保留选中）+ 词条身份 + 版本 + 导出 ───────────── -->
      <header class="wk-head">
        <div class="wk-head-top">
          <!-- 返回入口用 <a> + @click.prevent：中键/新标签页可用，左键不整页刷新（见 backToGraph） -->
          <a class="wk-btn wk-btn-ghost" :href="graphHref" data-testid="back-to-graph"
             title="返回当前授权范围图，并保留本词条的选中状态" @click.prevent="backToGraph">
            ← 关系图
          </a>
          <h1 class="wk-title" data-testid="wiki-entry-title">
            {{ entry?.title || (loading ? '词条读取中…' : '词条')
            }}<span v-if="entry?.demo_data" class="wk-title-note" data-testid="demo-badge">（数据合成）</span>
          </h1>
        </div>

        <!-- 状态标签单独一行：原来它们紧贴在标题后面，把标题挤得没有呼吸空间。
             用户已要求移除两个标签：
               「演示数据（demo_data=true）」—— 数据是合成演示这件事，用户打算换个位置说明，
                                             待定；图谱页「范围与边界」卡片里仍有一条；
               「指定版本」                    —— 当前读的是哪个版本，版本下拉框里已经写明，
                                             不必再挂一个标签。
             「历史版本」保留：它标的是"你正在看历史而非发布头"这种需要提醒的状态。 -->
        <div v-if="readingHistory" class="wk-tags">
          <span class="wk-tag" data-testid="history-badge">历史版本</span>
        </div>

        <!-- 三行，格式统一：都是纯文本、都不带英文括号。
             原来：词条是 code 框、类型带（conclusion）、分类整个值是（未分类）—— 三行三种样子。 -->
        <p class="wk-sub" data-testid="wiki-entry-id">
          <span class="wk-sub-item">
            <span class="wk-sub-key">词条</span>
            <span class="wk-sub-val">{{ entryId }}</span>
          </span>
          <template v-if="entry">
            <span class="wk-sub-item">
              <span class="wk-sub-key">类型</span>
              <span class="wk-sub-val">{{ entryTypeText(entry.entry_type) }}</span>
            </span>
            <span class="wk-sub-item">
              <span class="wk-sub-key">分类</span>
              <span class="wk-sub-val">{{ text(entry.category, '未分类') }}</span>
            </span>
          </template>
        </p>

        <div class="wk-toolbar">
          <label class="wk-field">
            <span class="wk-field-label">版本</span>
            <!-- 版本写在 query 上：刷新与分享都停在同一词条与同一版本（契约 §8.1） -->
            <select class="wk-select" data-testid="revision-select"
                    :value="requestedRevisionId"
                    @change="onRevisionChange">
              <option value="">当前发布头（不指定版本）</option>
              <option v-for="row in revisions" :key="row.revision_id" :value="row.revision_id">
                {{ revisionOptionText(row) }}
              </option>
            </select>
          </label>

          <a class="wk-btn" :href="exportHref" data-testid="export-link"
             title="按当前版本导出 Markdown（导出与页面走同一个授权投影）">导出 Markdown</a>
          <button class="wk-btn" type="button" data-testid="reload-entry" :disabled="loading" @click="reload">
            {{ loading ? '读取中…' : '重新读取' }}
          </button>
        </div>

        <!-- 这条说明原来挂在按钮后面，看起来像"操作的一部分"。移到单独一行、压小。
             括号里原本还有「版本在地址栏 ?revision=」并包了 code 框 ——
             URL 参数名是给开发者看的实现细节，读者只需要知道"刷新不会丢当前视图"。 -->
        <p class="wk-hint-inline">刷新页面仍停在这一词条与这一版本</p>

        <p v-if="requestedRevisionId && !requestedRevisionInList && !partErrors.revisions" class="wk-warn" data-testid="revision-not-in-list">
          地址栏里的版本 <code class="wk-code">{{ requestedRevisionId }}</code> 不在可见版本历史里：
          它可能已被授权过滤（例如别人的草稿行），或不属于这个词条。下面显示的仍是服务端对该版本的实际返回。
        </p>
      </header>

      <div ref="bodyEl" class="wk-body">
        <!-- ── 整页失败：明确说明，且**不**显示空态文案 ─────────────────── -->
        <section v-if="pageError" class="wk-card wk-card-error" role="alert" data-testid="entry-error">
          <h2 class="wk-card-title">{{ errorCopy(pageError.code).title }}</h2>
          <p class="wk-error-msg">{{ pageError.message }}</p>
          <p class="wk-note">{{ errorCopy(pageError.code).note }}</p>
          <p class="wk-note">
            这不是「这个知识库是空的」：读取失败与空集合是两件事，所以这里不显示空态文案。
          </p>
          <div class="wk-actions">
            <button v-if="errorCopy(pageError.code).retry" class="wk-btn" type="button" @click="reload">重试</button>
            <a class="wk-btn wk-btn-ghost" :href="graphHref" @click.prevent="backToGraph">返回关系图</a>
            <button
              v-if="pageError.code === 'WIKI_REVISION_NOT_FOUND' || pageError.code === 'WIKI_VERSION_CONFLICT'"
              class="wk-btn"
              type="button"
              data-testid="switch-to-head"
              @click="selectRevision('')"
            >
              改为读取当前发布头
            </button>
          </div>
        </section>

        <p v-else-if="loading && !entry" class="wk-loading">正在读取词条与来源…</p>

        <template v-else>
          <!-- ── 窄屏：关系图 / 详情 分步切换（宽屏两步并排，不显示这条切换栏）── -->
          <nav v-if="narrow" class="wk-steps" role="tablist" aria-label="关系图与详情分步切换">
            <button
              class="wk-step"
              :class="{ 'is-active': step === 'entry' }"
              type="button"
              role="tab"
              data-testid="step-entry"
              :aria-selected="step === 'entry'"
              @click="goStep('entry')"
            >
              ① 词条详情<small>正文 · 来源证据 · 运行轨迹</small>
            </button>
            <button
              class="wk-step"
              :class="{ 'is-active': step === 'graph' }"
              type="button"
              role="tab"
              data-testid="step-graph"
              :aria-selected="step === 'graph'"
              @click="goStep('graph')"
            >
              ② 当前授权范围图<small>选中本词条，可继续探索</small>
            </button>
          </nav>

          <!-- 始终单栏：原来宽屏会走 .is-split 变成「46% | 1fr」两栏，
               结果左边关系图被压窄、图谱设置的浮层还压在图上，
               右边的「三套状态」「词条正文」又都是长文字，挤在半宽里很挤。
               这页各块内容量都不小，单栏依次排列更好读。（与尺寸页、图谱页同样的处理。） -->
          <div class="wk-cols">
            <!-- ══ 关系图一步 ══ -->
            <section v-if="showGraph" class="wk-col" data-testid="graph-column">
              <h2 class="wk-col-title">当前授权范围图</h2>
              <ul class="wk-note-list">
                <li>这里嵌的是<strong>当前授权范围图</strong>，不是全库全图，也不是「这个词条的全库邻居」。</li>
                <li>进入这一步时已经预选了这个词条，可以直接看它在<strong>已加载</strong>集合里的位置。</li>
                <li>点别的节点只改选中；点「打开详情」才跳转到那个词条的详情页。</li>
                <li>这个词条自己的邻域统计在详情的「关系」卡片里，两个口径分开说，不混成一个数字。</li>
                <li>离开这一步会卸载关系图，布局计算随之停止，不会留在后台继续算。</li>
              </ul>
              <WikiForceGraph
                v-model:selectedEntryId="graphSelected"
                height="min(56vh, 620px)"
                @open-entry="openEntry"
              />
            </section>

            <!-- ══ 详情一步 ══ -->
            <section v-if="showDetail" class="wk-col" data-testid="detail-column">
              <!-- 三套状态：分别显示、不合并（契约 §7.1 / §5.4 的逐条禁令） -->
              <section class="wk-card" data-testid="status-card">
                <h2 class="wk-card-title">
                  三套状态<span class="wk-card-note">分别来自三个独立字段，不合并成一个"总状态"</span>
                </h2>
                <dl class="wk-kv">
                  <dt>发布流程状态（词条当前值）</dt>
                  <dd data-testid="status-publication">{{ statusLabel(entry?.status?.publication) }}</dd>
                  <dt>主张核验状态（词条当前值）</dt>
                  <dd data-testid="status-assertion">{{ statusLabel(entry?.status?.assertion) }}</dd>
                  <dt>来源有效性（词条当前值）</dt>
                  <dd data-testid="status-validity">{{ statusLabel(entry?.status?.source_validity) }}</dd>
                  <dt>本版本记录的状态快照</dt>
                  <dd data-testid="status-revision">
                    {{ revision ? statusLabel(revision.publication_status) : '（没有版本对象）' }}
                    <span class="wk-note-inline">
                      历史版本只带它自己记录的状态。把当前状态贴到历史版本上，等于用新事实改写历史的读取结果。
                    </span>
                  </dd>
                  <dt>业务键人工确认</dt>
                  <dd>{{ statusLabel(entry?.confirmation) }}</dd>
                </dl>

                <details v-if="entry?.business_key" class="wk-folds">
                  <summary>业务键原值（不做归一展示）</summary>
                  <dl class="wk-kv">
                    <dt>space_id</dt><dd>{{ entry.business_key.space_id }}</dd>
                    <dt>namespace</dt><dd>{{ entry.business_key.namespace }}</dd>
                    <dt>entry_type</dt><dd>{{ entry.business_key.entry_type }}</dd>
                    <dt>key_part</dt><dd class="wk-mono">{{ prettyJson(entry.business_key.key_part) }}</dd>
                    <dt>confirmation</dt><dd>{{ statusLabel(entry.business_key.confirmation) }}</dd>
                  </dl>
                </details>
              </section>

              <!-- 正文 -->
              <section class="wk-card" data-testid="body-card">
                <h2 class="wk-card-title">
                  词条正文<span class="wk-card-note">与版本一一对应；同一 revision_id 的任何请求逐字节一致</span>
                </h2>

                <p v-if="!revision" class="wk-note" data-testid="no-revision">
                  这个词条<strong>还没有任何版本</strong>：服务端返回 <code class="wk-code">revision: null</code>。
                  这是"确实没有正文"，不是读取失败 —— 正文的生成走整理任务（问答页的「整理为知识词条」）。
                </p>

                <template v-else>
                  <!-- 渲染后的正文。`data-testid` 留在容器上（渲染前后同一个测试点）；
                       样式在下方 `.wk-md`（标题蓝色加粗、正文黑色、行距 1.75、表格有边框、
                       行内 code 等宽浅底）。 -->
                  <div v-if="bodyHtml" class="wk-md" data-testid="body-markdown" v-html="bodyHtml"></div>
                  <p v-else class="wk-note" data-testid="body-markdown">
                    （本版本正文为空字符串）
                  </p>

                  <dl class="wk-kv">
                    <dt>revision_id</dt><dd class="wk-mono" data-testid="revision-id">{{ revision.revision_id }}</dd>
                    <dt>base_revision_id</dt>
                    <dd class="wk-mono">{{ revision.base_revision_id || 'null（首版）' }}</dd>
                    <dt>正文摘要 body_sha256</dt><dd class="wk-mono">{{ shortHash(revision.body_sha256) || '（字段缺失）' }}</dd>
                    <dt>作者</dt><dd>{{ text(revision.author?.display_name || revision.author?.user_id) }}</dd>
                    <dt>生成方式</dt>
                    <dd>
                      {{ text(revision.generation?.method) }}
                      <template v-if="revision.generation?.template_version">
                        · 模板 {{ revision.generation.template_version }}
                      </template>
                      <template v-if="revision.generation?.model_id">
                        · 模型 {{ revision.generation.model_id }}
                      </template>
                    </dd>
                    <dt>创建时间</dt><dd>{{ whenText(revision.created_at) || '（字段缺失）' }}</dd>
                  </dl>

                  <template v-if="revision.citations?.length">
                    <h3 class="wk-sub-title">
                      引用标记（{{ revision.citations.length }} 条，可定位到下方证据清单）
                    </h3>
                    <p class="wk-note">
                      点标记会滚动并聚焦到对应证据；<strong>不用</strong> <code class="wk-code">#锚点</code> 跳转 ——
                      hash 路由下那会跳到不存在的路由（本项目实测过白屏）。
                    </p>
                    <ul class="wk-cite-list">
                      <li v-for="cite in revision.citations" :key="cite.marker + cite.evidence_ref_id">
                        <button class="wk-cite-btn" type="button" @click="focusEvidence(cite.evidence_ref_id)">
                          {{ cite.marker || '引用' }}
                        </button>
                        <span class="wk-mono">{{ cite.evidence_ref_id }}</span>
                      </li>
                    </ul>
                  </template>
                  <p v-else class="wk-note">本版本没有引用标记（<code class="wk-code">citations: []</code>）。</p>

                  <p v-if="locateNotice" class="wk-warn" data-testid="locate-notice">{{ locateNotice }}</p>
                </template>
              </section>

              <!-- 主张与核验记账 -->
              <section class="wk-card" data-testid="assertions-card">
                <h2 class="wk-card-title">
                  主张与核验<span class="wk-card-note">核验状态是<strong>记账结果</strong>，不证明指标语义</span>
                </h2>
                <p v-if="!revision?.assertions?.length" class="wk-note">
                  本版本没有主张（<code class="wk-code">assertions: []</code>）。
                </p>
                <ol v-else class="wk-assertions">
                  <li v-for="assertion in revision.assertions" :key="assertion.assertion_id" class="wk-assertion">
                    <p class="wk-assertion-head">
                      {{ text(assertion.metric, '（未标注指标）') }}
                      <span class="wk-assertion-value">{{ assertionValueText(assertion) }}</span>
                    </p>
                    <dl class="wk-kv">
                      <dt>核验状态</dt><dd>{{ statusLabel(assertion.verification?.status) }}</dd>
                      <dt>统计口径 statistic</dt><dd>{{ text(assertion.statistic) }}</dd>
                      <dt>时间范围 time_scope</dt><dd class="wk-mono">{{ prettyJson(assertion.time_scope) }}</dd>
                      <dt>适用范围 applicability</dt><dd class="wk-mono">{{ prettyJson(assertion.applicability) }}</dd>
                      <dt>记录范围 record_scope</dt><dd class="wk-mono">{{ prettyJson(assertion.record_scope) }}</dd>
                      <dt>实体键 entity_key</dt><dd class="wk-mono">{{ prettyJson(assertion.entity_key) }}</dd>
                      <dt>比较身份 comparison_key</dt><dd class="wk-mono">{{ shortHash(assertion.comparison_key) || '（字段缺失）' }}</dd>
                      <dt>核验时间 / 人</dt>
                      <dd>
                        {{ whenText(assertion.verification?.checked_at) || '（字段缺失）' }} ·
                        {{ text(assertion.verification?.checked_by?.method) }}
                      </dd>
                      <dt>规则版本</dt><dd>{{ text(assertion.verification?.rule_version) }}</dd>
                      <dt>证据引用</dt>
                      <dd>
                        <template v-if="assertion.evidence_refs?.length">
                          <button
                            v-for="refId in assertion.evidence_refs"
                            :key="refId"
                            class="wk-cite-btn"
                            type="button"
                            @click="focusEvidence(refId)"
                          >{{ refId }}</button>
                        </template>
                        <template v-else>（没有证据引用：该主张的核验状态最多到 insufficient）</template>
                      </dd>
                    </dl>

                    <h4 class="wk-sub-title">核验记账（逐项显示，缺项就写缺项）</h4>
                    <p v-if="!countKeys(assertion.verification?.accounting)" class="wk-note">
                      服务端没有给出记账字段（<code class="wk-code">accounting</code> 缺失）——
                      界面<strong>不会</strong>因此显示"已核验"。
                    </p>
                    <dl v-else class="wk-kv">
                      <template v-for="(value, key) in assertion.verification?.accounting" :key="String(key)">
                        <dt>{{ key }}</dt>
                        <dd class="wk-mono">{{ typeof value === 'object' ? prettyJson(value) : String(value) }}</dd>
                      </template>
                    </dl>
                    <p v-if="assertion.verification?.coverage" class="wk-note">
                      覆盖范围 coverage：<span class="wk-mono">{{ prettyJson(assertion.verification.coverage) }}</span>
                    </p>
                  </li>
                </ol>
              </section>

              <!-- 关系：本版本的关系 + 该词条的授权邻域统计（两个口径分开说） -->
              <section class="wk-card" data-testid="relations-card">
                <h2 class="wk-card-title">
                  关系<span class="wk-card-note">本版本的关系 + 该词条的授权关系邻域</span>
                </h2>

                <h3 class="wk-sub-title">本版本记录的关系（{{ relations.length }} 条）</h3>
                <ul v-if="relations.length" class="wk-list">
                  <li v-for="relation in relations" :key="relation.relation_id" class="wk-list-item">
                    <p class="wk-list-head">
                      {{ text(relation.label || relation.relation_type) }}
                      <span class="wk-list-sub">
                        {{ relation.direction === 'undirected' ? '无向' : '有向' }} ·
                        {{ relation.supported ? '已支持的关系类型' : '未支持的关系类型（原值保留）' }}
                      </span>
                    </p>
                    <p class="wk-note">
                      另一端：<button class="wk-linkbtn" type="button" @click="openEntry(relationOtherEnd(relation))">
                        {{ relationOtherEnd(relation) }}
                      </button>
                       · 原始类型 <span class="wk-mono">{{ text(relation.raw_type) }}</span>
                       <template v-if="relation.rule_version"> · 规则 {{ relation.rule_version }}</template>
                    </p>
                    <p v-if="relation.basis" class="wk-note">
                      依据：<span class="wk-mono">{{ prettyJson(relation.basis) }}</span>
                    </p>
                    <p v-if="relation.unsupported_reason" class="wk-note">
                      未支持原因码：<span class="wk-mono">{{ relation.unsupported_reason }}</span>
                    </p>
                    <p v-if="relation.provenance_incomplete?.applies" class="wk-warn wk-warn-tight">
                      该关系的溯源不完整（provenance_incomplete.applies=true）：缺失字段原样呈现，不推测补全。
                    </p>
                  </li>
                </ul>
                <p v-else class="wk-note">本版本没有关系记录（<code class="wk-code">relations: []</code>）。</p>

                <h3 class="wk-sub-title">授权关系邻域（这一项是单独一次统计）</h3>
                <p v-if="partErrors.neighborhood" class="wk-error-note" data-testid="neighborhood-error">
                  邻域读取失败（错误码 {{ partErrors.neighborhood.errorCode || 'HTTP_ERROR' }}）：
                  {{ partErrors.neighborhood.message }}。这是读取失败，不是「没有邻居」。
                </p>
                <template v-else-if="neighborhood">
                  <p class="wk-note" data-testid="neighborhood-line">{{ neighborhoodLine }}</p>
                  <p class="wk-note">
                    邻域与空间图是两个不同的口径：这里算的是<strong>这一个词条</strong>在授权范围内的邻居
                    （只取一层，每批 50 个）；空间图是<strong>整个授权范围</strong>的一页。
                    两者都只统计当前授权、当前图层、已加载的对象。
                  </p>
                  <ul v-if="neighborEntries.length" class="wk-chips">
                    <li v-for="item in neighborEntries" :key="item.id">
                      <button class="wk-chip" type="button" @click="openEntry(item.id)">{{ item.title }}</button>
                    </li>
                  </ul>
                  <p v-else class="wk-note">本次邻域返回里没有其它词条节点（孤立词条，或邻居尚未加载）。</p>
                </template>
              </section>

              <!-- 来源证据 -->
              <section class="wk-card" data-testid="evidence-card">
                <h2 class="wk-card-title">
                  来源证据<span class="wk-card-note">定位逐项给出；不可解析时写"定位不可用"</span>
                </h2>

                <p v-if="partErrors.provenance" class="wk-error-note" data-testid="provenance-error">
                  证据与轨迹读取失败（错误码 {{ partErrors.provenance.errorCode || 'HTTP_ERROR' }}）：
                  {{ partErrors.provenance.message }}。读取失败<strong>不等于</strong>「没有证据」。
                  <template v-if="detail?.provenance_summary">
                    下面是词条详情接口顺带返回的摘要，口径相同但覆盖面更小。
                  </template>
                </p>

                <template v-if="provenanceSummary">
                  <dl class="wk-kv">
                    <dt>证据引用数</dt><dd>{{ provenanceSummary.evidence_ref_count }}</dd>
                    <dt>来源运行数</dt><dd>{{ provenanceSummary.run_count }}</dd>
                    <dt>事件数 / 错误事件数</dt>
                    <dd>{{ provenanceSummary.event_count }} / {{ provenanceSummary.error_count }}</dd>
                    <dt>来源有效性分布</dt>
                    <dd class="wk-mono">{{ prettyJson(provenanceSummary.source_validity) }}</dd>
                    <dt>溯源完整性</dt>
                    <dd>
                      {{ provenanceSummary.provenance_complete ? '完整' : '不完整（见下方声明）' }}
                    </dd>
                  </dl>
                </template>

                <p v-if="provenanceMeta?.truncated" class="wk-warn wk-warn-tight" data-testid="evidence-truncated">
                  证据引用<strong>已截断</strong>（超过单次可返回的条数上限）：这不是「只有这些」，
                  请按版本或范围缩小后再查。
                </p>
                <p v-else-if="provenanceMeta?.hasMore" class="wk-warn wk-warn-tight">
                  事件<strong>还有后续</strong>（本页只是前一批，可以继续翻页）。这与「已截断」不是同一件事。
                </p>

                <p v-if="!evidenceList.length && !partErrors.provenance" class="wk-note" data-testid="evidence-empty">
                  这次返回的证据清单是空的。它和「读取失败」是两件事：上面没有失败提示，所以这里确实是「没有」。
                </p>

                <ul v-else class="wk-list">
                  <li
                    v-for="item in evidenceList"
                    :id="`wk-evidence-${item.evidence_ref_id}`"
                    :key="item.evidence_ref_id"
                    class="wk-list-item"
                    tabindex="-1"
                  >
                    <p class="wk-list-head">
                      <span class="wk-ev-kind">{{ text(item.source_kind) }}</span>
                      <span class="wk-list-sub">
                        有效性 {{ statusLabel(item.source_validity) }}
                        <template v-if="!item.supported"> · 未支持的类型（原值保留）</template>
                      </span>
                    </p>
                    <p class="wk-note">
                      来源 <span class="wk-mono">{{ text(item.source_ref) }}</span>
                    </p>
                    <p class="wk-note">定位：{{ locatorText(item) }}</p>
                    <div v-if="item.excerpt" class="wk-quote wk-md wk-md-quote" v-html="renderMarkdown(item.excerpt)" />
                    <p v-else-if="item.excerpt_withheld" class="wk-note">
                      原文被授权策略扣留：<strong>有</strong>原文，但这一次不给。这与「没有可引用原文」是两件事。
                    </p>
                    <p v-else class="wk-note">
                      没有可引用的原文（服务端标注 <code class="wk-code">excerpt_available=false</code>）。
                    </p>
                    <p v-if="item.provenance_incomplete?.applies" class="wk-warn wk-warn-tight">
                      这条引用的溯源不完整，详见下方「声明与提示」里的说明。
                    </p>
                    <!-- 技术信息收进折叠区：引用 id、来源版本 JSON、两个哈希。
                         这些是给排查用的，不该占在正文主流程里（原来是一大坨 JSON 摊在句子里）。 -->
                    <details class="wk-ev-tech">
                      <summary>技术信息（引用 id / 来源版本 / 哈希）</summary>
                      <dl class="wk-kv">
                        <dt>引用 id</dt>
                        <dd class="wk-mono">{{ item.evidence_ref_id }}</dd>
                        <dt>内容摘要</dt>
                        <dd class="wk-mono">{{ item.content_sha256 || '（字段缺失）' }}</dd>
                        <dt>指纹</dt>
                        <dd class="wk-mono">{{ item.fingerprint || '（字段缺失）' }}</dd>
                        <template v-if="item.source_version">
                          <dt>来源版本</dt>
                          <dd class="wk-mono">{{ prettyJson(item.source_version) }}</dd>
                        </template>
                      </dl>
                    </details>
                  </li>
                </ul>
              </section>

              <!-- 运行轨迹 -->
              <section class="wk-card" data-testid="runs-card">
                <h2 class="wk-card-title">
                  运行轨迹<span class="wk-card-note">错误摘要必须能直接读到，不藏在展开里</span>
                </h2>

                <p v-if="eventsUnavailable" class="wk-warn" data-testid="events-unavailable">
                  轨迹事件<strong>读取失败</strong>：这里<strong>不</strong>显示成「没有事件」，
                  因为「读不到」与「确实没有」是两件事。
                </p>

                <h3 class="wk-sub-title">相关运行（{{ runs.length }} 条）</h3>
                <p v-if="!runs.length && !partErrors.provenance" class="wk-note">
                  本次返回的运行列表为空（确实没有关联运行，而不是读不到）。
                </p>
                <ul v-else class="wk-list">
                  <li v-for="run in runs" :key="run.run_id" class="wk-list-item">
                    <p class="wk-list-head">
                      <span class="wk-mono">{{ run.run_id }}</span>
                      <span class="wk-list-sub">
                        {{ text(run.route) }} · {{ text(run.status) }}
                        <template v-if="run.elapsed_ms != null"> · {{ Math.round(run.elapsed_ms) }} ms</template>
                      </span>
                    </p>
                    <p class="wk-note">问题：{{ text(run.question) }}</p>
                    <p class="wk-note">
                      开始 {{ whenText(run.started_at) || '（字段缺失）' }} ·
                      结束 {{ whenText(run.ended_at) || '（字段缺失）' }} ·
                      事件 {{ run.event_count ?? '（字段缺失）' }} 条
                    </p>
                    <!-- 错误摘要三种形状分开显示；有错误时权重不低（加粗 + 文字标记） -->
                    <p
                      class="wk-errline"
                      :class="{
                        'is-error': errorSummaryOf(run).kind === 'error',
                        'is-none': errorSummaryOf(run).kind === 'none',
                        'is-missing': errorSummaryOf(run).kind === 'unavailable' || errorSummaryOf(run).kind === 'missing',
                      }"
                      data-testid="run-error-summary"
                    >
                      错误摘要（{{ errorSummaryOf(run).kind }}）：{{ errorSummaryOf(run).text }}
                    </p>
                    <p v-if="run.event_types?.length" class="wk-note wk-mono">
                      事件类型：{{ run.event_types.join(' → ') }}
                    </p>
                  </li>
                </ul>

                <h3 class="wk-sub-title">事件时间线（{{ events.length }} 条，按真实时间顺序）</h3>
                <p v-if="!events.length" class="wk-note">
                  {{ eventsUnavailable ? '事件列表为空是因为读取失败（见上方提示）。' : '没有可见事件（visibility != visible 的事件按契约不返回）。' }}
                </p>
                <ol v-else class="wk-timeline">
                  <li
                    v-for="event in events"
                    :key="event.event_id"
                    class="wk-event"
                    :class="{ 'is-error': eventIsError(event) }"
                  >
                    <span class="wk-event-mark" aria-hidden="true">{{ eventIsError(event) ? '!' : '·' }}</span>
                    <span class="wk-event-body">
                      <span class="wk-event-type">{{ event.event_type }}</span>
                      <span class="wk-event-meta">
                        {{ text(event.status) }} · {{ text(event.agent_name) }}
                        <template v-if="event.tool_name"> · 工具 {{ event.tool_name }}</template>
                        · {{ whenText(event.started_at) || '（无时间）' }}
                        <template v-if="event.duration_ms != null"> · {{ Math.round(event.duration_ms) }} ms</template>
                      </span>
                      <span v-if="event.error_code" class="wk-event-error">错误码 {{ event.error_code }}</span>
                    </span>
                  </li>
                </ol>
              </section>

              <!-- 复核记录 -->
              <section class="wk-card" data-testid="reviews-card">
                <h2 class="wk-card-title">
                  复核记录<span class="wk-card-note">只追加、不改删</span>
                </h2>
                <p v-if="!reviewEvents.length" class="wk-note">
                  本版本没有复核记录（<code class="wk-code">review_events: []</code>）。
                  没有任何复核记录的版本不能发布（服务端会以 WIKI_REVIEW_REQUIRED 拒绝）。
                </p>
                <ul v-else class="wk-list">
                  <li v-for="event in reviewEvents" :key="event.review_event_id" class="wk-list-item">
                    <p class="wk-list-head">
                      {{ text(event.action) }}
                      <span class="wk-list-sub">
                        {{ text(event.actor?.user_id) }} · {{ whenText(event.at) || '（无时间）' }}
                      </span>
                    </p>
                    <p v-if="event.verdict?.assertion" class="wk-note">
                      裁决：{{ statusLabel(event.verdict.assertion) }}
                    </p>
                    <p v-if="event.note" class="wk-note">备注：{{ event.note }}</p>
                    <p v-if="event.conflicted_assertions?.length" class="wk-note">
                      冲突主张：<span class="wk-mono">{{ event.conflicted_assertions.join('、') }}</span>
                    </p>
                  </li>
                </ul>
              </section>

              <!-- 声明与提示：provenance_incomplete / warnings / 授权与演示属性 -->
              <section class="wk-card" data-testid="notice-card">
                <h2 class="wk-card-title">声明与提示<span class="wk-card-note">缺失要可见</span></h2>

                <template v-if="provenanceSummary?.provenance_incomplete">
                  <p
                    class="wk-warn"
                    :class="{ 'wk-warn-tight': !provenanceSummary.provenance_incomplete.applies }"
                    data-testid="provenance-incomplete"
                  >
                    溯源完整性：{{ provenanceSummary.provenance_incomplete.applies ? '不完整' : '本次投影不适用' }}<template
                      v-if="provenanceSummary.provenance_incomplete.applies"
                    >。缺失字段 <span class="wk-mono">{{ (provenanceSummary.provenance_incomplete.missing_fields || []).join('、') || '（未列出）' }}</span>
                      · 理由码 <span class="wk-mono">{{ text(provenanceSummary.provenance_incomplete.reason_code) }}</span>
                      · 检出时间 {{ whenText(provenanceSummary.provenance_incomplete.detected_at) || '（字段缺失）' }}
                    </template>
                    <template v-else>。缺失的字段不会被推测补全。</template>
                  </p>
                </template>
                <p v-else class="wk-note">
                  服务端没有给出溯源完整性这一段，所以本页不判断它是「完整」还是「缺失」。
                </p>

                <ul v-if="provenanceMeta?.warnings?.length" class="wk-warn-list">
                  <li v-for="code in provenanceMeta.warnings" :key="code">
                    {{ WARNING_TEXT[code] || `服务端提示：${code}` }}
                  </li>
                </ul>

                <p class="wk-note">
                  权限口径：本页所有读取都走同一份授权投影，「看不到」不等于「不存在」。
                  <template v-if="provenanceMeta?.truncated">证据引用已因上限截断，上方已标明。</template>
                  <template v-else>分页、邻域、轨迹的「还有后续」与「已截断」分别标注在各自区块里。</template>
                </p>
                <p v-if="entry?.demo_data" class="wk-warn" data-testid="demo-notice">
                  本库数据是<strong>合成演示数据</strong>，不代表真实工厂数据、真实审批或独立准确率结论。
                </p>
                <p class="wk-note">
                  词条自报的可见关系数：{{ entry?.relation_counts?.visible ?? '（字段缺失）' }}
                  （统计口径 {{ entry?.relation_counts?.scope ?? '（缺失）' }}；
                  该词条层面是否被截断：{{ entry?.relation_counts?.truncated == null ? '（字段缺失）' : entry.relation_counts.truncated ? '是' : '否' }}）。
                  它和本页上面「邻域已加载数」不是同一个口径。
                </p>
                <p class="wk-note">
                  版本数 {{ entry?.revision_count ?? '（字段缺失）' }} ·
                  证据引用数 {{ entry?.evidence_ref_count ?? '（字段缺失）' }} ·
                  最后更新 {{ whenText(entry?.updated_at) || '（字段缺失）' }}
                </p>
              </section>
            </section>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 wk- 前缀（见文件头注释）。 */

.wk-shell {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-void);
}

.wk-center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* ── 顶栏 ── */
/* 头部留白放大：原来 14/10，三行内容挤在一起。 */
.wk-head {
  flex-shrink: 0;
  padding: 20px 28px 16px;
  border-bottom: 1px solid var(--color-line);
}

.wk-head-top {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

/* 标题与「模型接入」页一致：蓝色、15px、600（原来是 16px 灰黑，属离群值） */
.wk-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
/* 标题后的「（数据合成）」括注：只在本词条 demo_data 为真时出现，颜色跟标题同色但降一档字重与字号，不抢标题。 */
.wk-title-note {
  font-size: 13px;
  font-weight: 400;
  color: var(--color-body);
}

/* 状态标签：原来紧贴标题，现在单独一行。 */
.wk-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
/* 元信息：词条 / 类型 / 分类 三格横排，标签在上、取值在下，不再用 · 串成一行。 */
/* 三行：每项占一行，标签在左、取值在右（原来三格横排，用户要求改成三行）。 */
.wk-sub {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 12px 0 0 0;
  font-size: 13.5px;
  color: var(--color-text);
}
.wk-sub-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
/* 标签固定一档宽度，三行的取值左边缘对齐。 */
.wk-sub-key {
  flex: 0 0 auto;
  min-width: 2.4em;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-accent);
}
/* 三行取值统一成同一种样式：都是正文字体、黑色、可换行。原来词条那行是 code 框（浅底 + 等宽），另两行是纯文本。 */
.wk-sub-val {
  min-width: 0;
  word-break: break-all;
  font-variant-numeric: tabular-nums;
}


/* 口径标签统一蓝底白字加粗椭圆（原来是灰底描边）；-demo 是警告语义色，保留 */
.wk-tag {
  display: inline-block;
  padding: 1px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  color: #ffffff;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.6;
  white-space: nowrap;
}

/* 演示数据标记：橙色是警告语义，保留；但基础样式现在是蓝底，必须自带浅橙底，
   否则会变成「蓝底橙字」（灰/橙字压蓝底读不出来）。与 .fields-badge.is-warn 同一写法。 */
.wk-tag-demo {
  border-color: transparent;
  background: #d97706;
  color: #ffffff;
  font-weight: 600;
}

.wk-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.wk-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.wk-field-label {
  font-size: 13px;
  color: var(--color-text);
}

.wk-select {
  max-width: 340px;
  padding: 5px 8px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
  font-family: inherit;
  font-size: 13.5px;
}

.wk-select:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

/* 工具条按钮统一蓝底白字加粗椭圆（原来是白底细描边）；ghost 变体与基础等价 */
.wk-btn {
  padding: 5px 14px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  color: #ffffff;
  font-family: inherit;
  font-size: 13.5px;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
}

.wk-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}

.wk-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

/* 禁用态只降透明度、不改文字色（蓝底压灰字对比度极差），与 .mp-btn:disabled 一致 */
.wk-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ghost 变体与基础样式等价（保留类名，避免动模板） */
.wk-btn-ghost {
  background: var(--color-accent);
  color: #ffffff;
}

.wk-hint-inline {
  margin: 10px 0 0;
  font-size: 12.5px;
  color: var(--color-text);
}

.wk-code {
  padding: 1px 5px;
  border: 1px solid var(--color-line);
  border-radius: 5px;
  background: var(--color-ink);
  color: var(--color-body);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  word-break: break-all;
}

.wk-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  word-break: break-all;
}

/* ── 内容区 ── */
.wk-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 28px 28px;
}

.wk-loading {
  margin: 0;
  font-size: 14px;
  color: var(--color-text);
}

/* 窄屏分步切换：页面级切换栏（不是卡片里的小控件） */
.wk-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.wk-step {
  flex: 1 1 220px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  color: var(--color-body);
  font-family: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.wk-step small {
  font-size: 12.5px;
  color: var(--color-text);
}

.wk-step.is-active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 500;
}

.wk-step:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wk-cols {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 宽屏：关系图与详情并排（窄屏才会走上面的分步切换） */
.wk-cols.is-split {
  display: grid;
  grid-template-columns: minmax(0, 46%) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.wk-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

/* 两栏标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.wk-col-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wk-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  background: var(--color-panel);
  border: 1px solid var(--color-line);
  border-radius: 12px;
}

.wk-card-error {
  border-color: rgba(225, 29, 72, 0.4);
}

/* 卡片标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.wk-card-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wk-card-note {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--color-text);
}

/* 子标题与其它页统一：蓝色、15px、600（原来是 13.5px 灰黑，属离群值） */
.wk-sub-title {
  margin: 6px 0 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wk-note {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
}

/* 说明文字按条列出：每行一条、前面一个蓝点（与 WikiPage 的 .wk-note-list 同一套写法）。
   类名前缀 wk- 不会撞 Tailwind 工具类。 */
.wk-note-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
}
.wk-note-list > li {
  position: relative;
  padding-left: 15px;
}
.wk-note-list > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.62em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
}
.wk-note-list strong {
  color: var(--color-text);
}

.wk-note-inline {
  font-size: 12.5px;
  color: var(--color-text);
}

.wk-error-msg {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #b91c1c;
  word-break: break-word;
}

/* 浅底错误/警告条：保持浅底（红/橙是判据），只把圆角收敛到 12px */
/* 去框：改左侧 2px 红细条 + 黑色正文。原来这里是「描边 + 圆角 + 浅底」的卡片，与全站其它提示块不统一；红作为语义保留在细线上。 */
.wk-error-note {
  margin: 0 0 18px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid #e11d48;
  border-radius: 0;
  background: none;
  color: var(--color-text);
}

/* 去框：改左侧 2px 橙细条 + 黑色正文。原来这里是「描边 + 圆角 + 浅底」的卡片，与全站其它提示块不统一；橙作为语义保留在细线上。 */
.wk-warn {
  margin: 0 0 18px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid #d97706;
  border-radius: 0;
  background: none;
  color: var(--color-text);
}

.wk-warn-tight {
  padding: 5px 8px;
}

.wk-warn-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: #92400e;
}

.wk-kv {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 3px 10px;
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.wk-kv dt {
  color: var(--color-muted);
}

.wk-kv dd {
  margin: 0;
  color: var(--color-text);
  word-break: break-word;
}

/* 正文：渲染后的 Markdown（`components/wiki/markdown.ts` 产出的受控 HTML）。
   原来是 <pre> + 等宽字体 —— 那让 `#`、`>`、反引号、`|---|` 全裸着，看着像代码。
   现在按本站的排版走：标题蓝色加粗、正文黑色、行距 1.75、表格有边框、
   行内 code 用等宽浅底。不再用等宽字体、不再 white-space: pre-wrap，
   也不再加 max-height 内部滚动（一份文档应该有文档的样子，而不是一个代码框）。

   **所有后代选择器都要走 `:deep()`**：这个 <style> 是 scoped，而 scoped 的实现是给
   模板里的元素加 `data-v-…` 属性；`v-html` 注入的节点**不带**那个属性，于是
   `.wk-md h3` 会被编译成 `.wk-md h3[data-v-…]` 而一条都匹配不上 —— 实测第一版就是
   这样：文字换行对了，但标题不蓝、表格没边框、列表没圆点。`:deep()` 让后代不受
   scope 约束，而容器 `.wk-md` 本身仍然受（不会外溢到别处）。 */
.wk-md {
  margin: 0 0 2px;
  color: var(--color-text);
  font-family: inherit;
  font-size: 14px;
  line-height: 1.75;
  word-break: break-word;
}

.wk-md :deep(h3),
.wk-md :deep(h4),
.wk-md :deep(h5) {
  margin: 18px 0 8px;
  color: var(--color-accent);
  font-weight: 700;
  line-height: 1.5;
}

.wk-md :deep(> :first-child) {
  margin-top: 0;
}

.wk-md :deep(h3) {
  font-size: 17px;
}

.wk-md :deep(h4) {
  font-size: 15px;
}

.wk-md :deep(h5) {
  font-size: 14px;
}

.wk-md :deep(p) {
  margin: 0 0 10px;
  color: var(--color-text);
  line-height: 1.75;
}

.wk-md :deep(ul),
.wk-md :deep(ol) {
  margin: 0 0 10px;
  padding-left: 22px;
}

.wk-md :deep(ul) {
  list-style: disc;
}

.wk-md :deep(ol) {
  list-style: decimal;
}

.wk-md :deep(li) {
  margin: 2px 0;
  color: var(--color-text);
  line-height: 1.75;
}

/* 列表项里的嵌套块（正文把表格挂在证据项下面）。 */
.wk-md :deep(.wkm-li-child > :first-child) {
  margin-top: 6px;
}

.wk-md :deep(.wkm-li-child > :last-child) {
  margin-bottom: 0;
}

.wk-md :deep(blockquote) {
  margin: 0 0 12px;
  padding: 2px 0 2px 12px;
  border-left: 2px solid var(--color-line);
  color: var(--color-muted);
  font-size: 12.5px;
  line-height: 1.7;
}

.wk-md :deep(blockquote p) {
  margin: 0;
  color: var(--color-muted);
  font-size: 12.5px;
  line-height: 1.7;
}

.wk-md :deep(code) {
  padding: 0 4px;
  border: 1px solid var(--color-line);
  border-radius: 4px;
  background: var(--color-ink);
  color: var(--color-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
}

.wk-md :deep(table) {
  margin: 4px 0 12px;
  border-collapse: collapse;
  border: 1px solid var(--color-line);
  font-size: 13px;
}

.wk-md :deep(th),
.wk-md :deep(td) {
  padding: 5px 12px;
  border: 1px solid var(--color-line);
  text-align: left;
  vertical-align: top;
  line-height: 1.7;
}

.wk-md :deep(thead th) {
  background: var(--color-ink);
  color: var(--color-text);
  font-weight: 700;
  white-space: nowrap;
}

.wk-md :deep(strong) {
  font-weight: 700;
}

/* 正文里的引用编号 [1]（与下方 citations 是同一套编号）：只上色，不做成链接，
   因为它不是锚点（hash 路由下锚点会跳到不存在的路由，本页已经踩过）。 */
.wk-md :deep(.wkm-marker) {
  color: var(--color-accent);
  font-variant-numeric: tabular-nums;
}

.wk-cite-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}

.wk-cite-btn {
  margin-right: 5px;
  padding: 1px 6px;
  border: 1px solid rgba(37, 99, 235, 0.4);
  border-radius: 6px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--color-accent);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
}

.wk-cite-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wk-linkbtn {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-accent);
  font-family: inherit;
  font-size: 13px;
  text-decoration: underline;
  cursor: pointer;
  word-break: break-all;
}

.wk-linkbtn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wk-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wk-list-item {
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  outline: none;
}

/* 被 focusEvidence 定位到的证据：焦点环 + 底色，键盘用户也能看出"跳到哪一条" */
.wk-list-item:focus-visible {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.25);
}

.wk-list-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--color-text);
}

.wk-list-sub {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--color-text);
}

/* 证据条目：种类标签压小、技术信息折叠区。 */
.wk-ev-kind {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-accent);
}
.wk-ev-tech {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--color-body);
}
.wk-ev-tech > summary {
  cursor: pointer;
  color: var(--color-accent);
}
.wk-ev-tech .wk-kv {
  margin-top: 6px;
  word-break: break-all;
}
/* 引用原文用 Markdown 渲染后，外边距由这里接管（原来是 <blockquote> 的默认缩进）。 */
.wk-md-quote {
  margin: 6px 0 0;
  padding-left: 12px;
  border-left: 2px solid var(--color-line);
}

.wk-quote {
  margin: 6px 0 0;
  padding-left: 9px;
  border-left: 2px solid var(--color-line);
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
  white-space: pre-wrap;
  word-break: break-word;
}

.wk-assertions {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.wk-assertion {
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
}

/* 断言标题与其它页统一：蓝色、15px、600（原来是 14px 灰黑） */
.wk-assertion-head {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

.wk-assertion-value {
  margin-left: 8px;
  font-weight: 400;
  color: var(--color-body);
}

/* 错误摘要三种形状：有错误最重（加粗 + 左侧记号），"无错误"最轻，缺失/不可用居中并且不被当成无错误 */
.wk-errline {
  margin: 0;
  padding: 4px 8px;
  border-left: 3px solid var(--color-line);
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
  word-break: break-word;
}

.wk-errline.is-error {
  border-left-color: #e11d48;
  color: #b91c1c;
  font-weight: 600;
}

.wk-errline.is-none {
  border-left-color: #16a34a;
}

.wk-errline.is-missing {
  border-left-color: #d97706;
  color: #92400e;
}

.wk-timeline {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wk-event {
  display: flex;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
  line-height: 1.6;
}

.wk-event-mark {
  width: 12px;
  flex-shrink: 0;
  text-align: center;
  color: var(--color-muted);
}

/* 错误事件的视觉权重**不得低于**普通事件：它加粗、加左侧记号 */
.wk-event.is-error .wk-event-mark {
  color: #e11d48;
  font-weight: 700;
}

.wk-event-body {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  min-width: 0;
}

.wk-event-type {
  color: var(--color-text);
}

.wk-event.is-error .wk-event-type {
  color: #b91c1c;
  font-weight: 600;
}

.wk-event-meta {
  color: var(--color-text);
}

.wk-event-error {
  color: #b91c1c;
  font-weight: 600;
}

.wk-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 可点的证据胶囊：蓝底白字加粗椭圆（原来是灰底描边） */
.wk-chip {
  padding: 3px 11px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  color: #ffffff;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.wk-chip:hover {
  background: #1d4ed8;
  color: #ffffff;
}

.wk-chip:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.wk-folds {
  font-size: 13px;
  color: var(--color-body);
}

.wk-folds summary {
  cursor: pointer;
  color: var(--color-accent);
}

.wk-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
}

/* 窄屏：分步切换本身就是响应式布局，正文与关系图各占一步；这里只收一下留白 */
@media (max-width: 1100px) {
  .wk-head {
    padding: 12px 16px 8px;
  }
  .wk-body {
    padding: 12px 16px 22px;
  }
  .wk-select {
    max-width: 220px;
  }
}
</style>
