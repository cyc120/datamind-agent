<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import {
  drawingFeatures,
  featureReviews,
  getBinding,
  getDrawing,
  getMeasurement,
  listBindings,
  registerBinding,
  registerMeasurement,
} from '../../api/client'
import type {
  CadBusinessIdentity,
  DimensionBindingHead,
  DimensionBindingInput,
  DimensionBindingPayload,
  DimensionBindingRegistrationResult,
  DimensionMeasurementHead,
  DimensionMeasurementInput,
  DimensionMeasurementPayload,
  DimensionMeasurementResult,
  DimensionSourceEntry,
  DrawingFeatureReview,
} from '../../api/types'
import {
  REASON_TEXT,
  apiErrorText,
  checkHandle,
  checkIdentifier,
  checkSha,
  checkTimestamp,
  newRequestId,
  shortHash,
  whenText,
} from './shared'

/**
 * 管理员登记表单：尺寸记录（MeasurementRegistration）与绑定（BindingRegistration）。
 *
 * 两个登记的公共形状是 **CAS（compare-and-swap）**：必须显式提交 previous_version 与
 * previous_sha256，且必须等于服务端当前链尾；不等于就是 409 *_STATE_CHANGED。所以
 * 表单里这两个字段不是装饰，而是并发控制的输入 —— 「首次登记」必须显式是 0 / null
 * （后端是 required-but-nullable，**省略会 422**，传空字符串同样不合法）。
 *
 * 绑定的三道前置条件（都要在提交前说清楚，而不是让用户撞一次错）：
 *   1. 必须有**已 confirmed 的关联复核**（关联复核在「图纸与实体」页产生）。目标有歧义
 *      时后端 409 DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED —— 本表单在读取复核历史后
 *      就会提示「有没有 confirmed」，没有就直说会被拒。
 *   2. 图纸 / 要素 / 尺寸记录的三元组哈希必须与提交时的链尾一致（所以有
 *      「读取上游证据」按钮，从真实接口取值，而不是让用户手抄）。
 *   3. valid_from / valid_to **必须带时区偏移**：没有偏移后端无法判定先后
 *      （DIMENSION_TIMEZONE_REQUIRED）。本表单在本地先拦一次，并给同一句中文。
 *
 * 一个诚实的能力边界：复核接口**不返回复核哈希**（AssociationReview 里没有
 * review_sha256 字段），所以 review_sha256 只能在**既有绑定**的载荷里读到。本表单因此
 * 提供「从既有绑定抄取」——找不到就如实说找不到，绝不用一个假值凑数。
 */

const props = defineProps<{
  isAdmin: boolean
  sources: DimensionSourceEntry[]
  sourcesError: string
  measurements: DimensionMeasurementHead[]
  bindings: DimensionBindingHead[]
}>()

const emit = defineEmits<{
  (e: 'registered-measurement', result: DimensionMeasurementResult): void
  (e: 'registered-binding', result: DimensionBindingRegistrationResult): void
}>()

const roleText = computed(() =>
  props.isAdmin ? '' : '登记尺寸记录与绑定都是 admin 操作（POST /v1/dimensions/...）：当前身份不是 admin，表单保持禁用。',
)

/* ══ 尺寸记录登记 ══════════════════════════════════════════════════════ */

const mForm = reactive({
  measurement_id: '',
  source_path: '',
  source_sha256: '',
  provenance: 'synthetic_demo' as 'synthetic_demo' | 'authorized_external',
  previous_version: 0,
  previous_sha256: '',
})
const mBusy = ref(false)
const mError = ref('')
const mOk = ref('')
const mNote = ref('')

/** 选中源文件后带出路径 / 哈希 / 来源：这三样由服务端扫描暂存目录给出，不手填。 */
function pickSource(): void {
  const hit = props.sources.find((s) => s.source_path === mForm.source_path)
  if (!hit) {
    // 回到占位项（空路径）时清掉哈希，避免把上一个源文件的 sha 提交给新路径。
    if (!mForm.source_path) mForm.source_sha256 = ''
    return
  }
  mForm.source_sha256 = hit.source_sha256
  if (hit.provenance === 'authorized_external' || hit.provenance === 'synthetic_demo') {
    mForm.provenance = hit.provenance
  }
}

