import { computed, ref } from 'vue'

import {
  createSession,
  deleteSession,
  getSession,
  listSessionResults,
  listSessions,
  renameSession,
} from '../api/client'
import { formatStamp } from './useModelAccess'
import type { FinalResult, SessionMessage, SessionRunResult, SessionSummary } from '../api/types'

/**
 * 会话（chat session）在前端只有**一份**模块级状态。
 *
 * WHY 必须是单例：侧边栏渲染列表、主区渲染时间线，两边读的是同一个 activeId；
 * 昵称/删除会让列表项消失或改名，切换会话要同时换消息与证据图。若每个组件各持
 * 一份 ref，就会出现「侧边栏高亮 A、主区显示 B」这种自相矛盾的状态
 * （与 useModelAccess 同一套写法，理由也相同）。
 *
 * 数据来源：GET/POST/PATCH/DELETE /v1/sessions（后端实现见 datamind/sessions.py）。
 * 后端**没有** messages 表：一次问答的完整记录就是 `runs` 里的一行
 * （question / answer / status / started_at），`runs.conversation_id` 把它挂到会话上。
 * 所以一个 SessionMessage = 一个 run，读出来时摊平成「用户气泡 + 助手气泡」。
 *
 * 富内容（SQL 结果表 / 引用 / 工业核验）**不在** `runs` 行里，而在该 run 终态 trace 事件
 * 的 `output_summary` 里；走独立接口 GET /v1/sessions/{id}/results 按 run 分页取
 * （最近优先，默认 20 轮）。为什么不分页不行：单轮负载平均 14 kB、最大 140 kB，
 * 一个 221 轮的会话全量就是约 4 MB。窗口之外的更早轮次只有正文，这是**已知边界**。
 *
 * 契约：这里所有动作都**不抛异常**，失败只写 `error`（一句中文），调用方照常渲染。
 */

/** 活动会话 id 的落盘键（沿用仓库的 `datamind.` 前缀） */
export const ACTIVE_STORAGE_KEY = 'datamind.sessions.active'
/** 历史区折叠状态的落盘键：折叠是「这一屏只想来问问题」的用户偏好，不是一次性状态 */
export const COLLAPSED_STORAGE_KEY = 'datamind.sessions.collapsed'
/** 每页条数：与后端 Query 的默认值一致（1..50） */
const PAGE_SIZE = 20
/**
 * 每次取多少轮的富内容（SQL 结果表 / 引用 / 工业核验）。
 *
 * 与 PAGE_SIZE 同值只是巧合：这里限的是**另一件东西**——每轮终态负载平均 14 kB、
 * 最大 140 kB，所以窗口要按字节而不是按「条数好看」来定。20 轮 ≈ 三百多 kB，
 * 而窗口外的更早轮次只剩正文（边界见 docs/SESSIONS.md）。
 */
const RESULT_PAGE_SIZE = 20
/** 标题长度上限，与 DDL 的 CHECK (length(title) <= 200) 对齐 */
const TITLE_MAX = 200
/** 运行中不允许切换/删除会话时的提示（见 setBusyGuard 的注释） */
const BUSY_TEXT = '正在生成回答，完成后再切换或删除会话'

/** 后端 UPPER_SNAKE 错误码 → 中文；未知码回落到原始 message */
const ERROR_TEXT: Record<string, string> = {
  SESSION_NOT_FOUND: '会话不存在或已被删除',
  SESSION_TITLE_REQUIRED: '会话标题不能为空',
  SESSION_TITLE_TOO_LONG: `会话标题过长（最多 ${TITLE_MAX} 字）`,
  SESSION_QUERY_TOO_LONG: '搜索词过长',
  SESSION_QUERY_INVALID: '搜索词不合法',
  SESSION_PAGINATION_INVALID: '分页参数不合法',
}

/* ── 模块级单例状态 ───────────────────────────────────────────────────── */

