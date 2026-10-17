<script setup lang="ts">
/**
 * Wiki 知识图谱：关系图入口页（`/#/wiki`，契约 docs/WIKI_API.md §9.1 的路由冻结行）。
 *
 * 这一页刻意很薄，理由与 GovernancePage 同源：**不重写已经冻结的组件**。
 * 「当前授权范围图」的画布、四组设置、图外可聚焦的词条目录、范围与边界说明、
 * 分页与截断标注全部在 `WikiForceGraph.vue` 里（1903 行，已通过复核），本页只负责
 * 三件它做不了的事：
 *
 *   (a) **外壳**：与其它页面一致的侧栏 + 顶栏（本项目每个页面自己渲染一份 SideNav）。
 *   (b) **路由接线**：组件通过 `open-entry` 事件说"用户要打开详情"，由这里决定那是
 *       哪条路由 —— `/#/wiki/:entryId`。组件因此不需要认识 router，也就能被单独测。
 *   (c) **选中项进地址栏**：`?select=<entryId>`。WHY 需要它：契约 §7.2 的最小要求 2
 *       要求"窄屏下进入详情后必须能返回关系图并**保留原选中词条**"，而窄屏是
 *       **分步切换**（WikiEntryPage），返回时组件已经重新挂载、内部选中状态已丢；
 *       把选中项写进 query，返回链接才能把它带回来。用 `replace` 而不是 `push`：
 *       选一个节点不是一次"导航"，不应该在后退键里堆出一串选中历史。
 *
 * 三条语义红线在本页的落点（其余由组件自己承担，见它的文件头注释）：
 *   1. 界面称"**当前授权范围图**"，本页的标题与说明一个字都不说"全库"；范围说明本身
 *      用服务端下发的 `scope_description`（组件里直接显示，本页不另写一句更好听的）。
 *   2. 圆点大小只表示**已授权且已加载**的连接数 —— 本页不引入任何第二个口径的数字。
 *   3. 图外**必须**有可聚焦的词条目录（组件里的「词条目录」卡片），本页不把它折叠掉。
 *
 * ⚠ 类名一律 `wk-` 前缀：Tailwind v4 的 @layer utilities 会生成同名工具类，
 * 撞上就会出现"只声明了部分属性、其余被工具类接管"的事故（本仓库已出过三次：
 * `.collapse` → `visibility:collapse`、`.result-caption` / `.table-caption` →
 * `display:table-caption`）。`wk-` 不落在任何工具类的名字空间里。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import WikiForceGraph from '../components/wiki/WikiForceGraph.vue'

const route = useRoute()
const router = useRouter()

/** 画布高度：跟着视口走，但给上下限，避免矮窗口里只剩一条缝、高窗口里拖着一条空白带 */
const graphHeight = computed(() => 'min(64vh, 760px)')

/**
 * `?select=` 只接受**非空字符串**；数组（`?select=a&select=b`）取第一个，其余一律当没有。
 * 与 GovernancePage 的 `normalizeTab` 同一套写法：非法输入回落默认值，并把地址栏
 * 改写成实际显示的那个值（显示 A、地址栏写 B 就是界面在撒谎）。
 */
function querySelect(value: unknown): string | null {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string') return null
  const text = raw.trim()
  return text === '' ? null : text
}

const selectedEntryId = ref<string | null>(querySelect(route.query.select))

/** 外部改动地址栏（手输、前进/后退、从词条详情页返回）→ 选中项跟着走 */
watch(
  () => route.query.select,
  (value) => {
    const next = querySelect(value)
    if (next !== selectedEntryId.value) selectedEntryId.value = next
  },
)

/** 组件里选中变化 → 写回地址栏（replace，不进历史） */
watch(selectedEntryId, (value) => {
  if (value === querySelect(route.query.select)) return
  const query: Record<string, string> = {}
  if (value) query.select = value
  void router.replace({ path: '/wiki', query })
})

