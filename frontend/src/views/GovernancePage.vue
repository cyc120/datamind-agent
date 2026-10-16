<script setup lang="ts">
/**
 * 数据治理：把侧栏原先那 4 条（字段复核 / 来源权威规则 / 图纸与实体 / 尺寸核验）
 * 收成 1 个入口，页内用分栏切换。
 *
 * WHY 合并：这四屏本来就是同一件事的四个环节（抽取保真 → 来源裁决 → 图纸关联 → 尺寸比对），
 * 在侧栏各占一行只让导航变长，没有多给任何信息。合并后侧栏只剩 1 条「数据治理」，
 * 四个板块在这里用标签页切换 —— 每屏原来的能力一条都不少。
 *
 * 三条实现约定（都属于「不撒谎」的一部分）：
 *
 *   (a) **不重写四个板块**。这里只提供外壳与切换，四个板块仍然是 FieldsPage /
 *       AuthorityPage / CadPage / DimensionsPage 自己 —— 标题、说明、刷新按钮、诚实声明
 *       都留在它们自己手里（这也意味着本页不需要、也不应该重复它们的内容）。
 *       代价是：子页面自己也渲染一份外壳（`100vh` 两栏 + 一个 SideNav）。所以这里用
 *       `:deep()` 让位：隐藏子页面的侧栏（同一份会话状态、同一个模块级单例，渲染两次
 *       只会让侧栏出现两条）、把它的高度交还给 flex。**子页面文件一个字都不改**。
 *
 *   (b) 地址栏如实反映当前板块（`?tab=fields|authority|cad|dimensions`）：刷新、收藏、
 *       分享链接都会停在同一个分栏。缺失或非法的 tab 回落到「字段复核」，并且**把地址栏
 *       改写成实际显示的那个分栏** —— 显示的是 A、地址栏写着 B，就是界面在撒谎。
 *
 *   (c) 旧的 4 条路由（/fields、/authority、/cad、/dimensions）原样保留：它们是深链，
 *       可能被书签、docs/ 或测试引用。合并入口不该以让旧链接失效为代价（见 router/index.ts）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import FieldsPage from './FieldsPage.vue'
import AuthorityPage from './AuthorityPage.vue'
import CadPage from './CadPage.vue'
import DimensionsPage from './DimensionsPage.vue'

interface GovTab {
  /** 同时是 `?tab=` 的取值：沿用旧路由的名字，深链含义不变 */
  key: string
  label: string
  icon: string
  /** 只作分栏上的副标题；板块自己的完整说明仍由子页面渲染 */
  note: string
  component: Component
}

/**
 * 四个板块。`key` 与旧路由同名（/fields → ?tab=fields），
 * `component` 必须指向现有页面组件本身 —— 这里不是复制品，就是那一屏。
 */
const TABS: GovTab[] = [
  { key: 'fields', label: '字段复核', icon: '▤', note: '抽取保真与逐条核对', component: FieldsPage },
  { key: 'authority', label: '来源权威规则', icon: '◇', note: '数值冲突的显式裁决', component: AuthorityPage },
  { key: 'cad', label: '图纸与实体', icon: '⌖', note: '图纸版本与关联复核', component: CadPage },
  { key: 'dimensions', label: '尺寸核验', icon: '↔', note: '测量绑定与批准比对', component: DimensionsPage },
]

const TAB_KEYS = TABS.map((tab) => tab.key)
/** 默认分栏：进 `/governance` 不带 tab 时显示这一屏（第一个板块） */
const DEFAULT_TAB = TABS[0].key

const route = useRoute()
const router = useRouter()

/** `?tab=` 只接受已知的四个值；数组（`?tab=a&tab=b`）取第一个，其余一律回落默认值。 */
function normalizeTab(value: unknown): string {
  const raw = Array.isArray(value) ? value[0] : value
  return typeof raw === 'string' && TAB_KEYS.includes(raw) ? raw : DEFAULT_TAB
}

const activeTab = ref(normalizeTab(route.query.tab))
const active = computed<GovTab>(() => TABS.find((tab) => tab.key === activeTab.value) ?? TABS[0])

const tabsEl = ref<HTMLElement | null>(null)

/**
 * 切换分栏。先改本地状态（点击立刻有反馈，不必等一次路由往返），再把地址栏同步过去。
 * 用 push 而不是 replace：浏览器后退键能退回上一个分栏，符合「分栏也是位置」的直觉。
 */
function selectTab(key: string): void {
  if (!TAB_KEYS.includes(key) || key === activeTab.value) return
  activeTab.value = key
  void router.push({ path: '/governance', query: { tab: key } })
}

function focusTabAt(index: number): void {
  void nextTick(() => {
    const buttons = tabsEl.value?.querySelectorAll<HTMLButtonElement>('[data-testid="gov-tab"]')
    buttons?.[index]?.focus()
  })
}

/** ARIA tabs 的键盘约定：←/→ 循环、Home/End 跳首尾，焦点跟着选中项走（roving tabindex）。 */
function onTabKeydown(event: KeyboardEvent, index: number): void {
  const last = TABS.length - 1
  let target: number | null = null
  if (event.key === 'ArrowRight') target = index === last ? 0 : index + 1
  else if (event.key === 'ArrowLeft') target = index === 0 ? last : index - 1
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = last
  if (target === null) return
  event.preventDefault()
  selectTab(TABS[target].key)
  focusTabAt(target)
}

/** 外部改动地址栏（手输、前进/后退、别的页面跳进来）时，分栏跟着走。 */
watch(
  () => route.query.tab,
  (value) => {
    activeTab.value = normalizeTab(value)
  },
)