/** 按 measurement_id 读链尾：找到就把 CAS 的 previous_* 填成当前值。 */
async function lookupMeasurementHead(): Promise<void> {
  mNote.value = ''
  const err = checkIdentifier('measurement_id', mForm.measurement_id)
  if (err) {
    mError.value = err
    return
  }
  const head = props.measurements.find((m) => m.measurement_id === mForm.measurement_id.trim())
  if (!head) {
    mForm.previous_version = 0
    mForm.previous_sha256 = ''
    mNote.value = '本地列表里没有这个 measurement_id：按首次登记处理（previous_version=0、previous_sha256=null）。'
    return
  }
  mBusy.value = true
  try {
    const res = await getMeasurement(head.measurement_id)
    mForm.previous_version = res.measurement.payload.source.record_version
    mForm.previous_sha256 = res.measurement.sha256
    mNote.value = `已按链尾填入：previous_version=${mForm.previous_version}、previous_sha256=${shortHash(res.measurement.sha256)}。`
    mError.value = ''
  } catch (e: any) {
    mError.value = apiErrorText(e, '读取尺寸记录链尾失败')
  } finally {
    mBusy.value = false
  }
}

async function submitMeasurement(): Promise<void> {
  mError.value = ''
  mOk.value = ''
  if (!props.isAdmin) {
    mError.value = roleText.value
    return
  }
  const problems = [
    checkIdentifier('measurement_id', mForm.measurement_id),
    mForm.source_path.trim() ? '' : 'source_path 不能为空（应从源文件列表选择）',
    checkSha('source_sha256', mForm.source_sha256),
  ].filter(Boolean)
  if (mForm.previous_version < 0) problems.push('previous_version 不能为负')
  if (mForm.previous_version > 0) problems.push(checkSha('previous_sha256', mForm.previous_sha256))
  if (problems.length) {
    mError.value = problems.join('；')
    return
  }
  const body: DimensionMeasurementInput = {
    request_id: newRequestId(),
    measurement_id: mForm.measurement_id.trim(),
    source_path: mForm.source_path.trim(),
    source_sha256: mForm.source_sha256.trim(),
    provenance: mForm.provenance,
    previous_version: mForm.previous_version,
    // required-but-nullable：首次登记必须**显式**传 null
    previous_sha256: mForm.previous_version === 0 ? null : mForm.previous_sha256.trim(),
  }
  mBusy.value = true
  try {
    const res = await registerMeasurement(body)
    mOk.value = `已登记尺寸记录 v${res.measurement.payload.source.record_version} · sha256=${shortHash(res.measurement.sha256)}`
    emit('registered-measurement', res)
  } catch (e: any) {
    mError.value = apiErrorText(e, '登记尺寸记录失败')
  } finally {
    mBusy.value = false
  }
}

/* ══ 绑定登记 ══════════════════════════════════════════════════════════ */

interface BindingForm {
  binding_id: string
  previous_version: number
  previous_sha256: string
  document_id: string
  document_version: number
  source_sha256: string
  bundle_sha256: string
  feature_id: string
  feature_sha256: string
  target_handle: string
  measurement_id: string
  measurement_record_version: number
  measurement_sha256: string
  review_id: string
  review_sha256: string
  valid_from: string
  valid_to: string
  rationale: string
}

const bForm = reactive<BindingForm>({
  binding_id: '',
  previous_version: 0,
  previous_sha256: '',
  document_id: '',
  document_version: 1,
  source_sha256: '',
  bundle_sha256: '',
  feature_id: '',
  feature_sha256: '',
  target_handle: '',
  measurement_id: '',
  measurement_record_version: 1,
  measurement_sha256: '',
  review_id: '',
  review_sha256: '',
  valid_from: '',
  valid_to: '',
  rationale: '',
})

const bErrors = ref<Record<string, string>>({})
const bBusy = ref(false)
const bError = ref('')
const bOk = ref('')
const upstreamNote = ref('')
const upstreamError = ref('')
const copyNote = ref('')
const copyBusy = ref(false)

