<script setup lang="ts">
import { ref } from 'vue'

import type { ModelProfile } from '../../api/types'
import { HAS_KEY_NOTE, protocolLabel, stamp } from './messages'

/**
 * 模型接入 —— 左栏「已保存的接入配置」列表。
 *
 * 这里是只读 + 动作按钮的展示层：所有请求由 ModelPage 发起（列表刷新、激活、探活、
 * 删除都要联动右栏编辑器与探活面板），所以本组件只 emit 意图，不自己调 client.ts。
 *
 * 每行的三个徽标各说一件事，互不代替：
 *   · 协议       —— 记录里存的 protocol（openai / anthropic）；
 *   · 密钥已填写 —— **只**说明密钥字段填过值（has_key），绝不等于密钥有效；
 *   · 启用中     —— 只有 true 才说明这条是当前生效的配置。
 */

const props = defineProps<{
  items: ModelProfile[]
  /** 服务端认定的当前启用配置 id；列表里这个值才是权威（create/update 返回体的 active 恒为 false） */
  active: string | null
  protocols: { id: string; label: string }[]
  /** 正在保存/激活的配置 id（对应按钮禁用） */
  busyId: string | null
  /** 正在删除的配置 id */
  deletingId: string | null
  /** 正在编辑的配置 id（高亮） */
  editingId: string | null
  /** 探活进行中的配置 id */
  probingId: string | null
}>()

const emit = defineEmits<{
  (e: 'edit', profile: ModelProfile): void
  (e: 'activate', profile: ModelProfile): void
  (e: 'probe', profile: ModelProfile): void
  (e: 'remove', profile: ModelProfile): void
}>()

/** 删除是破坏性动作：先在本行内确认（不用 window.confirm，避免阻塞式弹窗与误触）。 */
const confirmId = ref<string | null>(null)

function askRemove(profile: ModelProfile): void {
  confirmId.value = profile.id
}

function cancelRemove(): void {
  confirmId.value = null
}

function doRemove(profile: ModelProfile): void {
  confirmId.value = null
  emit('remove', profile)
}

function nameOf(profile: ModelProfile): string {
  const name = (profile.name || '').trim()
  return name || '未命名配置'
}

function keyText(profile: ModelProfile): string {
  if (!profile.has_key) return '未填写密钥'
  return `密钥已填写 ${profile.key_masked || ''}`.trim()
}

/** 敏感头只回显键名；这里只数个数，值永远不会出现在浏览器里。 */
function headerSummary(profile: ModelProfile): string {
  const names = Array.isArray(profile.header_keys) ? profile.header_keys : []
  const parts: string[] = []
  if (names.length) parts.push(`敏感头 ${names.length} 个（仅键名，值不下发）`)
  const plain = profile.headers && typeof profile.headers === 'object' ? Object.keys(profile.headers).length : 0
  if (plain) parts.push(`普通头 ${plain} 个`)
  if (profile.has_proxy) parts.push(profile.proxy_has_credentials ? '代理已配置（含凭据）' : '代理已配置')
  return parts.join(' · ')
}
</script>

