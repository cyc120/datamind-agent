<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import { getRun, listRuns } from '../api/client'
import type { RunDetail, RunSummary } from '../api/types'

/**
 * 运行记录（历史问答）。
 *
 * WHY 独立一页：侧边栏一直有「运行记录」入口，但它从落地页改版起就指向旧版的
 * `/#runs` —— 新前端没有这条路由，点进去是**白屏**。后端 `GET /v1/runs` 其实
 * 一直都在（750+ 条真实历史），前端也早就导出了 `listRuns()`，只是没有任何组件调用它。
 *
 * 两条必须如实呈现的事实：
 *   1. `conversation_id` 为 null 是**正常历史状态**，不是错误。会话功能上线之前的
 *      run 从未写过这一列，UI 说「未归属会话」而不是替它猜一个会话；
 *   2. `GET /v1/runs` 属于公开读前缀，页面上不显示任何凭据类字段。
 */

const PAGE_SIZE = 30

const router = useRouter()

const runs = ref<RunSummary[]>([])
const loading = ref(false)
const loadingMore = ref(false)
const hasMore = ref(false)
const error = ref('')

const selectedId = ref<string | null>(null)
const detail = ref<RunDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

const STATUS_TEXT: Record<string, string> = {
  COMPLETED: '已完成',
  FAILED: '失败',
  FAILED_WITH_EVIDENCE: '失败（附证据）',
  ABSTAINED: '拒答',
  RUNNING: '进行中',
  PLANNED: '已计划',
  // 这两个原来漏了，界面上直接显示英文状态码（用户截图看到 NEEDS_CLARIFICATION）。
  NEEDS_CLARIFICATION: '需要澄清',
  INSUFFICIENT_EVIDENCE: '证据不足',
}

function statusText(status?: string | null): string {
  if (!status) return '未知'
  return STATUS_TEXT[status] ?? status
}

/** COMPLETED 之外的终态都值得用警示色标出来，别让失败看起来和成功一样。
 *  NEEDS_CLARIFICATION 归 warn：它不是失败，但需要用户补充信息才能继续，
 *  落到 muted 会显示成与「已完成」同款蓝底，看不出"这条在等你"。 */
function statusTone(status?: string | null): string {
  if (status === 'COMPLETED') return 'ok'
  if (status === 'ABSTAINED' || status === 'NEEDS_CLARIFICATION' || status === 'INSUFFICIENT_EVIDENCE') return 'warn'
  if (status && (status.startsWith('FAILED') || status === 'RUNNING')) return 'bad'
  return 'muted'
}

/**
 * 跳到工作台并选中这条 run 所属的会话（MainPage 的 `?session=` 深链）。
 *
 * 为什么不把行里那个「会话 xxx」徽章本身做成链接：整行是 `<button>`（点开详情），
 * `<button>` 里不能再嵌 `<a>` —— 那是非法 HTML，点链接还会连带触发整行的 open()。
 * 所以入口放在展开后的详情里，语义也更清楚：「打开这条 run 归属的那个会话」。
 */
function openSession(conversationId: string): void {
  void router.push({ path: '/app', query: { session: conversationId } })
}

