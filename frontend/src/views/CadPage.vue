<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import {
  drawingEntities,
  drawingFeatures,
  drawingVersions,
  featureReviews,
  getDrawing,
  listDrawingSources,
  listDrawings,
  me,
  registerDrawing,
  submitFeatureReview,
} from '../api/client'
import type {
  AuthMe,
  CadAssociationState,
  DrawingDetail,
  DrawingEntitiesResult,
  DrawingFeatureReview,
  DrawingFeatureReviewInput,
  DrawingFeaturesResult,
  DrawingRegistrationInput,
  DrawingSourceEntry,
  DrawingVersion,
  DrawingVersionList,
  FactDecision,
  FeatureReviewHistory,
} from '../api/types'
import CadEntityTable from '../components/cad/CadEntityTable.vue'
import CadErrorNote from '../components/cad/CadErrorNote.vue'
import CadMachineExtraction from '../components/cad/CadMachineExtraction.vue'
import CadRegisterForm from '../components/cad/CadRegisterForm.vue'
import CadReviewPanel from '../components/cad/CadReviewPanel.vue'
import CadViewer from '../components/cad/CadViewer.vue'
import {
  CONFLICT_NOTE,
  cadError,
  forgetAllRequestIds,
  forgetRequestId,
  isCadConflict,
  orDash,
  provenanceText,
  shortHash,
  stableRequestId,
} from '../components/cad/cadCopy'
import type { CadErrorView } from '../components/cad/cadCopy'

/**
 * 图纸与实体（/cad）。
 *
 * 数据来源（全部为真实后端接口，见 datamind/api.py）：
 *   GET  /v1/drawings                        资产列表（每份只列当前版本）
 *   GET  /v1/drawings/{id}/versions          版本历史 + current_version
 *   GET  /v1/drawings/{id}[?version=N]       版本详情（来源快照 + 业务身份）
 *   GET  /v1/drawings/{id}/entities          图元 + 视图
 *   GET  /v1/drawings/{id}/features          机器要素 + inspection_gate + review_gate + association_reviews
 *   GET  /v1/drawings/{id}/features/{fid}/reviews
 *   POST /v1/drawings/{id}/features/{fid}/reviews   （reviewer）
 *   GET  /v1/drawings/sources | POST /v1/drawings   （admin）
 *
 * ★ 本屏唯一不能出错的事 ★
 * `drawingFeatures()` 同时返回**两份**互不相同的东西：
 *   - `features[]`：原始抽取。它的 `association_status` 是**机器**判定，落库即冻结，
 *     人工复核**不会**改变它；
 *   - `review_gate` + `association_reviews[]`：叠加**人工**复核后的门槛与决策。
 * 因此本页把两者放进两个独立组件、两个独立小节，标题各自写明来源，绝不合并。
 * 把 `features[].association_status` 当成人做出的结论，就是在伪造决定责任人。
 */
const router = useRouter()

/* ── 身份（角色决定哪些入口可用；不依赖登录的读接口也要如实说明 401）────── */
const identity = ref<AuthMe | null>(null)
const identityLoaded = ref(false)
const identityError = ref<CadErrorView | null>(null)

/* ── 资产列表 ─────────────────────────────────────────────────────────── */
const drawings = ref<DrawingVersion[]>([])
const drawingsLoading = ref(false)
const drawingsError = ref<CadErrorView | null>(null)
const selectedDocId = ref<string | null>(null)

/* ── 版本选择器 ───────────────────────────────────────────────────────── */
const versionList = ref<DrawingVersionList | null>(null)
const versionsLoading = ref(false)
const versionsError = ref<CadErrorView | null>(null)
/** null = 读当前版本；数字 = 读指定历史版本（只读） */
const viewVersion = ref<number | null>(null)

/* ── 版本内容（三块各自报错，互不清空）──────────────────────────────────── */
const detail = ref<DrawingDetail | null>(null)
const detailError = ref<CadErrorView | null>(null)
const featuresData = ref<DrawingFeaturesResult | null>(null)
const featuresError = ref<CadErrorView | null>(null)
const entitiesData = ref<DrawingEntitiesResult | null>(null)
const entitiesError = ref<CadErrorView | null>(null)
const versionLoading = ref(false)