onMounted(() => {
  // 首屏就把地址栏对齐到实际显示的分栏：`/governance` 会变成 `/governance?tab=fields`。
  if (route.query.tab !== activeTab.value) {
    void router.replace({ path: '/governance', query: { tab: activeTab.value } })
  }
})

function backToChat(): void {
  void router.push('/app')
}
</script>

<template>
  <div class="gov-shell">
    <SideNav @new-chat="backToChat" />

    <div class="gov-center">
      <!-- 页面上方只给一句总说明：每个板块的标题与完整说明由子页面自己提供（见文件头 (a)） -->
      <header class="gov-head">
        <h1 class="gov-title">数据治理</h1>
        <p class="gov-note" data-testid="gov-note">
          抽取保真、来源裁决、图纸关联、尺寸比对是同一件事的四个环节，收在这一页里用下面的分栏切换。
          每个板块的标题、说明、刷新按钮与诚实声明仍然由它自己的页面提供，这里只负责切换。
          旧入口（/#/fields、/#/authority、/#/cad、/#/dimensions）继续可用。
        </p>
      </header>

      <!-- 页面级顶栏导航：四个板块直接铺在页面上方（不是卡片里的小控件，也不在页内再嵌侧栏）。
           当前板块由 ?tab= 决定，下划线指示选中项。 -->
      <nav ref="tabsEl" class="gov-tabs" role="tablist" aria-label="数据治理板块">
        <button
          v-for="(tab, index) in TABS"
          :id="`gov-tab-${tab.key}`"
          :key="tab.key"
          class="gov-tab"
          :class="{ 'is-active': tab.key === activeTab }"
          type="button"
          role="tab"
          data-testid="gov-tab"
          :data-tab="tab.key"
          :title="tab.note"
          :aria-selected="tab.key === activeTab"
          :aria-controls="`gov-panel-${tab.key}`"
          :tabindex="tab.key === activeTab ? 0 : -1"
          @click="selectTab(tab.key)"
          @keydown="onTabKeydown($event, index)"
        >
          <span class="gov-tab-ico" aria-hidden="true">{{ tab.icon }}</span>
          <span class="gov-tab-label">{{ tab.label }}</span>
        </button>
      </nav>

      <!-- 面板：里面就是那一屏页面组件本身（KeepAlive：来回切分栏不重新拉一遍数据，
           每屏顶栏都有自己的「刷新」，要新数据随时刷） -->
      <div
        :id="`gov-panel-${activeTab}`"
        class="gov-panel"
        role="tabpanel"
        :aria-labelledby="`gov-tab-${activeTab}`"
        :data-testid="`gov-panel-${activeTab}`"
      >
        <KeepAlive>
          <component :is="active.component" />
        </KeepAlive>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 gov- 前缀。WHY：Tailwind v4 的 @layer utilities 会生成同名工具类，
   撞上就会出现「只声明了部分属性、其余被工具类接管」的怪象 —— 本仓库已经出过两次
   （.collapse → visibility:collapse、.result-caption/table-caption）。gov- 前缀不落在
   任何工具类的名字空间里，构建产物里也没有同名规则。 */
.gov-shell {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-void);
}
.gov-center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.gov-head {
  flex-shrink: 0;
  padding: 14px 28px 10px;
}
.gov-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}
.gov-note {
  margin: 5px 0 0;
  max-width: 1000px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-muted);
}

/* 顶栏导航：整条铺满内容区，底部一条分隔线，选中项用 2px 主色下划线压在这条线上。
   这是「页面级」的导航，不是卡片里的控件 —— 它直接坐在页面顶栏下面，横跨整个内容宽度。 */
.gov-tabs {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  /* 16 + 分栏自身的 12px 左内边距 = 28px，与上面标题、下面子页面顶栏对齐 */
  padding: 0 16px;
  border-bottom: 1px solid var(--color-line);
}
.gov-tab {
  /* 负外边距让 2px 下划线与容器的 1px 分隔线重叠，而不是叠成两条线 */
  margin-bottom: -1px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 12px 10px;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 7px 7px 0 0;
  background: transparent;
  color: var(--color-body);
  font-family: inherit;
  font-size: 14.5px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
}
.gov-tab:hover {
  background: rgba(15, 23, 42, 0.04);
  color: var(--color-text);
}
.gov-tab.is-active {
  border-bottom-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 500;
}
/* 键盘可达：按钮自己给焦点环（默认 outline 在浅底上偏弱） */
.gov-tab:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.45);
  outline-offset: -4px;
}
.gov-tab-ico {
  width: 16px;
  text-align: center;
  font-size: 14.5px;
  color: var(--color-muted);
}
.gov-tab.is-active .gov-tab-ico {
  color: var(--color-accent);
}
.gov-tab-label {
  white-space: nowrap;
}

/* ── 面板：让位给子页面自带的外壳 ─────────────────────────────────────
   子页面根元素是 `.mainpage`（`display:flex; height:100vh; width:100vw` + 一个 SideNav）。
   放进分栏容器时只做两件事：高度改由 flex 决定、隐藏它自己那份侧栏。
   `.gov-panel .mainpage` 的特异性高于子页面自己的 `.mainpage[data-v-x]`，不依赖样式顺序。 */
.gov-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.gov-panel :deep(.mainpage) {
  flex: 1 1 auto;
  height: auto;
  width: auto;
  min-width: 0;
  min-height: 0;
}
/* 子页面的 SideNav 是同一份会话状态的第二次渲染：留着就会出现两条侧栏。 */
.gov-panel :deep(.sidenav) {
  display: none;
}

/* 窄窗口：左右留白收窄，四个板块仍要排在一行里点得到（分栏会换行但不裁切） */
@media (max-width: 900px) {
  .gov-head {
    padding: 12px 18px 8px;
  }
  .gov-tabs {
    padding: 0 6px;
  }
}
</style>
