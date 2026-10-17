<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { me } from '../api/client'
import type { AuthUser } from '../api/types'
import { useModelAccess } from '../composables/useModelAccess'
import { useSessions } from '../composables/useSessions'

const emit = defineEmits<{ (e: 'new-chat'): void }>()

const router = useRouter()
const route = useRoute()

/**
 * 本地身份 —— 以前这里写死了「未登录 · 只读浏览」，那句话没有任何代码依据：
 * 本机回环请求其实是以 local-admin（admin）身份进入的，也就是说界面在**低报**权限；
 * 反过来若真的没登录，它又会把「只读浏览」说成事实。两种情况下都不是如实陈述。
 * 现在直接读 GET /v1/auth/me 并把真实角色显示出来；读不到就说读不到。
 */
const identity = ref<AuthUser | null>(null)
const identityState = ref<'loading' | 'ok' | 'error'>('loading')
const identityError = ref('')

const ROLE_LABELS: Record<string, string> = {
  viewer: '浏览',
  reviewer: '复核',
  approver: '审批',
  admin: '管理',
}

const identityText = computed(() => {
  if (identityState.value === 'loading') return '读取身份…'
  if (identityState.value === 'error') return '身份读取失败'
  return identity.value?.display_name || '未登录'
})

const identityDetail = computed(() => {
  if (identityState.value === 'error') return identityError.value || '请稍后重试'
  const user = identity.value
  if (!user) return '匿名 · 只读浏览'
  const roles = (user.roles || []).map((r) => ROLE_LABELS[r] || r)
  return roles.length ? roles.join(' · ') : '未声明角色'
})

async function loadIdentity(): Promise<void> {
  try {
    const result = await me()
    identity.value = result?.user ?? null
    identityState.value = 'ok'
    identityError.value = ''
  } catch (e: any) {
    identity.value = null
    identityState.value = 'error'
    identityError.value = e?.errorCode === 'LOGIN_REQUIRED' ? '未登录' : e?.message || '请求失败'
  }
}

/** 当前路由是否命中该导航项。精确匹配，避免 /app 把其它项也点亮。 */
function isActive(to: string): boolean {
  return route.path === to
}

/**
 * 接入状态三件事分开显示，各自有各自的依据（细节见 useModelAccess 的注释）：
 *   服务健康 ← /health · 模型配置 ← /v1/model/config · 连通性 ← /v1/model/config/test
 * 以前这里只有一行「模型 已接入」，而它的依据仅仅是 /health 返回 200——
 * 后端在线不等于模型可用，那条状态是假的，已删掉。
 */
const {
  serviceText,
  serviceDot,
  configState,
  config,
  configText,
  configDot,
  connDot,
  connText,
  testing,
  canTest,
  testBlockedReason,
  lastTest,
  lastTestText,
  lastTestDetail,
  bootstrap,
  runTest,
} = useModelAccess()

/**
 * 会话列表：与主区共用同一个（模块级单例的）会话状态。
 * 侧边栏只负责「列表 + 选中 + 删除」，transcript 由 MainPage 读同一个 activeId 后渲染，
 * 因此这里改 activeId 就等于让主区换内容，不需要 props/emit 传消息。
 */
const {
  rows: sessionRows,
  groupedRows: sessionGroups,
  query: sessionQuery,
  loading: sessionLoading,
  error: sessionError,
  hasMore: sessionHasMore,
  removingId: sessionRemovingId,
  creating: sessionCreating,
  isEmpty: sessionsEmpty,
  noResults: sessionsNoResults,
  historyCollapsed,
  historyFocusSeq,
  bootstrap: bootstrapSessionList,
  refresh: refreshSessions,
  loadMore: loadMoreSessions,
  select: selectSession,
  rename: renameSession,
  remove: removeSession,
  toggleHistory,
  revealHistory,
} = useSessions()

/**
 * 导航入口沿用旧版控制台（web/index.html）的那几项，能力一个都不少。
 * 旧版是后端的根页面（GET /），用 hash 深链切换视图：/#runs、/#fields …
 * 这里只做「图标 + 文字 + 分组小标题」的版式改造，入口本身保持可用。
 *
 * 现在**全部**是站内路由（to）：legacy 视图（模型接入/字段复核/来源权威规则/图纸与实体/
 * 尺寸核验/我的文件）都已经迁移进新前端，所以再没有 href 深链了 —— 那些深链指向的旧版
 * 控制台视图正是「点进去白屏」的根因。数据治理那 4 项后来又并成了 1 项（见 GROUPS），
 * 但四条路由仍然各自独立存在，深链照旧可用。
 */
