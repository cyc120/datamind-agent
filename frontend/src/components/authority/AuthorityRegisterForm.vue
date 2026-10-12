<script setup lang="ts">
import { computed } from 'vue'

import { failureText } from './useAuthority'
import type { AuthorityDraft, AuthorityFailure } from './useAuthority'

/**
 * 登记表单（后端 AuthorityRequest，extra=forbid，多一个字段就 422）。
 *
 * WHY 有效期用**文本框**而不是 datetime-local：datetime-local 输出的是
 * `2026-10-01T08:00`（不带时区偏移），后端 timestamp() 解析不了 → 422
 * AUTHORITY_VALIDITY_INVALID。时区偏移是这条规则语义的一部分，不能由浏览器悄悄补，
 * 所以候选建议值（自带 +08:00）原样预填，人也能看到自己到底提交了什么。
 *
 * WHY 来源没有默认值：来源名字（「质检报告」）不构成任何权威，默认选中一个来源就是
 * 在替人做裁决。必须显式选 production 或 report。
 */

const props = defineProps<{
  /** produced_at 来自候选，用于本地镜像「窗口必须覆盖它」这条服务端规则 */
  producedAt: string
  canRegister: boolean
  registering: boolean
  failure: AuthorityFailure | null
  /** 依据页链接（相对路径）；basis 不完整时为空串 */
  basisLink: string
}>()

const emit = defineEmits<{ (e: 'submit'): void }>()

/** v-model：草稿对象由页面持有，这里只负责渲染与改写字段。 */
const draft = defineModel<AuthorityDraft>({ required: true })

const rationaleLength = computed(() => draft.value.rationale.trim().length)

/** 只提示「还差什么」，不替代服务端校验（服务端仍是唯一裁决者）。 */
const rationaleHint = computed(() => {
  if (rationaleLength.value === 0) return '至少 10 字（后端 rationale 约束 10..1000）'
  if (rationaleLength.value < 10) return `还差 ${10 - rationaleLength.value} 字（下限 10）`
  if (rationaleLength.value > 1000) return `超出 ${rationaleLength.value - 1000} 字（上限 1000）`
  return '长度合格'
})

function onSelectSource(value: 'production' | 'report'): void {
  draft.value.selected_source = value
}
</script>

