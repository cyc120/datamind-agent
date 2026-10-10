<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'

defineProps<{
  health: 'checking' | 'ok' | 'down'
  model: 'checking' | 'ok' | 'down'
}>()

const emit = defineEmits<{ (e: 'new-chat'): void }>()

const router = useRouter()

/**
 * 导航入口沿用旧版控制台（web/index.html）的八项，一个都不少。
 * 旧版是后端的根页面（GET /），用 hash 深链切换视图：/#runs、/#fields …
 * 这里只做「图标 + 文字 + 分组小标题」的版式改造，入口本身保持可用。
 */
interface NavItem {
  key: string
  label: string
  icon: string
  /** 站内路由：问数与问答就是本页 */
  to?: string
  /** 旧版控制台的 hash 深链 */
  href?: string
}

const GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: '工作台',
    items: [
      { key: 'chat', label: '问数与问答', icon: '◎', to: '/app' },
      { key: 'runs', label: '运行记录', icon: '↺', href: '/#runs' },
      { key: 'model', label: '模型接入', icon: '⚙', href: '/#model' },
    ],
  },
  {
    title: '数据治理',
    items: [
      { key: 'fields', label: '字段复核', icon: '▤', href: '/#fields' },
      { key: 'authority', label: '来源权威规则', icon: '◇', href: '/#authority' },
      { key: 'cad', label: '图纸与实体', icon: '⌖', href: '/#cad' },
      { key: 'dimensions', label: '尺寸核验', icon: '↔', href: '/#dimensions' },
    ],
  },
  {
    title: '我的',
    items: [{ key: 'files', label: '我的文件', icon: '⇈', href: '/#files' }],
  },
]

function newChat() {
  emit('new-chat')
}

/** Ctrl/⌘ + K：与按钮上标注的快捷键一致 */
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    newChat()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
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
    <button class="new-chat" type="button" @click="newChat">
      <span class="nc-plus" aria-hidden="true">＋</span>
      <span class="nc-label">新对话</span>
      <span class="nc-kbd">Ctrl K</span>
    </button>

    <nav class="nav">
      <template v-for="g in GROUPS" :key="g.title">
        <div class="group-title">{{ g.title }}</div>
        <template v-for="it in g.items" :key="it.key">
          <a v-if="it.href" class="nav-item" :href="it.href" :title="it.label">
            <span class="ico" aria-hidden="true">{{ it.icon }}</span>
            <span class="txt">{{ it.label }}</span>
          </a>
          <button v-else class="nav-item active" type="button" :title="it.label">
            <span class="ico" aria-hidden="true">{{ it.icon }}</span>
            <span class="txt">{{ it.label }}</span>
          </button>
        </template>
      </template>
    </nav>

    <!-- 底部固定：服务状态 + 用户区 -->
    <div class="side-foot">
      <div class="status">
        <div class="row">
          <span class="dot" :class="health"></span>
          <span class="k">服务</span>
          <span class="v">{{ health === 'ok' ? '在线' : health === 'down' ? '离线' : '检测中' }}</span>
        </div>
        <div class="row">
          <span class="dot" :class="model"></span>
          <span class="k">模型</span>
          <span class="v">{{ model === 'ok' ? '已接入' : model === 'down' ? '未接入' : '检测中' }}</span>
        </div>
      </div>

      <a class="user" href="/#model" title="本地身份与模型接入在旧版控制台">
        <span class="avatar">DM</span>
        <span class="who">
          <strong>本地演示账户</strong>
          <small>未登录 · 只读浏览</small>
        </span>
      </a>
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
  overflow-y: auto;
}

.side-head {
  display: flex;
  align-items: center;
  padding: 2px 4px;
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
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row .v {
  margin-left: auto;
  color: var(--color-body);
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
.dot.checking {
  background: #d97706;
  animation: breathe 1.6s ease-in-out infinite;
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
