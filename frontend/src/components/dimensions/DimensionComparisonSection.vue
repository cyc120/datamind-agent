<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { comparisonHistory, getComparison, saveComparison } from '../../api/client'
import type {
  DimensionComparisonInput,
  DimensionComparisonRecord,
  DimensionComparisonSummary,
} from '../../api/types'
import DimensionResultCard from './DimensionResultCard.vue'
import { apiErrorText, newRequestId, reasonText, shortHash, whenText } from './shared'

/**
 * 对照区：保存快照 / 分页历史 / 回放。
 *
 * 后端契约（datamind/cad_dimensions.py 的 comparison / comparison_history / replay）：
 *   · `POST .../comparisons` 是 **admin** 操作，响应是**顶层**记录（不是 {result:…} 包裹），
 *     带 replay 标志：false = 这次是新算并落库的；同一个 request_id 再次提交会命中幂等，
 *     直接回放已存记录（replay=true）。
 *   · `GET .../comparisons` 是分页历史，只给结论摘要 + next_offset（null = 没有更多）。
 *   · `GET /v1/dimensions/comparisons/{id}` 是**回放**：返回当时落库的那份记录并校验
 *     `result == evaluate(bundle, evaluation)`，逐字节一致，服务端**不会**重算。
 *
 * 这正是本区块存在的意义：历史结论必须可回放。回放出来的东西是「当时那一刻的结论」，
 * 不是「用今天的数据重算的结果」——两者都会被展示，但标签必须分开，绝不能混为一谈。
 *
 * 同样地：一次对照只对**它的 binding_version 与当时的证据**有效。历史行上显示的
 * binding_version 是那条结论自己的版本号，不是「最新版本」。
 */

const PAGE_SIZE = 20

const props = defineProps<{
  bindingId: string
  /** 当前绑定的版本与哈希：保存快照要显式提交，服务端据此拒绝过期提交 */
  bindingVersion: number
  bindingSha256: string
  isAdmin: boolean
}>()

const saving = ref(false)
const saveError = ref('')
const saveNote = ref('')
/** 刚保存（或幂等回放）的顶层记录 */
const snapshot = ref<DimensionComparisonRecord | null>(null)

const history = ref<DimensionComparisonSummary[]>([])
const nextOffset = ref<number | null>(null)
const historyLoading = ref(false)
const historyLoadingMore = ref(false)
const historyError = ref('')

const replayingId = ref<string | null>(null)
const replayError = ref('')
/** 回放出来的**旧**快照：与 snapshot 分开保存，标签才不会串 */
const replayed = ref<DimensionComparisonRecord | null>(null)

const canSave = computed(() => props.isAdmin && !!props.bindingId && !saving.value)

function statusLabel(status: string): string {
  if (status === 'compared') return '已对照'
  if (status === 'blocked') return '核验暂停'
  return `未知状态：${status}`
}

/** 回放记录的 payload.evaluation 是「当时评估输入」的原样 JSON：拿不到就如实说未知。 */
function replayedBindingVersion(record: DimensionComparisonRecord): string {
  const evaluation = record?.payload?.evaluation as any
  const value = evaluation?.binding?.payload?.binding_version
  return typeof value === 'number' ? String(value) : '（不知道）'
}

async function loadHistory(reset = true): Promise<void> {
  if (!props.bindingId) return
  if (reset) {
    historyLoading.value = true
    nextOffset.value = null
  } else {
    historyLoadingMore.value = true
  }
  try {
    const offset = reset ? 0 : nextOffset.value ?? 0
    const res = await comparisonHistory(props.bindingId, { offset, limit: PAGE_SIZE })
    const rows = Array.isArray(res?.comparisons) ? res.comparisons : []
    history.value = reset ? rows : [...history.value, ...rows]
    nextOffset.value = typeof res?.next_offset === 'number' ? res.next_offset : null
    historyError.value = ''
  } catch (e: any) {
    if (reset) history.value = []
    nextOffset.value = null
    historyError.value = apiErrorText(e, '对照历史加载失败')
  } finally {
    historyLoading.value = false
    historyLoadingMore.value = false
  }
}