/** 读取到的上游证据（用于在表单上如实展示，而不是替用户猜） */
const drawingIdentity = ref<CadBusinessIdentity | null>(null)
const featureStatus = ref('')
const featureCandidates = ref<string[]>([])
const featureExcluded = ref('')
const reviews = ref<DrawingFeatureReview[]>([])
const measurementInfo = ref<DimensionMeasurementPayload | null>(null)

/** 已加载复核历史里，最后一条 confirmed（后端注册绑定用的就是 reviews[-1]，必须与它一致） */
const lastReview = computed<DrawingFeatureReview | null>(() => {
  const rows = reviews.value
  return rows.length ? rows[rows.length - 1] : null
})
const confirmedReviews = computed(() => reviews.value.filter((r) => r.decision === 'confirmed'))

function setField(key: keyof BindingForm, raw: string, kind: 'text' | 'number' | 'textarea'): void {
  if (kind === 'number') (bForm as any)[key] = Number(raw || 0)
  else (bForm as any)[key] = raw
  if (bErrors.value[key]) delete bErrors.value[key]
}

interface FieldSpec {
  key: keyof BindingForm
  label: string
  kind: 'text' | 'number' | 'textarea'
  ph?: string
}

const bGroups: { title: string; fields: FieldSpec[] }[] = [
  {
    title: '绑定标识与 CAS',
    fields: [
      { key: 'binding_id', label: 'binding_id', kind: 'text' },
      { key: 'previous_version', label: 'previous_version（首次 0）', kind: 'number' },
      { key: 'previous_sha256', label: 'previous_sha256（首次留空 = null）', kind: 'text', ph: '64 位小写 hex，首次留空' },
    ],
  },
  {
    title: '图纸版本元组（用「读取上游证据」取值）',
    fields: [
      { key: 'document_id', label: 'document_id', kind: 'text' },
      { key: 'document_version', label: 'document_version', kind: 'number' },
      { key: 'source_sha256', label: 'source_sha256', kind: 'text' },
      { key: 'bundle_sha256', label: 'bundle_sha256', kind: 'text' },
    ],
  },
  {
    title: '要素与关联目标',
    fields: [
      { key: 'feature_id', label: 'feature_id', kind: 'text' },
      { key: 'feature_sha256', label: 'feature_sha256', kind: 'text' },
      { key: 'target_handle', label: 'target_handle（必须等于复核选定的目标）', kind: 'text' },
    ],
  },
  {
    title: '尺寸记录版本元组（生产范围由该记录决定，表单里不手填）',
    fields: [
      { key: 'measurement_id', label: 'measurement_id', kind: 'text' },
      { key: 'measurement_record_version', label: 'measurement_record_version', kind: 'number' },
      { key: 'measurement_sha256', label: 'measurement_sha256', kind: 'text' },
    ],
  },
  {
    title: '关联复核',
    fields: [
      { key: 'review_id', label: 'review_id', kind: 'text' },
      { key: 'review_sha256', label: 'review_sha256（复核接口不返回，只能从既有绑定抄）', kind: 'text' },
    ],
  },
  {
    title: '有效期与依据',
    fields: [
      { key: 'valid_from', label: 'valid_from（必须带时区偏移）', kind: 'text', ph: '2026-01-01T00:00:00+00:00' },
      { key: 'valid_to', label: 'valid_to（必须带时区偏移）', kind: 'text', ph: '2026-12-31T00:00:00+00:00' },
      { key: 'rationale', label: 'rationale（必填）', kind: 'textarea', ph: '为什么把这条尺寸记录绑定到这个要素' },
    ],
  },
]