interface NavItem {
  key: string
  label: string
  icon: string
  /** 站内路由：问数与问答就是本页 */
  to?: string
  /**
   * 站内**动作**而不是路由（当前没有条目使用，保留类型以备将来）。
   * （/app），若也给一个 `to`，两个条目会同时高亮，看着像有两个页面。
   */
  action?: 'history'
  /**
   * 旧版控制台的 hash 深链。所有条目都已迁移，当前没有任何条目再用它；
   * 字段与模板里的兜底分支一起保留，作为「万一又被塞进一个未迁移入口」时的
   * 安全网 —— 宁可显式禁用并标注，也不能让用户撞上白屏。
   */
  href?: string
}

const GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: '工作台',
    items: [
      { key: 'chat', label: '问数与问答', icon: '◎', to: '/app' },
      { key: 'runs', label: '运行记录', icon: '↺', to: '/runs' },
      { key: 'model', label: '模型接入', icon: '⚙', to: '/model' },
      /**
       * 数据治理：原先它自成一个分组，下面挂 4 条（字段复核 / 来源权威规则 / 图纸与实体 /
       * 尺寸核验）。那 4 条各占一行只让导航变长，多不出任何信息 —— 现在收成 **1 个平级条目**，
       * 四个板块改为**页面顶部的分栏导航**（/governance?tab=…，见 GovernancePage.vue）。
       *
       * WHY 直接并进本组、不再单立分组：分组标题只能又叫「数据治理」，标题 + 同名条目是重复。
       * 侧栏里现在就是一个和「运行记录」「模型接入」同级的条目。
       * 旧深链（/fields、/authority、/cad、/dimensions）仍然保留可用，见 router/index.ts。
       */
      { key: 'governance', label: '数据治理', icon: '⬡', to: '/governance' },
      /**
       * 知识图谱：`/#/wiki` 是**当前授权范围图**的入口（界面不得把它说成全库全图），
       * 词条正文是它的深链 `/#/wiki/:entryId`（见 router/index.ts 与 WikiPage.vue）。
       * 与上面几条同级、同样指向站内路由 —— 不是旧版控制台的 hash 深链。
       */
      { key: 'wiki', label: '知识图谱', icon: '◈', to: '/wiki' },
    ],
  },
  {
    title: '我的',
    items: [{ key: 'files', label: '我的文件', icon: '⇈', to: '/files' }],
  },
]

/* ── 历史区：折叠 / 行内重命名 / 行菜单 ─────────────────────────────── */

const searchEl = ref<HTMLInputElement | null>(null)

/**
 * 「展开并聚焦搜索框」的请求（落地页按钮、外部深链）。
 * 组合式函数只发序号信号，DOM 在这里处理：`revealHistory()` 已先把折叠态置为展开，
 * 所以这里只需 nextTick 之后聚焦——否则会聚焦一个还没渲染出来的输入框。
 */
watch(historyFocusSeq, async () => {
  await nextTick()
  searchEl.value?.focus()
})

/** 行菜单（⋯）当前展开的行：同一时刻只允许一行，点别处或 Esc 收起 */
const menuId = ref<string | null>(null)
/**
 * 菜单是否**向上**展开。
 *
 * WHY 需要它（实测 bug）：`.sess-menu` 是行内的 `position: absolute`，而列表容器
 * `.sessions` 是 `overflow-y: auto`（滚动裁剪容器）。靠近列表底部的行，菜单向下展开会
 * 被容器裁掉——`document.elementFromPoint(菜单项中心)` 返回的是侧栏本身，真实鼠标点击
 * 落不到「重命名/删除」上（实测最底部那行点了没有任何反应）。所以打开时量一次：
 * 下方放不下且上方放得下，就翻到行上方。
 *
 * 不把它 teleport 到 body 用 fixed 定位：那样滚动时菜单会脱开它所属的行；
 * 留在行内则菜单跟着行走（滚出视野一起消失），行为更可预期。
 */
const menuUp = ref(false)
/** 正在改名的行与草稿标题 */
const editingId = ref<string | null>(null)
const editTitle = ref('')
/** 改名前的原标题：内容没变就不发 PATCH（省一次往返，也避免空改名制造 updated_at 抖动） */
let editOriginal = ''

async function toggleMenu(id: string) {
  if (menuId.value === id) {
    menuId.value = null
    return
  }
  menuId.value = id
  menuUp.value = false
  await nextTick()
  const container = listEl.value
  const row = container?.querySelector('.sess-row[data-menu="true"]') ?? null
  const popup = container?.querySelector<HTMLElement>('[data-testid="session-menu-popup"]') ?? null
  if (container && row && popup) {
    const box = container.getBoundingClientRect()
    const item = popup.getBoundingClientRect()
    menuUp.value = item.bottom > box.bottom && row.getBoundingClientRect().top - item.height >= box.top
  }
}