/** 组件说"用户要打开详情" → 词条正文深链（版本由详情页自己决定，这里不带 revision） */
function openEntry(entryId: string): void {
  if (!entryId) return
  void router.push({ name: 'wiki-entry', params: { entryId } })
}

function backToChat(): void {
  void router.push('/app')
}

/** 深链格式对用户可见：让人知道地址栏里那条链接是可以直接分享/收藏的 */
const exampleDeepLink = '/#/wiki/<entryId>?revision=<revisionId>'
</script>

<template>
  <div class="wk-shell" data-testid="wiki-page">
    <SideNav @new-chat="backToChat" />

    <div class="wk-center">
      <header class="wk-head">
        <div class="wk-head-top">
          <h1 class="wk-title">知识图谱</h1>
          <span class="wk-tag">当前授权范围图</span>
        </div>
        <ul class="wk-note" data-testid="wiki-head-note">
          <li>
            这张图是<strong>当前授权范围图</strong>，<strong>不是</strong>全库全图：只包含本次授权、
            当前图层、<strong>已加载</strong>的节点与关系。
          </li>
          <li>
            圆点大小只表示<strong>已授权且已加载</strong>的连接数，位置只用于排布 —— 两者都不代表任何结论。
          </li>
          <li>
            分页、邻域、轨迹的状态在图下方的「范围与边界」里<strong>分别</strong>标明，
            「还有后续」与「已截断」不是一回事。
          </li>
        </ul>
        <ul class="wk-note" data-testid="wiki-head-help">
          <li>
            图外有一份<strong>可以用键盘操作的词条目录</strong>：按 Tab 键就能选词条，不需要拖拽，也不靠辨色。
          </li>
          <li>
            选中后点「打开详情」进入词条正文，地址形如
            <code class="wk-code">{{ exampleDeepLink }}</code>，刷新与分享都停在同一词条、同一版本。
          </li>
          <li>
            服务端返回空集合时这里显示<strong>空态</strong>，读取失败时显示<strong>失败</strong>，
            两者绝不互相冒充。
          </li>
        </ul>
      </header>

      <div class="wk-body">
        <!-- 关系图本体（含四组设置、图外目录、范围与边界）。两个事件在这里接线：
             select → ?select=；open-entry → /#/wiki/:entryId。 -->
        <WikiForceGraph
          v-model:selectedEntryId="selectedEntryId"
          :height="graphHeight"
          @open-entry="openEntry"
        />
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

.wk-head {
  flex-shrink: 0;
  padding: 14px 28px 10px;
  border-bottom: 1px solid var(--color-line);
}

.wk-head-top {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

/* 标题与「模型接入」页一致：蓝色、15px、600 */
.wk-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}

/* 「当前授权范围图」是口径标签：统一成蓝色药丸（蓝是品牌主色，不承载好坏判据） */
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
}

.wk-note {
  margin: 5px 0 0;
  padding: 0;
  max-width: 1080px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 14px;
  line-height: 1.75;
  color: var(--color-text);
}

/* 说明文字按条列出：每行一条、前面一个蓝点（与 GovernancePage 的 .gov-note、
   FieldsPage 的 .fields-note 同一套写法）。类名前缀 wk- 不会撞 Tailwind 工具类。 */
.wk-note > li {
  position: relative;
  padding-left: 16px;
}
.wk-note > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.62em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
}

.wk-note strong {
  color: var(--color-body);
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

/* 内容区自己滚：图 + 三张卡片（目录 / 选中 / 范围与边界）在矮窗口里放不下，
   裁掉就等于把「范围与边界」和「图外目录」藏起来（契约要求它们必须可到达）。 */
.wk-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 28px 24px;
}

@media (max-width: 1100px) {
  .wk-head {
    padding: 12px 16px 8px;
  }
  .wk-body {
    padding: 12px 16px 20px;
  }
}
</style>
