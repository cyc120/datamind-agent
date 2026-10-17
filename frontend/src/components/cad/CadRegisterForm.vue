<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { DrawingRegistrationInput, DrawingSourceEntry } from '../../api/types'
import CadErrorNote from './CadErrorNote.vue'
import { forgetRequestId, provenanceText, shortHash, stableRequestId } from './cadCopy'
import type { CadErrorView } from './cadCopy'

/**
 * 管理员登记：把暂存目录里的一份 .dxf 登记为**新版本**（POST /v1/drawings）。
 *
 * 三件必须说清楚、且直接影响可用性的事实：
 *   1. 登记需要 ADMIN。登记只是把「本地解析 + 版本链」写下来，
 *      **不代表审批或生产质量结论**；
 *   2. 幂等键是 request_id：内容完全一致的重发会拿回同一条版本（这才是重试路径）；
 *      内容或操作者一变就会 409 CAD_REQUEST_ID_REUSED；
 *   3. 本读取器是 `dxf-native-v1`，它**要求源文件带 handle**。没有 handle 的 DXF 会直接
 *      422 失败 —— 本页不提供、也不承诺「部分解析/按位置对齐」的降级结果。
 */
const props = defineProps<{
  sources: DrawingSourceEntry[]
  sourcesLoading: boolean
  sourcesError: CadErrorView | null
  canRegister: boolean
  identityLoaded: boolean
  anonymous: boolean
  rolesText: string
  /** 当前选中的资产（用于把版本链尾填进 previous_*） */
  defaultDocumentId: string
  defaultTitle: string
  chainVersion: number | null
  chainSourceSha256: string | null
  submitting: boolean
  submitError: CadErrorView | null
  submitNotice: string
  /** 成功登记后自增：重置表单与 request_id */
  formKey: number
}>()

const emit = defineEmits<{ (e: 'submit', body: DrawingRegistrationInput): void }>()

const docId = ref('')
const title = ref('')
const sourcePath = ref('')
const prevVersion = ref('0')
const prevSha = ref('')

const currentSource = computed<DrawingSourceEntry | undefined>(() =>
  props.sources.find((s) => s.source_path === sourcePath.value),
)

/** 按「链尾」预填：默认版本 = 当前版本（首次 = 0）、source_sha256 = 当前版本源哈希。 */
function prefillFromChain(): void {
  docId.value = props.defaultDocumentId || ''
  title.value = props.defaultTitle || ''
  prevVersion.value = props.chainVersion != null ? String(props.chainVersion) : '0'
  prevSha.value = props.chainSourceSha256 || ''
  sourcePath.value = ''
}

watch(
  () => [props.defaultDocumentId, props.chainVersion, props.chainSourceSha256, props.formKey],
  () => {
    prefillFromChain()
    if (props.formKey) forgetRequestId('cadreg')
  },
  { immediate: true },
)

const errors = computed<string[]>(() => {
  const list: string[] = []
  const id = docId.value.trim()
  if (!id) list.push('document_id 不能为空。')
  else if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(id) || id.length > 64)
    list.push('document_id 必须匹配 ^[A-Za-z0-9][A-Za-z0-9_.-]*$ 且 ≤64 字符。')
  if (!title.value.trim()) list.push('title 不能为空。')
  else if (title.value.trim().length > 200) list.push('title 最长 200 字符。')
  if (!sourcePath.value) list.push('必须从暂存源文件列表里选一份 .dxf（handle 缺失的文件会在服务端 422）。')
  else if (!currentSource.value) list.push('选中的 source_path 不在服务端返回的暂存列表里。')
  const n = Number(prevVersion.value)
  if (!Number.isInteger(n) || n < 0) list.push('previous_version 必须是不小于 0 的整数（首次登记填 0）。')
  const sha = prevSha.value.trim()
  if (sha && !/^[a-f0-9]{64}$/.test(sha)) list.push('previous_source_sha256 必须是 64 位小写十六进制。')
  if (Number.isInteger(n) && n === 0 && sha)
    list.push('previous_version=0（首次登记）时 previous_source_sha256 必须留空（服务端要求显式 null）。')
  if (Number.isInteger(n) && n > 0 && !sha)
    list.push('previous_version>0 时必须填上一版本的 previous_source_sha256（否则 409 CAD_VERSION_CHANGED）。')
  return list
})

