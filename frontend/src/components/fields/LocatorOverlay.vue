<script setup lang="ts">
/**
 * 「定位原页」浮层：把一条证据引用的 bbox 画在它声称的那一页上。
 *
 * 唯一允许的两种结果（见 ./locator.ts 的判定）：
 *   ① 判定通过 → 用 PDF 点坐标系直接铺一层 SVG（viewBox="0 0 宽 高"），
 *      坐标不做任何换算，因此不存在「大概在这个位置」的可能；
 *   ② 任一条必要条件不满足 → 该引用**不画**，只显示 `无法绘制：<原因>`。
 * 绝不为了「看起来有定位」而回落到估算位置：那会把猜出来的区域伪装成证据。
 */

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { pageImageUrl } from '../../api/client'
import {
  locatorRefusalLabel,
  locatorVerdict,
  pageForRefs,
  refStrokeColor,
  type EvidenceRefLike,
} from './locator'

const props = defineProps<{
  documentId: string
  /** 该事实的全部证据引用 */
  refs: EvidenceRefLike[]
  /** 用户点击的那一条（浮层的焦点） */
  focusIndex: number
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

/** 有可信页码才显示页面图；全部引用页码无效时连图都不显示。 */
const pageNo = computed<number | null>(() => pageForRefs(props.refs, props.focusIndex))
const focusRef = computed<EvidenceRefLike | null>(() => props.refs?.[props.focusIndex] ?? null)
const focusVerdict = computed(() => locatorVerdict(focusRef.value))

interface PlannedRef {
  index: number
  ref: EvidenceRefLike
  verdict: ReturnType<typeof locatorVerdict>
}

const planned = computed<PlannedRef[]>(() =>
  (props.refs ?? []).map((ref, index) => ({ index, ref, verdict: locatorVerdict(ref) })),
)

/** 本页可绘制：进 SVG。 */
const drawnRefs = computed(() =>
  planned.value.filter(
    (p) => p.verdict.drawable && pageNo.value !== null && p.verdict.pageNo === pageNo.value,
  ),
)

/** 判定失败：只出标签。 */
const refusedRefs = computed(() => planned.value.filter((p) => !p.verdict.drawable))

/** 判定通过但不在本页：不画（画了就等于换了一页去猜位置），只说明它在第几页。 */
const offPageRefs = computed(() =>
  planned.value.filter(
    (p) => p.verdict.drawable && pageNo.value !== null && p.verdict.pageNo !== pageNo.value,
  ),
)

/** SVG 用户坐标系 = 该页的 PDF 点尺寸，与 bbox 同系，因此无需缩放推断。 */
const viewBox = computed(() => {
  const drawn = drawnRefs.value[0]
  if (drawn && drawn.verdict.drawable) return `0 0 ${drawn.verdict.pageSize[0]} ${drawn.verdict.pageSize[1]}`
  if (focusVerdict.value.drawable) {
    return `0 0 ${focusVerdict.value.pageSize[0]} ${focusVerdict.value.pageSize[1]}`
  }
  return ''
})

const imageUrl = computed(() =>
  pageNo.value === null ? '' : pageImageUrl(props.documentId, pageNo.value),
)

/**
 * 页面图加载失败（例如 SOURCE_PAGE_UNAVAILABLE / 图片未渲染）必须显式说出，
 * 不能留一片空白让用户以为「这一页本来就是空的」——那等于伪造了「没有证据」。
 */
const imageError = ref(false)
watch(imageUrl, () => {
  imageError.value = false
})

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div
    class="fields-overlay"
    data-testid="fields-locator-overlay"
    role="dialog"
    aria-modal="true"
    aria-label="定位原页"
    @click.self="emit('close')"
  >
    <div class="fo-panel">
      <header class="fo-head">
        <span class="fo-title">定位原页</span>
        <span v-if="pageNo !== null" class="fo-page">第 {{ pageNo }} 页</span>
        <span v-else class="fo-page is-bad">没有可信页码</span>
        <button class="fo-close" type="button" data-testid="fields-locator-close" @click="emit('close')">
          关闭
        </button>
      </header>

      <!-- 焦点引用的判定结论：通过才画，否则只出标签 -->
      <p class="fo-focus">
        <span class="fo-focus-role" :style="{ color: refStrokeColor(focusRef?.role) }">
          {{ focusRef?.role || '未声明角色' }}
        </span>
        <span v-if="focusRef?.field" class="fo-focus-field">{{ focusRef.field }}</span>
        <span v-if="focusRef?.field_value" class="fo-focus-value">= {{ focusRef.field_value }}</span>
        <span v-if="focusVerdict.drawable" class="fo-ok" data-testid="fields-locator-drawable">
          可绘制
        </span>
        <span v-else class="fo-bad" data-testid="fields-locator-refusal">
          {{ locatorRefusalLabel(focusVerdict.reason) }}
        </span>
      </p>

      <div class="fo-canvas">
        <p v-if="pageNo === null" class="fo-empty" data-testid="fields-locator-no-page">
          没有任何一条引用给出可信页码，因此不显示页面图，也不绘制任何框。
        </p>

        <template v-else>
          <p v-if="imageError" class="fo-error" data-testid="fields-locator-image-error">
            第 {{ pageNo }} 页的页面图加载失败（后端可能返回 SOURCE_PAGE_UNAVAILABLE）。
            图上没有画任何框——定位未获证明，不代表本节没有证据。
          </p>
          <div v-else class="fo-frame">
            <img class="fo-img" :src="imageUrl" :alt="`第 ${pageNo} 页`" @error="imageError = true" />
            <!-- 只有判定通过的引用才会出现在这个 SVG 里 -->
            <svg
              v-if="drawnRefs.length"
              class="fo-svg"
              :viewBox="viewBox"
              preserveAspectRatio="none"
              data-testid="fields-locator-svg"
            >
              <rect
                v-for="p in drawnRefs"
                :key="`rect-${p.index}`"
                :x="p.verdict.drawable ? p.verdict.bbox[0] : 0"
                :y="p.verdict.drawable ? p.verdict.bbox[1] : 0"
                :width="p.verdict.drawable ? p.verdict.bbox[2] - p.verdict.bbox[0] : 0"
                :height="p.verdict.drawable ? p.verdict.bbox[3] - p.verdict.bbox[1] : 0"
                fill="none"
                :stroke="refStrokeColor(p.ref.role)"
                :stroke-width="p.index === focusIndex ? 1.6 : 0.9"
                :stroke-dasharray="p.index === focusIndex ? '' : '4 3'"
                vector-effect="non-scaling-stroke"
                :data-testid="`fields-locator-rect-${p.index}`"
              >
                <title>
                  {{ p.ref.role || '未声明角色' }} · {{ p.ref.field || '' }} {{ p.ref.field_value || '' }}
                </title>
              </rect>
            </svg>
          </div>
        </template>
      </div>

      <ul v-if="drawnRefs.length" class="fo-legend" data-testid="fields-locator-legend">
        <li v-for="p in drawnRefs" :key="`legend-${p.index}`" class="fo-legend-item">
          <span class="fo-swatch" :style="{ background: refStrokeColor(p.ref.role) }"></span>
          <span class="fo-legend-text">
            {{ p.ref.role || '未声明角色' }} · {{ p.ref.field || '—' }}
            <template v-if="p.ref.field_value"> = {{ p.ref.field_value }}</template>
          </span>
          <span v-if="p.index === focusIndex" class="fo-legend-focus">焦点</span>
        </li>
      </ul>

      <ul v-if="refusedRefs.length" class="fo-refused" data-testid="fields-locator-refused">
        <li v-for="p in refusedRefs" :key="`refused-${p.index}`" class="fo-refused-item">
          <span class="fo-refused-role">{{ p.ref.role || '未声明角色' }}</span>
          <span class="fo-refused-text">
            {{ locatorRefusalLabel(p.verdict.drawable ? '' : p.verdict.reason) }}
          </span>
        </li>
      </ul>

      <ul v-if="offPageRefs.length" class="fo-refused" data-testid="fields-locator-offpage">
        <li v-for="p in offPageRefs" :key="`off-${p.index}`" class="fo-refused-item">
          <span class="fo-refused-role">{{ p.ref.role || '未声明角色' }}</span>
          <span class="fo-refused-text">
            第 {{ p.verdict.drawable ? p.verdict.pageNo : '?' }} 页（不在本页，未在本图上绘制）
          </span>
        </li>
      </ul>

      <p class="fo-note">
        框选区域来自证据引用自带的 bbox（坐标系 pdf_points），未做任何位置推断；
        没有框就是「定位未被证明」，不等于这段原文不存在。
      </p>
    </div>
  </div>
</template>

<style scoped>
/* 类名统一 fields-/fo- 前缀：Tailwind v4 的 @layer utilities 会生成同名工具类，
   撞上会出现「只生成了部分属性」的怪象（仓库里已出过两次事故）。 */
.fields-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.45);
}
.fo-panel {
  width: min(880px, 100%);
  max-height: 88vh;
  overflow-y: auto;
  background: #ffffff;
  border: 1px solid var(--color-line);
  border-radius: 14px;
  padding: 16px 18px 20px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
}
.fo-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}
.fo-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
.fo-page {
  font-size: 12px;
  color: var(--color-muted);
}
.fo-page.is-bad {
  color: #d97706;
}
.fo-close {
  margin-left: auto;
  font: inherit;
  font-size: 12px;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.fo-close:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.fo-focus {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 12.5px;
  color: var(--color-body);
}
.fo-focus-role {
  font-weight: 600;
}
.fo-focus-field {
  color: var(--color-muted);
}
.fo-focus-value {
  color: var(--color-text);
}
.fo-ok {
  font-size: 11.5px;
  color: #16a34a;
  border: 1px solid rgba(22, 163, 74, 0.35);
  background: rgba(22, 163, 74, 0.08);
  border-radius: 999px;
  padding: 2px 8px;
}
.fo-bad {
  font-size: 11.5px;
  color: #d97706;
  border: 1px solid rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
  border-radius: 999px;
  padding: 2px 8px;
}
.fo-canvas {
  border: 1px solid var(--color-line);
  border-radius: 10px;
  background: var(--color-ink);
  padding: 8px;
}
.fo-frame {
  position: relative;
  line-height: 0;
}
.fo-img {
  display: block;
  width: 100%;
  height: auto;
  border-radius: 4px;
}
/* SVG 覆盖层与图片同尺寸：viewBox 用 PDF 点，preserveAspectRatio=none 让点坐标
   线性映射到显示尺寸，无需也不允许任何手工缩放。 */
.fo-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.fo-empty,
.fo-error {
  margin: 0;
  padding: 14px 12px;
  font-size: 12.5px;
  line-height: 1.7;
}
.fo-empty {
  color: var(--color-muted);
}
.fo-error {
  color: #e11d48;
}
.fo-legend,
.fo-refused {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fo-legend-item,
.fo-refused-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: var(--color-body);
}
.fo-swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}
.fo-legend-text {
  min-width: 0;
}
.fo-legend-focus {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--color-muted);
  border: 1px solid var(--color-line);
  border-radius: 999px;
  padding: 1px 6px;
}
.fo-refused-role {
  flex-shrink: 0;
  width: 76px;
  color: var(--color-muted);
}
.fo-refused-text {
  color: #d97706;
  line-height: 1.6;
}
.fo-note {
  margin: 12px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-muted);
}
</style>