/** 一次读齐上游证据：图纸版本 → 要素与复核历史 → 尺寸记录。任一步失败就如实说失败。 */
async function loadUpstream(): Promise<void> {
  upstreamError.value = ''
  upstreamNote.value = ''
  const docErr = checkIdentifier('document_id', bForm.document_id, 64)
  if (docErr) {
    upstreamError.value = docErr
    return
  }
  const featErr = checkIdentifier('feature_id', bForm.feature_id)
  if (featErr) {
    upstreamError.value = featErr
    return
  }
  bBusy.value = true
  try {
    const drawing = await getDrawing(bForm.document_id.trim())
    bForm.document_version = drawing.version.document_version
    bForm.source_sha256 = drawing.version.source_sha256
    bForm.bundle_sha256 = drawing.version.bundle_sha256
    drawingIdentity.value = drawing.identity

    const features = await drawingFeatures(bForm.document_id.trim(), drawing.version.document_version)
    const state = (features.association_reviews ?? []).find((s) => s.feature_id === bForm.feature_id.trim())
    const feature = (features.features ?? []).find((f) => f.feature_id === bForm.feature_id.trim())
    const excluded = (features.review_gate?.excluded ?? []).find((x) => x.feature_id === bForm.feature_id.trim())
    featureStatus.value = feature?.association_status ?? '(复核历史里没有这个要素)'
    featureCandidates.value = feature?.target_candidates ?? []
    featureExcluded.value = excluded?.reason ?? ''
    if (state) bForm.feature_sha256 = state.feature_sha256

    const history = await featureReviews(bForm.document_id.trim(), bForm.feature_id.trim(), drawing.version.document_version)
    reviews.value = Array.isArray(history?.reviews) ? history.reviews : []
    const latest = history?.latest_review ?? null
    if (latest) {
      // 预填的是**最后一条复核**（后端注册绑定引用的就是 reviews[-1]），并明确提示请核对，
      // 绝不静默替用户做关联决策。
      bForm.review_id = latest.review_id
      if (latest.decision === 'confirmed' && latest.selected_target) {
        bForm.target_handle = latest.selected_target
      }
      upstreamNote.value = `已按最后一条关联复核预填：review_id=${latest.review_id}（decision=${latest.decision}）。请核对，不要直接当作已确认。`
    } else {
      upstreamNote.value = '这个要素还没有任何关联复核记录：绑定会被 409 DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED 拒绝。'
    }

    if (bForm.measurement_id.trim()) {
      const m = await getMeasurement(bForm.measurement_id.trim())
      bForm.measurement_record_version = m.measurement.payload.source.record_version
      bForm.measurement_sha256 = m.measurement.sha256
      measurementInfo.value = m.measurement.payload
    } else {
      measurementInfo.value = null
    }
  } catch (e: any) {
    upstreamError.value = apiErrorText(e, '读取上游证据失败')
  } finally {
    bBusy.value = false
  }
}

/**
 * review_sha256 只能从既有绑定抄：关联复核接口不暴露复核哈希。
 * 扫描前 20 条绑定，找到引用同一 document + review 的那条就复制；找不到就如实说找不到。
 */
async function copyReviewSha(): Promise<void> {
  copyNote.value = ''
  if (!bForm.review_id.trim()) {
    copyNote.value = '先填 review_id（或点「读取上游证据」带出复核历史）再抄取。'
    return
  }
  copyBusy.value = true
  try {
    const list = await listBindings()
    const heads = (list.bindings ?? []).slice(0, 20)
    for (const head of heads) {
      try {
        const detail = await getBinding(head.binding_id, head.current_version)
        const p: DimensionBindingPayload = detail.binding.payload
        if (p.document_id === bForm.document_id.trim() && p.review_id === bForm.review_id.trim()) {
          bForm.review_sha256 = p.review_sha256
          if (!bForm.bundle_sha256) bForm.bundle_sha256 = p.bundle_sha256
          if (!bForm.source_sha256) bForm.source_sha256 = p.source_sha256
          if (!bForm.feature_sha256) bForm.feature_sha256 = p.feature_sha256
          if (!bForm.target_handle) bForm.target_handle = p.target_handle
          if (bForm.document_version < 1) bForm.document_version = p.document_version
          copyNote.value = `已从既有绑定 ${p.binding_id}（v${p.binding_version}）抄取 review_sha256=${shortHash(p.review_sha256)}。`
          return
        }
      } catch {
        /* 单条读不到就跳过；循环结束后如实报告「没找到」 */
      }
    }
    copyNote.value =
      '扫描前 20 条绑定后没有找到引用同一复核的记录：复核接口不返回复核哈希，review_sha256 必须由登记人从可信来源提供，本页不会替它编一个值。'
  } catch (e: any) {
    copyNote.value = apiErrorText(e, '读取绑定列表失败')
  } finally {
    copyBusy.value = false
  }
}