/* ── 人工关联复核 ─────────────────────────────────────────────────────── */
const selectedFeatureId = ref<string | null>(null)
const history = ref<FeatureReviewHistory | null>(null)
const historyLoading = ref(false)
const historyError = ref<CadErrorView | null>(null)
const reviewSubmitting = ref(false)
const reviewError = ref<CadErrorView | null>(null)
const reviewNotice = ref('')
const reviewFormKey = ref(0)

/* ── 管理员登记 ───────────────────────────────────────────────────────── */
const sources = ref<DrawingSourceEntry[]>([])
const sourcesLoading = ref(false)
const sourcesError = ref<CadErrorView | null>(null)
const registerSubmitting = ref(false)
const registerError = ref<CadErrorView | null>(null)
const registerNotice = ref('')
const registerFormKey = ref(0)

/* ── 409 冲突提示（可恢复：指纹过期或 request_id 复用）──────────────────── */
const conflictNotice = ref<CadErrorView | null>(null)
let conflictDepth = 0

/* ── 身份派生 ─────────────────────────────────────────────────────────── */
const roles = computed<string[]>(() => {
  const raw = identity.value?.user?.roles
  return Array.isArray(raw) ? raw.map(String) : []
})
const rolesText = computed(() => roles.value.join(' / '))
const identityResolved = computed(() => identityLoaded.value && identity.value != null)
const anonymous = computed(() => identityResolved.value && identity.value?.user == null)
/** admin 通过一切角色闸门（后端 auth.current 的语义），所以 admin 也能复核。 */
const canReview = computed(() => roles.value.includes('reviewer') || roles.value.includes('admin'))
const canRegister = computed(() => roles.value.includes('admin'))

/* ── 版本派生 ─────────────────────────────────────────────────────────── */
const versions = computed<DrawingVersion[]>(() =>
  [...(versionList.value?.versions ?? [])].sort((a, b) => a.document_version - b.document_version),
)
const currentVersion = computed(() => versionList.value?.current_version ?? null)
const chainEntry = computed<DrawingVersion | null>(
  () => versions.value.find((v) => v.document_version === currentVersion.value) ?? null,
)
const loadedVersion = computed(() => featuresData.value?.version ?? detail.value?.version ?? null)
const loadedVersionNumber = computed(() => loadedVersion.value?.document_version ?? null)
const readOnlyVersion = computed(
  () =>
    loadedVersionNumber.value != null &&
    currentVersion.value != null &&
    loadedVersionNumber.value !== currentVersion.value,
)
const versionLabel = computed(() =>
  loadedVersion.value ? `v${loadedVersion.value.document_version}${readOnlyVersion.value ? '（历史版本·只读）' : '（当前版本）'}` : '',
)

const features = computed(() => featuresData.value?.features ?? [])
const associationReviews = computed<CadAssociationState[]>(() => featuresData.value?.association_reviews ?? [])
const selectedFeature = computed(
  () => features.value.find((f) => f.feature_id === selectedFeatureId.value) ?? null,
)
const associationState = computed(
  () => associationReviews.value.find((s) => s.feature_id === selectedFeatureId.value) ?? null,
)
const historyReviews = computed<DrawingFeatureReview[]>(() => history.value?.reviews ?? [])

const busy = computed(() => drawingsLoading.value || versionsLoading.value || versionLoading.value)

