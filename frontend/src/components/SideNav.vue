<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
  query: sessionQuery,
  loading: sessionLoading,
  error: sessionError,
  hasMore: sessionHasMore,
  removingId: sessionRemovingId,
  creating: sessionCreating,
  isEmpty: sessionsEmpty,
  noResults: sessionsNoResults,
  bootstrap: bootstrapSessionList,
  refresh: refreshSessions,
  loadMore: loadMoreSessions,
  select: selectSession,
  remove: removeSession,
} = useSessions()

/**
 * 导航入口沿用旧版控制台（web/index.html）的八项，一个都不少。
 * 旧版是后端的根页面（GET /），用 hash 深链切换视图：/#runs、/#fields …
 * 这里只做「图标 + 文字 + 分组小标题」的版式改造，入口本身保持可用。
 *
 * 现在八项**全部**是站内路由（to）：六个 legacy 视图（模型接入/字段复核/来源权威规则/
 * 图纸与实体/尺寸核验/我的文件）已经迁移进新前端，所以再没有 href 深链了 —— 那些
 * 深链指向的旧版控制台视图正是「点进去白屏」的根因。
 */
interface NavItem {
  key: string
  label: string
  icon: string
  /** 站内路由：问数与问答就是本页 */
  to?: string
  /**
   * 旧版控制台的 hash 深链。八项已全部迁移，当前没有任何条目再用它；
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
    ],
  },
  {
    title: '数据治理',
    items: [
      { key: 'fields', label: '字段复核', icon: '▤', to: '/fields' },
      { key: 'authority', label: '来源权威规则', icon: '◇', to: '/authority' },
      { key: 'cad', label: '图纸与实体', icon: '⌖', to: '/cad' },
      { key: 'dimensions', label: '尺寸核验', icon: '↔', to: '/dimensions' },
    ],
  },
  {
    title: '我的',
    items: [{ key: 'files', label: '我的文件', icon: '⇈', to: '/files' }],
  },
]

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
  // 已进入确认态的行不响应「切换会话」，免得确认过程中误触
  if (armedId.value === id) return
  void selectSession(id)
}

async function confirmDelete(id: string) {
  armedId.value = null
  await removeSession(id)
}

/** 点击别处取消确认（捕获阶段先判断，再轮到按钮自己的 @click.stop） */
function onDocClick(e: MouseEvent) {
  if (!armedId.value) return
  const armed = listEl.value?.querySelector('.sess-row[data-armed="true"]') ?? null
  if (armed && e.target instanceof Node && armed.contains(e.target)) return
  armedId.value = null
}

/** Ctrl/⌘ + K：与按钮上标注的快捷键一致；Esc 先用来取消删除确认 */
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    newChat()
    return
  }
  if (e.key === 'Escape' && armedId.value) armedId.value = null
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

    <!-- 会话列表：夹在「新对话」与主导航之间。列表自己滚动（.sessions），
         侧栏整体不再滚动，底部「接入状态 / 用户区」始终钉住。 -->
    <section ref="listEl" class="sessions" data-testid="session-list">
      <div class="sess-title">会话</div>
      <div class="sess-search">
        <span class="sess-search-ico" aria-hidden="true">⌕</span>
        <input
          v-model="sessionQuery"
          class="sess-search-input"
          type="search"
          data-testid="session-search"
          placeholder="搜索会话…"
          maxlength="200"
          autocomplete="off"
          aria-label="搜索会话"
        />
      </div>

      <p v-if="sessionError" class="sess-error" data-testid="session-error">{{ sessionError }}</p>

      <div v-if="sessionRows.length" class="sess-rows">
        <div
          v-for="row in sessionRows"
          :key="row.id"
          class="sess-row"
          :class="{ 'is-active': row.active }"
          data-testid="session-row"
          :data-armed="armedId === row.id ? 'true' : 'false'"
          :title="row.tip"
          role="button"
          tabindex="0"
          @click="onRowClick(row.id)"
          @keydown.enter.prevent="onRowClick(row.id)"
          @keydown.space.prevent="onRowClick(row.id)"
        >
          <span class="sess-row-txt">
            <span class="sess-row-title">{{ row.title }}</span>
            <span class="sess-row-meta">
              <span class="sess-row-time">{{ row.whenText }}</span>
              <span v-if="row.runCount" class="sess-row-count">{{ row.runCount }} 轮</span>
            </span>
          </span>

          <!-- 未进入确认态：一个「✕」；进入确认态：确认/取消两个按钮 -->
          <button
            v-if="armedId !== row.id"
            class="sess-del"
            type="button"
            data-testid="session-delete"
            title="删除会话"
            aria-label="删除会话"
            :disabled="sessionRemovingId === row.id"
            @click.stop="armRow(row.id)"
          >
            ✕
          </button>
          <span v-else class="sess-confirm">
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
        </div>

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
        没有匹配的会话
      </p>
      <p v-else-if="sessionsEmpty" class="sess-hint" data-testid="session-empty">
        还没有会话，点上方「新对话」开始
      </p>
    </section>

    <nav class="nav">
      <template v-for="g in GROUPS" :key="g.title">
        <div class="group-title">{{ g.title }}</div>
        <template v-for="it in g.items" :key="it.key">
          <!-- 八项现在都走站内路由，并对当前路由高亮（isActive 精确比较 route.path）。
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
  /* 侧栏本身不再滚动：滚动交给 .sessions 一个区域，
     否则会出现「会话列表 + 整栏」两层嵌套滚动条。 */
  overflow: hidden;
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
  font-size: 13px;
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
  font-size: 13.5px;
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
  font-size: 13px;
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
  font-size: 10.5px;
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
  font-size: 11px;
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
  border-radius: 10px;
  background: transparent;
  color: var(--color-body);
  font-size: 13.5px;
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
.legacy-tag {
  margin-left: auto;
  font-size: 10px;
  line-height: 1;
  padding: 3px 5px;
  border-radius: 5px;
  border: 1px solid var(--color-line);
  color: var(--color-muted);
  white-space: nowrap;
}
.ico {
  width: 16px;
  text-align: center;
  font-size: 13px;
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
  font-size: 12px;
  color: var(--color-muted);
}
.st-title {
  font-size: 10.5px;
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
  font-size: 10.5px;
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
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--color-body);
}
.evidence.detail-line {
  color: var(--color-muted);
  word-break: break-all;
}
.hint {
  margin: 0;
  padding-left: 15px;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--color-muted);
}
.test-btn {
  margin: 2px 0 0 15px;
  padding: 5px 10px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: #ffffff;
  color: var(--color-text);
  font-family: inherit;
  font-size: 11.5px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.test-btn:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
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
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0;
}

