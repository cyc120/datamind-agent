<script setup lang="ts">
import { ref } from 'vue'

import type { UploadProvenance } from '../../api/types'
import FilesUploadRow from './FilesUploadRow.vue'
import {
  ACCEPT_ATTR,
  ALLOWED_SUFFIXES,
  DWG_LOCAL_MESSAGE,
  MAX_BATCH_FILES,
  MAX_CONCURRENT_UPLOADS,
  MIB,
  PROVENANCE_OPTIONS,
  SIZE_LIMITS,
  type FilesRowVM,
} from './uploadHonesty'

/**
 * 上传面板（纯展示 + 一个文件选择输入）。
 *
 * 上传方式是本屏最容易做错的地方，所以把事实写在界面上而不是注释里：
 *   唯一入口是 `POST /v1/uploads?filename=&provenance=`，**File 本身作为请求体**、
 *   `Content-Type: application/octet-stream` —— **不是** multipart / FormData；
 *   `/v1/files/uploads` + `/v1/files/jobs` 是退役的两步家族，本页一次都不调用。
 *
 * 预检（后缀/大小/批量上限/.dwg）在页面里做，这里只展示结果行 —— 被拒的文件**必须**
 * 逐条显示原因，绝不静默丢弃。
 */
const props = defineProps<{
  provenance: UploadProvenance
  /** 本批仍在跑（上传或轮询） */
  running: boolean
  /** 本批的展示行（含被本地预检拒绝的） */
  rows: FilesRowVM[]
  /** 本批的一句话小结（空串 = 没有本批） */
  summary: string
}>()

const emit = defineEmits<{
  (e: 'update:provenance', value: UploadProvenance): void
  (e: 'pick', files: File[]): void
  (e: 'toggle', row: FilesRowVM): void
  (e: 'approve', row: FilesRowVM): void
}>()

const inputEl = ref<HTMLInputElement | null>(null)

function openPicker(): void {
  inputEl.value?.click()
}

function onPick(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  // 立刻清空 value：否则连续选择同一个文件不会再触发 change。
  input.value = ''
  if (files.length) emit('pick', files)
}

function onProvenance(event: Event): void {
  emit('update:provenance', (event.target as HTMLSelectElement).value as UploadProvenance)
}
</script>

<template>
  <section class="fup-panel" data-testid="files-upload-panel">
    <div class="fup-head">
      <h2 class="fup-h2">上传文件</h2>
      <button
        class="fup-pick"
        type="button"
        data-testid="files-upload-pick"
        :disabled="running"
        @click="openPicker"
      >
        {{ running ? '本批处理中…' : '选择文件' }}
      </button>
      <input
        ref="inputEl"
        class="fup-input"
        type="file"
        multiple
        :accept="ACCEPT_ATTR"
        data-testid="files-upload-input"
        @change="onPick"
      />
    </div>

    <div class="fup-controls">
      <label class="fup-label" for="files-provenance">来源（provenance）</label>
      <select
        id="files-provenance"
        class="fup-select"
        data-testid="files-provenance"
        :value="props.provenance"
        @change="onProvenance"
      >
        <option v-for="opt in PROVENANCE_OPTIONS" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
      <span class="fup-hint">
        {{
          PROVENANCE_OPTIONS.find((o) => o.value === props.provenance)?.hint ||
          '来源由你选择，服务端按所选值登记'
        }}
      </span>
    </div>

    <!-- 上传方式与限制：这些数字与服务端 UPLOAD_LIMITS 一一对应；本地预检失败会逐条列出 -->
    <ul class="fup-facts">
      <li data-testid="files-raw-note">
        上传方式是<strong>原始字节</strong>：File 直接作为请求体，
        <code>Content-Type: application/octet-stream</code>，查询参数带
        <code>filename</code>/<code>provenance</code> —— 不是 multipart，也不使用已退役的
        <code>/v1/files/uploads</code>+<code>/v1/files/jobs</code> 两步家族。
      </li>
      <li data-testid="files-limits-note">
        接受 {{ ALLOWED_SUFFIXES.join(' ') }}；上限：
        <code v-for="(limit, suffix) in SIZE_LIMITS" :key="suffix" class="fup-chip">
          {{ suffix }} ≤ {{ limit / MIB }} MiB
        </code>
      </li>
      <li>
        每批最多 {{ MAX_BATCH_FILES }} 个文件，上传并发 {{ MAX_CONCURRENT_UPLOADS }}；
        超出部分与不合规文件会在下面逐条说明。
      </li>
      <li data-testid="files-dwg-note">
        <code>.dwg</code> {{ DWG_LOCAL_MESSAGE }}（本地直接拒绝；服务端同样以 415
        CAD_DWG_CONVERTER_UNAVAILABLE 拒绝）。
      </li>
    </ul>

    <p v-if="summary" class="fup-summary" data-testid="files-batch-summary">{{ summary }}</p>

    <ul v-if="rows.length" class="fup-rows" data-testid="files-batch">
      <FilesUploadRow
        v-for="row in rows"
        :key="row.key"
        :row="row"
        @toggle="emit('toggle', $event)"
        @approve="emit('approve', $event)"
      />
    </ul>
  </section>
</template>

<style scoped>
/* 类名统一 fup- 前缀，避免与 Tailwind v4 工具类重名（仓库已出过两次事故）。 */
.fup-panel {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px 16px;
  margin-bottom: 20px;
}
.fup-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.fup-h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.fup-pick {
  margin-left: auto;
  font: inherit;
  font-size: 14px;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid rgba(37, 99, 235, 0.4);
  background: rgba(37, 99, 235, 0.08);
  color: var(--color-accent);
  cursor: pointer;
}
.fup-pick:hover:not(:disabled) {
  background: rgba(37, 99, 235, 0.14);
}
.fup-pick:disabled {
  opacity: 0.6;
  cursor: default;
}
.fup-input {
  display: none;
}
.fup-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.fup-label {
  font-size: 13.5px;
  color: var(--color-muted);
}
.fup-select {
  font: inherit;
  font-size: 14px;
  padding: 5px 8px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-text);
  max-width: 340px;
}
.fup-hint {
  font-size: 13px;
  color: var(--color-muted);
}
.fup-facts {
  margin: 10px 0 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fup-facts li {
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
}
.fup-facts code {
  font-size: 12.5px;
  color: var(--color-muted);
}
.fup-chip {
  display: inline-block;
  margin: 0 4px 2px 0;
  padding: 1px 6px;
  border: 1px solid var(--color-line);
  border-radius: 6px;
  background: var(--color-ink);
}
.fup-summary {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-body);
}
.fup-rows {
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
