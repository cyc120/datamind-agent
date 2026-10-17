<script setup lang="ts">
import { computed } from 'vue'
import type { DimensionEvaluation } from '../../api/types'
import { reasonText, valueWithUnit } from './shared'

/**
 * 一次评估结论的展示卡。
 *
 * ★ 本组件存在的**唯一理由**：把两个都叫「authentication」、却回答完全不同问题的
 *   事实分开渲染。混淆它们就是本页最严重的失真：
 *
 *   · 内层 —— `current_result.authentication_verified / formal_drawing_approval /
 *     product_quality_conclusion`（后端恒为 false）。
 *     它回答的是：**「这份输入与这次对照能不能当作正式依据？」**
 *     答案是不能：输入是作者自述的合成/本地记录，本流程不做图纸正式批准，
 *     对照结果也不是产品质量判定。
 *   · 外层 —— 响应信封里的 `authentication:{verified,scope}`（后端恒为 true）。
 *     它回答的是：**「这一次读取是不是在本地服务上、用鉴权过且带角色快照的
 *     记录完成的？」** 答案只是「是」。
 *
 *   两者互不推导：外层 true 既不能把内层的 false 变成 true，也不能把这次对照
 *   升级成制造授权。所以它们必须同屏、分段、各有各的标题，绝不能合并成一行
 *   「已认证」。
 *
 * 另外：结果的状态是 compared / blocked，**没有**任何「OK / NG」语义。
 * compared 用蓝色而不是绿色 —— 绿色会被读成「判定为没问题」，而那正是后端
 * 明确否认的东西。区间关系只描述「落在标注上下界的哪一侧」。
 */

const props = withDefaults(
  defineProps<{
    result: DimensionEvaluation | null
    /** 外层信封；只有绑定详情与对照记录会带，缺失时这里如实说「没有带」 */
    authentication?: { verified: boolean; scope: string } | null
    title?: string
    subtitle?: string
  }>(),
  { authentication: null, title: '当前对照结论', subtitle: '' },
)

const comparison = computed(() => props.result?.comparison ?? null)

/** compared 用 info（蓝）而不是 ok（绿）：这不是「没问题」，只是「算出了区间关系」 */
const tone = computed(() => {
  const status = props.result?.status
  if (status === 'compared') return 'info'
  if (status === 'blocked') return 'warn'
  return 'muted'
})

const statusLabel = computed(() => {
  const status = props.result?.status
  if (status === 'compared') return '已对照（区间关系，非判定）'
  if (status === 'blocked') return '核验暂停'
  return `未知状态：${status ?? ''}`
})

/** 区间关系的中文说法。刻意不写任何「判定词」——那是判定，本案不产出判定。 */
const comparisonLabel = computed(() => {
  const status = comparison.value?.status
  if (status === 'within_annotation_range') return '落在标注区间内（不是判定结论）'
  if (status === 'outside_annotation_range') return '落在标注区间外（不是判定结论）'
  return `未知区间关系：${status ?? '（缺失）'}`
})

const withinRange = computed(() => comparison.value?.status === 'within_annotation_range')
</script>