function validateBinding(): boolean {
  const errs: Record<string, string> = {}
  errs.binding_id = checkIdentifier('binding_id', bForm.binding_id)
  if (bForm.previous_version < 0) errs.previous_version = 'previous_version 不能为负'
  if (bForm.previous_version > 0) errs.previous_sha256 = checkSha('previous_sha256', bForm.previous_sha256)
  errs.document_id = checkIdentifier('document_id', bForm.document_id, 64)
  if (bForm.document_version < 1) errs.document_version = 'document_version 必须 ≥ 1'
  errs.source_sha256 = checkSha('source_sha256', bForm.source_sha256)
  errs.bundle_sha256 = checkSha('bundle_sha256', bForm.bundle_sha256)
  errs.feature_id = checkIdentifier('feature_id', bForm.feature_id)
  errs.feature_sha256 = checkSha('feature_sha256', bForm.feature_sha256)
  errs.target_handle = checkHandle('target_handle', bForm.target_handle)
  errs.measurement_id = checkIdentifier('measurement_id', bForm.measurement_id)
  if (bForm.measurement_record_version < 1) errs.measurement_record_version = 'measurement_record_version 必须 ≥ 1'
  errs.measurement_sha256 = checkSha('measurement_sha256', bForm.measurement_sha256)
  errs.review_id = checkIdentifier('review_id', bForm.review_id)
  errs.review_sha256 = checkSha('review_sha256', bForm.review_sha256)
  errs.valid_from = checkTimestamp('valid_from', bForm.valid_from)
  errs.valid_to = checkTimestamp('valid_to', bForm.valid_to)
  if (!errs.valid_from && !errs.valid_to && bForm.valid_from.trim() >= bForm.valid_to.trim()) {
    errs.valid_to = `valid_to 必须晚于 valid_from：${REASON_TEXT.DIMENSION_VALIDITY_INTERVAL_INVALID}`
  }
  errs.rationale = bForm.rationale.trim() ? '' : 'rationale（依据）不能为空'
  for (const key of Object.keys(errs)) if (!errs[key]) delete errs[key]
  bErrors.value = errs
  return Object.keys(errs).length === 0
}

async function submitBinding(): Promise<void> {
  bError.value = ''
  bOk.value = ''
  if (!props.isAdmin) {
    bError.value = roleText.value
    return
  }
  if (!validateBinding()) {
    bError.value = '有字段不满足契约（见各字段下方提示）。绑定载荷是 extra=forbid 的严格模型，本地先拦一次。'
    return
  }
  const body: DimensionBindingInput = {
    request_id: newRequestId(),
    binding_id: bForm.binding_id.trim(),
    previous_version: bForm.previous_version,
    previous_sha256: bForm.previous_version === 0 ? null : bForm.previous_sha256.trim(),
    document_id: bForm.document_id.trim(),
    document_version: bForm.document_version,
    source_sha256: bForm.source_sha256.trim(),
    bundle_sha256: bForm.bundle_sha256.trim(),
    feature_id: bForm.feature_id.trim(),
    feature_sha256: bForm.feature_sha256.trim(),
    target_handle: bForm.target_handle.trim(),
    measurement_id: bForm.measurement_id.trim(),
    measurement_record_version: bForm.measurement_record_version,
    measurement_sha256: bForm.measurement_sha256.trim(),
    review_id: bForm.review_id.trim(),
    review_sha256: bForm.review_sha256.trim(),
    valid_from: bForm.valid_from.trim(),
    valid_to: bForm.valid_to.trim(),
    rationale: bForm.rationale.trim(),
  }
  bBusy.value = true
  try {
    const res = await registerBinding(body)
    bOk.value = `已登记绑定 ${res.binding.payload.binding_id} v${res.binding.payload.binding_version}`
    emit('registered-binding', res)
  } catch (e: any) {
    bError.value = apiErrorText(e, '登记绑定失败')
  } finally {
    bBusy.value = false
  }
}