/* 搜索框：焦点态沿用 ChatPanel 的 .composer:focus-within（同一种「正在输入」的强调） */
.sess-title {
  font-size: 10.5px;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  padding: 2px 4px 0;
}
.sess-search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid var(--color-line);
  border-radius: 10px;
  background: #ffffff;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sess-search:focus-within {
  border-color: rgba(37, 99, 235, 0.45);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 10px 28px rgba(37, 99, 235, 0.1);
}
.sess-search-ico {
  font-size: 12px;
  color: var(--color-muted);
}
.sess-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
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
  border-radius: 10px;
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
  font-size: 12.5px;
  color: var(--color-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sess-row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: var(--color-muted);
}
.sess-row-count {
  border-left: 1px solid var(--color-line);
  padding-left: 6px;
  white-space: nowrap;
}
.sess-del {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-muted);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s, color 0.15s;
}
/* 删除按钮平时隐藏，悬停该行或已聚焦时才现身，避免列表噪声 */
.sess-row:hover .sess-del,
.sess-row.is-active .sess-del,
.sess-del:focus-visible {
  opacity: 1;
}
.sess-del:hover {
  background: rgba(225, 29, 72, 0.1);
  color: #e11d48;
}
.sess-confirm {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.sess-confirm-yes,
.sess-confirm-no {
  border: 1px solid var(--color-line);
  border-radius: 7px;
  padding: 2px 6px;
  font-family: inherit;
  font-size: 10.5px;
  line-height: 1.6;
  cursor: pointer;
  white-space: nowrap;
}
.sess-confirm-yes {
  border-color: rgba(225, 29, 72, 0.35);
  background: rgba(225, 29, 72, 0.08);
  color: #e11d48;
}
.sess-confirm-yes:disabled,
.sess-del:disabled {
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
  padding: 5px 8px;
  border: 1px dashed var(--color-line);
  border-radius: 9px;
  background: transparent;
  color: var(--color-muted);
  font-family: inherit;
  font-size: 11.5px;
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
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-muted);
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
  font-size: 11px;
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
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
}
.who small {
  font-size: 11px;
  color: var(--color-muted);
}
</style>
