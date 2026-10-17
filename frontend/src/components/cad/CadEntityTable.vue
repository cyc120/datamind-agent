<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CadEntity, CadView } from '../../api/types'
import CadErrorNote from './CadErrorNote.vue'
import type { CadErrorView } from './cadCopy'

/**
 * 图元与视图明细：本地解析出的原始实体列表。
 *
 * WHY 一定要显示 handle/layer/view_id：要素的定位器（CadLocator）就是靠
 * 「source_sha256 + handle + layer + view_id + coordinate_system + layout」六项
 * 联合定位的；少一项就是过期定位。这张表是让复核人核对定位器是否真的对得上原文，
 * 而不是用来证明图纸已获批准。
 */
const props = defineProps<{
  entities: CadEntity[]
  views: CadView[]
  loading: boolean
  error: CadErrorView | null
}>()

const expanded = ref(false)
/** 默认只渲染前 80 行：一份 DXF 可能有上千个图元，整屏渲染会让页面卡住。 */
const VISIBLE_LIMIT = 80

const shown = computed(() => (expanded.value ? props.entities : props.entities.slice(0, VISIBLE_LIMIT)))
const hiddenCount = computed(() => Math.max(0, props.entities.length - shown.value.length))

function pointText(p?: { x: number; y: number } | null): string {
  if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') return '—'
  return `(${round(p.x)}, ${round(p.y)})`
}

function round(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 1000) / 1000)
}

/** 按 entity_type 读各自几何字段：LINE/CIRCLE/TEXT 之外的类型不做猜测。 */
function geometryText(e: CadEntity): string {
  if (e.entity_type === 'LINE') return `${pointText(e.start)} → ${pointText(e.end)}`
  if (e.entity_type === 'CIRCLE')
    return `圆心 ${pointText(e.center)} r=${typeof e.radius === 'number' ? round(e.radius) : '—'}`
  if (e.entity_type === 'TEXT')
    return `“${typeof e.text === 'string' ? e.text : '—'}” @ ${pointText(e.insertion)}`
  return '（本页未识别的图元类型，几何字段不做猜测）'
}

function viewBBox(viewId: string): string {
  const v = props.views.find((item) => item.view_id === viewId)
  const b = v?.bbox
  if (!b) return '—'
  return `x ${round(b.xmin)}…${round(b.xmax)} / y ${round(b.ymin)}…${round(b.ymax)}`
}
</script>

<template>
  <section class="cad-section" data-testid="cad-entities">
    <h2 class="cad-sec-title">
      图元明细<span class="cad-sec-tag">{{ entities.length }} 个图元 / {{ views.length }} 个视图</span>
    </h2>
    <p class="cad-hint">
      本地解析的原文证据。要素定位器只有
      <code>source_sha256 + handle + layer + view_id + coordinate_system + layout</code>
      六项全部一致才算有效；这张表用来核对定位器，不用来证明图纸获得审批或生产资格。
    </p>

    <CadErrorNote :error="error" testid="cad-entities-error" />
    <p v-if="loading" class="cad-hint">正在读取图元…</p>
    <p v-else-if="!error && !entities.length" class="cad-hint" data-testid="cad-entities-empty">
      本版本没有任何可显示的图元。
    </p>

    <template v-else-if="entities.length">
      <ul class="cad-views">
        <li v-for="v in views" :key="v.view_id">
          <code>{{ v.view_id }}</code> 边界 {{ viewBBox(v.view_id) }}
        </li>
      </ul>
      <div class="cad-tablewrap">
        <table class="cad-table">
          <thead>
            <tr>
              <th>handle</th>
              <th>类型</th>
              <th>图层</th>
              <th>视图</th>
              <th>几何</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in shown" :key="e.handle" data-testid="cad-entity-row">
              <td><code>{{ e.handle }}</code></td>
              <td>{{ e.entity_type }}</td>
              <td>{{ e.layer }}</td>
              <td>{{ e.view_id }}</td>
              <td class="cad-geo">{{ geometryText(e) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <button
        v-if="hiddenCount > 0"
        class="cad-more"
        type="button"
        data-testid="cad-entities-more"
        @click="expanded = true"
      >
        还有 {{ hiddenCount }} 个图元未显示 —— 点此全部展开
      </button>
    </template>
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
  display: flex;
  align-items: baseline;
  gap: 10px;
}
/* 节标题旁的计数标签：蓝底白字加粗椭圆（原来是灰字描边） */
.cad-sec-tag {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-accent);
  padding: 2px 9px;
}
.cad-hint {
  margin: 0 0 8px;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.cad-hint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: var(--color-ink);
  border-radius: 5px;
  padding: 1px 5px;
}
.cad-views {
  list-style: none;
  margin: 0 0 10px;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 13.5px;
  color: var(--color-text);
}
.cad-views code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--color-text);
}
.cad-tablewrap {
  overflow-x: auto;
  border: 1px solid var(--color-line);
  border-radius: 12px;
}
.cad-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 14px;
  min-width: 640px;
}
.cad-table th,
.cad-table td {
  text-align: left;
  padding: 7px 10px;
  border-bottom: 1px solid var(--color-line);
  white-space: nowrap;
  color: var(--color-body);
}
.cad-table th {
  color: var(--color-muted);
  font-weight: 500;
  background: var(--color-ink);
}
.cad-table tr:last-child td {
  border-bottom: 0;
}
.cad-table code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  color: var(--color-text);
}
.cad-geo {
  white-space: normal;
}
/* 「加载更多」：与其它按钮统一成蓝底白字加粗椭圆（原来是白底细描边） */
.cad-more {
  display: block;
  margin: 12px auto 0;
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
.cad-more:hover {
  background: #1d4ed8;
  color: #ffffff;
}
</style>