async function save(): Promise<void> {
  if (!props.isAdmin) {
    // 角色门槛在提交前就说清楚，而不是让用户撞一次 403 PERMISSION_DENIED
    saveError.value = '保存快照需要 admin 角色（POST /v1/dimensions/bindings/{id}/comparisons）。'
    return
  }
  saveError.value = ''
  saveNote.value = ''
  replayed.value = null
  saving.value = true
  const body: DimensionComparisonInput = {
    request_id: newRequestId(),
    binding_version: props.bindingVersion,
    binding_sha256: props.bindingSha256,
  }
  try {
    const record = await saveComparison(props.bindingId, body)
    snapshot.value = record
    // replay=true 只可能来自幂等命中：同一个 request_id 已经落过库，服务端把旧记录原样退回。
    saveNote.value = record.replay
      ? '本次为重复请求，返回已存快照（replay=true，服务端没有重算）。'
      : '已新算并落库一条对照（replay=false）。'
    await loadHistory(true)
  } catch (e: any) {
    saveError.value = apiErrorText(e, '保存对照快照失败')
  } finally {
    saving.value = false
  }
}

/**
 * 回放一条历史对照：加载的是**当时落库的**那份记录（replay=true），
 * 结果与结果哈希都由服务端按原字节校验，绝不重新计算。
 */
async function replay(row: DimensionComparisonSummary): Promise<void> {
  replayingId.value = row.comparison_id
  replayError.value = ''
  try {
    replayed.value = await getComparison(row.comparison_id)
  } catch (e: any) {
    replayed.value = null
    replayError.value = apiErrorText(e, '回放对照失败')
  } finally {
    replayingId.value = null
  }
}

function clearReplay(): void {
  replayed.value = null
  replayError.value = ''
}

watch(
  () => props.bindingId,
  () => {
    snapshot.value = null
    saveNote.value = ''
    saveError.value = ''
    replayed.value = null
    replayError.value = ''
    history.value = []
    nextOffset.value = null
    void loadHistory(true)
  },
)

onMounted(() => {
  void loadHistory(true)
})
</script>

<template>
  <section class="dimc-card" data-testid="dim-comparison-section">
    <h3 class="dimc-title">对照（comparison）</h3>
    <p class="dimc-note">
      保存快照会按当时冻结的评估器<b>重算并落库</b>；回放只读旧记录，<b>永不重算</b>。回放返回的结论与
      落库时逐字节一致（record_sha256 不变），它证明的是「当时那条结论仍可复现」，不是「今天的数据也得
      同一结论」。POST 的响应是<b>顶层</b>对照记录（result / authentication 在 payload 里），不是
      <code>{result: …}</code> 包裹。
    </p>

    <div class="dimc-actions">
      <button
        class="dimc-btn"
        type="button"
        :disabled="!canSave"
        data-testid="dim-comparison-save"
        @click="save"
      >
        {{ saving ? '保存中…' : '保存快照' }}
      </button>
      <span class="dimc-target">
        binding_version = <code>{{ bindingVersion }}</code> · binding_sha256 =
        <code :title="bindingSha256">{{ shortHash(bindingSha256) }}</code>
      </span>
    </div>

    <p v-if="!isAdmin" class="dimc-refuse" data-testid="dim-comparison-refuse">
      当前身份不是 admin：POST .../comparisons 属于 admin 操作，按钮保持禁用（这不是网络故障）。
    </p>
    <p v-if="saveError" class="dimc-error" data-testid="dim-comparison-error">{{ saveError }}</p>
    <p v-if="saveNote" class="dimc-ok" data-testid="dim-comparison-note">{{ saveNote }}</p>

    <DimensionResultCard
      v-if="snapshot"
      class="dimc-result"
      :result="snapshot.payload.result"
      :authentication="snapshot.payload.authentication"
      :title="snapshot.replay ? '本次响应 · 幂等回放既有快照（replay=true）' : '本次响应 · 新落库快照（replay=false）'"
      :subtitle="`comparison_id = ${snapshot.payload.comparison_id} · record_sha256 = ${shortHash(snapshot.record_sha256)} · 落库时间 ${whenText(snapshot.registered_at)}`"
    />

    <DimensionResultCard
      v-if="replayed"
      class="dimc-result"
      :result="replayed.payload.result"
      :authentication="replayed.payload.authentication"
      :title="`回放的历史快照（replay=true）· 当时绑定版本 ${replayedBindingVersion(replayed)}`"
      :subtitle="`comparison_id = ${replayed.payload.comparison_id} · record_sha256 = ${shortHash(replayed.record_sha256)} · 落库时间 ${whenText(replayed.registered_at)} · 逐字节一致，未重算`"
    />
    <p v-if="replayError" class="dimc-error" data-testid="dim-comparison-replay-error">{{ replayError }}</p>
    <button
      v-if="replayed"
      class="dimc-link"
      type="button"
      data-testid="dim-comparison-replay-close"
      @click="clearReplay"
    >
      收起回放结果
    </button>

    <h4 class="dimc-subtitle">对照历史（分页）</h4>
    <p v-if="historyLoading" class="dimc-hint" data-testid="dim-comparison-history-loading">正在加载历史…</p>
    <p v-else-if="historyError" class="dimc-error" data-testid="dim-comparison-history-error">{{ historyError }}</p>
    <p v-else-if="!history.length" class="dimc-empty" data-testid="dim-comparison-history-empty">
      这条绑定还没有任何对照快照。上面点「保存快照」就会产生第一条（需要 admin）。
    </p>
    <ul v-else class="dimc-list" data-testid="dim-comparison-history">
      <li v-for="row in history" :key="row.comparison_id" class="dimc-row" data-testid="dim-comparison-row">
        <span class="dimc-when">{{ whenText(row.registered_at) }}</span>
        <span class="dimc-badge" :class="row.status === 'compared' ? 'dimc-badge-info' : 'dimc-badge-warn'">
          {{ statusLabel(row.status) }}
        </span>
        <span class="dimc-ver">v{{ row.binding_version }}</span>
        <span v-if="row.reason" class="dimc-reason" :title="reasonText(row.reason)">{{ row.reason }}</span>
        <span v-else class="dimc-reason dimc-reason-ok">无阻断码</span>
        <span class="dimc-hash" :title="row.record_sha256">{{ shortHash(row.record_sha256) }}</span>
        <button
          class="dimc-replay"
          type="button"
          :disabled="replayingId === row.comparison_id"
          data-testid="dim-comparison-replay"
          @click="replay(row)"
        >
          {{ replayingId === row.comparison_id ? '回放中…' : '回放' }}
        </button>
      </li>
    </ul>

    <button
      v-if="nextOffset != null && !historyLoading"
      class="dimc-more"
      type="button"
      :disabled="historyLoadingMore"
      data-testid="dim-comparison-more"
      @click="loadHistory(false)"
    >
      {{ historyLoadingMore ? '加载中…' : '加载更多' }}
    </button>
  </section>
