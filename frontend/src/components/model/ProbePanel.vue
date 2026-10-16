<script setup lang="ts">
import { computed } from 'vue'

import type { ModelListResult, ModelTestResult } from '../../api/types'
import { PROBE_NOT_PERSISTED, probeFailureText, protocolLabel, stamp } from './messages'

/**
 * 模型接入 —— 探活 / 模型列表结果面板。
 *
 * 本屏三件事必须分开说，任何一件都不能冒充另一件：
 *   1. 配置    —— GET /v1/model/config：只说明「配了没有」（has_key）；
 *   2. 探活    —— POST /v1/model/config/test：**按需**发起，唯一能证明"这一刻能应答"的证据；
 *   3. 模型列表 —— POST /v1/model/config/models：只说明该密钥能看到哪些模型名。
 * 后端**不持久化**任何检测结果（没有 /v1/model/status 这类接口），所以这里显示的是
 * 本页刚刚做的那一次探活（带时间戳），刷新页面即丢；绝不把「密钥配了」渲染成「可用」。
 */

const props = defineProps<{
  probing: boolean
  /** 探活响应体（后端探针用 HTTP 200 + ok:false 报错，只有 HTTP 层失败才走 testError） */
  testResult: ModelTestResult | null
  /** 请求本身失败（网络/权限/CSRF/跨源 400…）时的中文解释 */
  testError: string
  /** 本次探活完成的本地时间（ISO）；服务端不保存，时间戳由本页记录 */
  testAt: string | null
  /** 这次探的是谁（哪个配置 / 表单当前内容） */
  testTarget: string
  modelsLoading: boolean
  models: ModelListResult | null
  modelsError: string
  modelsAt: string | null
}>()

const emit = defineEmits<{ (e: 'pick-model', name: string): void }>()

/** 服务端一次最多回这么多模型，与旧版控制台一致；多了只显示前 60 个并说明总数。 */
const MAX_CHIPS = 60

const modelNames = computed<string[]>(() => {
  const list = props.models?.models
  return Array.isArray(list) ? list : []
})

const shownModels = computed(() => modelNames.value.slice(0, MAX_CHIPS))

const truncated = computed(() => modelNames.value.length > MAX_CHIPS)

function testFailure(): string {
  const result = props.testResult
  if (!result) return ''
  return probeFailureText(result.error_code, result.message)
}
</script>