const sessions = ref<SessionSummary[]>([])
const activeId = ref<string | null>(null)
const activeSession = ref<SessionSummary | null>(null)
/** 列表搜索词：直接 v-model 到侧边栏输入框，refresh() 读它 */
const query = ref('')
const loading = ref(false)
const error = ref('')
const hasMore = ref(false)
/** 活动会话的 transcript（每个 run 一条），切换会话时整体替换 */
const messages = ref<SessionMessage[]>([])
/** 活动会话的富内容（每个 run 一条，`result` 可能是 null），只覆盖最近 RESULT_PAGE_SIZE 轮 */
const results = ref<SessionRunResult[]>([])
/** 新建会话在飞行中（侧边栏据此禁用按钮） */
const creating = ref(false)
/** 任意一次改名/删除在飞行中 */
const busy = ref(false)
/** 正在删除的会话 id（侧边栏据此禁用该行） */
const removingId = ref<string | null>(null)
/**
 * 历史区是否折叠。**默认展开**——主流 AI 应用的历史是默认可见的，把列表藏进一个按钮
 * 会让每次「找回上次的对话」都多一次点击。折叠只是给「这一屏只想来问问题」的用户让出
 * 导航空间，且是持久偏好（落盘）。
 */
const historyCollapsed = ref(readStoredCollapsed())
/**
 * 「请展开并聚焦搜索框」的请求序号：+1 即一次请求。
 *
 * 为什么用序号而不是布尔量：连续两次请求（落地页点两次）必须都能触发聚焦，布尔量在
 * 第二次就「已经是 true」而不再变化。搜索框的 DOM 在侧栏组件里，所以这里只发信号，
 * 由 SideNav `watch` 它去 `focus()`——组合式函数不碰 DOM。
 */
const historyFocusSeq = ref(0)

let bootstrapped = false
/** 已加载 transcript 的会话 id：命中就不重复请求 */
let loadedFor: string | null = null
/** 同一会话的并发加载合并成一个 promise，select() 与 watch 不会打两次 */
let inflightLoad: { id: string; promise: Promise<SessionHistory> } | null = null
/** 列表请求序号：搜索抖动时丢弃过期响应，避免旧结果盖掉新结果 */
let listSeq = 0
/** 「运行中」守卫，由 MainPage 注册（见 setBusyGuard） */
let busyGuard: (() => boolean) | null = null

/* 会话接口调用入口就是 client.ts 的 6 个具名导出（listSessions / getSession /
   listSessionResults / createSession / renameSession / deleteSession），直接 import 使用。
   这里曾经有一层「命名空间取值」的间接层，用来在 client.ts 尚未合入时不打断构建；
   现在那 6 个函数已经就位，间接层反而会掩盖签名漂移（它靠一段手写 interface 断言，
   真实签名改了 TypeScript 也发现不了），故删除。 */

/**
 * 一次历史加载的完整结果：正文（全部轮次）+ 富内容（最近若干轮）。
 *
 * 两者必须一起返回：分开两次 await 会让 MainPage 有机会先用正文渲染一遍、
 * 再用富内容覆盖一次，中间的闪动（结果表先没有后出现）没有必要。
 */
export interface SessionHistory {
  messages: SessionMessage[]
  results: SessionRunResult[]
}

/* ── 小工具 ───────────────────────────────────────────────────────────── */

/** 任何异常 → 一句中文；优先认错误码，其次在 message 里找码，最后回落原文 */
function toText(e: any, fallback: string): string {
  const code = typeof e?.errorCode === 'string' ? e.errorCode : ''
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  const raw = typeof e?.message === 'string' ? e.message : ''
  if (raw) {
    for (const key of Object.keys(ERROR_TEXT)) {
      if (raw.includes(key)) return ERROR_TEXT[key]
    }
    // 后端 detail 可能是 JSON 字符串，码就藏在里面
    return `${fallback}：${raw}`
  }
  return fallback
}

/**
 * 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前，再久就退回绝对时间。
 * 列表里只需要「新不新」，精确到分钟的意义不大，但超过一周还是日期更好读。
 */