/* ── 展示工具 ─────────────────────────────────────────────────────────── */
function stamp(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function backToChat(): void {
  void router.push('/app')
}

/* ── 409 恢复：显示代码 + 按当前数据重新加载（不递归）────────────────────── */
async function handleLoadError(
  e: any,
  set: (v: CadErrorView) => void,
  fallback: string,
): Promise<void> {
  const code = typeof e?.errorCode === 'string' ? e.errorCode : ''
  if (isCadConflict(code) && conflictDepth === 0) {
    conflictNotice.value = cadError(e, fallback)
    // request_id 可能正是冲突来源：整套忘掉，下次提交会换成新 id。
    forgetAllRequestIds()
    conflictDepth += 1
    try {
      await refreshAll()
    } finally {
      conflictDepth -= 1
    }
    return
  }
  set(cadError(e, fallback))
}

/* ── 加载 ─────────────────────────────────────────────────────────────── */
async function loadIdentity(): Promise<void> {
  try {
    identity.value = await me()
    identityError.value = null
  } catch (e: any) {
    identityError.value = cadError(e, '身份读取失败（/v1/auth/me）')
  } finally {
    identityLoaded.value = true
  }
}

function clearVersionData(): void {
  detail.value = null
  featuresData.value = null
  entitiesData.value = null
  detailError.value = null
  featuresError.value = null
  entitiesError.value = null
  conflictNotice.value = null
}

function clearSelection(): void {
  selectedDocId.value = null
  versionList.value = null
  viewVersion.value = null
  selectedFeatureId.value = null
  history.value = null
  historyError.value = null
  versionsError.value = null
  clearVersionData()
}

async function loadDrawings(): Promise<void> {
  drawingsLoading.value = true
  drawingsError.value = null
  try {
    const res = await listDrawings()
    const rows = Array.isArray(res?.drawings) ? res.drawings : []
    drawings.value = rows
    if (selectedDocId.value && !rows.some((d) => d.document_id === selectedDocId.value)) {
      clearSelection()
    }
  } catch (e: any) {
    drawings.value = []
    await handleLoadError(e, (v) => { drawingsError.value = v }, '图纸资产列表加载失败')
  } finally {
    drawingsLoading.value = false
  }
}

async function loadVersions(): Promise<void> {
  const id = selectedDocId.value
  if (!id) return
  versionsLoading.value = true
  versionsError.value = null
  try {
    const res = await drawingVersions(id)
    versionList.value = res
    const rows = Array.isArray(res?.versions) ? res.versions : []
    // 选中的历史版本在新数据里不存在了 → 回落到当前版本，不装作还在读它。
    if (viewVersion.value != null && !rows.some((v) => v.document_version === viewVersion.value)) {
      viewVersion.value = null
    }
  } catch (e: any) {
    versionList.value = null
    await handleLoadError(e, (v) => { versionsError.value = v }, '版本历史加载失败')
  } finally {
    versionsLoading.value = false
  }
}

async function loadVersionData(): Promise<void> {
  const id = selectedDocId.value
  if (!id) return
  const version = viewVersion.value ?? undefined
  versionLoading.value = true
  detailError.value = null
  featuresError.value = null
  entitiesError.value = null
  const [d, f, e] = await Promise.allSettled([
    getDrawing(id, version),
    drawingFeatures(id, version),
    drawingEntities(id, version),
  ])
  detail.value = d.status === 'fulfilled' ? d.value : null
  featuresData.value = f.status === 'fulfilled' ? f.value : null
  entitiesData.value = e.status === 'fulfilled' ? e.value : null
  versionLoading.value = false
  // 三块各自报错：要素读得到就不该因为图元失败而空白。
  if (d.status === 'rejected')
    await handleLoadError(d.reason, (v) => { detailError.value = v }, '图纸详情加载失败')
  if (f.status === 'rejected')
    await handleLoadError(f.reason, (v) => { featuresError.value = v }, '要素与复核快照加载失败')
  if (e.status === 'rejected')
    await handleLoadError(e.reason, (v) => { entitiesError.value = v }, '图元加载失败')
  // 换版本后原选中的 feature_id 可能不存在了：清掉选择而不是显示一条不存在的数据。
  if (
    selectedFeatureId.value &&
    !features.value.some((x) => x.feature_id === selectedFeatureId.value)
  ) {
    selectedFeatureId.value = null
    history.value = null
  }
}

async function loadHistory(): Promise<void> {
  const id = selectedDocId.value
  const featureId = selectedFeatureId.value
  if (!id || !featureId) return
  historyLoading.value = true
  historyError.value = null
  try {
    history.value = await featureReviews(id, featureId, viewVersion.value ?? undefined)
  } catch (e: any) {
    history.value = null
    await handleLoadError(e, (v) => { historyError.value = v }, '复核历史加载失败')
  } finally {
    historyLoading.value = false
  }
}

async function loadSources(): Promise<void> {
  if (!canRegister.value) {
    sources.value = []
    sourcesError.value = null
    return
  }
  sourcesLoading.value = true
  sourcesError.value = null
  try {
    const res = await listDrawingSources()
    sources.value = Array.isArray(res?.sources) ? res.sources : []
  } catch (e: any) {
    sources.value = []
    await handleLoadError(e, (v) => { sourcesError.value = v }, '暂存源文件列表加载失败（该接口需要 ADMIN）')
  } finally {
    sourcesLoading.value = false
  }
}

async function refreshAll(): Promise<void> {
  await loadIdentity()
  await loadDrawings()
  if (selectedDocId.value) {
    await loadVersions()
    await loadVersionData()
    if (selectedFeatureId.value) await loadHistory()
  }
  await loadSources()
}

/* ── 交互 ─────────────────────────────────────────────────────────────── */
async function selectDrawing(id: string): Promise<void> {
  if (id === selectedDocId.value) return
  selectedDocId.value = id
  viewVersion.value = null
  selectedFeatureId.value = null
  history.value = null
  historyError.value = null
  reviewNotice.value = ''
  versionsError.value = null
  clearVersionData()
  await loadVersions()
  await loadVersionData()
}

async function selectVersion(next: number | null): Promise<void> {
  if (next === viewVersion.value) return
  viewVersion.value = next
  selectedFeatureId.value = null
  history.value = null
  historyError.value = null
  reviewNotice.value = ''
  clearVersionData()
  await loadVersionData()
}

async function selectFeature(featureId: string): Promise<void> {
  if (featureId === selectedFeatureId.value) return
  selectedFeatureId.value = featureId
  history.value = null
  historyError.value = null
  reviewNotice.value = ''
  await loadHistory()
}

function reloadHistory(): void {
  void loadHistory()
}

/* ── 提交人工复核（reviewer；CAS 校验整组版本指纹）──────────────────────── */
async function submitReview(payload: {
  decision: FactDecision
  selectedTarget: string | null
  reason: string
}): Promise<void> {
  const id = selectedDocId.value
  const feature = selectedFeature.value
  const version = loadedVersion.value
  const state = associationState.value
  reviewError.value = null
  reviewNotice.value = ''
  if (!id || !feature || !version || !state?.feature_sha256) {
    reviewError.value = {
      code: 'CAD_REVIEW_CONTEXT_MISSING',
      text: '缺少提交所需的版本 / 要素 / feature_sha256，已阻止提交：这组指纹必须由服务端给出，本页不盲填。',
    }
    return
  }
  if (historyLoading.value || historyError.value) {
    reviewError.value = {
      code: 'CAD_REVIEW_CONTEXT_MISSING',
      text: '复核历史尚未就绪，previous_review_id 未知，已阻止提交（猜一个会让服务端 409 CAD_REVIEW_STATE_CHANGED）。',
    }
    return
  }
  const previous = history.value?.latest_review?.review_id ?? null
  const body = {
    document_version: version.document_version,
    source_sha256: version.source_sha256,
    parser_version: version.parser_version,
    bundle_sha256: version.bundle_sha256,
    feature_sha256: state.feature_sha256,
    previous_review_id: previous,
    decision: payload.decision,
    selected_target: payload.decision === 'confirmed' ? payload.selectedTarget : null,
    reason: payload.reason.trim(),
  }
  // 内容签名 → 同一个 request_id：相同内容重发是幂等重试，内容一改就换新 id。
  const request_id = stableRequestId('cadrev', JSON.stringify(body))
  const full: DrawingFeatureReviewInput = { request_id, ...body }
  reviewSubmitting.value = true
  try {
    await submitFeatureReview(id, feature.feature_id, full)
    forgetRequestId('cadrev')
    reviewFormKey.value += 1
    reviewNotice.value =
      '复核已记录。它只写入 association_reviews / review_gate：features[] 的机器判定与 inspection_gate 不会被改写，原文、单位和几何不变，也不代表图纸审批或生产合格。'
    await loadVersionData()
    await loadHistory()
  } catch (e: any) {
    const code = typeof e?.errorCode === 'string' ? e.errorCode : ''
    if (isCadConflict(code)) forgetRequestId('cadrev')
    await handleLoadError(e, (v) => { reviewError.value = v }, '复核提交失败')
  } finally {
    reviewSubmitting.value = false
  }
}

/* ── 登记新版本（admin）────────────────────────────────────────────────── */
async function submitRegistration(body: DrawingRegistrationInput): Promise<void> {
  registerError.value = null
  registerNotice.value = ''
  registerSubmitting.value = true
  try {
    const res = await registerDrawing(body)
    forgetRequestId('cadreg')
    registerFormKey.value += 1
    const created = res?.version
    registerNotice.value = created
      ? `已登记 ${created.document_id} v${created.document_version}（服务端 semantics：${res.semantics || '—'}）。登记不代表审批或生产质量结论。`
      : `登记接口返回了 2xx，但没有 version 字段 —— 不能据此认为登记成功。`
    if (created?.document_id) {
      selectedDocId.value = created.document_id
      viewVersion.value = null
      selectedFeatureId.value = null
      history.value = null
      clearVersionData()
      await loadDrawings()
      await loadVersions()
      await loadVersionData()
      await loadSources()
    }
  } catch (e: any) {
    const code = typeof e?.errorCode === 'string' ? e.errorCode : ''
    if (isCadConflict(code)) forgetRequestId('cadreg')
    await handleLoadError(e, (v) => { registerError.value = v }, '登记失败')
  } finally {
    registerSubmitting.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadIdentity(), loadDrawings()])
  // 自动选中第一份资产：让页面立刻可用，但仍显式标注「读的是哪一版」。
  if (!selectedDocId.value && drawings.value.length) {
    await selectDrawing(drawings.value[0].document_id)
  }
  await loadSources()
})
</script>