async function startRename(id: string, title: string) {
  menuId.value = null
  armedId.value = null
  editingId.value = id
  editTitle.value = title
  editOriginal = title
  await nextTick()
  // 用查询取输入框而不是模板 ref：这个 input 在 v-for 里，模板 ref 会变成数组，
  // 而同一时刻只可能有一个，查询反而更直白。
  const input = listEl.value?.querySelector<HTMLInputElement>('.sess-edit') ?? null
  input?.focus()
  input?.select()
}

function cancelRename() {
  editingId.value = null
  editTitle.value = ''
  editOriginal = ''
}

/**
 * 保存改名。`rename()` 自己会做空白/超长校验并写中文提示（`ERROR_TEXT`），这里不重复实现。
 * 失焦也保存（主流应用的行为）；「Esc 取消」不受影响 —— cancelRename 先把 editingId 清空，
 * 随后那次 blur 会在这行开头直接返回。
 */
async function commitRename() {
  const id = editingId.value
  if (!id) return
  const next = editTitle.value
  editingId.value = null
  editTitle.value = ''
  if (next.trim() === editOriginal.trim()) return
  await renameSession(id, next)
}

function onEditKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault()
    void commitRename()
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    cancelRename()
  }
}

/* ── 会话列表：搜索 + 两步删除 ────────────────────────────────────────── */

/** 搜索框 250ms 防抖：每敲一个字都打一次 /v1/sessions 没有必要 */
let searchTimer: number | null = null
watch(sessionQuery, () => {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    searchTimer = null
    void refreshSessions()
  }, 250)
})

/**
 * 新对话：先清掉搜索词 —— 新会话标题是默认的「新对话」，若当前正筛着别的内容，
 * 它不会出现在筛选结果里，看着像没建成。真正的「建一个服务端会话」由 MainPage
 * 决定并调用组合式函数的 create()（emit 的语义保持 'new-chat' 不变）。
 */
function newChat() {
  sessionQuery.value = ''
  armedId.value = null
  menuId.value = null
  cancelRename()
  emit('new-chat')
}

/**
 * 每行的删除用「两步内联确认」：第一下把该行置为确认态（按钮变成「确认删除」），
 * 第二下才真的删。仓库里没有模态框组件，也不用 window.confirm —— 阻塞式弹窗在
 * headless 下无法断言，还会打断键盘操作。点别处或按 Esc 取消。
 */
const armedId = ref<string | null>(null)
const listEl = ref<HTMLElement | null>(null)

function armRow(id: string) {
  armedId.value = id
}
function disarmRow() {
  armedId.value = null
}

function onRowClick(id: string) {
  // 已进入确认态或正在改名的行不响应「切换会话」，免得确认/输入过程中误触
  if (armedId.value === id || editingId.value === id) return
  menuId.value = null
  void selectSession(id)
}

async function confirmDelete(id: string) {
  armedId.value = null
  await removeSession(id)
}

/** 点击别处收起行菜单并取消删除确认（捕获阶段先判断，再轮到按钮自己的 @click.stop） */
function onDocClick(e: MouseEvent) {
  if (menuId.value) {
    const owner = listEl.value?.querySelector('.sess-row[data-menu="true"]') ?? null
    if (!(owner && e.target instanceof Node && owner.contains(e.target))) menuId.value = null
  }
  if (!armedId.value) return
  const armed = listEl.value?.querySelector('.sess-row[data-armed="true"]') ?? null
  if (armed && e.target instanceof Node && armed.contains(e.target)) return
  armedId.value = null
}

/** Ctrl/⌘ + K：与按钮上标注的快捷键一致；Esc 依次收起菜单 / 取消改名 / 取消删除确认 */
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    newChat()
    return
  }
  if (e.key !== 'Escape') return
  if (menuId.value) menuId.value = null
  else if (editingId.value) cancelRename()
  else if (armedId.value) armedId.value = null
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('click', onDocClick, true)
  // 读回上次连通性检测结果 + 刷新服务健康与模型配置
  void bootstrap()
  // 读回落盘的 activeId + 拉第一页会话（主区 watch(activeId) 据此拉 transcript）
  void bootstrapSessionList()
  // 真实身份（角色门禁的显示依据，见上方 identity 注释）
  void loadIdentity()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('click', onDocClick, true)
  if (searchTimer !== null) window.clearTimeout(searchTimer)
})
</script>