function relativeTime(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  if (diff < 0) return formatStamp(iso)
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`
  return formatStamp(iso)
}

/** 读取落盘的 activeId：格式不对（被改坏/别的页面写过）一律当「没有」，不报错 */
function readStoredActive(): string | null {
  try {
    const raw = localStorage.getItem(ACTIVE_STORAGE_KEY)
    if (typeof raw !== 'string' || !raw) return null
    // 服务端格式："sess-" + uuid4().hex[:24]（共 29 字符），放宽到 64 以内
    if (!raw.startsWith('sess-') || raw.length > 64) return null
    return raw
  } catch {
    return null
  }
}

function persistActive(id: string | null): void {
  try {
    if (id) localStorage.setItem(ACTIVE_STORAGE_KEY, id)
    else localStorage.removeItem(ACTIVE_STORAGE_KEY)
  } catch {
    /* 隐私模式/存储被禁：不影响本次会话内的状态 */
  }
}

/** 读回折叠偏好：读不到/被改坏一律当「展开」（历史默认可见） */
function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function setHistoryCollapsed(collapsed: boolean): void {
  historyCollapsed.value = collapsed
  try {
    if (collapsed) localStorage.setItem(COLLAPSED_STORAGE_KEY, '1')
    else localStorage.removeItem(COLLAPSED_STORAGE_KEY)
  } catch {
    /* 同 persistActive：落盘失败不影响本次会话内的状态 */
  }
}

function toggleHistory(): void {
  setHistoryCollapsed(!historyCollapsed.value)
}

/**
 * 展开历史区并请求聚焦搜索框（落地页「历史对话」按钮、外部深链用）。
 * 顺序固定：先展开再发信号，否则折叠状态下聚焦了一个不可见的输入框。
 */
function revealHistory(): void {
  setHistoryCollapsed(false)
  historyFocusSeq.value += 1
}

/** 把服务端返回的 session 合并进列表（改名后 updated_at 变新，按后端排序挪到最前） */
function mergeSession(session: SessionSummary | null | undefined): void {
  if (!session || typeof session.session_id !== 'string' || !session.session_id) return
  const rest = sessions.value.filter((s) => s.session_id !== session.session_id)
  sessions.value = [session, ...rest]
}

/* ── 动作 ─────────────────────────────────────────────────────────────── */

/** 拉第一页（或搜索结果）。传 q 会同时改写 query，供外部脚本/测试使用。 */
async function refresh(nextQuery?: string): Promise<void> {
  if (nextQuery !== undefined) query.value = nextQuery
  const token = ++listSeq
  loading.value = true
  try {
    const res = await listSessions({
      q: query.value.trim() || undefined,
      limit: PAGE_SIZE,
      offset: 0,
    })
    if (token !== listSeq) return
    sessions.value = Array.isArray(res?.sessions) ? res.sessions : []
    hasMore.value = !!res?.has_more
    error.value = ''
  } catch (e: any) {
    if (token !== listSeq) return
    sessions.value = []
    hasMore.value = false
    error.value = toText(e, '会话列表加载失败')
  } finally {
    if (token === listSeq) loading.value = false
  }
}

/** 追加下一页：offset 用当前已加载数量，同 id 去重（并发/重复点击都安全） */
async function loadMore(): Promise<void> {
  if (loading.value || !hasMore.value) return
  const token = listSeq
  loading.value = true
  try {
    const res = await listSessions({
      q: query.value.trim() || undefined,
      limit: PAGE_SIZE,
      offset: sessions.value.length,
    })
    if (token !== listSeq) return
    const known = new Set(sessions.value.map((s) => s.session_id))
    const extra = (Array.isArray(res?.sessions) ? res.sessions : []).filter(
      (s) => s && !known.has(s.session_id),
    )
    sessions.value = [...sessions.value, ...extra]
    hasMore.value = !!res?.has_more
    error.value = ''
  } catch (e: any) {
    if (token !== listSeq) return
    error.value = toText(e, '加载更多会话失败')
  } finally {
    if (token === listSeq) loading.value = false
  }
}

/**
 * 读取某个会话的 transcript（一个 run 一条）+ 最近若干轮的富内容。
 *
 * 结果只在「请求的 id 仍是当前 activeId」时才写回 messages/results——搜索/切换很快时
 * 旧请求可能后到，若无条件写回，主区会显示上一个会话的内容。
 *
 * 正文与富内容**并行**取（两个接口互不依赖）：串行等于让打开历史会话多等一个 RTT。
 * 富内容失败**不影响**正文：它只是「结果表 / 引用 / 星图」的补充，拿不到就退回纯文本，
 * 不能因为富内容接口出错就把整个会话判成加载失败（那是两件严重程度不同的事）。
 */
function loadTranscript(id: string): Promise<SessionHistory> {
  if (!id) return Promise.resolve({ messages: [], results: [] })
  if (loadedFor === id) return Promise.resolve({ messages: messages.value, results: results.value })
  if (inflightLoad && inflightLoad.id === id) return inflightLoad.promise

  const promise = (async () => {
    const [res, page] = await Promise.all([
      getSession(id),
      listSessionResults(id, { limit: RESULT_PAGE_SIZE, offset: 0 }).catch(() => null),
    ])
    const rows = Array.isArray(res?.messages) ? res.messages : []
    const rich = Array.isArray(page?.results) ? page.results : []
    if (activeId.value === id) {
      loadedFor = id
      messages.value = rows
      results.value = rich
      if (res?.session) activeSession.value = res.session
    }
    return { messages: rows, results: rich }
  })()
    .catch((e: any) => {
      // 详情读不到（404/网络/接口未就绪）：清掉激活态，当作「没有选中会话」，
      // 而不是让主区停在一个永远加载不出来的会话上。
      loadedFor = null
      if (activeId.value === id) {
        activeId.value = null
        activeSession.value = null
        messages.value = []
        results.value = []
        persistActive(null)
      }
      error.value = toText(e, '会话内容加载失败')
      return { messages: [] as SessionMessage[], results: [] as SessionRunResult[] }
    })
    .finally(() => {
      if (inflightLoad && inflightLoad.id === id) inflightLoad = null
    })

  inflightLoad = { id, promise }
  return promise
}

/** 读当前活动会话的 transcript（没有活动会话就返回空） */
function loadActive(): Promise<SessionHistory> {
  const id = activeId.value
  return id ? loadTranscript(id) : Promise.resolve({ messages: [], results: [] })
}

/**
 * 选中一个会话：写 activeId（顺带落盘）并拉 transcript + 最近若干轮富内容。
 * 点已经选中的那一行是 no-op —— 本地这一份是**刚刚流式写进去**的原始富对象
 * （sql_result / citations / industrial_result），比重新拉取的服务端窗口更新、更全
 * （窗口只覆盖最近 RESULT_PAGE_SIZE 轮，且 live 态下窗口里根本还没有这一轮）。
 */
async function select(id: string): Promise<void> {
  if (!id || activeId.value === id) return
  if (busyGuard?.()) {
    error.value = BUSY_TEXT
    return
  }
  activeId.value = id
  persistActive(id)
  const hit = sessions.value.find((s) => s.session_id === id)
  if (hit) activeSession.value = hit
  await loadTranscript(id)
}

/**
 * 新建会话并立即切过去。
 * 之所以先本地插进列表再等刷新：新会话一定排在 updated_at DESC 的第一位，
 * 本地插入能让侧边栏立刻出现选中态，不用等一次往返。
 */
async function create(title?: string): Promise<SessionSummary | null> {
  if (busyGuard?.()) {
    error.value = BUSY_TEXT
    return null
  }
  if (creating.value) return activeSession.value
  creating.value = true
  try {
    const clean = typeof title === 'string' ? title.trim() : ''
    const res = await createSession(clean || undefined)
    const session = res?.session
    if (!session || typeof session.session_id !== 'string' || !session.session_id) {
      throw new Error('响应缺少 session_id')
    }
    mergeSession(session)
    activeId.value = session.session_id
    persistActive(session.session_id)
    activeSession.value = session
    messages.value = []
    results.value = []
    loadedFor = session.session_id
    error.value = ''
    return session
  } catch (e: any) {
    error.value = toText(e, '新建会话失败')
    return null
  } finally {
    creating.value = false
  }
}

/** 改名：空/超长的本地先拦一次，省一次往返，也保证提示与后端码一致 */
async function rename(id: string, title: string): Promise<boolean> {
  const clean = typeof title === 'string' ? title.trim() : ''
  if (!clean) {
    error.value = ERROR_TEXT.SESSION_TITLE_REQUIRED
    return false
  }
  if (clean.length > TITLE_MAX) {
    error.value = ERROR_TEXT.SESSION_TITLE_TOO_LONG
    return false
  }
  busy.value = true
  try {
    const res = await renameSession(id, clean)
    if (res?.session) {
      mergeSession(res.session)
      if (activeId.value === id) activeSession.value = res.session
    }
    error.value = ''
    return true
  } catch (e: any) {
    error.value = toText(e, '重命名会话失败')
    return false
  } finally {
    busy.value = false
  }
}

/**
 * 软删除会话。返回「删掉的是不是当前活动会话」。
 *
 * 删的若是活动会话，这里把 activeId 置空并清掉 transcript —— 主区 watch(activeId)
 * 看到 null 就会把消息与证据图一起清掉，不需要侧边栏去通知主区（模块级单例的意义）。
 */
async function remove(id: string): Promise<boolean> {
  if (!id || removingId.value) return false
  if (busyGuard?.()) {
    error.value = BUSY_TEXT
    return false
  }
  const wasActive = activeId.value === id
  removingId.value = id
  try {
    await deleteSession(id)
    sessions.value = sessions.value.filter((s) => s.session_id !== id)
    if (wasActive) {
      activeId.value = null
      activeSession.value = null
      messages.value = []
      results.value = []
      loadedFor = null
      persistActive(null)
    }
    error.value = ''
    return wasActive
  } catch (e: any) {
    error.value = toText(e, '删除会话失败')
    return false
  } finally {
    removingId.value = null
  }
}

/**
 * 注册「运行中」守卫（MainPage 传 `() => running.value`）。
 *
 * 选择**阻塞切换**而不是「快照后丢弃」：流式回答期间 assistant 那条消息对象
 * 还在被 SSE 回调持续写入，一旦切走就重建 messages，写入会落进一个已经不在
 * 数组里的对象上——用户看到的是答案凭空消失。阻塞只多一句提示，却不会丢任何
 * 已经产出（并且已经计费）的内容。
 */
function setBusyGuard(fn: (() => boolean) | null): void {
  busyGuard = fn
}

/** 清掉当前会话相关的服务端状态（登出/换账号时用；当前页面没有换账号入口） */
function reset(): void {
  sessions.value = []
  activeId.value = null
  activeSession.value = null
  messages.value = []
  results.value = []
  loadedFor = null
  inflightLoad = null
  hasMore.value = false
  error.value = ''
  persistActive(null)
}

/**
 * 页面加载时调用一次：读回落盘的 activeId，再拉第一页。
 *
 * activeId 的读回是**同步**的：MainPage 的 watch(activeId) 在挂载前就注册好了，
 * 侧边栏 onMounted 里读回之后主区才会去拉 transcript，两边不会各自请求一次。
 */
async function bootstrap(): Promise<void> {
  if (bootstrapped) return
  bootstrapped = true
  if (!activeId.value) activeId.value = readStoredActive()
  await refresh()
}

/* ── 视图模型 ─────────────────────────────────────────────────────────── */

export interface SessionRow {
  id: string
  title: string
  /** 列表里显示的相对时间 */
  whenText: string
  /**
   * 原始 `updated_at`（ISO）。时间分组按它分档——**不要**拿 `whenText` 反推：
   * 那是一个已经被人话术化过的字符串（「3 分钟前」），拿它分档等于用显示值做业务判断。
   */
  stamp: string
  /** 悬浮提示：绝对时间 + 最后一次提问 */
  tip: string
  active: boolean
  /** 该会话下已有的 run 数（列表接口的聚合字段，可能缺省） */
  runCount: number | null
}

/** 时间分档的显示名；数组顺序即列表顺序（最近在前） */
export const HISTORY_GROUPS = ['今天', '昨天', '前 7 天', '更早'] as const

/**
 * 某一行的本地日历日分档。
 *
 * WHY 用**日历日**而不是「距今 24 小时」：昨晚 23:50 的对话在凌晨 00:10 应该出现在
 * 「昨天」而不是「今天」——用户是按日历找东西的。用 `new Date(y, m, d - n)` 而不是
 * 减 `n * 86400000`，夏令时切换那天也不会算错一档。
 */
function historyBucket(stamp: string, now: Date): string {
  const at = new Date(stamp)
  if (Number.isNaN(at.getTime())) return '更早'
  const dayStart = (offsetDays: number) =>
    new Date(now.getFullYear(), now.getMonth(), now.getDate() - offsetDays).getTime()
  if (at.getTime() >= dayStart(0)) return '今天'
  if (at.getTime() >= dayStart(1)) return '昨天'
  if (at.getTime() >= dayStart(7)) return '前 7 天'
  return '更早'
}

export interface SessionGroup {
  /** 分组标题；搜索态只有一个空标题的组（不显示标题行） */
  title: string
  rows: SessionRow[]
}

export function useSessions() {
  const rows = computed<SessionRow[]>(() =>
    sessions.value.map((s) => {
      const title = typeof s.title === 'string' && s.title.trim() ? s.title : '未命名会话'
      const stamp = s.updated_at || s.created_at || ''
      const preview = typeof s.last_question === 'string' ? s.last_question : ''
      const absolute = stamp ? formatStamp(stamp) : ''
      return {
        id: s.session_id,
        title,
        whenText: relativeTime(stamp),
        stamp,
        tip: [absolute, preview].filter(Boolean).join(' · '),
        active: s.session_id === activeId.value,
        runCount: typeof s.run_count === 'number' ? s.run_count : null,
      }
    }),
  )

  /**
   * 分组后的列表。
   *
   * **搜索态不分组**：搜索结果的顺序仍然是 `updated_at DESC`（最近活动优先），
   * 不是相关度——分成「今天/昨天」只会让「为什么这条排在前面」更难理解。界面上也如实
   * 这样说明（见 SideNav 的搜索结果提示）。
   */
  const groupedRows = computed<SessionGroup[]>(() => {
    if (query.value.trim() || rows.value.length === 0) {
      return [{ title: '', rows: rows.value }]
    }
    const now = new Date()
    const buckets = new Map<string, SessionRow[]>()
    for (const row of rows.value) {
      const key = historyBucket(row.stamp, now)
      const bucket = buckets.get(key)
      if (bucket) bucket.push(row)
      else buckets.set(key, [row])
    }
    // 按 HISTORY_GROUPS 的固定顺序输出，空档不出现（不留空标题）。
    return HISTORY_GROUPS.filter((name) => buckets.has(name))
      .map((name) => ({ title: name, rows: buckets.get(name) as SessionRow[] }))
  })

  /** 「还没有任何会话」：与「搜索无结果」是两种状态，文案不能混 */
  const isEmpty = computed(
    () => !loading.value && !error.value && sessions.value.length === 0 && !query.value.trim(),
  )
  /** 「搜索没有结果」 */
  const noResults = computed(
    () => !loading.value && !error.value && sessions.value.length === 0 && !!query.value.trim(),
  )

  const activeTitle = computed(() => {
    if (activeSession.value && typeof activeSession.value.title === 'string') {
      return activeSession.value.title
    }
    return rows.value.find((r) => r.active)?.title ?? ''
  })

  return {
    // 状态
    sessions,
    rows,
    groupedRows,
    activeId,
    activeSession,
    activeTitle,
    query,
    loading,
    error,
    hasMore,
    messages,
    results,
    creating,
    busy,
    removingId,
    isEmpty,
    noResults,
    // 历史区（标题 / 折叠 / 聚焦请求）
    historyCollapsed,
    historyFocusSeq,
    // 动作
    bootstrap,
    refresh,
    loadMore,
    loadTranscript,
    loadActive,
    select,
    create,
    rename,
    remove,
    reset,
    setBusyGuard,
    toggleHistory,
    revealHistory,
  }
}