/** 后端给的是带时区的 ISO 串，直接交给浏览器按本机时区渲染。 */
function whenText(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function load(offset = 0): Promise<void> {
  const first = offset === 0
  if (first) loading.value = true
  else loadingMore.value = true
  try {
    const res = await listRuns({ limit: PAGE_SIZE, offset })
    const rows = Array.isArray(res?.runs) ? res.runs : []
    runs.value = first ? rows : [...runs.value, ...rows]
    hasMore.value = !!res?.has_more
    error.value = ''
  } catch (e: any) {
    if (first) runs.value = []
    hasMore.value = false
    error.value = e?.message || '运行记录加载失败'
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

async function open(runId: string): Promise<void> {
  if (selectedId.value === runId) {
    selectedId.value = null
    detail.value = null
    return
  }
  selectedId.value = runId
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await getRun(runId)
  } catch (e: any) {
    detailError.value = e?.errorCode === 'RUN_NOT_FOUND' ? '该运行记录不存在' : (e?.message || '加载失败')
  } finally {
    detailLoading.value = false
  }
}

function backToChat(): void {
  void router.push('/app')
}

onMounted(() => {
  void load(0)
})
</script>

<template>
  <div class="mainpage">
    <SideNav @new-chat="backToChat" />

    <div class="center">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">运行记录</span>
          <span class="tb-note">历史问答：每一次运行的问题、状态与耗时</span>
        </div>
        <button class="tb-action" type="button" :disabled="loading" @click="load(0)">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="body">
        <p v-if="error" class="run-error" data-testid="runs-error">{{ error }}</p>

        <p v-if="loading" class="run-hint">正在加载…</p>
        <p v-else-if="!runs.length && !error" class="run-empty" data-testid="runs-empty">
          还没有任何运行记录。回到「问数与问答」提一个问题，这里就会出现。
        </p>

        <ul v-else class="run-list" data-testid="run-list">
          <li v-for="r in runs" :key="r.run_id">
            <button
              class="run-row"
              type="button"
              :class="{ 'is-open': selectedId === r.run_id }"
              data-testid="run-row"
              :title="r.question"
              @click="open(r.run_id)"
            >
              <span class="run-when">{{ whenText(r.started_at) }}</span>
              <span class="run-q">{{ r.question }}</span>
              <span class="run-badge" :class="`tone-${statusTone(r.status)}`">{{ statusText(r.status) }}</span>
              <span class="run-owner" :class="{ 'is-null': !r.conversation_id }">
                {{ r.conversation_id ? '会话 ' + r.conversation_id.slice(5, 13) : '未归属会话' }}
              </span>
            </button>

            <section v-if="selectedId === r.run_id" class="run-detail">
              <p v-if="detailLoading" class="run-hint">正在读取…</p>
              <p v-else-if="detailError" class="run-error">{{ detailError }}</p>
              <template v-else-if="detail">
                <div class="d-row"><span class="d-key">运行 ID</span><code class="d-val">{{ detail.run_id }}</code></div>
                <div class="d-row">
                  <span class="d-key">置信度</span>
                  <span class="d-val">{{ detail.confidence_level || '—' }}</span>
                </div>
                <div class="d-row">
                  <span class="d-key">会话归属</span>
                  <code class="d-val">{{ detail.conversation_id || '未归属会话（早于会话功能）' }}</code>
                  <!-- 会话深链入口；见 openSession() 的注释（整行是 button，不能再嵌 a） -->
                  <button
                    v-if="detail.conversation_id"
                    class="d-link"
                    type="button"
                    data-testid="open-session"
                    @click="openSession(detail.conversation_id)"
                  >
                    打开该会话 →
                  </button>
                </div>
                <div class="d-block">
                  <div class="d-key">回答</div>
                  <p class="d-answer">{{ detail.answer || '（没有回答正文）' }}</p>
                </div>
              </template>
            </section>
          </li>
        </ul>

        <button
          v-if="hasMore && !loading"
          class="run-more"
          type="button"
          :disabled="loadingMore"
          data-testid="runs-more"
          @click="load(runs.length)"
        >
          {{ loadingMore ? '加载中…' : '加载更多' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 run- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会
   出现「只声明了部分属性、其余被工具类接管」的怪象（仓库里已出过两次事故）。 */
.mainpage {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-void);
}
.center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 28px;
  border-bottom: 1px solid var(--color-line);
}
.tb-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}
/* 标题与「模型接入」页一致：蓝色、15px、600（原来是 16px 灰黑，属离群值） */
.tb-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.tb-note {
  font-size: 14px;
  color: var(--color-text);
}
/* 顶栏按钮统一蓝底白字加粗椭圆（原来是白底细描边，看着不像按钮） */
.tb-action {
  margin-left: auto;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.tb-action:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.tb-action:disabled {
  opacity: 0.6;
  cursor: default;
}
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 28px 40px;
  max-width: 1080px;
  width: 100%;
}
.run-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.run-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  text-align: left;
  font: inherit;
  padding: 10px 12px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.run-row:hover {
  border-color: rgba(37, 99, 235, 0.4);
}
.run-row.is-open {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.06);
}
.run-when {
  flex-shrink: 0;
  font-size: 13.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
  width: 118px;
}
.run-q {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text);
  font-size: 15px;
}
.run-badge {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
}
.run-badge.tone-ok {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
/* 警示态：橙底白字无边框（用户要求去掉浅色描边，与蓝/红药丸统一成实心圆角）。
   橙色本身作为语义保留 —— 它表示「需要你注意」，与蓝(完成)/红(失败)区分。 */
.run-badge.tone-warn {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: #d97706;
}
.run-badge.tone-bad {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: #e11d48;
}
/* tone-muted 现在与基础样式等价（保留类名，避免动模板）；白字压蓝底才读得出来 */
.run-badge.tone-muted {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.run-owner {
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
.run-owner.is-null {
  opacity: 0.75;
  font-style: italic;
}
.run-detail {
  margin: 6px 0 14px;
  padding: 14px 16px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
}
.d-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}
.d-key {
  flex-shrink: 0;
  width: 72px;
  font-size: 13.5px;
  color: var(--color-muted);
}
.d-val {
  font-size: 14px;
  color: var(--color-text);
  word-break: break-all;
}
/* 「打开该会话 →」：跳工作台的会话深链，做成与其它按钮一致的蓝底白字椭圆 */
.d-link {
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 3px 12px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  background: var(--color-accent);
  cursor: pointer;
  white-space: nowrap;
}
.d-link:hover {
  background: #1d4ed8;
  color: #ffffff;
}
.d-block {
  margin-top: 10px;
}
.d-block .d-key {
  width: auto;
  margin-bottom: 6px;
}
.d-answer {
  margin: 0;
  font-size: 15px;
  line-height: 1.75;
  color: var(--color-body);
  white-space: pre-wrap;
}
.run-hint,
.run-empty {
  font-size: 14px;
  color: var(--color-text);
  line-height: 1.7;
}
.run-empty {
  padding: 28px 0;
}
.run-error {
  font-size: 14px;
  color: #e11d48;
  padding: 10px 0;
}
.run-more {
  display: block;
  margin: 16px auto 0;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 8px 20px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.run-more:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.run-more:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