<template>
  <form class="arf-form" data-testid="authority-register-form" @submit.prevent="emit('submit')">
    <p class="arf-timezone" data-testid="authority-timezone-note">
      有效期<strong>必须</strong>带显式时区偏移（例如 <code>2026-10-01T00:00:00+08:00</code>）。
      不带偏移的 naive 时间会被后端以 422 <code>AUTHORITY_VALIDITY_INVALID</code> 拒绝。
      适用判定是<strong>左闭右开</strong>：<code>valid_from ≤ produced_at &lt; valid_to</code>。
      本候选的 produced_at = <code data-testid="authority-form-produced-at">{{ producedAt }}</code>。
    </p>

    <label class="arf-field">
      <span class="arf-label">问题 question（服务端会用它重新解析候选）</span>
      <input
        v-model="draft.question"
        class="arf-input"
        type="text"
        maxlength="2000"
        :disabled="registering"
        data-testid="authority-form-question"
      />
      <span class="arf-help">
        改这里等于换一个问题：候选哈希会对不上，服务端以 409 <code>AUTHORITY_CONTEXT_CHANGED</code> 拒绝。
      </span>
    </label>

    <div class="arf-grid">
      <label class="arf-field">
        <span class="arf-label">测量 ID measurement_id（来自候选 scope）</span>
        <input
          v-model="draft.measurement_id"
          class="arf-input"
          type="text"
          readonly
          data-testid="authority-form-measurement-id"
        />
      </label>

      <label class="arf-field">
        <span class="arf-label">候选哈希 candidate_sha256（原样回传，服务端重算比对）</span>
        <input
          v-model="draft.candidate_sha256"
          class="arf-input arf-mono"
          type="text"
          readonly
          data-testid="authority-form-candidate-sha"
        />
      </label>
    </div>

    <fieldset class="arf-fieldset">
      <legend class="arf-label">以哪一侧为准 selected_source（必须显式选择，没有默认值）</legend>
      <div class="arf-radios">
        <label class="arf-radio">
          <input
            type="radio"
            name="authority-selected-source"
            value="production"
            :checked="draft.selected_source === 'production'"
            :disabled="registering"
            data-testid="authority-selected-source-production"
            @change="onSelectSource('production')"
          />
          <span>生产数据（SQL 实测）</span>
        </label>
        <label class="arf-radio">
          <input
            type="radio"
            name="authority-selected-source"
            value="report"
            :checked="draft.selected_source === 'report'"
            :disabled="registering"
            data-testid="authority-selected-source-report"
            @change="onSelectSource('report')"
          />
          <span>报告字段（PDF 事实）</span>
        </label>
      </div>
      <span class="arf-help">选择只登记「以哪一侧为准」，不会改动任何一侧的原值。</span>
    </fieldset>

    <div class="arf-grid">
      <label class="arf-field">
        <span class="arf-label">有效期起点（含时区，包含） valid_from</span>
        <input
          v-model="draft.valid_from"
          class="arf-input arf-mono"
          type="text"
          :disabled="registering"
          placeholder="2026-10-01T00:00:00+08:00"
          data-testid="authority-valid-from"
        />
      </label>

      <label class="arf-field">
        <span class="arf-label">有效期终点（含时区，不包含） valid_to</span>
        <input
          v-model="draft.valid_to"
          class="arf-input arf-mono"
          type="text"
          :disabled="registering"
          placeholder="2026-11-01T00:00:00+08:00"
          data-testid="authority-valid-to"
        />
      </label>
    </div>

    <fieldset class="arf-fieldset">
      <legend class="arf-label">依据 basis（必须是<strong>已发布</strong>的 PDF；上传件不能作依据）</legend>
      <div class="arf-grid">
        <label class="arf-field">
          <span class="arf-label">document_id</span>
          <input
            v-model="draft.document_id"
            class="arf-input arf-mono"
            type="text"
            :disabled="registering"
            data-testid="authority-basis-document-id"
          />
        </label>
        <label class="arf-field">
          <span class="arf-label">document_version</span>
          <input
            v-model="draft.document_version"
            class="arf-input"
            type="number"
            min="1"
            :disabled="registering"
            data-testid="authority-basis-document-version"
          />
        </label>
        <label class="arf-field">
          <span class="arf-label">source_sha256</span>
          <input
            v-model="draft.source_sha256"
            class="arf-input arf-mono"
            type="text"
            :disabled="registering"
            data-testid="authority-basis-source-sha"
          />
        </label>
        <label class="arf-field">
          <span class="arf-label">page_no</span>
          <input
            v-model="draft.page_no"
            class="arf-input"
            type="number"
            min="1"
            :disabled="registering"
            data-testid="authority-basis-page-no"
          />
        </label>
      </div>
      <p class="arf-help">
        预填来自候选的 basis_suggestion（报告侧事实的依据页）。服务端仍会重新校验它是不是
        <strong>已发布</strong>的 PDF 且版本/SHA-256/页码一致，否则 409
        <code>AUTHORITY_BASIS_INVALID</code>。
        <a
          v-if="basisLink"
          class="arf-link"
          :href="basisLink"
          target="_blank"
          rel="noopener"
          data-testid="authority-basis-link"
        >查看依据页</a>
        <span v-else class="arf-muted">（依据字段不完整，暂无可打开的页面）</span>
      </p>
    </fieldset>

    <label class="arf-field">
      <span class="arf-label">登记理由 rationale（10..1000 字，必填）</span>
      <textarea
        v-model="draft.rationale"
        class="arf-textarea"
        rows="3"
        maxlength="1000"
        :disabled="registering"
        placeholder="写清楚为什么以这一侧为准（依据页码、核对方式、范围限制）。"
        data-testid="authority-form-rationale"
      ></textarea>
      <span class="arf-help">
        {{ rationaleLength }} / 1000 · {{ rationaleHint }}
      </span>
    </label>

    <p class="arf-help">
      幂等键 request_id：<code>{{ draft.request_id }}</code>
      —— 同一次填写的重试沿用同一个 ID（避免网络超时重试登记出两条规则）；
      换候选会重新生成。
    </p>

    <p v-if="failure" class="arf-error" data-testid="authority-register-error">
      {{ failureText(failure) }}
    </p>

    <button
      class="arf-submit"
      type="submit"
      :disabled="!props.canRegister || props.registering"
      data-testid="authority-register-submit"
    >
      {{ props.registering ? '登记中…' : '登记规则（提交后仍是待审批）' }}
    </button>
    <p class="arf-help">
      登记成功不等于生效：规则要由<strong>另一个</strong>审批账户启用，运行时还会再核验一次版本、审批、
      依据与重叠规则。
    </p>
  </form>
</template>

<style scoped>
/* 类名统一 arf- 前缀，避免与 Tailwind v4 @layer utilities 生成的同名类互相接管。 */
.arf-form {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 16px;
}
.arf-timezone {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid rgba(217, 119, 6, 0.35);
  border-radius: 10px;
  background: rgba(217, 119, 6, 0.07);
  font-size: 12px;
  line-height: 1.75;
  color: var(--color-body);
}
.arf-form code {
  font-size: 11.5px;
  word-break: break-all;
}
.arf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px 14px;
}
.arf-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
  min-width: 0;
}
.arf-fieldset {
  border: 1px solid var(--color-line);
  border-radius: 10px;
  padding: 10px 12px 2px;
  margin: 0 0 12px;
}
.arf-label {
  font-size: 11.5px;
  color: var(--color-muted);
}
.arf-input,
.arf-textarea {
  font: inherit;
  font-size: 12.5px;
  padding: 6px 9px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  color: var(--color-text);
  min-width: 0;
}
.arf-input:read-only {
  color: var(--color-body);
  background: rgba(15, 23, 42, 0.03);
}
.arf-textarea {
  resize: vertical;
  line-height: 1.6;
}
.arf-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
}
.arf-help {
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-muted);
}
.arf-radios {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.arf-radio {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--color-body);
}
.arf-link {
  margin-left: 6px;
  color: var(--color-accent);
}
.arf-muted {
  margin-left: 6px;
  opacity: 0.8;
}
.arf-error {
  margin: 0 0 10px;
  font-size: 12.5px;
  color: #e11d48;
}
.arf-submit {
  font: inherit;
  font-size: 12.5px;
  padding: 8px 18px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.arf-submit:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.arf-submit:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
