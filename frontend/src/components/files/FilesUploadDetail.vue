<script setup lang="ts">
import { computed } from 'vue'

import type { UploadDetail } from '../../api/types'
import { RETRY_HONESTY_TEXT, formatWhen, statusLabel } from './uploadHonesty'

/**
 * 一条上传的**详情**块（纯展示）。
 *
 * WHY 独立组件：详情字段多且都是「契约证据」——analysis / locator_stores / contract_url /
 * error_code / attempt——列表行只需要状态与索引三态。拆开后列表行不会因为详情渲染逻辑
 * 变长，详情也能单独被复核（同样的字段名、同样的缺口呈现）。
 *
 * 三条不许含糊的呈现规则：
 *   1. `unsupported_features` 渲染成「缺口：<codes>」。空数组**不等于**「格式全支持」，
 *      只说明解析器没有声明缺口，所以空的时候必须把这句话说出来；
 *   2. `contract_url` 只有在以 `/` 开头时才允许变成 href —— 绝对地址（或畸形值）一律
 *      以纯文本 + 警告显示，绝不把外链/畸形 URL 渲染成可点链接；
 *   3. `attempt` 与重试说明必须一起出现：重试是**从头解析整个文件**，没有断点/检查点。
 */
const props = defineProps<{
  detail: UploadDetail | null
  loading: boolean
  error: string
}>()

const analysis = computed(() => props.detail?.analysis ?? null)

/** analysis 里「有就显示」的契约元信息：后端不同适配器给的键不一样，缺了不编。 */
const ANALYSIS_EXTRA_LABELS: Record<string, string> = {
  contract_version: '契约版本（contract_version）',
  pages: '页数（pages）',
  layers: '图层（layers）',
}

const analysisExtras = computed(() => {
  const raw = (analysis.value ?? {}) as Record<string, unknown>
  return Object.entries(ANALYSIS_EXTRA_LABELS)
    .filter(([key]) => raw[key] !== undefined && raw[key] !== null && raw[key] !== '')
    .map(([key, label]) => ({ key, label, value: String(raw[key]) }))
})

/** block_types 是 Record<string, number>：按 key 排序，保证同一份数据每次渲染一致。 */
const blockTypes = computed(() => {
  const raw = analysis.value?.block_types
  if (!raw || typeof raw !== 'object') return []
  return Object.entries(raw as Record<string, number>)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => ({ key, value }))
})

const gaps = computed(() => {
  const fromDetail = Array.isArray(props.detail?.unsupported_features)
    ? props.detail!.unsupported_features
    : []
  const fromAnalysis = Array.isArray(analysis.value?.unsupported_features)
    ? analysis.value!.unsupported_features
    : []
  return Array.from(new Set([...fromDetail, ...fromAnalysis]))
})

const qualityFindings = computed(() =>
  Array.isArray(analysis.value?.quality_findings) ? analysis.value!.quality_findings : [],
)

const vectorIndex = computed(() => props.detail?.vector_index ?? '')

/** 契约链接：只认站内相对路径。 */
const contractHref = computed(() => {
  const url = props.detail?.contract_url
  if (typeof url !== 'string' || !url) return ''
  return url.startsWith('/') ? url : ''
})

const contractRaw = computed(() =>
  typeof props.detail?.contract_url === 'string' ? (props.detail!.contract_url as string) : '',
)

const approval = computed(() => props.detail?.approval ?? null)
</script>