<template>
  <section class="dimr-card" data-testid="dim-result-card">
    <div class="dimr-head">
      <span class="dimr-title">{{ title }}</span>
      <span class="dimr-badge" :class="`dimr-tone-${tone}`" data-testid="dim-result-status">
        {{ statusLabel }}
      </span>
    </div>
    <p v-if="subtitle" class="dimr-sub">{{ subtitle }}</p>

    <p v-if="!result" class="dimr-empty" data-testid="dim-result-empty">
      后端没有给出对照结论（例如绑定刚登记、尚未评估）。这里不替它猜一个结论。
    </p>

    <template v-else>
      <!-- blocked：原因码与中文解释都必须在，未知码也原样露出 -->
      <div v-if="result.status === 'blocked'" class="dimr-block" data-testid="dim-result-blocked">
        <div class="dimr-code-row">
          <span class="dimr-key">阻断码</span>
          <code class="dimr-code" data-testid="dim-reason-code">{{ result.reason || '（后端未给出 reason）' }}</code>
        </div>
        <p class="dimr-why" data-testid="dim-reason-text">{{ reasonText(result.reason) }}</p>
        <p class="dimr-caveat">
          阻断意味着这次<b>没有</b>产出区间关系。它不是任何判定结论，也不是「授权与否」。
        </p>
      </div>

      <!-- compared：原值与归一值都给，且说明这不是判定 -->
      <div
        v-else-if="result.status === 'compared' && comparison"
        class="dimr-compare"
        data-testid="dim-result-compared"
      >
        <div class="dimr-row">
          <span class="dimr-key">区间关系</span>
          <span class="dimr-val" :class="{ 'dimr-val-flag': withinRange }" data-testid="dim-comparison-status">
            {{ comparisonLabel }}
          </span>
        </div>

        <div class="dimr-grid">
          <div class="dimr-cell">
            <div class="dimr-key">测量原值 measurement_original</div>
            <div class="dimr-val" data-testid="dim-measurement-original">
              {{ valueWithUnit(comparison.measurement_original.value, comparison.measurement_original.unit) }}
            </div>
          </div>
          <div class="dimr-cell">
            <div class="dimr-key">标注原值 annotation_original</div>
            <div class="dimr-val" data-testid="dim-annotation-original">
              raw = {{ comparison.annotation_original.raw }}
            </div>
            <div class="dimr-val">
              [{{ comparison.annotation_original.lower }}, {{ comparison.annotation_original.upper }}]
              {{ comparison.annotation_original.unit }}
            </div>
          </div>
          <div class="dimr-cell">
            <div class="dimr-key">归一后 normalized_mm</div>
            <div class="dimr-val" data-testid="dim-normalized-mm">
              measurement = {{ comparison.normalized_mm.measurement }}
            </div>
            <div class="dimr-val">
              [{{ comparison.normalized_mm.lower }}, {{ comparison.normalized_mm.upper }}]
            </div>
          </div>
        </div>

        <div class="dimr-row">
          <span class="dimr-key">句柄</span>
          <span class="dimr-val">
            <code>{{ comparison.annotation_handle }}</code> → <code>{{ comparison.target_handle }}</code>
          </span>
        </div>

        <p class="dimr-caveat">
          原值与归一值<b>都</b>显示：inch→mm 换算后才可比较，但原始记录不被替换。区间关系只来自图纸标注的
          上下界；它不等于公差判定、不等于产品质量判定，也不构成任何制造授权。
        </p>
      </div>

      <!-- 未认识的 status：原样露出，不猜 -->
      <div v-else class="dimr-block" data-testid="dim-result-unknown">
        <p class="dimr-why">状态未知：{{ result.status }}（本页只认 compared / blocked，原样显示不猜）</p>
        <p v-if="result.reason" class="dimr-why">{{ reasonText(result.reason) }}</p>
        <p v-else class="dimr-why">后端未给出 reason。</p>
      </div>

      <!-- ★ 两份「认证」事实：内层 ← 结论自己的声明；外层 ← 本次读取的信封 -->
      <div class="dimr-facts">
        <section class="dimr-fact dimr-fact-inner" data-testid="dim-inner-facts">
          <h4 class="dimr-fact-title">内层 · 这份结论自己的声明</h4>
          <p class="dimr-fact-q">回答的问题：<b>这份输入与这次对照，能不能当作正式依据？</b></p>
          <ul class="dimr-fact-list">
            <li>
              authentication_verified =
              <code :class="{ 'dimr-bool-true': result.authentication_verified }">
                {{ result.authentication_verified }}
              </code>
              —— 输入是作者自述的合成 / 本地记录，不是外部认证过的数据。
            </li>
            <li>
              formal_drawing_approval =
              <code :class="{ 'dimr-bool-true': result.formal_drawing_approval }">
                {{ result.formal_drawing_approval }}
              </code>
              —— 本流程不做图纸的正式批准。
            </li>
            <li>
              product_quality_conclusion =
              <code :class="{ 'dimr-bool-true': result.product_quality_conclusion }">
                {{ result.product_quality_conclusion }}
              </code>
              —— 对照结果不是产品质量判定。
            </li>
          </ul>
        </section>

        <section class="dimr-fact dimr-fact-outer" data-testid="dim-outer-fact">
          <h4 class="dimr-fact-title">外层 · 本次读取的本地认证信封</h4>
          <p class="dimr-fact-q">
            回答的问题：<b>这一次请求，是不是在本地服务上用鉴权过、带角色快照的记录完成的？</b>
          </p>
          <p v-if="authentication" class="dimr-fact-line">
            authentication.verified =
            <code>{{ authentication.verified }}</code>
            · scope = <span class="dimr-scope">{{ authentication.scope }}</span>
          </p>
          <p v-else class="dimr-fact-line">
            这次响应没有带外层信封（只有绑定详情与对照记录会带）。缺失就是缺失，不补一个 true。
          </p>
          <p class="dimr-caveat">
            外层 verified=true 只说明本地读取走了鉴权；它<b>不</b>等于外部认证，也<b>不</b>把内层的三个
            false 变成 true。两者回答的是不同的问题。
          </p>
        </section>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 类名统一 dimr- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会出现
   「只声明了部分属性、其余被工具类接管」的怪象（仓库里已出过两次事故）。 */
