<script setup lang="ts">
import type { CadFeatureGate, DrawingFeature } from '../../api/types'
import CadErrorNote from './CadErrorNote.vue'
import CadGateSummary from './CadGateSummary.vue'
import { gateReasonText, machineStatusText, orDash } from './cadCopy'
import type { CadErrorView } from './cadCopy'

/**
 * 「机器提取（原文/几何）」—— 本区只呈现**抽取器**的原始结果与它自己的门槛。
 *
 * ★ 本屏最重要的区分 ★
 * `features[].association_status` 是机器在解析时的判定，落库即**冻结**：人工复核
 * **不会**改写它（复核只往 association_reviews / review_gate 写）。所以这张表把
 * 关联状态标成「机器判定（冻结）」，把 review_gate 的结论单独放一列并注明来源。
 * 一旦把 association_status 渲染成人工结论，就是在篡改「谁做的决定」。
 */
const props = defineProps<{
  features: DrawingFeature[]
  inspectionGate: CadFeatureGate | null
  /** 人工复核叠加后的门槛：本表只引用它的**结论**，与机器状态分列显示 */
  reviewGate: CadFeatureGate | null
  selectedFeatureId: string | null
  loading: boolean
  error: CadErrorView | null
  versionLabel: string
}>()

const emit = defineEmits<{ (e: 'select-feature', featureId: string): void }>()

/** review_gate 对某条要素的结论：在 features[] 里 = 可用；在 excluded[] 里 = 连同原因。 */
function outcome(featureId: string): { kind: 'eligible' | 'excluded' | 'absent'; reason: string } {
  const gate = props.reviewGate
  if (!gate) return { kind: 'absent', reason: '' }
  if (gate.features.some((f) => f.feature_id === featureId)) return { kind: 'eligible', reason: '' }
  const excluded = gate.excluded.find((f) => f.feature_id === featureId)
  if (excluded) return { kind: 'excluded', reason: excluded.reason }
  // 既不在可用、也不在排除：服务端没有对它表态，**不能**当成可用。
  return { kind: 'absent', reason: '' }
}
</script>