<template>
  <div class="mainpage">
    <SideNav @new-chat="backToChat" />

    <div class="center">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">图纸与实体</span>
          <span class="tb-note">
            版本化 CAD 图纸资产 + 标注/几何关联复核：登记不代表审批或生产质量结论
          </span>
        </div>
        <button class="tb-action" type="button" :disabled="busy" data-testid="cad-refresh" @click="refreshAll">
          {{ busy ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="body">
        <!-- 身份与角色：读接口要身份，写接口要角色；两件事分开说 -->
        <p class="cad-ident" data-testid="cad-identity">
          <template v-if="!identityLoaded">正在读取身份（GET /v1/auth/me）…</template>
          <template v-else-if="anonymous">
            当前是**匿名**访问：图纸接口组不在公开读前缀（/v1/documents、/v1/runs、/assets/）里，
            所以下面每一项读操作都会以 401 <code>LOGIN_REQUIRED</code> 被拒。请先登录再回来。
          </template>
          <template v-else-if="identity?.user">
            当前身份：{{ identity.user.display_name }}（角色 {{ rolesText || '—' }}）。
            登记需要 <b>ADMIN</b>；关联复核需要 <b>REVIEWER</b>（ADMIN 可过所有闸门，APPROVER 在复核接口会被 403 PERMISSION_DENIED）。
          </template>
          <template v-else>身份未知（/v1/auth/me 读取失败）：下面按钮可能被服务端拒绝，页面照实报错。</template>
        </p>
        <CadErrorNote :error="identityError" testid="cad-identity-error" />

        <!-- 诚实声明：本页所有结论的边界，放在最显眼处 -->
        <section class="cad-declare" data-testid="cad-honesty">
          <p>登记不代表审批或生产质量结论。</p>
          <p>复核只确认标注对应哪个候选圆；原文、单位和几何保持不变，不代表图纸审批或生产合格。</p>
          <p>
            页面上所有 provenance（来源）都标着「（登记声明）」：它是登记时的自述，服务端不验证其真实性。
            门槛冲突（例如 <code>CAD_GEOMETRY_ANNOTATION_CONFLICT</code>）来自原文本身，复核或审批都消除不了。
          </p>
        </section>

        <CadErrorNote :error="conflictNotice" testid="cad-conflict" />
        <p v-if="conflictNotice" class="cad-conflict-note" data-testid="cad-conflict-note">{{ CONFLICT_NOTE }}</p>

        <!-- 1) 资产列表 -->
        <section class="cad-section" data-testid="cad-assets">
          <h2 class="cad-sec-title">
            图纸资产<span class="cad-sec-tag">listDrawings · 每份只列当前版本</span>
          </h2>
          <p class="cad-hint">
            列表只说明「本地登记过哪些版本」；provenance 是登记人自述
            （界面统一标注「（登记声明）」），不代表来源已被核实。
          </p>

          <CadErrorNote :error="drawingsError" testid="cad-drawings-error" />
          <p v-if="drawingsLoading" class="cad-hint">正在加载图纸资产…</p>
          <p v-else-if="!drawingsError && !drawings.length" class="cad-hint" data-testid="cad-drawings-empty">
            还没有登记过任何图纸版本。管理员可以在页面下方「登记图纸版本」里登记第一份 .dxf
            （需要 ADMIN；本读取器要求源文件带 handle）。
          </p>

          <ul v-else class="cad-assets" data-testid="cad-asset-list">
            <li v-for="d in drawings" :key="d.document_id">
              <button
                class="cad-asset"
                type="button"
                :class="{ 'cad-is-active': d.document_id === selectedDocId }"
                data-testid="cad-asset-row"
                @click="selectDrawing(d.document_id)"
              >
                <span class="cad-asset-title">{{ d.title || '（无标题）' }}</span>
                <span class="cad-asset-meta">
                  <code>{{ d.document_id }}</code> · v{{ d.document_version }} ·
                  {{ provenanceText(d.provenance) }} · 登记人 {{ d.registrar_label || '—' }} ·
                  {{ stamp(d.created_at) }}
                </span>
                <span class="cad-asset-hashes">
                  source <code>{{ shortHash(d.source_sha256, 10) }}</code>
                  · bundle <code>{{ shortHash(d.bundle_sha256, 10) }}</code>
                  · parser <code>{{ d.parser_version }}</code>
                </span>
              </button>
            </li>
          </ul>
        </section>

        <!-- 2) 版本选择器 -->
        <section v-if="selectedDocId" class="cad-section" data-testid="cad-versions">
          <h2 class="cad-sec-title">
            版本选择<span class="cad-sec-tag">drawingVersions · 当前 v{{ currentVersion ?? '—' }}</span>
          </h2>
          <p class="cad-hint">
            当前版本是 <b>v{{ currentVersion ?? '—' }}</b>。历史版本**可以读，但只读**：切版本会重新拉取该版本的
            要素与复核历史。复核记录按 (版本, 要素) 分开存储 —— **新版本不继承上一版本的复核结论**。
          </p>

          <CadErrorNote :error="versionsError" testid="cad-versions-error" />
          <p v-if="versionsLoading" class="cad-hint">正在读取版本历史…</p>
          <div v-else-if="versions.length" class="cad-version-row" data-testid="cad-version-list">
            <button
              v-for="v in versions"
              :key="v.document_version"
              class="cad-version"
              type="button"
              :class="{
                'cad-is-active': v.document_version === (viewVersion ?? currentVersion),
                'cad-is-current': v.document_version === currentVersion,
              }"
              data-testid="cad-version-btn"
              :title="`v${v.document_version} · ${v.title} · ${stamp(v.created_at)}`"
              @click="selectVersion(v.document_version === currentVersion ? null : v.document_version)"
            >
              v{{ v.document_version }}
              <span class="cad-version-tag">{{ v.document_version === currentVersion ? '当前' : '历史·只读' }}</span>
              <span class="cad-version-when">{{ stamp(v.created_at) }}</span>
            </button>
          </div>
          <p
            v-else-if="!versionsError"
            class="cad-hint"
            data-testid="cad-versions-empty"
          >
            服务端没有返回任何版本历史：这份资产目前没有任何可读版本，不能据此认为它可用。
          </p>

          <div v-if="loadedVersion" class="cad-vinfo" data-testid="cad-version-info">
            <p>
              正在读：<b>{{ versionLabel }}</b>
              · title {{ loadedVersion.title || '—' }}
              · 登记人 {{ loadedVersion.registrar_label || '—' }}
              · {{ stamp(loadedVersion.created_at) }}
            </p>
            <p>
              来源快照：format <code>{{ detail?.source?.format ?? '—' }}</code>
              · parser <code>{{ loadedVersion.parser_version }}</code>
              · INSUNITS <code>{{ orDash(detail?.source?.insunits) }}</code>
              → unit <code>{{ detail?.source?.unit ?? '—' }}</code>
              · layout <code>{{ detail?.source?.layout ?? '—' }}</code>
              · 坐标系 <code>{{ detail?.source?.coordinate_system ?? '—' }}</code>
              · {{ provenanceText(loadedVersion.provenance) }}
            </p>
            <p class="cad-hint">
              单位只认 DXF 的 INSUNITS 设置，不是从几何或文件名猜的；画不出来或单位缺失时会照实报 unknown。
            </p>
            <p v-if="detail" class="cad-hint">
              业务身份：
              drawing_id <code>{{ detail.identity?.drawing_id?.value ?? '—' }}</code>
              / drawing_revision <code>{{ detail.identity?.drawing_revision?.value ?? '—' }}</code>
              / part_id <code>{{ detail.identity?.part_id?.value ?? '—' }}</code>。
              图纸版次**不等于**工艺版次，本页不做隐式映射。
            </p>
            <p class="cad-hint">
              版本指纹：source <code>{{ shortHash(loadedVersion.source_sha256, 16) }}</code>
              · bundle <code>{{ shortHash(loadedVersion.bundle_sha256, 16) }}</code>
              （复核提交时服务端会 CAS 校验整组指纹）
            </p>
          </div>
          <CadErrorNote :error="detailError" testid="cad-detail-error" />
        </section>

        <p v-else-if="!drawingsLoading && drawings.length" class="cad-hint" data-testid="cad-no-selection">
          还没有选中图纸：请在上面的资产列表里点一份，版本、要素、复核与图元才会加载。
        </p>

        <!-- 3) 查看器 -->
        <CadViewer
          v-if="selectedDocId && loadedVersion"
          :document-id="selectedDocId || ''"
          :version="loadedVersion?.document_version ?? null"
          :version-label="versionLabel"
        />

        <!-- 4) 机器提取（原文/几何） -->
        <CadMachineExtraction
          v-if="selectedDocId"
          :features="features"
          :inspection-gate="featuresData?.inspection_gate ?? null"
          :review-gate="featuresData?.review_gate ?? null"
          :selected-feature-id="selectedFeatureId"
          :loading="versionLoading"
          :error="featuresError"
          :version-label="versionLabel"
          @select-feature="selectFeature"
        />

        <!-- 5) 人工关联复核 -->
        <CadReviewPanel
          v-if="selectedDocId"
          :version="loadedVersion"
          :current-version="currentVersion"
          :read-only-version="readOnlyVersion"
          :feature="selectedFeature"
          :association-state="associationState"
          :association-reviews="associationReviews"
          :review-gate="featuresData?.review_gate ?? null"
          :reviews="historyReviews"
          :previous-review-id="history?.latest_review?.review_id ?? null"
          :history-loading="historyLoading"
          :history-error="historyError"
          :review-semantics="featuresData?.review_semantics ?? ''"
          :submitting="reviewSubmitting"
          :submit-error="reviewError"
          :submit-notice="reviewNotice"
          :can-review="canReview"
          :roles-text="rolesText"
          :identity-loaded="identityLoaded"
          :anonymous="anonymous"
          :form-key="reviewFormKey"
          @submit="submitReview"
          @reload-history="reloadHistory"
          @select-feature="selectFeature"
        />

        <!-- 6) 图元明细 -->
        <CadEntityTable
          v-if="selectedDocId"
          :entities="entitiesData?.entities ?? []"
          :views="entitiesData?.views ?? []"
          :loading="versionLoading"
          :error="entitiesError"
        />

        <!-- 7) 管理员登记 -->
        <CadRegisterForm
          :sources="sources"
          :sources-loading="sourcesLoading"
          :sources-error="sourcesError"
          :can-register="canRegister"
          :identity-loaded="identityLoaded"
          :anonymous="anonymous"
          :roles-text="rolesText"
          :default-document-id="selectedDocId ?? ''"
          :default-title="chainEntry?.title ?? ''"
          :chain-version="currentVersion"
          :chain-source-sha256="chainEntry?.source_sha256 ?? null"
          :submitting="registerSubmitting"
          :submit-error="registerError"
          :submit-notice="registerNotice"
          :form-key="registerFormKey"
          @submit="submitRegistration"
        />

        <p class="cad-foot">
          「机器提取」与「人工关联复核」是两份独立数据：复核记录只进 association_reviews / review_gate，
          不会改写 features[].association_status，也不会让原文、单位或几何发生变化。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 cad- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会出现
   「只声明了部分属性、其余被工具类接管」的怪象（本仓库已出过两次事故）。 */
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
  flex-wrap: wrap;
}
.tb-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.tb-note {
  font-size: 12.5px;
  color: var(--color-muted);
}
.tb-action {
  margin-left: auto;
  font: inherit;
  font-size: 12.5px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.tb-action:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
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
  max-width: 1180px;
  width: 100%;
  box-sizing: border-box;
}
.cad-ident {
  margin: 0 0 10px;
  padding: 9px 11px;
  border: 1px solid var(--color-line);
  border-radius: 10px;
  background: var(--color-ink);
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--color-body);
}
.cad-ident code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 5px;
  padding: 1px 5px;
}
.cad-declare {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid rgba(217, 119, 6, 0.35);
  border-radius: 10px;
  background: rgba(217, 119, 6, 0.06);
  font-size: 12px;
  line-height: 1.8;
  color: #b45309;
}
.cad-declare p {
  margin: 0 0 4px;
}
.cad-declare p:last-child {
  margin-bottom: 0;
}
.cad-declare code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  background: rgba(217, 119, 6, 0.12);
  border-radius: 5px;
  padding: 1px 5px;
}
.cad-conflict-note {
  margin: 0 0 14px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--color-muted);
}
.cad-section {
  margin: 0 0 22px;
  padding: 14px 16px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
}
.cad-sec-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.cad-sec-tag {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--color-muted);
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 2px 8px;
}
.cad-hint {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--color-muted);
}
.cad-hint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  background: var(--color-ink);
  border-radius: 5px;
  padding: 1px 5px;
  color: var(--color-text);
}
.cad-assets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cad-asset {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  text-align: left;
  font: inherit;
  padding: 10px 12px;
  border: 1px solid var(--color-line);
  border-radius: 10px;
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.cad-asset:hover {
  border-color: rgba(37, 99, 235, 0.4);
}
.cad-asset.cad-is-active {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.06);
}
.cad-asset-title {
  font-size: 13.5px;
  color: var(--color-text);
}
.cad-asset-meta,
.cad-asset-hashes {
  font-size: 11.5px;
  color: var(--color-muted);
  line-height: 1.6;
  word-break: break-word;
}
.cad-asset-meta code,
.cad-asset-hashes code,
.cad-vinfo code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  background: var(--color-ink);
  border-radius: 5px;
  padding: 1px 5px;
  color: var(--color-text);
}
.cad-version-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.cad-version {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font: inherit;
  font-size: 12.5px;
  padding: 7px 12px;
  border-radius: 10px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
  text-align: left;
}
.cad-version.cad-is-current {
  border-color: rgba(22, 163, 74, 0.45);
}
.cad-version.cad-is-active {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.07);
}
.cad-version-tag {
  font-size: 10.5px;
  color: var(--color-muted);
}
.cad-version-when {
  font-size: 10.5px;
  color: var(--color-muted);
}
.cad-vinfo {
  padding: 10px 12px;
  border: 1px solid var(--color-line);
  border-radius: 10px;
  background: var(--color-ink);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-body);
}
.cad-vinfo p {
  margin: 0 0 6px;
}
.cad-vinfo p:last-child {
  margin-bottom: 0;
}
.cad-foot {
  margin: 0;
  padding-top: 4px;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-muted);
}
</style>