/** 从绑定列表读链尾，填 CAS 字段（与尺寸记录同一套并发控制口径）。 */
async function lookupBindingHead(): Promise<void> {
  const err = checkIdentifier('binding_id', bForm.binding_id)
  if (err) {
    bErrors.value = { ...bErrors.value, binding_id: err }
    return
  }
  const head = props.bindings.find((b) => b.binding_id === bForm.binding_id.trim())
  if (!head) {
    bForm.previous_version = 0
    bForm.previous_sha256 = ''
    upstreamNote.value = '本地列表里没有这个 binding_id：按首次登记处理（previous_version=0、previous_sha256=null）。'
    return
  }
  bBusy.value = true
  try {
    const detail = await getBinding(head.binding_id)
    bForm.previous_version = detail.binding.payload.binding_version
    bForm.previous_sha256 = detail.binding.sha256
    upstreamNote.value = `已按链尾填入：previous_version=${bForm.previous_version}、previous_sha256=${shortHash(detail.binding.sha256)}。`
  } catch (e: any) {
    upstreamError.value = apiErrorText(e, '读取绑定链尾失败')
  } finally {
    bBusy.value = false
  }
}
</script>

<template>
  <div class="dimg-wrap">
    <p v-if="!isAdmin" class="dimg-refuse" data-testid="dim-admin-refuse">{{ roleText }}</p>

    <!-- ── 尺寸记录登记 ─────────────────────────────────────────────── -->
    <section class="dimg-card" data-testid="dim-measurement-form">
      <h3 class="dimg-title">登记尺寸记录（追加新版本）</h3>
      <p class="dimg-note">
        measurement_id <b>必须已经写在源文件里</b>（后端会用它和文件内容核对，不一致按
        DIMENSION_SOURCE_INVALID 拒绝）；previous_version / previous_sha256 必须等于服务端当前链尾，否则 409
        DIMENSION_MEASUREMENT_STATE_CHANGED。provenance 决定源文件必须位于哪个暂存目录，所以它随所选源文件
        自动带出，不是自由选项。
      </p>
      <p v-if="sourcesError" class="dimg-error" data-testid="dim-sources-error">{{ sourcesError }}</p>

      <div class="dimg-grid">
        <label class="dimg-field dimg-field-wide">
          <span class="dimg-label">源文件（来自 /v1/dimensions/sources）</span>
          <select
            v-model="mForm.source_path"
            class="dimg-input"
            :disabled="!isAdmin"
            data-testid="dim-measurement-source"
            @change="pickSource"
          >
            <option value="">（选择一个暂存源文件）</option>
            <option v-for="s in sources" :key="s.source_path" :value="s.source_path">
              {{ s.name }} · {{ s.provenance }} · {{ shortHash(s.source_sha256) }}
            </option>
          </select>
        </label>

        <label class="dimg-field">
          <span class="dimg-label">measurement_id</span>
          <input
            v-model="mForm.measurement_id"
            class="dimg-input"
            type="text"
            :disabled="!isAdmin"
            data-testid="dim-measurement-id"
            @blur="lookupMeasurementHead"
          />
        </label>
        <label class="dimg-field">
          <span class="dimg-label">provenance</span>
          <input class="dimg-input" type="text" :value="mForm.provenance" disabled />
        </label>
        <label class="dimg-field">
          <span class="dimg-label">source_path</span>
          <input v-model="mForm.source_path" class="dimg-input" type="text" :disabled="!isAdmin" />
        </label>
        <label class="dimg-field">
          <span class="dimg-label">source_sha256</span>
          <input v-model="mForm.source_sha256" class="dimg-input" type="text" :disabled="!isAdmin" data-testid="dim-measurement-sha" />
        </label>
        <label class="dimg-field">
          <span class="dimg-label">previous_version</span>
          <input
            :value="mForm.previous_version"
            class="dimg-input"
            type="number"
            min="0"
            :disabled="!isAdmin"
            data-testid="dim-measurement-previous-version"
            @input="mForm.previous_version = Number(($event.target as HTMLInputElement).value || 0)"
          />
        </label>
        <label class="dimg-field">
          <span class="dimg-label">previous_sha256（首次留空 → 显式 null）</span>
          <input v-model="mForm.previous_sha256" class="dimg-input" type="text" :disabled="!isAdmin" />
        </label>
      </div>

      <p v-if="mNote" class="dimg-hint" data-testid="dim-measurement-note">{{ mNote }}</p>
      <button class="dimg-btn" type="button" :disabled="!isAdmin || mBusy" data-testid="dim-measurement-submit" @click="submitMeasurement">
        {{ mBusy ? '提交中…' : '登记尺寸记录' }}
      </button>
      <p v-if="mError" class="dimg-error" data-testid="dim-measurement-error">{{ mError }}</p>
      <p v-if="mOk" class="dimg-ok" data-testid="dim-measurement-ok">{{ mOk }}</p>
    </section>

    <!-- ── 绑定登记 ─────────────────────────────────────────────────── -->
    <section class="dimg-card" data-testid="dim-binding-form">
      <h3 class="dimg-title">登记绑定（图纸标注 ↔ 尺寸记录）</h3>
      <p class="dimg-note" data-testid="dim-binding-prereq">
        绑定<b>必须</b>引用一条已 confirmed 的关联复核；目标存在歧义时后端 409
        DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED。关联复核在「图纸与实体」页产生 —— 本表单只读取、不代替人工确认。
      </p>

      <div class="dimg-tools">
        <button class="dimg-btn" type="button" :disabled="bBusy" data-testid="dim-binding-load-upstream" @click="loadUpstream">
          {{ bBusy ? '读取中…' : '读取上游证据（图纸/要素/复核/尺寸）' }}
        </button>
        <button class="dimg-btn" type="button" :disabled="bBusy" data-testid="dim-binding-lookup-head" @click="lookupBindingHead">
          按 binding_id 读链尾
        </button>
        <button class="dimg-btn" type="button" :disabled="copyBusy" data-testid="dim-binding-copy-review-sha" @click="copyReviewSha">
          {{ copyBusy ? '扫描中…' : '从既有绑定抄取 review_sha256' }}
        </button>
      </div>

      <div v-if="drawingIdentity || reviews.length || measurementInfo" class="dimg-evidence" data-testid="dim-binding-evidence">
        <div v-if="drawingIdentity" class="dimg-ev-row">
          <span class="dimg-key">图纸标识</span>
          <span class="dimg-val">
            图号 {{ drawingIdentity.drawing_id?.value || '—' }} · 版次
            {{ drawingIdentity.drawing_revision?.value || '—' }} · 件号 {{ drawingIdentity.part_id?.value || '—' }}
          </span>
        </div>
        <div class="dimg-ev-row">
          <span class="dimg-key">要素关联状态</span>
          <span class="dimg-val" data-testid="dim-binding-feature-status">
            {{ featureStatus || '（尚未读取）' }}
            <template v-if="featureCandidates.length">· 候选 {{ featureCandidates.join('、') }}</template>
            <template v-if="featureExcluded">· 门槛原因 {{ featureExcluded }}</template>
          </span>
        </div>
        <div v-if="measurementInfo" class="dimg-ev-row">
          <span class="dimg-key">尺寸记录</span>
          <span class="dimg-val">
            {{ measurementInfo.metric }} · 件号 {{ measurementInfo.part_id }} · 批次 {{ measurementInfo.batch_id }} · 工序
            {{ measurementInfo.operation_no }} · 测量时间 {{ whenText(measurementInfo.measured_at) }}
          </span>
        </div>
        <div v-if="reviews.length" class="dimg-ev-row">
          <span class="dimg-key">复核历史（{{ reviews.length }} 条）</span>
          <span class="dimg-val">
            最后一条：{{ lastReview?.review_id }} · {{ lastReview?.decision }} · 复核人
            {{ lastReview?.reviewer_label || lastReview?.reviewer_user_id }} ·
            {{ whenText(lastReview?.created_at) }}
          </span>
        </div>
        <p
          v-if="lastReview && lastReview.decision === 'confirmed'"
          class="dimg-ok"
          data-testid="dim-binding-has-confirmed"
        >
          已读到 confirmed 复核（{{ confirmedReviews.length }} 条）：review_id={{ lastReview.review_id }}、目标
          {{ lastReview.selected_target || '（未选目标）' }}。绑定只能引用 reviews[-1]，请核对。
        </p>
        <p v-else-if="reviews.length" class="dimg-warn" data-testid="dim-binding-no-confirmed">
          复核历史里最后一条不是 confirmed（当前：{{ lastReview?.decision }}）：提交绑定会被 409
          DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED 拒绝，请先去「图纸与实体」页确认关联。
        </p>
      </div>

      <p v-if="upstreamNote" class="dimg-hint" data-testid="dim-binding-note">{{ upstreamNote }}</p>
      <p v-if="upstreamError" class="dimg-error" data-testid="dim-binding-upstream-error">{{ upstreamError }}</p>
      <p v-if="copyNote" class="dimg-hint" data-testid="dim-binding-copy-note">{{ copyNote }}</p>

      <div v-for="group in bGroups" :key="group.title" class="dimg-group">
        <h4 class="dimg-group-title">{{ group.title }}</h4>
        <div class="dimg-grid">
          <label v-for="f in group.fields" :key="f.key" class="dimg-field" :class="{ 'dimg-field-wide': f.kind === 'textarea' }">
            <span class="dimg-label">{{ f.label }}</span>
            <textarea
              v-if="f.kind === 'textarea'"
              class="dimg-input dimg-textarea"
              rows="3"
              :value="bForm[f.key]"
              :placeholder="f.ph || ''"
              :disabled="!isAdmin"
              :data-testid="`dim-binding-${f.key}`"
              @input="setField(f.key, ($event.target as HTMLTextAreaElement).value, 'textarea')"
            />
            <input
              v-else
              class="dimg-input"
              :type="f.kind === 'number' ? 'number' : 'text'"
              :value="bForm[f.key]"
              :placeholder="f.ph || ''"
              :disabled="!isAdmin"
              :data-testid="`dim-binding-${f.key}`"
              @input="setField(f.key, ($event.target as HTMLInputElement).value, f.kind)"
            />
            <span v-if="bErrors[f.key]" class="dimg-field-error" :data-testid="`dim-binding-${f.key}-error`">
              {{ bErrors[f.key] }}
            </span>
          </label>
        </div>
      </div>

      <button class="dimg-btn" type="button" :disabled="!isAdmin || bBusy" data-testid="dim-binding-submit" @click="submitBinding">
        {{ bBusy ? '提交中…' : '登记绑定' }}
      </button>
      <p v-if="bError" class="dimg-error" data-testid="dim-binding-error">{{ bError }}</p>
      <p v-if="bOk" class="dimg-ok" data-testid="dim-binding-ok">{{ bOk }}</p>
    </section>
  </div>