</template>

<style scoped>
/* 类名统一 dimc- 前缀（Tailwind v4 @layer utilities 撞名的老事故）。 */
.dimc-card {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
}
/* 面板标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.dimc-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dimc-note {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dimc-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 10px;
}
/* 按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.dimc-btn {
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 7px 18px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.dimc-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.dimc-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.dimc-target {
  font-size: 13px;
  color: var(--color-text);
  word-break: break-all;
}
.dimc-refuse {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dimc-result {
  display: block;
  margin-top: 12px;
}
/* 子标题与其它页统一：蓝色、15px、600（原来是 14px 灰黑） */
.dimc-subtitle {
  margin: 16px 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dimc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dimc-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
  font-size: 13.5px;
}
.dimc-when {
  flex-shrink: 0;
  width: 118px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
/* 徽标统一蓝底白字加粗椭圆；-warn 是警告语义色（橙），保留 */
.dimc-badge {
  flex-shrink: 0;
  font-size: 12.5px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
}
.dimc-badge-info {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
/* 警告态徽标：橙色是判据，保留；基础样式现在是蓝底，必须自带浅橙底 */
.dimc-badge-warn {
  background: #d97706;
  color: #ffffff; font-weight: 600;
  border-color: transparent;
}
.dimc-ver {
  flex-shrink: 0;
  color: var(--color-body);
  font-variant-numeric: tabular-nums;
}
.dimc-reason {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #b45309;
}
.dimc-reason-ok {
  color: var(--color-text);
}
.dimc-hash {
  flex-shrink: 0;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
/* 「重放这一条」「加载更多」：与其它按钮统一成蓝底白字加粗椭圆 */
.dimc-replay {
  flex-shrink: 0;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 3px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.dimc-replay:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.dimc-replay:disabled {
  opacity: 0.55;
  cursor: default;
}
.dimc-more {
  display: block;
  margin: 12px auto 0;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 7px 20px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.dimc-more:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.dimc-more:disabled {
  opacity: 0.55;
  cursor: default;
}
.dimc-link {
  margin-top: 8px;
  font: inherit;
  font-size: 13px;
  border: none;
  background: none;
  color: var(--color-accent);
  cursor: pointer;
  padding: 0;
}
.dimc-hint,
.dimc-empty {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.dimc-empty {
  padding: 10px 0;
}
.dimc-error {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #e11d48;
}
.dimc-ok {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #16a34a;
}
</style>