<template>
  <aside class="sidenav">
    <!-- 顶部：方形 logo（点击回落地页） -->
    <div class="side-head">
      <button class="logo-btn" title="返回首页" aria-label="返回首页" @click="router.push('/')">
        <span class="logo">DM</span>
      </button>
    </div>

    <!-- 新对话 -->
    <button
      class="new-chat"
      type="button"
      data-testid="new-session"
      :disabled="sessionCreating"
      @click="newChat"
    >
      <span class="nc-plus" aria-hidden="true">＋</span>
      <span class="nc-label">{{ sessionCreating ? '正在新建…' : '新对话' }}</span>
      <span class="nc-kbd">Ctrl K</span>
    </button>

    <!-- 历史对话：夹在「新对话」与主导航之间。
         结构上分两层：`.sessions` 只负责占位（**不滚动**），标题与搜索框钉在里面，
         `.sess-body` 才是滚动容器。WHY 要分层：标题与搜索框原来跟列表在同一个滚动
         容器里，列表一往下滚，「历史对话」和搜索框就一起滚没了——找一个旧会话得先滚回
         顶部才能搜。现在只有列表滚，搜索框始终在手边。
         标题写「历史对话」而不是「会话」：用户找的就是这个词（接口与文档仍叫 session/会话，
         只是界面文案对齐心智）。折叠开关给「这一屏只想来问问题」的用户让出导航空间。 -->
    <section class="sessions" :class="{ 'is-collapsed': historyCollapsed }"
             data-testid="session-list">
      <div class="sess-head">
        <span class="sess-title">历史对话</span>
        <button
          class="sess-fold"
          type="button"
          data-testid="history-toggle"
          :aria-expanded="!historyCollapsed"
          :title="historyCollapsed ? '展开历史对话' : '折叠历史对话'"
          :aria-label="historyCollapsed ? '展开历史对话' : '折叠历史对话'"
          @click="toggleHistory"
        >
          {{ historyCollapsed ? '⌄' : '⌃' }}
        </button>
      </div>

      <template v-if="!historyCollapsed">
        <div class="sess-search">
          <span class="sess-search-ico" aria-hidden="true">⌕</span>
          <input
            ref="searchEl"
            v-model="sessionQuery"
            class="sess-search-input"
            type="search"
            data-testid="session-search"
            placeholder="搜索对话…"
            maxlength="200"
            autocomplete="off"
            aria-label="搜索对话"
          />
        </div>

        <p v-if="sessionError" class="sess-error" data-testid="session-error">{{ sessionError }}</p>

        <!-- 只有这一层滚：`.sess-head` / `.sess-search` 钉在外面，滚动列表不会把它们带走。
             listEl 也改指这里 —— 菜单翻向与「改名输入框」的查询都以这个裁剪容器为界。 -->
        <div ref="listEl" class="sess-body">
        <div v-if="sessionRows.length" class="sess-rows">
          <template v-for="group in sessionGroups" :key="group.title || 'all'">
            <!-- 时间分组只在非搜索态出现；分组标题不参与行点击 -->
            <div v-if="group.title" class="sess-group" role="presentation">{{ group.title }}</div>
            <div
              v-for="row in group.rows"
              :key="row.id"
              class="sess-row"
              :class="{ 'is-active': row.active }"
              data-testid="session-row"
              :data-armed="armedId === row.id ? 'true' : 'false'"
              :data-menu="menuId === row.id ? 'true' : 'false'"
              :title="row.tip"
              role="button"
              tabindex="0"
              @click="onRowClick(row.id)"
              @keydown.enter.prevent="onRowClick(row.id)"
              @keydown.space.prevent="onRowClick(row.id)"
            >
              <span class="sess-row-txt">
                <!-- 改名：行内输入，Enter 保存 / Esc 取消 / 失焦保存（见 commitRename） -->
                <input
                  v-if="editingId === row.id"
                  v-model="editTitle"
                  class="sess-edit"
                  data-testid="session-rename-input"
                  maxlength="200"
                  aria-label="会话标题"
                  @click.stop
                  @keydown="onEditKeydown"
                  @blur="commitRename"
                />
                <template v-else>
                  <span class="sess-row-title">{{ row.title }}</span>
                  <span class="sess-row-meta">
                    <span class="sess-row-time">{{ row.whenText }}</span>
                    <span v-if="row.runCount" class="sess-row-count">{{ row.runCount }} 轮</span>
                  </span>
                </template>
              </span>

              <!-- 删除保留两步内联确认：第一下把按钮换成「确认删除」，第二下才真删 -->
              <span v-if="armedId === row.id" class="sess-confirm">
                <button
                  class="sess-confirm-yes"
                  type="button"
                  data-testid="session-delete-confirm"
                  :disabled="sessionRemovingId === row.id"
                  @click.stop="confirmDelete(row.id)"
                >
                  确认删除
                </button>
                <button class="sess-confirm-no" type="button" @click.stop="disarmRow">取消</button>
              </span>

              <!-- 行内动作收进 ⋯ 菜单：改名前每行常驻一个 ✕，视觉噪声大又容易误触 -->
              <template v-else-if="editingId !== row.id">
                <button
                  class="sess-more-btn"
                  type="button"
                  data-testid="session-menu"
                  title="更多操作"
                  aria-label="更多操作"
                  :aria-expanded="menuId === row.id"
                  @click.stop="toggleMenu(row.id)"
                >
                  ⋯
                </button>
                <span v-if="menuId === row.id" class="sess-menu" :class="{ 'is-up': menuUp }"
                      data-testid="session-menu-popup">
                  <button
                    class="sess-menu-item"
                    type="button"
                    data-testid="session-rename"
                    @click.stop="startRename(row.id, row.title)"
                  >
                    重命名
                  </button>
                  <button
                    class="sess-menu-item is-danger"
                    type="button"
                    data-testid="session-delete"
                    :disabled="sessionRemovingId === row.id"
                    @click.stop="armRow(row.id)"
                  >
                    删除
                  </button>
                </span>
              </template>
            </div>
          </template>

          <button
            v-if="sessionHasMore"
            class="sess-more"
            type="button"
            :disabled="sessionLoading"
            @click="loadMoreSessions"
          >
            {{ sessionLoading ? '加载中…' : '加载更多' }}
          </button>
        </div>

        <!-- 「还没有会话」与「搜索无结果」是两件事，文案分开 -->
        <p v-else-if="sessionLoading" class="sess-hint">会话加载中…</p>
        <p v-else-if="sessionsNoResults" class="sess-hint" data-testid="session-no-results">
          没有匹配的对话（按最近活动排序，不是相关度）
        </p>
        <p v-else-if="sessionsEmpty" class="sess-hint" data-testid="session-empty">
          还没有历史对话，点上方「新对话」开始
        </p>
        </div>
      </template>
    </section>

    <nav class="nav">
      <template v-for="g in GROUPS" :key="g.title">
        <div class="group-title">{{ g.title }}</div>
        <template v-for="it in g.items" :key="it.key">
          <!-- 各入口现在都走站内路由，并对当前路由高亮（isActive 精确比较 route.path）。
               v-else 的兜底分支已无可渲染的条目，但刻意保留：它只会出现在「又有人加了一个
               只有 href 的入口」时，那时必须显式禁用并标注，而不是让用户撞上一个白屏。 -->
          <RouterLink v-if="it.to" class="nav-item" :class="{ active: isActive(it.to) }"
                      :to="it.to" :title="it.label" :data-nav="it.key" data-testid="nav-link">
            <span class="ico" aria-hidden="true">{{ it.icon }}</span>
            <span class="txt">{{ it.label }}</span>
          </RouterLink>
          <span v-else class="nav-item is-legacy" :title="`${it.label}：新前端尚未迁移（旧版入口）`"
                :data-nav="it.key" data-testid="nav-legacy">
            <span class="ico" aria-hidden="true">{{ it.icon }}</span>
            <span class="txt">{{ it.label }}</span>
            <span class="legacy-tag">未迁移</span>
          </span>
        </template>
      </template>
    </nav>

    <!-- 底部固定：接入状态 + 用户区 -->
    <div class="side-foot">
      <div class="status" data-testid="model-access">
        <div class="st-title">接入状态</div>

        <!-- ① 服务健康：只反映 /health，说明后端进程活着 -->
        <div class="row">
          <span class="dot" :class="serviceDot"></span>
          <span class="k">服务健康</span>
          <span class="v">{{ serviceText }}</span>
        </div>

        <!-- ② 模型配置：只反映 /v1/model/config 的 has_key / model / base_url -->
        <div class="row">
          <span class="dot" :class="configDot"></span>
          <span class="k">模型配置</span>
          <span class="v">{{ configText }}</span>
        </div>
        <div v-if="config" class="sub">
          <span class="sub-model" :title="config.model || ''">{{ config.model || '—' }}</span>
          <span class="sub-url" :title="config.base_url || ''">{{ config.base_url || '—' }}</span>
        </div>

        <!-- ③ 连通性：只是「配了密钥」不等于「模型可用」，所以单独一行且默认未检测 -->
        <div class="row">
          <span class="dot" :class="connDot"></span>
          <span class="k">连通性</span>
          <span class="v">{{ connText }}</span>
        </div>
        <p class="evidence" data-testid="conn-evidence">{{ lastTestText }}</p>
        <p v-if="lastTestDetail" class="evidence detail-line">{{ lastTestDetail }}</p>

        <button
          class="test-btn"
          type="button"
          data-testid="test-conn"
          :disabled="!canTest"
          @click="runTest"
        >
          {{ testing ? '检测中…' : '测试连通性' }}
        </button>
        <p v-if="testBlockedReason" class="hint">{{ testBlockedReason }}</p>
        <p v-else-if="!lastTest" class="hint">检测结果只存在本机浏览器，未检测时不会显示通过。</p>
      </div>

      <RouterLink class="user" to="/model" title="本地身份与模型接入（模型接入需要管理角色）">
        <span class="avatar">DM</span>
        <span class="who">
          <strong>{{ identityText }}</strong>
          <small>{{ identityDetail }}</small>
        </span>
      </RouterLink>
    </div>
  </aside>