</template>

<style scoped>
/* 类名统一 dimg- 前缀（Tailwind v4 @layer utilities 撞名的老事故）。 */
.dimg-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.dimg-refuse {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid rgba(217, 119, 6, 0.3);
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.06);
  font-size: 13.5px;
  line-height: 1.7;
  color: #b45309;
}
.dimg-card {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
}
/* 面板标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.dimg-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dimg-note {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dimg-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.dimg-evidence {
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
}
.dimg-ev-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 5px;
}
.dimg-key {
  flex-shrink: 0;
  width: 130px;
  font-size: 13px;
  color: var(--color-muted);
}
.dimg-val {
  font-size: 13.5px;
  color: var(--color-text);
  word-break: break-word;
  line-height: 1.7;
}
.dimg-group {
  margin-top: 12px;
}
/* 分组小标题与其它页统一：蓝色、15px、600（原来是 13.5px 灰黑，属离群值） */
.dimg-group-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dimg-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px;
}
.dimg-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.dimg-field-wide {
  grid-column: 1 / -1;
}
.dimg-label {
  font-size: 13px;
  color: var(--color-muted);
  line-height: 1.5;
}
.dimg-input {
  font: inherit;
  font-size: 14px;
  padding: 7px 9px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
  min-width: 0;
}
.dimg-input:focus {
  outline: none;
  border-color: var(--color-accent);
}
.dimg-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.dimg-textarea {
  resize: vertical;
  line-height: 1.6;
}
.dimg-field-error {
  font-size: 12.5px;
  color: #e11d48;
  line-height: 1.6;
}
/* 按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.dimg-btn {
  margin-top: 10px;
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
.dimg-tools .dimg-btn {
  margin-top: 0;
}
.dimg-btn:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.dimg-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.dimg-hint {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dimg-warn {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: #b45309;
}
.dimg-error {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #e11d48;
}
.dimg-ok {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #16a34a;
}
</style>
