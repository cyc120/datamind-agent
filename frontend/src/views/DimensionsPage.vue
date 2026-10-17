<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import AnimatedList from '../components/bits/AnimatedList/AnimatedList.vue'
import {
  featureReviews,
  getBinding,
  getMeasurement,
  listBindings,
  listDimensionSources,
  listMeasurements,
  me,
} from '../api/client'
import type {
  DimensionBindingHead,
  DimensionBindingRegistrationResult,
  DimensionBindingResult,
  DimensionMeasurementHead,
  DimensionMeasurementResult,
  DimensionSourceEntry,
} from '../api/types'
import DimensionAdminForms from '../components/dimensions/DimensionAdminForms.vue'
import DimensionApprovalForm from '../components/dimensions/DimensionApprovalForm.vue'
import DimensionComparisonSection from '../components/dimensions/DimensionComparisonSection.vue'
import DimensionResultCard from '../components/dimensions/DimensionResultCard.vue'
import { apiErrorText, approvalDecisionText, shortHash, valueWithUnit, whenText } from '../components/dimensions/shared'

/**
 * 尺寸核验（measurement registry）。
 *
 * 记录链是四步，本页每一步都必须在场、且顺序不能糊在一起：
 *   1. **注册尺寸记录**（append-only 版本链，admin）；
 *   2. **绑定**：把一条尺寸记录绑到图纸上的一个标注要素，必须引用一条已 confirmed 的
 *      人工关联复核（review_id + review_sha256）；
 *   3. **独立使用审批**：审批人不得是登记人，也不得是该复核的复核人；
 *   4. **对照**：落一条快照，之后可以分页查看历史并按 id **回放**旧结论。
 *
 * ★ 本页最重要的一条诚实规则：两个都叫 authentication 的事实**不能合并**（详见
 *   DimensionResultCard：内层三个 false 说「这份输入能不能当依据」，外层 verified 说
 *   「这次本地读取是否鉴权过」）。除了「结论卡」里逐条显示的版本，页首也再申明一次。
 *
 * 另一条：对照状态只有 compared / blocked；区间关系只有 within/outside_annotation_range。
 * 本页任何地方都不会把它渲染成判定词（后端明确否认这层含义）。
 *
 * 角色：读取任意已登录身份；POST /approvals 需 approver；登记与保存快照需 admin（admin
 * 越过全部角色门槛）。列表接口只回 id + 当前版本，要读内容必须再取详情，所以这里是
 * 「列表 + 点击取详情」而不是一次性把 200 条全拉回来。
 */

const router = useRouter()

/* ── 身份 ─────────────────────────────────────────────────────────── */
const identity = ref<{ user_id: string; display_name: string; roles: string[] } | null>(null)
const identityError = ref('')

const roles = computed<string[]>(() => (identity.value?.roles ?? []).filter((r) => typeof r === 'string'))
const isAdmin = computed(() => roles.value.includes('admin'))
const roleSummary = computed(() => roles.value.join('、') || '（无角色）')

/* ── 顶层加载状态 ─────────────────────────────────────────────────── */
const loading = ref(false)
const error = ref('')

/* ── 尺寸记录列表 ─────────────────────────────────────────────────── */
const measurements = ref<DimensionMeasurementHead[]>([])
const measurementsTruncated = ref(false)
const measurementsError = ref('')

const selectedMeasurementId = ref<string | null>(null)
const measurementDetail = ref<DimensionMeasurementResult | null>(null)
const measurementLoading = ref(false)
const measurementError = ref('')

/* ── 绑定列表 ─────────────────────────────────────────────────────── */
const bindings = ref<DimensionBindingHead[]>([])
const bindingsTruncated = ref(false)
const bindingsError = ref('')

const selectedBindingId = ref<string | null>(null)
const detail = ref<DimensionBindingResult | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

/** 关联复核的复核人：读到了才能替用户证明「你不是复核人」 */
const reviewerUserId = ref<string | null>(null)
const reviewerKnown = ref(false)
const reviewerError = ref('')