</template>

<style scoped>
/* 浅色侧栏：底色用次级灰，主区保持纯白，两者靠 1px 描边分开 */
.sidenav {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px 12px 12px;
  background: var(--color-ink);
  border-right: 1px solid var(--color-line);
  /* 侧栏整体可滚。
     WHY 改掉了原来的 `overflow: hidden`（实测 bug，不是风格偏好）：侧栏的固定高度块
     加起来约 880px（主导航 449 + 底部状态区 298 + 顶部与「新对话」约 80 + 间距 56），
     比任何常见视口都高。`overflow: hidden` 会把它裁掉，而 flex 又把中间的历史区压到
     4px —— 实测 1440×900 与 800×600 **都是 4px、完整可见行 0 行**，连「历史对话」这个
     标题都被裁没了（`overflow-y: auto` 的 4px 容器）。也就是说用户**根本看不到历史**，
     同时矮窗口下连底部状态区也点不到。
     现在：窗口够高时内容刚好放得下，侧栏不出现滚动条（只有历史列表自己滚，维持原设计）；
     窗口不够高时整体滚动，而不是把中间压没、把底部裁掉。 */
  overflow-y: auto;
  overflow-x: hidden;
}

.side-head {
  display: flex;
  align-items: center;
  padding: 2px 4px;
  flex-shrink: 0;
}
.logo-btn {
  border: none;
  background: transparent;
  padding: 0;
  cursor: pointer;
  line-height: 0;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 14.5px;
  color: #ffffff;
  background: linear-gradient(135deg, #2563eb, #6366f1);
}

/* 新对话：圆角、白底、右下角一个快捷键提示 */
.new-chat {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  flex-shrink: 0;
  padding: 9px 10px;
  border-radius: 12px;
  border: 1px solid var(--color-line);
  background: #ffffff;
  color: var(--color-text);
  font-size: 15px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: background 0.15s, border-color 0.15s;
}
.new-chat:hover {
  background: #ffffff;
  border-color: rgba(15, 23, 42, 0.2);
}
.nc-plus {
  font-size: 14.5px;
  color: var(--color-accent);
}
.new-chat:disabled {
  cursor: default;
  color: var(--color-muted);
}
.nc-label {
  flex: 1;
  text-align: left;
}
.nc-kbd {
  font-size: 12.5px;
  color: var(--color-muted);
  border: 1px solid var(--color-line);
  border-radius: 6px;
  padding: 1px 6px;
  background: var(--color-ink);
  white-space: nowrap;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 2px;
  /* 侧栏只让 .sessions 收缩/滚动，其余区块保持原高 */
  flex-shrink: 0;
}
.group-title {
  font-size: 12.5px;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  padding: 12px 10px 5px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 12px;
  background: transparent;
  color: var(--color-body);
  font-size: 15px;
  font-family: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: rgba(15, 23, 42, 0.05);
  color: var(--color-text);
}
.nav-item.active {
  background: #ffffff;
  color: var(--color-text);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.nav-item.active .ico {
  color: var(--color-accent);
}
/* 尚未迁移的旧版入口：不可点、明确标注，避免用户点进一个白屏页面。
   八项已全部迁移，下面两条规则当前不会命中任何元素；保留是因为模板里的兜底分支
   还在 —— 哪天再出现「只有 href 的入口」，样式必须跟得上，否则就会退化成一个
   看起来能点、实际白屏的链接。 */
.nav-item.is-legacy {
  cursor: not-allowed;
  opacity: 0.55;
}
.nav-item.is-legacy:hover {
  background: transparent;
  color: var(--color-body);
}
/* 「未迁移」标签：统一蓝底白字加粗椭圆（当前八项已全部迁移，这条不会命中任何元素） */
.legacy-tag {
  margin-left: auto;
  font-size: 12px;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  font-weight: 600;
  white-space: nowrap;
}
.ico {
  width: 16px;
  text-align: center;
  font-size: 14.5px;
  color: var(--color-muted);
}
.txt {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.side-foot {
  margin-top: auto;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--color-line);
}
.status {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 4px;
  font-size: 13.5px;
  color: var(--color-muted);
}
.st-title {
  font-size: 12.5px;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  padding-bottom: 1px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row .v {
  margin-left: auto;
  color: var(--color-body);
  white-space: nowrap;
}
/* 模型名 / 网关地址：只作依据展示，压到最小号，超长省略 */
.sub {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-left: 15px;
  font-size: 12.5px;
  line-height: 1.5;
}
.sub-model {
  color: var(--color-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sub-url {
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 「最近检测：…」是连通性这一行唯一的依据，必须比普通说明更醒目 */
.evidence {
  margin: 0;
  padding-left: 15px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--color-body);
}
.evidence.detail-line {
  color: var(--color-text);
  word-break: break-all;
}
.hint {
  margin: 0;
  padding-left: 15px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--color-text);
}
/* 「测试连通性」：与其它按钮统一成蓝底白字加粗椭圆（原来是白底细描边） */
.test-btn {
  margin: 2px 0 0 15px;
  padding: 5px 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.test-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.test-btn:disabled {
  cursor: not-allowed;
  color: var(--color-muted);
  background: var(--color-ink);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cbd5e1;
}
.dot.ok {
  background: #16a34a;
}
.dot.down {
  background: #e11d48;
}
/* 警告态（如「密钥没配」）：既不是成功也不是失败 */
.dot.warn {
  background: #d97706;
}
/* 无记录：灰点，明确区别于通过（绿）与失败（红） */
.dot.idle {
  background: #cbd5e1;
}
.dot.checking {
  background: #d97706;
  animation: breathe 1.6s ease-in-out infinite;
}

/* ── 会话列表 ──
   类名统一 `sess-` 前缀：Tailwind 的 @layer utilities 会生成一批同名工具类，
   `.collapse` / `.result-caption` 已经各坑过一次（见 MainPage / ChatPanel 的注释）。
   容器类名按约定叫 .sessions，只承担「占满剩余高度并自己滚动」这一件事。 */
.sessions {
  flex: 1;
  /* 历史区**永远**留出可读高度。`min-height: 0`（修 bug 之前的值）等于允许它被压成 0：
     侧栏里比它「硬」的兄弟（导航、底部状态区）`min-height` 都是 auto，缩不动，于是空间
     不足时全部压力都落在历史区上，实测被压到 4px —— 用户看到的就是「没有历史」。
     216px ≈ 标题 + 搜索框 + 3 行会话（每行实测 50px）。 */
  min-height: 216px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0;
  /* 这一层不滚：标题与搜索框钉住，滚动交给下面的 .sess-body */
  overflow: hidden;
}
/*
 * 折叠态必须**真的把空间还回去**。
 *
 * WHY 需要这条（实测 bug）：`min-height: 216px` 是为展开态定的（保证历史区不被压没），
 * 但它对折叠态一样生效——折叠后只剩标题一行，那块 216px 的空白照样占着，底部导航并不会
 * 上移，看起来就是「点了没反应 / 收不起来」。CSS 不知道 Vue 的状态，所以由模板挂
 * `.is-collapsed`：折叠时取消最小高度、也不参与 flex 增长，高度回到标题那一行。
 */
.sessions.is-collapsed {
  min-height: 0;
  flex: 0 0 auto;
}
.sess-head,
.sess-search,
.sess-error {
  flex: 0 0 auto;
}
.sess-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 搜索框：焦点态沿用 ChatPanel 的 .composer:focus-within（同一种「正在输入」的强调） */
.sess-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px 0;
}
.sess-title {
  font-size: 12.5px;
  letter-spacing: 0.08em;
  color: var(--color-muted);
}
/* 折叠开关：默认展开，折叠后整块只剩标题一行 */
.sess-fold {
  border: none;
  background: transparent;
  color: var(--color-muted);
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 8px;
  cursor: pointer;
}
.sess-fold:hover {
  background: rgba(15, 23, 42, 0.06);
  color: var(--color-body);
}

/* 时间分组小标题：只做视觉分隔，不参与点击 */
.sess-group {
  font-size: 12px;
  letter-spacing: 0.06em;
  color: var(--color-muted);
  padding: 8px 8px 2px;
}
.sess-search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: #ffffff;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sess-search:focus-within {
  border-color: rgba(37, 99, 235, 0.45);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 10px 28px rgba(37, 99, 235, 0.1);
}
.sess-search-ico {
  font-size: 13.5px;
  color: var(--color-muted);
}
.sess-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  color: var(--color-text);
}
.sess-search-input::placeholder {
  color: var(--color-muted);
}

.sess-rows {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sess-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border-radius: 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.sess-row:hover {
  background: rgba(15, 23, 42, 0.05);
}
/* 键盘可达：行是 div，必须自己给出可见的焦点环 */
.sess-row:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.45);
  outline-offset: -2px;
}
.sess-row.is-active {
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.sess-row.is-active .sess-row-title {
  color: var(--color-text);
  font-weight: 500;
}
.sess-row-txt {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.sess-row-title {
  font-size: 14px;
  color: var(--color-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sess-row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--color-muted);
}
.sess-row-count {
  border-left: 1px solid var(--color-line);
  padding-left: 6px;
  white-space: nowrap;
}
/* 行内改名输入框：与标题同字号，占满文字区，避免改名时行高跳动 */
.sess-edit {
  width: 100%;
  min-width: 0;
  border: 1px solid rgba(37, 99, 235, 0.45);
  border-radius: 8px;
  padding: 2px 6px;
  font-family: inherit;
  font-size: 14px;
  color: var(--color-text);
  background: #ffffff;
  outline: none;
}
/* ⋯ 行菜单：平时隐藏，悬停该行或已聚焦时才现身，避免列表噪声 */
.sess-more-btn {
  flex-shrink: 0;
  width: 22px;
  height: 20px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--color-muted);
  font-family: inherit;
  font-size: 14.5px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s, color 0.15s;
}
.sess-row:hover .sess-more-btn,
.sess-row.is-active .sess-more-btn,
.sess-more-btn:focus-visible,
.sess-row[data-menu='true'] .sess-more-btn {
  opacity: 1;
}
.sess-more-btn:hover {
  background: rgba(15, 23, 42, 0.08);
  color: var(--color-body);
}
.sess-more-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.sess-menu {
  position: absolute;
  right: 6px;
  top: 30px;
  z-index: 5;
  min-width: 96px;
  display: flex;
  flex-direction: column;
  padding: 4px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
}
/* 靠近列表底部的行翻到上方展开：`.sessions` 是 `overflow-y: auto` 的裁剪容器，
   向下展开会被裁掉，菜单项就点不到了（见 menuUp 的注释）。 */
.sess-menu.is-up {
  top: auto;
  bottom: 30px;
}
.sess-menu-item {
  border: none;
  border-radius: 8px;
  padding: 5px 8px;
  text-align: left;
  font-family: inherit;
  font-size: 13.5px;
  color: var(--color-body);
  background: transparent;
  cursor: pointer;
  white-space: nowrap;
}
.sess-menu-item:hover {
  background: rgba(15, 23, 42, 0.06);
}
.sess-menu-item.is-danger {
  color: #e11d48;
}
.sess-menu-item.is-danger:hover {
  background: rgba(225, 29, 72, 0.1);
}
.sess-menu-item:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
/* 菜单是绝对定位，需要一个定位祖先 */
.sess-row {
  position: relative;
}
/* 悬停/激活时不再需要旧的常驻 ✕ —— 已收进 ⋯ 菜单（见模板注释） */
.sess-confirm {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.sess-confirm-yes,
.sess-confirm-no {
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 3px 9px;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.6;
  cursor: pointer;
  white-space: nowrap;
}
.sess-confirm-yes {
  border-color: transparent;
  background: #e11d48;
  color: #ffffff; font-weight: 600;
}
.sess-confirm-yes:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.sess-confirm-no {
  background: #ffffff;
  color: var(--color-muted);
}
.sess-confirm-no:hover {
  color: var(--color-text);
}
.sess-more {
  margin-top: 2px;
  padding: 5px 10px;
  border: 1px dashed var(--color-line);
  border-radius: 999px;
  background: transparent;
  color: var(--color-muted);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.sess-more:hover:not(:disabled) {
  color: var(--color-accent);
  border-color: rgba(37, 99, 235, 0.4);
}
.sess-more:disabled {
  cursor: default;
}
.sess-hint,
.sess-error {
  margin: 0;
  padding: 2px 4px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-text);
}
.sess-error {
  color: #e11d48;
  word-break: break-all;
}

.user {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: 12px;
  text-decoration: none;
  transition: background 0.15s;
}
.user:hover {
  background: rgba(15, 23, 42, 0.05);
}
.avatar {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 12.5px;
  font-weight: 700;
  color: #ffffff;
  background: linear-gradient(135deg, #2563eb, #6366f1);
}
.who {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.who strong {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
.who small {
  font-size: 12.5px;
  color: var(--color-muted);
}
</style>