<template>
  <ul class="mpl-list" data-testid="model-profile-list">
    <li
      v-for="p in props.items"
      :key="p.id"
      class="mpl-card"
      :class="{ 'is-active': props.active === p.id, 'is-editing': props.editingId === p.id }"
      data-testid="model-profile-row"
    >
      <div class="mpl-head">
        <strong class="mpl-name" data-testid="model-profile-name">{{ nameOf(p) }}</strong>
        <span class="mpl-pill" data-testid="model-profile-protocol">
          {{ protocolLabel(p.protocol, props.protocols) }}
        </span>
        <span
          class="mpl-pill"
          :class="p.has_key ? 'tone-ok' : 'tone-muted'"
          :title="HAS_KEY_NOTE"
          data-testid="model-profile-key-badge"
        >
          {{ keyText(p) }}
        </span>
        <span
          class="mpl-pill"
          :class="props.active === p.id ? 'tone-on' : 'tone-muted'"
          data-testid="model-profile-active-badge"
        >
          {{ props.active === p.id ? '启用中' : '未启用' }}
        </span>
      </div>

      <p class="mpl-model" :class="{ 'is-missing': !p.model }" data-testid="model-profile-model">
        {{ p.model || '未填模型（保存/探活前必须填写）' }}
      </p>
      <p class="mpl-meta">{{ p.base_url || '未填接口地址' }}</p>
      <p v-if="headerSummary(p)" class="mpl-meta">{{ headerSummary(p) }}</p>
      <p v-if="p.updated_at" class="mpl-meta">更新于 {{ stamp(p.updated_at) }}</p>

      <div class="mpl-actions">
        <button
          v-if="props.active !== p.id"
          class="mpl-btn"
          type="button"
          :disabled="props.busyId === p.id"
          data-testid="model-profile-activate"
          @click="emit('activate', p)"
        >
          {{ props.busyId === p.id ? '启用中…' : '启用' }}
        </button>
        <button
          class="mpl-btn"
          type="button"
          :disabled="props.busyId === p.id"
          data-testid="model-profile-edit"
          @click="emit('edit', p)"
        >
          编辑
        </button>
        <button
          class="mpl-btn"
          type="button"
          :disabled="props.probingId === p.id"
          data-testid="model-profile-test"
          @click="emit('probe', p)"
        >
          {{ props.probingId === p.id ? '探测中…' : '测试' }}
        </button>
        <template v-if="confirmId !== p.id">
          <button
            class="mpl-btn is-danger"
            type="button"
            :disabled="props.deletingId === p.id"
            data-testid="model-profile-delete"
            @click="askRemove(p)"
          >
            {{ props.deletingId === p.id ? '删除中…' : '删除' }}
          </button>
        </template>
        <template v-else>
          <span class="mpl-confirm">确认删除「{{ nameOf(p) }}」？</span>
          <button
            class="mpl-btn is-danger"
            type="button"
            data-testid="model-profile-delete-confirm"
            @click="doRemove(p)"
          >
            确认删除
          </button>
          <button class="mpl-btn" type="button" data-testid="model-profile-delete-cancel" @click="cancelRemove">
            取消
          </button>
        </template>
      </div>
    </li>
  </ul>
</template>

<style scoped>
/* 类名统一 mpl- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，撞上就会出现
   「只声明了部分属性、其余被工具类接管」的怪象（仓库里已出过两次事故）。 */
.mpl-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mpl-card {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 12px 14px;
}
.mpl-card.is-editing {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.05);
}
.mpl-card.is-active {
  border-left: 3px solid rgba(22, 163, 74, 0.6);
}
.mpl-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.mpl-name {
  font-size: 15px;
  color: var(--color-text);
}
.mpl-pill {
  font-size: 12.5px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  color: var(--color-muted);
  white-space: nowrap;
}
.mpl-pill.tone-ok {
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.35);
  background: rgba(22, 163, 74, 0.08);
}
.mpl-pill.tone-on {
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.45);
  background: rgba(22, 163, 74, 0.12);
}
.mpl-pill.tone-muted {
  color: var(--color-muted);
}
.mpl-model {
  margin: 8px 0 2px;
  font-size: 14px;
  color: var(--color-text);
  word-break: break-all;
}
.mpl-model.is-missing {
  color: #d97706;
}
.mpl-meta {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--color-muted);
  word-break: break-all;
}
.mpl-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
}
.mpl-btn {
  font: inherit;
  font-size: 13.5px;
  padding: 4px 10px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.mpl-btn:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.mpl-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.mpl-btn.is-danger {
  color: #e11d48;
  border-color: rgba(225, 29, 72, 0.3);
}
.mpl-confirm {
  font-size: 13px;
  color: #e11d48;
}
</style>
