<script setup lang="ts">
import type { CadFeatureGate } from '../../api/types'
import { gateReasonText, listText, orDash } from './cadCopy'

/**
 * 一道使用门槛（inspection_gate 或 review_gate）的完整呈现。
 *
 * WHY 必须把 excluded[] 连同原因列出来：门槛说「不可用」时，excluded 才是**理由**。
 * 只显示一个 needs_review 而不显示每条被排除的原因，读者会以为「再点一下就能过」，
 * 或者以为数据丢了；藏起拒绝理由等于把拒绝伪装成通过。
 */
defineProps<{
  gate: CadFeatureGate | null
  caption: string
  testid: string
}>()
</script>

<template>
  <div v-if="gate" class="cad-gate" :data-testid="testid">
    <p class="cad-gate-line">
      <span class="cad-gate-caption">{{ caption }}</span>
      状态 <b class="cad-gate-status" :class="gate.status === 'ready' ? 'cad-is-ready' : 'cad-is-blocked'">{{ gate.status }}</b>
      <span class="cad-gate-count">可用 {{ gate.features.length }} / 排除 {{ gate.excluded.length }}</span>
    </p>

    <p v-if="gate.reason" class="cad-gate-reason" data-testid="cad-gate-reason">
      整道门槛不可用：{{ gateReasonText(gate.reason) }}
      <code class="cad-code">{{ gate.reason }}</code>
      —— 这类冲突来自原文本身，复核或审批都消除不了它。
    </p>

    <div class="cad-gate-cols">
      <div class="cad-gate-col">
        <h4>可被下游引用的要素（{{ gate.features.length }}）</h4>
        <p v-if="!gate.features.length" class="cad-hint">没有要素达到这道门槛。</p>
        <ul v-else class="cad-gate-list">
          <li v-for="f in gate.features" :key="f.feature_id" data-testid="cad-gate-eligible">
            <code class="cad-code">{{ f.feature_id }}</code>
            <span class="cad-gate-meta">
              {{ orDash(f.nominal_value) }} {{ f.unit || '' }}
              · 下 {{ orDash(f.lower) }} / 上 {{ orDash(f.upper) }}
              · 标注 <code class="cad-code">{{ orDash(f.annotation_handle) }}</code>
              → 目标 <code class="cad-code">{{ orDash(f.target_handle) }}</code>
            </span>
          </li>
        </ul>
        <p class="cad-gate-foot">
          上下界是**标注偏差**（名义值加减），不是几何公差。
        </p>
      </div>

      <div class="cad-gate-col">
        <h4>被排除的要素（{{ gate.excluded.length }}）</h4>
        <p v-if="!gate.excluded.length" class="cad-hint">没有被排除的要素。</p>
        <ul v-else class="cad-gate-list">
          <li v-for="f in gate.excluded" :key="f.feature_id" data-testid="cad-gate-excluded">
            <code class="cad-code">{{ f.feature_id }}</code>
            <span class="cad-gate-meta">
              <code class="cad-code">{{ f.reason }}</code>
              {{ gateReasonText(f.reason) }}
            </span>
          </li>
        </ul>
        <p class="cad-gate-foot">
          排除原因照录原始代码：{{ listText(gate.excluded.map((f) => f.reason)) }}
        </p>
      </div>
    </div>
  </div>
  <p v-else class="cad-hint" :data-testid="testid">门槛数据尚未加载。</p>
</template>

<style scoped>
.cad-gate {
  border: 1px solid var(--color-line);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--color-ink);
  margin-bottom: 12px;
}
.cad-gate-line {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
  font-size: 14px;
  color: var(--color-body);
}
.cad-gate-caption {
  font-weight: 600;
  color: var(--color-text);
}
.cad-gate-status {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 13px;
  border: 1px solid var(--color-line);
}
.cad-gate-status.cad-is-ready {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.cad-gate-status.cad-is-blocked {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.08);
}
.cad-gate-count {
  color: var(--color-muted);
}
.cad-gate-reason {
  margin: 8px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #b45309;
}
.cad-gate-cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-top: 10px;
}
.cad-gate-col h4 {
  margin: 0 0 6px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--color-text);
}
.cad-gate-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.cad-gate-list li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--color-panel);
  border: 1px solid var(--color-line);
}
.cad-gate-meta {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-muted);
  word-break: break-word;
}
.cad-gate-foot {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--color-muted);
  line-height: 1.6;
}
.cad-hint {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-muted);
}
.cad-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 5px;
  padding: 1px 5px;
  color: var(--color-text);
}
</style>