const blockedReasons = computed<string[]>(() => {
  const list = [...errors.value]
  if (props.anonymous) list.push('当前匿名：登记写操作会 401 LOGIN_REQUIRED。')
  else if (props.identityLoaded && !props.canRegister)
    list.push(`登记需要 ADMIN 角色；当前角色：${props.rolesText || '无'}。`)
  if (props.sourcesError) list.push('暂存源文件列表读取失败：拿不到可信的 source_sha256 就不该登记。')
  return list
})

const canSubmit = computed(() => blockedReasons.value.length === 0 && !props.submitting)

function submit(): void {
  if (!canSubmit.value) return
  const entry = currentSource.value
  if (!entry) return
  const provenance: DrawingRegistrationInput['provenance'] =
    entry.provenance === 'authorized_external' ? 'authorized_external' : 'synthetic_demo'
  const sha = prevSha.value.trim()
  // 内容签名：内容一致 → 复用同一个 request_id（幂等重试）；内容改了 → 换新 id。
  const signature = JSON.stringify({
    document_id: docId.value.trim(),
    title: title.value.trim(),
    source_path: entry.source_path,
    source_sha256: entry.source_sha256,
    provenance,
    previous_version: Number(prevVersion.value),
    previous_source_sha256: sha || null,
  })
  const body: DrawingRegistrationInput = {
    request_id: stableRequestId('cadreg', signature),
    document_id: docId.value.trim(),
    title: title.value.trim(),
    source_path: entry.source_path,
    source_sha256: entry.source_sha256,
    provenance,
    previous_version: Number(prevVersion.value),
    previous_source_sha256: sha || null,
  }
  emit('submit', body)
}
</script>

<template>
  <section class="cad-section" data-testid="cad-register">
    <h2 class="cad-sec-title">登记图纸版本（管理员）</h2>

    <p class="cad-hint cad-honesty" data-testid="cad-honesty-register">
      登记不代表审批或生产质量结论。
    </p>
    <p class="cad-hint" data-testid="cad-provenance-note">
      provenance 是登记人的自述（界面上标成「（登记声明）」），服务端不验证其真实性。
    </p>
    <p class="cad-hint" data-testid="cad-idempotent-note">
      幂等重试路径：**重发同一个 request_id、且内容完全一致**会返回既有版本，不会重复登记；
      内容或操作者一旦不同就会 409 <code>CAD_REQUEST_ID_REUSED</code>。所以本页对同一份内容复用同一个
      request_id，内容一改就换新 id。
    </p>
    <p class="cad-hint" data-testid="cad-parser-note">
      本读取器 <code>dxf-native-v1</code> **要求源文件带 handle**：没有 handle 的 DXF 会直接 422 失败。
      本页不提供、也不承诺「部分解析」或「按位置对齐」的降级结果。
    </p>

    <p v-if="!identityLoaded" class="cad-hint">正在读取身份…</p>
    <p v-else-if="!canRegister" class="cad-hint cad-warnline" data-testid="cad-register-denied">
      当前身份不能登记：本动作需要 ADMIN 角色（当前：{{ anonymous ? '匿名' : rolesText || '—' }}）。
      表单仍然显示，但提交入口是关闭的 —— 不假装能写。
    </p>

    <CadErrorNote :error="sourcesError" testid="cad-sources-error" />
    <p v-if="sourcesLoading" class="cad-hint">正在读取暂存源文件…</p>
    <p
      v-else-if="!sourcesError && !sources.length"
      class="cad-hint"
      data-testid="cad-sources-empty"
    >
      白名单暂存目录里没有可登记的 .dxf（只列 data/cad_demo/development 与 data/cad_demo/authorized）。
      请先把文件放进这两个目录，再刷新。
    </p>

    <form class="cad-form" data-testid="cad-register-form" @submit.prevent="submit">
      <label class="cad-field-block">
        <span>暂存源文件（.dxf，选中后自动带出 source_sha256 与 provenance）</span>
        <select v-model="sourcePath" data-testid="cad-source-select">
          <option value="">— 请选择 —</option>
          <option v-for="s in sources" :key="s.source_path" :value="s.source_path">
            {{ s.name }} · {{ provenanceText(s.provenance) }} · {{ shortHash(s.source_sha256, 12) }}
          </option>
        </select>
      </label>
      <p v-if="currentSource" class="cad-hint" data-testid="cad-source-detail">
        source_path <code>{{ currentSource.source_path }}</code>
        · source_sha256 <code>{{ currentSource.source_sha256 }}</code>
        · provenance {{ provenanceText(currentSource.provenance) }}
      </p>

      <label class="cad-field-block">
        <span>document_id（新资产自定；登记新版本必须与既有 document_id 完全一致）</span>
        <input v-model="docId" type="text" maxlength="64" data-testid="cad-register-docid" />
      </label>

      <label class="cad-field-block">
        <span>title（≤200 字）</span>
        <input v-model="title" type="text" maxlength="200" data-testid="cad-register-title" />
      </label>

      <div class="cad-two">
        <label class="cad-field-block">
          <span>previous_version（首次登记 = 0）</span>
          <input v-model="prevVersion" type="number" min="0" step="1" data-testid="cad-register-prev-version" />
        </label>
        <label class="cad-field-block">
          <span>previous_source_sha256（首次登记留空 = null）</span>
          <input v-model="prevSha" type="text" data-testid="cad-register-prev-sha" />
        </label>
      </div>
      <p class="cad-hint">
        链尾必须对齐：第一次登记 previous_version=0 且 previous_source_sha256 为空；
        已有版本的资产要填 v{{ chainVersion ?? '—' }} 与
        <code>{{ shortHash(chainSourceSha256, 16) }}</code>。对不上会 409
        <code>CAD_VERSION_CHANGED</code>，重新加载后再试即可。
      </p>
      <p
        v-if="defaultDocumentId && docId.trim() && docId.trim() !== defaultDocumentId"
        class="cad-warnline"
        data-testid="cad-register-docid-warning"
      >
        你改动了 document_id（当前选中资产的 id 是 <code>{{ defaultDocumentId }}</code>）：
        如果这是一份**全新资产**，previous_version 必须是 0 且 previous_source_sha256 留空；
        如果这份资产**已经存在**，必须填它自己的当前版本号与当前 source_sha256 ——
        本页只知道当前选中资产的链尾，不会替另一份资产猜链尾。填错会 409 CAD_VERSION_CHANGED。
      </p>

      <CadErrorNote :error="submitError" testid="cad-register-error" />
      <p v-if="submitNotice" class="cad-ok" data-testid="cad-register-notice">{{ submitNotice }}</p>

      <div v-if="blockedReasons.length" class="cad-blocked" data-testid="cad-register-blocked">
        <p>现在不能提交，原因如下（不做假提交）：</p>
        <ul>
          <li v-for="(b, i) in blockedReasons" :key="i">{{ b }}</li>
        </ul>
      </div>

      <button class="cad-submit" type="submit" :disabled="!canSubmit" data-testid="cad-register-submit">
        {{ submitting ? '登记中…' : '登记为新版本' }}
      </button>
    </form>
  </section>