/* ── 可登记的源文件（admin）───────────────────────────────────────── */
const sources = ref<DimensionSourceEntry[]>([])
const sourcesError = ref('')

const adminOpen = ref(false)

const bindingPayload = computed(() => detail.value?.binding.payload ?? null)
const bindingSha = computed(() => detail.value?.binding.sha256 ?? '')
const approvals = computed(() => detail.value?.approvals ?? [])

/** 证据原始 JSON：后端原样返回，不在这里改写任何字段 */
const evidenceJson = computed(() => {
  const evidence = detail.value?.current_result?.evidence
  if (!evidence) return ''
  try {
    return JSON.stringify(evidence, null, 2)
  } catch {
    return '（证据无法序列化）'
  }
})

function backToChat(): void {
  void router.push('/app')
}

async function loadIdentity(): Promise<void> {
  identityError.value = ''
  try {
    const res = await me()
    const user = res?.user
    if (!user) {
      identity.value = null
      // /v1/auth/me 是公开路径：匿名时它返回 200 + user:null，而不是错误
      identityError.value = '未取得本地身份（user=null）：/v1/dimensions/* 需要登录身份，列表会以 401 LOGIN_REQUIRED 失败。'
      return
    }
    identity.value = {
      user_id: String(user.user_id ?? ''),
      display_name: String(user.display_name ?? user.username ?? user.user_id ?? ''),
      roles: Array.isArray(user.roles) ? user.roles.map((r) => String(r)) : [],
    }
  } catch (e: any) {
    identity.value = null
    identityError.value = apiErrorText(e, '读取本地身份失败')
  }
}

async function loadMeasurements(): Promise<void> {
  measurementsError.value = ''
  try {
    const res = await listMeasurements()
    // 列表路由没有 version 过滤参数，只回 id + 当前版本；truncated=true 表示只回了前 200 条。
    measurements.value = Array.isArray(res?.measurements) ? res.measurements : []
    measurementsTruncated.value = !!res?.truncated
  } catch (e: any) {
    measurements.value = []
    measurementsTruncated.value = false
    measurementsError.value = apiErrorText(e, '尺寸记录列表加载失败')
  }
}

async function loadBindings(): Promise<void> {
  bindingsError.value = ''
  try {
    const res = await listBindings()
    bindings.value = Array.isArray(res?.bindings) ? res.bindings : []
    bindingsTruncated.value = !!res?.truncated
  } catch (e: any) {
    bindings.value = []
    bindingsTruncated.value = false
    bindingsError.value = apiErrorText(e, '绑定列表加载失败')
  }
}

async function loadSources(): Promise<void> {
  sourcesError.value = ''
  sources.value = []
  if (!isAdmin.value) {
    // GET /v1/dimensions/sources 也在 admin 门槛下：不是 admin 就别去撞 403
    sourcesError.value = `尺寸记录源文件列表需要 admin 角色（当前：${roleSummary.value}）。`
    return
  }
  try {
    const res = await listDimensionSources()
    sources.value = Array.isArray(res?.sources) ? res.sources : []
  } catch (e: any) {
    sourcesError.value = apiErrorText(e, '读取尺寸记录源文件失败')
  }
}

async function refresh(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await loadIdentity()
    await Promise.all([loadMeasurements(), loadBindings()])
    await loadSources()
  } catch (e: any) {
    error.value = apiErrorText(e, '页面初始化失败')
  } finally {
    loading.value = false
  }
}

/**
 * 读关联复核记录，判断当前身份是不是这条绑定的复核人。
 * 复核算不算「已知」必须如实标注：读不到就说读不到，绝不默认「你不是复核人」。
 */