<template>
  <section class="mpp" data-testid="model-probe-panel">
    <div class="mpp-head">
      <h2 class="mpp-title">探活结果</h2>
      <span v-if="props.testAt" class="mpp-stamp" data-testid="model-probe-stamp">
        {{ stamp(props.testAt) }}
      </span>
    </div>

    <!-- 探活是本屏唯一能证明"能应答"的证据，但它不是持久事实：这句话必须一直在。 -->
    <p class="mpp-hint" data-testid="model-probe-honesty">{{ PROBE_NOT_PERSISTED }}</p>

    <p v-if="props.probing" class="mpp-state" data-testid="model-probe-loading">正在探测…</p>

    <p v-else-if="props.testError" class="mpp-bad" data-testid="model-probe-error">{{ props.testError }}</p>

    <p v-else-if="!props.testResult" class="mpp-state" data-testid="model-probe-empty">
      还没有探活结果。点「测试连接」（或列表里某一行的「测试」）才会真的调用一次上游。
    </p>

    <template v-else>
      <p class="mpp-target" data-testid="model-probe-target">探测目标：{{ props.testTarget }}</p>
      <div
        v-if="props.testResult.ok"
        class="mpp-box is-ok"
        data-testid="model-probe-ok"
      >
        <strong>这一次探活通过</strong>
        <p class="mpp-line">
          模型 {{ props.testResult.model || '(未回报模型名)' }} ·
          {{ protocolLabel(props.testResult.protocol) }} ·
          {{ props.testResult.elapsed_ms != null ? `${Math.round(Number(props.testResult.elapsed_ms))}ms` : '未回报耗时' }}
        </p>
        <p class="mpp-line">接口地址：{{ props.testResult.base_url || '(未回报)' }}</p>
        <p class="mpp-line">上游回复摘录：{{ props.testResult.reply || '(空)' }}</p>
        <p class="mpp-hint">
          注意：这只证明「刚才、这个地址、这个模型」返回了内容；它不证明密钥长期有效，
          也不证明该模型适合本项目的问数链路（SQL 生成质量要由评测集证明）。
        </p>
      </div>

      <div v-else class="mpp-box is-bad" data-testid="model-probe-fail">
        <strong>这一次探活失败</strong>
        <p class="mpp-line">
          错误码：<code>{{ props.testResult.error_code || '未给出' }}</code>
        </p>
        <p class="mpp-line" data-testid="model-probe-fail-text">{{ testFailure() }}</p>
        <p class="mpp-line">
          实际使用：模型 {{ props.testResult.configured_model || props.testResult.model || '(未知)' }} ·
          地址 {{ props.testResult.base_url || '(未知)' }} ·
          {{ props.testResult.elapsed_ms != null ? `${Math.round(Number(props.testResult.elapsed_ms))}ms` : '未回报耗时' }}
        </p>
      </div>
    </template>

    <div class="mpp-models">
      <div class="mpp-head">
        <h3 class="mpp-h3">上游模型列表</h3>
        <span v-if="props.modelsAt" class="mpp-stamp" data-testid="model-models-stamp">{{ stamp(props.modelsAt) }}</span>
      </div>
      <p class="mpp-hint">
        列表只说明「这个密钥在这些地址上能看到哪些模型名」，不代表它们都能用于本项目的问数链路。
      </p>

      <p v-if="props.modelsLoading" class="mpp-state" data-testid="model-models-loading">正在获取模型列表…</p>
      <p v-else-if="props.modelsError" class="mpp-bad" data-testid="model-models-error">{{ props.modelsError }}</p>
      <p v-else-if="!props.models" class="mpp-state" data-testid="model-models-empty">
        还没有拉取过模型列表。点「获取模型列表」会向当前表单/所选配置指向的上游要一次。
      </p>
      <template v-else-if="props.models.ok">
        <p class="mpp-state" data-testid="model-models-count">
          共 {{ modelNames.length }} 个模型{{ truncated ? `，只展示前 ${MAX_CHIPS} 个` : '' }}。点一个名字会填入表单的模型名。
        </p>
        <div class="mpp-chips" data-testid="model-model-chips">
          <button
            v-for="name in shownModels"
            :key="name"
            class="mpp-chip"
            type="button"
            data-testid="model-model-chip"
            :title="name"
            @click="emit('pick-model', name)"
          >
            {{ name }}
          </button>
        </div>
      </template>
      <div v-else class="mpp-box is-bad" data-testid="model-models-fail">
        <p class="mpp-line">错误码：<code>{{ props.models.error_code || '未给出' }}</code></p>
        <p class="mpp-line" data-testid="model-models-fail-text">
          {{ probeFailureText(props.models.error_code, props.models.message) }}
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 类名统一 mpp- 前缀，避开 Tailwind v4 @layer utilities 的同名工具类。 */
.mpp {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px 16px;
  margin-top: 14px;
}
.mpp-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.mpp-title {
  margin: 0;
  font-size: 15px;
  color: var(--color-accent);
}
.mpp-h3 {
  margin: 12px 0 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--color-accent);
}
.mpp-stamp {
  font-size: 12.5px;
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
}
.mpp-hint {
  margin: 6px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--color-text);
}
.mpp-state {
  margin: 8px 0 0;
  font-size: 13.5px;
  color: var(--color-text);
}
.mpp-bad {
  margin: 8px 0 0;
  font-size: 13.5px;
  color: #e11d48;
  line-height: 1.6;
}
.mpp-target {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--color-text);
}
.mpp-box {
  margin-top: 8px;
  border-radius: 10px;
  padding: 10px 12px;
  border: 1px solid var(--color-line);
}
.mpp-box.is-ok {
  border-color: rgba(22, 163, 74, 0.35);
  background: rgba(22, 163, 74, 0.06);
}
.mpp-box.is-bad {
  border-color: rgba(225, 29, 72, 0.3);
  background: rgba(225, 29, 72, 0.05);
}
.mpp-box strong {
  font-size: 14px;
  color: var(--color-text);
}
.mpp-line {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--color-body);
  word-break: break-word;
}
.mpp-models {
  margin-top: 6px;
  border-top: 1px dashed var(--color-line);
  padding-top: 4px;
}
.mpp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  max-height: 240px;
  overflow-y: auto;
}
.mpp-chip {
  font: inherit;
  font-size: 13px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  color: var(--color-body);
  cursor: pointer;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mpp-chip:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
code {
  font-size: 13px;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--color-ink);
  color: var(--color-text);
}
</style>
