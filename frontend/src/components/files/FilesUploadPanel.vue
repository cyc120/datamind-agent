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
      <label class="fup-label" for="files-provenance">来源类别</label>
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
          '来源由你选择，服务端按所选的值登记'
        }}
      </span>
    </div>

    <!-- 上传方式与限制：这些数字与服务端 UPLOAD_LIMITS 一一对应；本地预检失败会逐条列出 -->
    <ul class="fup-facts">
      <li data-testid="files-raw-note">
        上传的是<b>文件原始内容</b>本身，不是表单打包，也不经过任何中转格式。
      </li>
      <li data-testid="files-limits-note">接受这些后缀：{{ ALLOWED_SUFFIXES.join(' ') }}</li>
      <!-- 体积上限原来跟上一句挤在同一行、又用蓝色药丸渲染，把一句话撕成两行还满屏蓝块。
           改成独立一条、纯文本排列：一句一个信息点。 -->
      <li>
        各自的体积上限：
        <span v-for="(limit, suffix) in SIZE_LIMITS" :key="suffix" class="fup-limit">
          {{ suffix }} {{ limit / MIB }} MiB
        </span>
      </li>
      <li>
        每批最多 {{ MAX_BATCH_FILES }} 个文件，同时最多上传 {{ MAX_CONCURRENT_UPLOADS }} 个；
        超出部分与不合规文件会在下面逐条说明。
      </li>
      <li data-testid="files-dwg-note">
        <code>.dwg</code> {{ DWG_LOCAL_MESSAGE }}：本页直接拒绝，服务端同样会以 415 拒绝。
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
/* 面板标题与其它页统一：蓝色、15px、600 */
.fup-h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
/* 「选择文件」统一成实心蓝（原来是浅蓝底蓝字，同页其它按钮已是实心蓝） */
.fup-pick {
  margin-left: auto;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 6px 16px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.fup-pick:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
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
  color: var(--color-text);
}
/* 蓝点列表：原来只有 padding-left，没有 list-style:none + ::before 圆点，所以看起来是「缩进的裸文字」，与站内其它蓝点列表不一致。 */
.fup-facts {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.fup-facts > li {
  position: relative;
  padding-left: 14px;
}
.fup-facts > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.66em;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-accent);
}
.fup-facts li {
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text);
}
.fup-facts code {
  font-size: 12.5px;
  color: var(--color-text);
}
/* 体积上限用纯文本：原来每个上限都是一颗蓝色药丸，一句里塞 6 颗，
   把句子撕成两行、还满屏蓝块。改成等宽小字 + 间隔点分隔。 */
.fup-limit {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  white-space: nowrap;
}
.fup-limit + .fup-limit::before {
  content: '  ·  ';
  font-family: inherit;
  color: var(--color-body);
}
/* 扩展名标签统一蓝底白字加粗椭圆（原来是 6px 圆角的灰底描边） */
.fup-chip {
  display: inline-block;
  margin: 0 4px 2px 0;
  padding: 1px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  color: #ffffff;
  font-weight: 600;
}

/* 这一句是必须的，不是冗余：上面 `.fup-facts code`（0,1,1）比 `.fup-chip`（0,1,0）更具体，
   会把蓝底上的文字重新压成灰色 —— 实测对比度极差（灰字压蓝底）。这里用更具体的选择器压回去。 */
.fup-facts code.fup-chip {
  color: #ffffff;
  background: var(--color-accent);
  border-color: transparent;
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