async function loadReviewer(): Promise<void> {
  reviewerUserId.value = null
  reviewerKnown.value = false
  reviewerError.value = ''
  const payload = bindingPayload.value
  if (!payload) return
  try {
    const history = await featureReviews(payload.document_id, payload.feature_id, payload.document_version)
    const rows = Array.isArray(history?.reviews) ? history.reviews : []
    const hit = rows.find((r) => r.review_id === payload.review_id)
    if (hit) {
      reviewerUserId.value = hit.reviewer_user_id
      reviewerKnown.value = true
    } else if (rows.length) {
      reviewerError.value = `复核历史里没有这条绑定引用的 review_id=${payload.review_id}：本页无法确认复核人是谁。`
    } else {
      reviewerError.value = `该要素（${payload.feature_id}）没有关联复核历史：本页无法确认复核人是谁。`
    }
  } catch (e: any) {
    reviewerError.value = apiErrorText(e, '读取关联复核记录失败，因此无法证明你不是复核人')
  }
}

async function openBinding(bindingId: string): Promise<void> {
  if (selectedBindingId.value === bindingId && detail.value) {
    selectedBindingId.value = null
    detail.value = null
    return
  }
  selectedBindingId.value = bindingId
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await getBinding(bindingId)
    await loadReviewer()
  } catch (e: any) {
    detailError.value = apiErrorText(e, '绑定详情加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function openMeasurement(measurementId: string): Promise<void> {
  if (selectedMeasurementId.value === measurementId && measurementDetail.value) {
    selectedMeasurementId.value = null
    measurementDetail.value = null
    return
  }
  selectedMeasurementId.value = measurementId
  measurementDetail.value = null
  measurementError.value = ''
  measurementLoading.value = true
  try {
    measurementDetail.value = await getMeasurement(measurementId)
  } catch (e: any) {
    measurementError.value = apiErrorText(e, '尺寸记录详情加载失败')
  } finally {
    measurementLoading.value = false
  }
}

/** 审批/登记之后：重新拉当前选中绑定的详情（审批链与 current_result 都会变）。 */
async function reloadOpenBinding(): Promise<void> {
  const id = selectedBindingId.value
  if (!id) return
  detailLoading.value = true
  detailError.value = ''
  try {
    detail.value = await getBinding(id)
    await loadReviewer()
  } catch (e: any) {
    detailError.value = apiErrorText(e, '绑定详情重新加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function onMeasurementRegistered(): Promise<void> {
  await loadMeasurements()
  await loadSources()
}

/** 登记绑定成功后：刷新列表并直接打开新绑定，让用户马上核对审批链。 */
async function onBindingRegistered(result: DimensionBindingRegistrationResult): Promise<void> {
  await loadBindings()
  const id = result?.binding?.payload?.binding_id
  if (id) {
    selectedBindingId.value = null
    await openBinding(id)
  }
}

onMounted(() => {
  void refresh()
})
</script>

<template>
  <div class="mainpage">
    <SideNav @new-chat="backToChat" />

    <div class="center">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">尺寸核验</span>
          <span class="tb-note">
            尺寸记录 → 绑定 → 独立使用审批 → 对照：结论只说明与图纸标注区间的关系，不是制造授权，也不是产品质量判定
          </span>
        </div>
        <button class="tb-action" type="button" :disabled="loading" data-testid="dim-refresh" @click="refresh">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="body">
        <div class="dim-identity" data-testid="dim-identity">
          <template v-if="identity">
            <p>
              当前身份：{{ identity.display_name }}（{{ identity.user_id }}） · 角色：{{ roleSummary }}
            </p>
            <ul class="dim-note">
              <li>保存快照与登记：需要 admin（管理员）角色。</li>
              <li>使用审批：需要 approver（审批员）角色。</li>
            </ul>
          </template>
          <template v-else>{{ identityError || '身份未知' }}</template>
        </div>
        <p v-if="identity && identityError" class="dim-error" data-testid="dim-identity-error">{{ identityError }}</p>

        <p v-if="error" class="dim-error" data-testid="dim-page-error">{{ error }}</p>
        <p v-if="loading" class="dim-hint" data-testid="dim-loading">正在加载…</p>

        <template v-else>
          <!-- 页首诚实声明：记录链 + 两个 authentication 事实的分工。
               框保留（用户觉得全去掉会单调），但各段落逐条淡入、段间距放大。 -->
          <AnimatedList tag="section" class="dim-contract" data-testid="dim-contract-note">
            <p class="dim-contract-line">
              记录链：注册尺寸记录 → 绑定（必须引用一条已确认的关联复核）→ 独立使用审批 → 对照快照（可回放）。
            </p>
            <p class="dim-contract-line">
              任何一次评估里，下面三项都是 <b>false</b>，这一点不会随操作变化：
            </p>
            <ul class="dim-note">
              <li><code>authentication_verified</code>（输入是否经过外部认证）：输入是作者自述的合成 / 本地记录。</li>
              <li><code>formal_drawing_approval</code>（图纸是否正式批准）：本流程不做图纸的正式批准。</li>
              <li><code>product_quality_conclusion</code>（是否是产品质量判定）：对照结果不是产品质量判定。</li>
            </ul>
            <p class="dim-contract-line">
              响应外层还有一个 <code>authentication.verified = true</code>：它只说这次本地读取走了鉴权，
              与上面三个 false 回答的是<b>不同</b>的问题，不能互相推导。
            </p>
            <p class="dim-contract-line dim-contract-muted">
              对照结论只有两种区间关系（落在标注区间内 / 外），它不构成公差判定、不构成制造授权。
            </p>
          </AnimatedList>

          <div class="dim-cols">
            <!-- 左栏：两个列表 -->
            <div class="dim-col dim-col-left">
              <section class="dim-panel" data-testid="dim-measurements">
                <h3 class="dim-panel-title">尺寸记录（measurements）</h3>
                <p class="dim-panel-note">
                  列表只回 id 与当前版本，点一行才去读内容。
                </p>
                <p v-if="measurementsError" class="dim-error" data-testid="dim-measurements-error">{{ measurementsError }}</p>
                <p v-else-if="!measurements.length" class="dim-empty" data-testid="dim-measurements-empty">
                  还没有登记任何尺寸记录。管理员可在页尾「管理员登记」里登记，需要一份暂存目录里的 .json 源文件。
                </p>
                <ul v-else class="dim-list" data-testid="dim-measurement-list">
                  <li v-for="m in measurements" :key="m.measurement_id">
                    <button
                      class="dim-row"
                      type="button"
                      :class="{ 'is-open': selectedMeasurementId === m.measurement_id }"
                      data-testid="dim-measurement-row"
                      @click="openMeasurement(m.measurement_id)"
                    >
                      <span class="dim-row-id">{{ m.measurement_id }}</span>
                      <span class="dim-row-ver">v{{ m.current_version }}</span>
                    </button>
                  </li>
                </ul>
                <p v-if="measurementsTruncated" class="dim-warn" data-testid="dim-measurements-truncated">
                  服务端只返回了前 200 条：这里不是全集。
                </p>
              </section>

              <section class="dim-panel" data-testid="dim-bindings">
                <h3 class="dim-panel-title">绑定（bindings）</h3>
                <p class="dim-panel-note">点一行查看绑定载荷、审批链、当前对照结论与对照历史。</p>
                <p v-if="bindingsError" class="dim-error" data-testid="dim-bindings-error">{{ bindingsError }}</p>
                <p v-else-if="!bindings.length" class="dim-empty" data-testid="dim-bindings-empty">
                  还没有任何绑定。绑定必须先有一条已确认的关联复核，否则服务端会以 409
                  <code>DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED</code> 拒绝。
                </p>
                <ul v-else class="dim-list" data-testid="dim-binding-list">
                  <li v-for="b in bindings" :key="b.binding_id">
                    <button
                      class="dim-row"
                      type="button"
                      :class="{ 'is-open': selectedBindingId === b.binding_id }"
                      data-testid="dim-binding-row"
                      @click="openBinding(b.binding_id)"
                    >
                      <span class="dim-row-id">{{ b.binding_id }}</span>
                      <span class="dim-row-ver">v{{ b.current_version }}</span>
                    </button>
                  </li>
                </ul>
                <p v-if="bindingsTruncated" class="dim-warn" data-testid="dim-bindings-truncated">
                  服务端只返回了前 200 条：这里不是全集。
                </p>
              </section>
            </div>

            <!-- 右栏：详情 -->
            <div class="dim-col dim-col-right">
              <!-- 尺寸记录详情 -->
              <section v-if="selectedMeasurementId" class="dim-panel" data-testid="dim-measurement-detail">
                <h3 class="dim-panel-title">尺寸记录 {{ selectedMeasurementId }}</h3>
                <p v-if="measurementLoading" class="dim-hint">正在读取…</p>
                <p v-else-if="measurementError" class="dim-error" data-testid="dim-measurement-detail-error">{{ measurementError }}</p>
                <template v-else-if="measurementDetail">
                  <div class="dim-kv">
                    <div class="dim-kv-row"><span class="dim-k">record_version</span><span class="dim-v">{{ measurementDetail.measurement.payload.source.record_version }}</span></div>
                    <div class="dim-kv-row"><span class="dim-k">sha256</span><code class="dim-v" :title="measurementDetail.measurement.sha256">{{ shortHash(measurementDetail.measurement.sha256) }}</code></div>
                    <div class="dim-kv-row"><span class="dim-k">part_id / batch_id</span><span class="dim-v">{{ measurementDetail.measurement.payload.part_id }} / {{ measurementDetail.measurement.payload.batch_id }}</span></div>
                    <div class="dim-kv-row"><span class="dim-k">operation_no / process_revision</span><span class="dim-v">{{ measurementDetail.measurement.payload.operation_no }} / {{ measurementDetail.measurement.payload.process_revision }}</span></div>
                    <div class="dim-kv-row"><span class="dim-k">metric / feature_role</span><span class="dim-v">{{ measurementDetail.measurement.payload.metric }} / {{ measurementDetail.measurement.payload.feature_role }}</span></div>
                    <div class="dim-kv-row"><span class="dim-k">method</span><span class="dim-v">{{ measurementDetail.measurement.payload.method }}</span></div>
                    <div class="dim-kv-row">
                      <span class="dim-k">measured_at</span>
                      <span class="dim-v">{{ measurementDetail.measurement.payload.measured_at }}（{{ whenText(measurementDetail.measurement.payload.measured_at) }}）</span>
                    </div>
                    <div class="dim-kv-row">
                      <span class="dim-k">值 / 单位</span>
                      <span class="dim-v" data-testid="dim-measurement-value">
                        {{ valueWithUnit(measurementDetail.measurement.payload.value, measurementDetail.measurement.payload.unit) }}
                      </span>
                    </div>
                    <div class="dim-kv-row"><span class="dim-k">source_id / provenance</span><span class="dim-v">{{ measurementDetail.measurement.payload.source.source_id }} / {{ measurementDetail.measurement.payload.source.provenance }}</span></div>
                    <div class="dim-kv-row"><span class="dim-k">登记</span><span class="dim-v">{{ measurementDetail.measurement.actor.label }}（{{ measurementDetail.measurement.actor.user_id }}） · {{ whenText(measurementDetail.measurement.registered_at) }}</span></div>
                  </div>
                  <p class="dim-caveat">
                    原值可能为空：那表示<b>源文件里就没有值</b>，不是「量到 0」。这条记录是本地登记，不是质量结论。
                  </p>
                </template>
              </section>

              <!-- 绑定详情 -->
              <template v-if="selectedBindingId">
                <section class="dim-panel" data-testid="dim-binding-detail">
                  <h3 class="dim-panel-title">绑定 {{ selectedBindingId }}</h3>
                  <p v-if="detailLoading" class="dim-hint">正在读取…</p>
                  <p v-else-if="detailError" class="dim-error" data-testid="dim-binding-detail-error">{{ detailError }}</p>
                  <template v-else-if="bindingPayload">
                    <div class="dim-kv">
                      <div class="dim-kv-row"><span class="dim-k">binding_version</span><span class="dim-v">{{ bindingPayload.binding_version }}</span></div>
                      <div class="dim-kv-row"><span class="dim-k">binding_sha256</span><code class="dim-v" :title="bindingSha">{{ shortHash(bindingSha) }}</code></div>
                      <div class="dim-kv-row"><span class="dim-k">图纸</span><span class="dim-v">{{ bindingPayload.document_id }} v{{ bindingPayload.document_version }} · {{ bindingPayload.drawing_id }} / {{ bindingPayload.drawing_revision }} / {{ bindingPayload.part_id }}</span></div>
                      <div class="dim-kv-row"><span class="dim-k">要素 / 目标句柄</span><span class="dim-v">{{ bindingPayload.feature_id }} → <code>{{ bindingPayload.target_handle }}</code></span></div>
                      <div class="dim-kv-row"><span class="dim-k">尺寸记录</span><span class="dim-v">{{ bindingPayload.measurement_id }} v{{ bindingPayload.measurement_record_version }} · {{ bindingPayload.metric }} · 批次 {{ bindingPayload.batch_id }}</span></div>
                      <div class="dim-kv-row"><span class="dim-k">关联复核</span><span class="dim-v">{{ bindingPayload.review_id }} · review_sha256=<code :title="bindingPayload.review_sha256">{{ shortHash(bindingPayload.review_sha256) }}</code></span></div>
                      <div class="dim-kv-row"><span class="dim-k">有效期</span><span class="dim-v">{{ bindingPayload.valid_from }} → {{ bindingPayload.valid_to }}</span></div>
                      <div class="dim-kv-row"><span class="dim-k">登记人</span><span class="dim-v">{{ bindingPayload.registrar_user_id }}（{{ bindingPayload.registrar_roles.join('、') }}）</span></div>
                      <div class="dim-kv-row"><span class="dim-k">rationale</span><span class="dim-v">{{ bindingPayload.rationale }}</span></div>
                    </div>
                    <p class="dim-caveat">
                      有效期是左闭右开区间：起点算在内、终点不算，而且必须带时区偏移。
                      测量时间不落在里面，核验就会以 DIMENSION_OUTSIDE_VALIDITY 暂停。
                    </p>
                  </template>
                </section>

                <section v-if="detail" class="dim-panel" data-testid="dim-binding-evidence">
                  <h3 class="dim-panel-title">证据（后端原样返回）</h3>
                  <p class="dim-panel-note">
                    这是评估时冻结的证据 JSON：图纸标识 / 尺寸记录 / 绑定与复核 id / 评估时间。本页不加工、不挑选字段。
                  </p>
                  <p v-if="!evidenceJson" class="dim-empty">该结论没有带 evidence。</p>
                  <details v-else class="dim-details">
                    <summary class="dim-summary" data-testid="dim-evidence-summary">展开证据 JSON</summary>
                    <pre class="dim-pre" data-testid="dim-evidence-json">{{ evidenceJson }}</pre>
                  </details>
                </section>

                <DimensionResultCard
                  v-if="detail"
                  class="dim-block"
                  :result="detail.current_result"
                  :authentication="detail.authentication"
                  title="当前对照结论（current_result）"
                  subtitle="这是「现在」这个绑定版本的评估结果；历史版本请用下面的对照历史回放。"
                />

                <section v-if="detail" class="dim-panel" data-testid="dim-approvals">
                  <h3 class="dim-panel-title">使用审批链（按顺序）</h3>
                  <p class="dim-panel-note">
                    每一行是一条审批记录：结论、审批人、时间与理由都照原样显示。
                  </p>
                  <p v-if="!approvals.length" class="dim-empty" data-testid="dim-approvals-empty">
                    还没有任何使用审批，所以当前结论会因 DIMENSION_USE_APPROVAL_REQUIRED 暂停。
                    审批人不得是登记人，也不得是该复核的复核人。
                  </p>
                  <ol v-else class="dim-approval-list" data-testid="dim-approval-list">
                    <li v-for="(item, index) in approvals" :key="item.payload.approval_id" class="dim-approval-row" data-testid="dim-approval-row">
                      <span class="dim-approval-seq">#{{ index + 1 }}</span>
                      <span class="dim-approval-decision" :class="`tone-${item.payload.decision}`">{{ approvalDecisionText(item.payload.decision) }}</span>
                      <span class="dim-approval-who">{{ item.payload.approver_user_id }}</span>
                      <span class="dim-approval-when">{{ whenText(item.payload.created_at) }}</span>
                      <span class="dim-approval-reason" :title="item.payload.reason">{{ item.payload.reason }}</span>
                      <code class="dim-approval-id" :title="item.payload.approval_id">{{ shortHash(item.payload.approval_id) }}</code>
                    </li>
                  </ol>
                  <p v-if="approvals.length" class="dim-caveat">
                    链尾决定这次对照能不能继续。审批只放开「使用」这道闸门，不构成制造授权。
                  </p>
                </section>

                <DimensionApprovalForm
                  v-if="detail && bindingPayload"
                  class="dim-block"
                  :binding-id="bindingPayload.binding_id"
                  :binding-version="bindingPayload.binding_version"
                  :binding-sha256="bindingSha"
                  :registrar-user-id="bindingPayload.registrar_user_id"
                  :approvals="approvals"
                  :identity="identity"
                  :reviewer-user-id="reviewerUserId"
                  :reviewer-known="reviewerKnown"
                  @submitted="reloadOpenBinding"
                />
                <p v-if="reviewerError" class="dim-warn" data-testid="dim-reviewer-unknown">{{ reviewerError }}</p>

                <DimensionComparisonSection
                  v-if="bindingPayload"
                  class="dim-block"
                  :binding-id="bindingPayload.binding_id"
                  :binding-version="bindingPayload.binding_version"
                  :binding-sha256="bindingSha"
                  :is-admin="isAdmin"
                />
              </template>

              <!-- 什么都没选时的显式空态 -->
              <section v-if="!selectedBindingId && !selectedMeasurementId" class="dim-panel" data-testid="dim-detail-empty">
                <h3 class="dim-panel-title">详情</h3>
                <p class="dim-empty">
                  从左边选一条尺寸记录或一条绑定。绑定的详情里会显示证据、审批链、当前对照结论与可回放的对照历史。
                </p>
              </section>
            </div>
          </div>

          <!-- 管理员登记表单 -->
          <section class="dim-panel dim-admin" data-testid="dim-admin">
            <div class="dim-admin-head">
              <h3 class="dim-panel-title">管理员登记</h3>
              <button class="dim-toggle" type="button" data-testid="dim-admin-toggle" @click="adminOpen = !adminOpen">
                {{ adminOpen ? '收起表单' : '展开表单' }}
              </button>
            </div>
            <ul class="dim-note">
              <li>登记尺寸记录与登记绑定都是 admin（管理员）操作。</li>
              <li>绑定必须引用一条已确认的关联复核。</li>
              <li>本页只读取复核记录，不代替人工确认。</li>
            </ul>
            <DimensionAdminForms
              v-if="adminOpen"
              :is-admin="isAdmin"
              :sources="sources"
              :sources-error="sourcesError"
              :measurements="measurements"
              :bindings="bindings"
              @registered-measurement="onMeasurementRegistered"
              @registered-binding="onBindingRegistered"
            />
          </section>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 dim- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会出现
   「只声明了部分属性、其余被工具类接管」的怪象（仓库里已出过两次事故：
   .collapse、.result-caption / table-caption）。外壳的 .mainpage/.center/.topbar/
   .body 与 RunsPage 保持一致，保证八个视图的骨架完全相同。 */
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
/* 顶栏按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
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
  padding: 18px 28px 48px;
  max-width: 1320px;
  width: 100%;
}
.dim-identity {
  margin: 0 0 12px;
  font-size: 13.5px;
  color: var(--color-text);
  line-height: 1.7;
}
.dim-identity > p {
  margin: 0 0 4px;
}
/* 说明文字按条列出：每行一条、前面一个蓝点（与 GovernancePage 的 .gov-note、
   FieldsPage 的 .fields-note 同一套写法）。类名前缀 dim- 不会撞 Tailwind 工具类。 */
.dim-note {
  margin: 4px 0 16px;
  padding: 0;
  max-width: 1000px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.dim-note > li {
  position: relative;
  padding-left: 15px;
}
.dim-note > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.62em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
}
.dim-note b {
  font-weight: 600;
  color: var(--color-accent);
}
.dim-note code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  background: var(--color-ink);
  border-radius: 4px;
  padding: 1px 5px;
}
/* 去框：改成左侧 2px 橙细线分组，不加底色、不加圆角。与 CAD 页的 .cad-declare、权威页的 .auth-failclosed 同一套处理（原来这几处都是「圆角 + 浅底 + 描边」的卡片，用户反馈太丑）。这个块外面包了 Vue Bits 的 AnimatedList，各段逐条淡入。 */
.dim-contract {
  border: 0;
  border-left: 2px solid #d97706;
  border-radius: 0;
  background: none;
  padding: 2px 0 2px 18px;
  margin-bottom: 32px;
}
.dim-contract-line {
  margin: 0 0 14px;
  font-size: 13.5px;
  line-height: 1.85;
  color: var(--color-body);
}
.dim-contract-line:last-child {
  margin-bottom: 0;
}
.dim-contract-muted {
  color: var(--color-text);
}
.dim-cols {
  display: grid;
  grid-template-columns: minmax(300px, 380px) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
@media (max-width: 1080px) {
  .dim-cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
.dim-col {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.dim-panel {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
  min-width: 0;
}
/* 面板标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.dim-panel-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dim-panel-note {
  margin: 5px 0 10px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dim-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dim-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  text-align: left;
  font: inherit;
  font-size: 14px;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.dim-row:hover {
  border-color: rgba(37, 99, 235, 0.4);
}
.dim-row.is-open {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.06);
}
.dim-row-id {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text);
}
.dim-row-ver {
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
.dim-kv {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dim-kv-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.dim-k {
  flex-shrink: 0;
  width: 150px;
  font-size: 13px;
  color: var(--color-muted);
}
.dim-v {
  font-size: 14px;
  color: var(--color-text);
  word-break: break-word;
  line-height: 1.7;
}
.dim-caveat {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dim-details {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
  padding: 8px 10px;
}
.dim-summary {
  font-size: 13.5px;
  color: var(--color-body);
  cursor: pointer;
}
.dim-pre {
  margin: 8px 0 0;
  max-height: 320px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-body);
  white-space: pre-wrap;
  word-break: break-word;
}
.dim-block {
  display: block;
}
.dim-approval-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dim-approval-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
  font-size: 13.5px;
}
.dim-approval-seq {
  flex-shrink: 0;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
.dim-approval-decision {
  flex-shrink: 0;
  font-size: 12.5px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
}
.dim-approval-decision.tone-approved {
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.35);
}
.dim-approval-decision.tone-rejected {
  color: #e11d48;
  border-color: rgba(225, 29, 72, 0.35);
}
.dim-approval-decision.tone-revoked {
  color: var(--color-muted);
}
.dim-approval-who {
  flex-shrink: 0;
  color: var(--color-body);
}
.dim-approval-when {
  flex-shrink: 0;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
.dim-approval-reason {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text);
}
.dim-approval-id {
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--color-muted);
}
.dim-admin {
  margin-top: 14px;
}
.dim-admin-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
/* 「展开/收起」按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.dim-toggle {
  margin-left: auto;
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.dim-toggle:hover {
  background: #1d4ed8;
  color: #ffffff;
}
.dim-hint,
.dim-empty {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.dim-empty {
  padding: 8px 0;
}
.dim-warn {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: #b45309;
}
.dim-error {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #e11d48;
}
</style>