<template>
  <section class="fud-detail" data-testid="files-detail">
    <p v-if="loading" class="fud-hint" data-testid="files-detail-loading">正在读取上传详情…</p>
    <p v-else-if="error" class="fud-error" data-testid="files-detail-error">{{ error }}</p>
    <p v-else-if="!detail" class="fud-hint">这条记录还没有读到详情。</p>

    <template v-else>
      <!-- ① 身份/来源：都是接口原值，缺失就写「未给出」 -->
      <div class="fud-grid">
        <div class="fud-row">
          <span class="fud-k">upload_id</span><code class="fud-v">{{ detail.upload_id }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">job_id</span><code class="fud-v">{{ detail.job_id }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">document_id</span><code class="fud-v">{{ detail.document_id }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">media_type</span>
          <code class="fud-v">{{ detail.media_type || '未给出' }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">provenance</span>
          <code class="fud-v">{{ detail.provenance || '未给出' }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">source_sha256</span>
          <code class="fud-v">{{ detail.source_sha256 || '未给出' }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">parser_version</span>
          <code class="fud-v">{{ detail.parser_version || '未给出（尚未解析）' }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">collection</span><code class="fud-v">{{ detail.collection }}</code>
        </div>
        <div class="fud-row">
          <span class="fud-k">page_count</span>
          <span class="fud-v">{{ detail.page_count == null ? '未给出' : detail.page_count }}</span>
        </div>
        <div class="fud-row">
          <span class="fud-k">status</span>
          <span class="fud-v" data-testid="files-detail-status">
            {{ statusLabel(detail.status) }}（{{ detail.status }}）
          </span>
        </div>
        <div class="fud-row">
          <span class="fud-k">created_at</span>
          <span class="fud-v">{{ formatWhen(detail.created_at) || '未给出' }}</span>
        </div>
        <div class="fud-row">
          <span class="fud-k">started_at</span>
          <span class="fud-v">{{ formatWhen(detail.started_at) || '未开始（排队中）' }}</span>
        </div>
        <div class="fud-row">
          <span class="fud-k">finished_at</span>
          <span class="fud-v">{{ formatWhen(detail.finished_at) || '未结束' }}</span>
        </div>
      </div>

      <!-- ② 解析分析：只在真的解析过之后才有内容 -->
      <div class="fud-block" data-testid="files-detail-analysis">
        <div class="fud-bt">解析分析（analysis）</div>
        <div class="fud-grid">
          <div class="fud-row">
            <span class="fud-k">parse_status</span>
            <code class="fud-v">{{ analysis?.parse_status || '未给出' }}</code>
          </div>
          <div class="fud-row">
            <span class="fud-k">adapter</span>
            <code class="fud-v">{{ analysis?.adapter || '未给出' }}</code>
          </div>
          <div class="fud-row">
            <span class="fud-k">blocks</span>
            <span class="fud-v">{{ analysis?.blocks ?? 0 }}</span>
          </div>
          <div class="fud-row">
            <span class="fud-k">locators_written</span>
            <span class="fud-v">{{ analysis?.locators_written ?? 0 }}</span>
          </div>
          <div class="fud-row">
            <span class="fud-k">relations</span>
            <span class="fud-v">{{ analysis?.relations ?? 0 }}</span>
          </div>
          <div v-for="extra in analysisExtras" :key="extra.key" class="fud-row">
            <span class="fud-k">{{ extra.label }}</span>
            <code class="fud-v">{{ extra.value }}</code>
          </div>
        </div>

        <p class="fud-line">
          <span class="fud-k2">块类型（block_types）</span>
          <span v-if="blockTypes.length" class="fud-chips">
            <code v-for="bt in blockTypes" :key="bt.key" class="fud-chip">{{ bt.key }}×{{ bt.value }}</code>
          </span>
          <span v-else class="fud-v">未给出（解析未产出块）</span>
        </p>

        <!-- 缺口：空数组 ≠ 全支持 -->
        <p class="fud-line" data-testid="files-detail-gaps">
          <span class="fud-k2">缺口</span>
          <span v-if="gaps.length" class="fud-chips">
            <code v-for="g in gaps" :key="g" class="fud-chip fud-chip-warn">缺口：{{ g }}</code>
          </span>
          <span v-else class="fud-v">缺口：无（未声明缺口，不等于格式全支持）</span>
        </p>

        <p class="fud-line" data-testid="files-detail-quality">
          <span class="fud-k2">质量发现（quality_findings）</span>
          <span v-if="qualityFindings.length" class="fud-v">{{ qualityFindings.join('；') }}</span>
          <span v-else class="fud-v">无（未报告发现，不代表解析没有质量问题）</span>
        </p>
      </div>

      <!-- ③ 失败/重试与定位器 -->
      <div class="fud-block" data-testid="files-detail-failure">
        <div class="fud-row">
          <span class="fud-k">error_code</span>
          <code class="fud-v" :class="{ 'is-bad': !!detail.error_code }">
            {{ detail.error_code || '无' }}
          </code>
        </div>
        <div class="fud-row">
          <span class="fud-k">attempt</span>
          <span class="fud-v">被领取 {{ detail.attempt }} 次</span>
        </div>
        <p class="fud-note">{{ RETRY_HONESTY_TEXT }}</p>
        <div class="fud-row">
          <span class="fud-k">wave</span>
          <span class="fud-v" data-testid="files-detail-wave">
            单波时限 {{ detail.wave_deadline_seconds }}s · 已超时 {{ detail.wave_timeouts }} 次
          </span>
        </div>
        <div class="fud-row">
          <span class="fud-k">locator_stores</span>
          <span class="fud-v" data-testid="files-detail-locators">
            契约块 {{ detail.locator_stores?.contract_blocks ?? 0 }} · 页式
            {{ detail.locator_stores?.page_shaped ?? 0 }}
          </span>
        </div>
        <div class="fud-row">
          <span class="fud-k">vector_index</span>
          <span class="fud-v">
            <code>{{ vectorIndex || '未给出' }}</code>
            <template v-if="vectorIndex === 'deferred'">
              ——本管道不写 chunks/Qdrant 行，未建立向量索引（索引由离线路径完成）。
            </template>
            <template v-else-if="vectorIndex">
              ——非预期取值（本管道应为 deferred），这里按响应原样显示。
            </template>
          </span>
        </div>
        <p v-if="detail.vector_index_note" class="fud-note">{{ detail.vector_index_note }}</p>

        <!-- 契约链接：只接受以 / 开头的站内相对路径 -->
        <p class="fud-line">
          <span class="fud-k2">解析契约</span>
          <a
            v-if="contractHref"
            class="fud-link"
            :href="contractHref"
            target="_blank"
            rel="noopener noreferrer"
            data-testid="files-contract-link"
          >
            查看解析契约（contract_url）
          </a>
          <span v-else class="fud-v">
            链接不可用：contract_url 不是以 / 开头的站内路径，已按纯文本显示（原值：{{
              contractRaw || '未给出'
            }}）
          </span>
        </p>
      </div>

      <!-- ④ 复核记录 -->
      <div class="fud-block" data-testid="files-detail-approval">
        <div class="fud-bt">复核记录（approval）</div>
        <template v-if="approval">
          <div class="fud-grid">
            <div class="fud-row">
              <span class="fud-k">复核人</span>
              <span class="fud-v">{{ approval.display_name || approval.user_id || '未给出' }}</span>
            </div>
            <div class="fud-row">
              <span class="fud-k">复核时间</span>
              <span class="fud-v">{{ formatWhen(approval.approved_at) || '未给出' }}</span>
            </div>
            <div class="fud-row">
              <span class="fud-k">索引结果</span>
              <span class="fud-v">
                indexed={{ approval.indexed === true ? 'true' : approval.indexed === false ? 'false' : '未给出' }}
                · 契约块 {{ approval.block_count ?? 0 }} · chunks {{ approval.chunk_count ?? 0 }}
              </span>
            </div>
          </div>
          <p v-if="approval.reason" class="fud-note">复核说明：{{ approval.reason }}</p>
        </template>
        <p v-else class="fud-v">尚未复核。</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 类名统一 fud- 前缀：Tailwind v4 的 @layer utilities 会生成同名工具类，
   撞上就会出现「只声明了部分属性、其余被工具类接管」的怪象（仓库已出过两次事故）。 */
.fud-detail {
  margin-top: 8px;
  padding: 12px 14px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
}
.fud-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 4px 18px;
}
.fud-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.fud-k {
  flex-shrink: 0;
  width: 132px;
  font-size: 11.5px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.fud-v {
  font-size: 12px;
  color: var(--color-text);
  word-break: break-all;
  line-height: 1.7;
}
.fud-v.is-bad {
  color: #e11d48;
}
.fud-block {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--color-line);
}
.fud-bt {
  font-size: 11.5px;
  letter-spacing: 0.04em;
  color: var(--color-muted);
  margin-bottom: 6px;
}
.fud-line {
  margin: 6px 0 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.fud-k2 {
  flex-shrink: 0;
  font-size: 11.5px;
  color: var(--color-muted);
}
.fud-chips {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}
.fud-chip {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 6px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
}
.fud-chip-warn {
  border-color: rgba(217, 119, 6, 0.35);
  color: #d97706;
  background: rgba(217, 119, 6, 0.08);
}
.fud-note {
  margin: 8px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-muted);
}
.fud-link {
  font-size: 12px;
  color: var(--color-accent);
}
.fud-hint {
  margin: 0;
  font-size: 12px;
  color: var(--color-muted);
}
.fud-error {
  margin: 0;
  font-size: 12px;
  color: #e11d48;
  word-break: break-all;
}
</style>