.dimr-card {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
}
.dimr-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 面板标题与其它页统一：蓝色、15px、600（原来是 14.5px 灰黑，属离群值） */
.dimr-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
/* 徽标统一蓝底白字加粗椭圆；tone-warn 是警告语义色（橙），保留 */
.dimr-badge {
  margin-left: auto;
  font-size: 13px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  white-space: nowrap;
}
.dimr-tone-info {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.dimr-tone-warn {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
/* tone-muted 现在与基础样式等价（保留类名，避免动模板） */
.dimr-tone-muted {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.dimr-sub {
  margin: 4px 0 0;
  font-size: 13.5px;
  color: var(--color-text);
}
.dimr-empty {
  margin: 10px 0 0;
  font-size: 14px;
  color: var(--color-text);
  line-height: 1.7;
}
.dimr-block {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid rgba(217, 119, 6, 0.3);
  border-radius: 12px;
  background: rgba(217, 119, 6, 0.06);
}
.dimr-code-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.dimr-key {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--color-muted);
}
.dimr-code {
  font-size: 13.5px;
  color: var(--color-text);
  word-break: break-all;
}
.dimr-why {
  margin: 8px 0 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-body);
}
.dimr-caveat {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
}
.dimr-compare {
  margin-top: 10px;
}
.dimr-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}
.dimr-val {
  font-size: 14px;
  color: var(--color-text);
  word-break: break-all;
}
.dimr-val-flag {
  color: #2563eb;
}
.dimr-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
  margin-bottom: 8px;
}
.dimr-cell {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  padding: 8px 10px;
  background: var(--color-ink);
}
.dimr-cell .dimr-key {
  margin-bottom: 4px;
}
.dimr-facts {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}
.dimr-fact {
  border: 1px dashed var(--color-line);
  border-radius: 12px;
  padding: 10px 12px;
}
.dimr-fact-inner {
  background: rgba(217, 119, 6, 0.04);
}
.dimr-fact-outer {
  background: rgba(37, 99, 235, 0.04);
}
/* 事实卡标题与其它页统一：蓝色、15px、600（原来是 14px 灰黑） */
.dimr-fact-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.dimr-fact-q {
  margin: 5px 0 0;
  font-size: 13px;
  color: var(--color-text);
  line-height: 1.7;
}
.dimr-fact-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 13.5px;
  color: var(--color-body);
  line-height: 1.8;
}
.dimr-fact-line {
  margin: 6px 0 0;
  font-size: 13.5px;
  color: var(--color-body);
  line-height: 1.7;
  word-break: break-word;
}
.dimr-scope {
  color: var(--color-text);
}
.dimr-bool-true {
  color: #e11d48;
  font-weight: 600;
}
</style>