<template>
  <section class="cad-section" data-testid="cad-machine-extraction">
    <h2 class="cad-sec-title">
      机器提取（原文/几何）<span class="cad-sec-tag">{{ versionLabel }}</span>
    </h2>

    <p class="cad-hint cad-emph" data-testid="cad-machine-note">
      「机器关联状态」是抽取器在解析原文时给出的判定（confirmed / ambiguous / unresolved），
      <b>落库即冻结</b>：人工复核**不会**改写它，`inspection_gate` 也不会因为复核而变化。
      人工做出的结论只出现在下方「人工关联复核」区的 `review_gate` 与 `association_reviews`。
    </p>
    <p class="cad-hint">
      门槛冲突（如 <code>CAD_GEOMETRY_ANNOTATION_CONFLICT</code>）来自原文本身；本页唯一能做的
      人工动作是关联复核，它**消除不了**这类冲突，也不能把它「批准掉」。
    </p>

    <CadGateSummary
      :gate="inspectionGate"
      caption="原样抽取 / 对象一致性门槛（inspection_gate）"
      testid="cad-inspection-gate"
    />

    <CadErrorNote :error="error" testid="cad-features-error" />
    <p v-if="loading" class="cad-hint">正在读取要素…</p>
    <p v-else-if="!error && !features.length" class="cad-hint" data-testid="cad-features-empty">
      本版本没有解析出任何尺寸要素：没有可复核的对象，也没有可用的门槛条目。
    </p>
    <p v-else-if="!error && !reviewGate" class="cad-hint">复核门槛尚未加载：下方「使用门槛」列在拿到数据前一律显示为未表态。</p>

    <div v-if="features.length" class="cad-tablewrap">
      <table class="cad-table">
        <thead>
          <tr>
            <th>标注原文</th>
            <th>单位</th>
            <th>名义值</th>
            <th>下偏差</th>
            <th>上偏差</th>
            <th>候选目标圆</th>
            <th>机器选定目标</th>
            <th>机器关联状态（冻结）</th>
            <th>使用门槛（review_gate）</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="f in features"
            :key="f.feature_id"
            data-testid="cad-feature-row"
            :class="{ 'cad-is-selected': selectedFeatureId === f.feature_id }"
            @click="emit('select-feature', f.feature_id)"
          >
            <td class="cad-raw" :title="f.annotation_raw">
              <code>{{ f.annotation_raw }}</code>
              <span class="cad-fid">{{ f.feature_id }}</span>
            </td>
            <td>{{ orDash(f.unit) }}</td>
            <td>{{ orDash(f.nominal_value) }}</td>
            <td>{{ orDash(f.tolerance_lower) }}</td>
            <td>{{ orDash(f.tolerance_upper) }}</td>
            <td class="cad-cands">
              <span v-if="!f.target_candidates?.length">—（没有候选圆）</span>
              <code v-for="h in f.target_candidates" :key="h" class="cad-code">{{ h }}</code>
            </td>
            <td><code class="cad-code">{{ orDash(f.selected_target) }}</code></td>
            <td class="cad-status">{{ machineStatusText(f.association_status) }}</td>
            <td class="cad-gate-cell">
              <template v-if="outcome(f.feature_id).kind === 'eligible'">
                <span class="cad-pill cad-is-ok">可用（review_gate）</span>
              </template>
              <template v-else-if="outcome(f.feature_id).kind === 'excluded'">
                <span class="cad-pill cad-is-bad">被排除</span>
                <code class="cad-code">{{ outcome(f.feature_id).reason }}</code>
                <span class="cad-gate-why">{{ gateReasonText(outcome(f.feature_id).reason) }}</span>
              </template>
              <template v-else>
                <span class="cad-pill cad-is-muted">未表态</span>
                <span class="cad-gate-why">该要素既不在 review_gate.features 也不在 excluded —— 不视为可用</span>
              </template>
            </td>
            <td>
              <button
                class="cad-pick"
                type="button"
                data-testid="cad-feature-review-btn"
                @click.stop="emit('select-feature', f.feature_id)"
              >
                在复核区打开
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
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
  line-height: 1.75;
  color: var(--color-text);
}
.cad-emph {
  color: var(--color-body);
}
.cad-hint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: var(--color-ink);
  border-radius: 5px;
  padding: 1px 5px;
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
  min-width: 1180px;
}
.cad-table th,
.cad-table td {
  text-align: left;
  vertical-align: top;
  padding: 7px 10px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-body);
}
.cad-table th {
  color: var(--color-muted);
  font-weight: 500;
  background: var(--color-ink);
  white-space: nowrap;
}
.cad-table tbody tr {
  cursor: pointer;
}
.cad-table tbody tr:hover {
  background: rgba(37, 99, 235, 0.04);
}
.cad-table tbody tr.cad-is-selected {
  background: rgba(37, 99, 235, 0.08);
}
.cad-table tr:last-child td {
  border-bottom: 0;
}
.cad-raw {
  max-width: 220px;
}
.cad-raw code {
  display: block;
  white-space: normal;
  word-break: break-word;
}
.cad-fid {
  display: block;
  margin-top: 3px;
  font-size: 12.5px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.cad-cands {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 150px;
}
.cad-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 5px;
  padding: 1px 5px;
  color: var(--color-text);
  white-space: nowrap;
}
.cad-status {
  max-width: 190px;
  white-space: normal;
}
.cad-gate-cell {
  max-width: 260px;
}
.cad-gate-why {
  display: block;
  margin-top: 3px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-text);
}
/* 要素状态徽标：cad-is-ok / cad-is-bad 是判据，语义色保留；基础样式与 is-muted 统一成蓝底白字 */
.cad-pill {
  display: inline-block;
  font-size: 12.5px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  margin-bottom: 3px;
}
.cad-pill.cad-is-ok {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.cad-pill.cad-is-bad {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
.cad-pill.cad-is-muted {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
/* 「选这一行」：与其它按钮统一成蓝底白字加粗椭圆（原来是白底细描边） */
.cad-pick {
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
  white-space: nowrap;
}
.cad-pick:hover {
  background: #1d4ed8;
  color: #ffffff;
}
</style>
