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
.cad-errnote {
  margin: 0 0 10px;
  padding: 9px 11px;
  border: 1px solid rgba(225, 29, 72, 0.35);
  border-radius: 10px;
  background: rgba(225, 29, 72, 0.06);
  color: #b91c1c;
  font-size: 12.5px;
  line-height: 1.7;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
}
.cad-errnote-code {
  flex-shrink: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
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
