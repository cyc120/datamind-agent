<script setup lang="ts">
import type { CadErrorView } from './cadCopy'

/**
 * 显式错误态。WHY 独立组件：本页每个数据块（资产/版本/要素/复核历史/图元/登记）
 * 都必须能各自报错而**不**把其它块一起清空；同时后端错误信封恒为
 * `{"detail":{"error_code":"CODE"}}`，error_code 必须原样可读，不能被一句中文盖掉。
 */
defineProps<{
  error: CadErrorView | null
  testid?: string
}>()
</script>

<template>
  <p v-if="error" class="cad-errnote" :data-testid="testid || 'cad-error'">
    <code v-if="error.code" class="cad-errnote-code">{{ error.code }}</code>
    <span class="cad-errnote-text">{{ error.text }}</span>
  </p>
</template>

<style scoped>
/* cad- 前缀：Tailwind v4 的 @layer utilities 会生成同名工具类，撞上就会出现
   「只生效一半」的怪象（本仓库已出过两次事故）。 */
/* 浅底错误提示条：保持浅底（红是判据），只把圆角收敛到卡片档 12px */
/* 去框：改左侧 2px 红细条 + 黑色正文。原来这里是「描边 + 圆角 + 浅底」的卡片，与全站其它提示块不统一；红作为语义保留在细线上。 */
.cad-errnote {
  margin: 0 0 18px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid #e11d48;
  border-radius: 0;
  background: none;
  color: var(--color-text);
}
.cad-errnote-code {
  flex-shrink: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  padding: 1px 6px;
  border-radius: 6px;
  background: rgba(225, 29, 72, 0.12);
  color: #9f1239;
}
.cad-errnote-text {
  flex: 1;
  min-width: 0;
}
</style>