</template>

<style scoped>
.cad-section {
  margin: 0 0 22px;
  padding: 14px 16px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
}
.cad-sec-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.cad-hint {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--color-text);
}
.cad-honesty {
  color: #b45309;
}
.cad-warnline {
  color: #b45309;
}
.cad-hint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: var(--color-ink);
  border-radius: 5px;
  padding: 1px 5px;
  color: var(--color-text);
}
.cad-form {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  padding: 12px 14px;
  background: var(--color-ink);
  margin-top: 8px;
}
.cad-field-block {
  display: block;
  margin-bottom: 12px;
}
.cad-field-block span {
  display: block;
  font-size: 13px;
  color: var(--color-muted);
  margin-bottom: 4px;
}
.cad-field-block input,
.cad-field-block select {
  width: 100%;
  font: inherit;
  font-size: 14px;
  padding: 7px 10px;
  border: 1px solid var(--color-line);
  border-radius: 8px;
  background: var(--color-panel);
  color: var(--color-text);
  box-sizing: border-box;
}
.cad-two {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}
.cad-blocked {
  margin: 0 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(217, 119, 6, 0.35);
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.07);
  font-size: 13.5px;
  color: #b45309;
}
.cad-blocked p {
  margin: 0 0 4px;
}
.cad-blocked ul {
  margin: 0;
  padding-left: 18px;
  line-height: 1.7;
}
/* 提交按钮本来就是蓝底，补齐加粗以与「模型接入」页的按钮等价 */
.cad-submit {
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 8px 20px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #fff;
  cursor: pointer;
}
.cad-submit:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.cad-submit:disabled {
  opacity: 0.5;
  cursor: default;
  background: var(--color-panel);
  color: var(--color-muted);
  border-color: var(--color-line);
}
.cad-ok {
  margin: 0 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(22, 163, 74, 0.35);
  border-radius: 12px;
  background: rgba(22, 163, 74, 0.07);
  font-size: 13.5px;
  color: #15803d;
  line-height: 1.7;
}
</style>
