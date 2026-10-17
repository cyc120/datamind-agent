<script setup lang="ts">
/**
 * 字段复核（文档事实的抽取保真度复核 + 独立审批）。
 *
 * WHY 这一屏存在：后端的 document_facts 里存着「原文这一段字 → 一条结构化事实」的
 * 映射，每条事实都带经过哈希校验的逐块定位（evidence_refs）。没有这一屏，用户只能
 * 相信抽取结果；有了这一屏，用户可以逐条打开原页、核对定位、留下实名复核，再由
 * 另一个人独立审批。
 *
 * 三条本屏必须守住的事实（每一条都在代码里有对应实现与注释）：
 *   (a) **不按 searchable 过滤文档列表**。工业演示文档 0 个 chunk（searchable=false），
 *       却装着全部 17 条工业事实——按可检索性过滤会把这一屏最该复核的数据藏起来。
 *       searchable / index_state / index_note 只作为**信息**显示，并配一句
 *       「审批 ≠ 可检索」。
 *   (b) 定位框只在证据引用真的可绘制时画（见 components/fields/locator.ts），否则只
 *       显示 `无法绘制：<原因>`——绝不猜位置。
 *   (c) reviewer_user_id 为 null 的复核是历史未认证记录，只显示为「历史未认证」，
 *       并且**不提供**审批按钮（服务端会以 409 CONFIRMED_AUTHENTICATED_REVIEW_REQUIRED
 *       拒绝）；历史未认证记录不补身份。
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SideNav from '../components/SideNav.vue'
import AnimatedList from '../components/bits/AnimatedList/AnimatedList.vue'
import FactReviewPanel from '../components/fields/FactReviewPanel.vue'
import LocatorOverlay from '../components/fields/LocatorOverlay.vue'
import { getDocumentFacts, listDocuments } from '../api/client'
import type {
  DocumentFact,
  DocumentFactsResult,
  DocumentSummary,
  FactEvidenceRef,
  FactFieldState,
} from '../api/types'
import {
  approvalSemantics,
  describeError,
  extractionScope,
  formalApprovalText,
  formalApprovalTone,
  indexNoteText,
  indexStateText,
  reviewDecisionText,
  unresolvedCount,
} from '../components/fields/messages'

const router = useRouter()

/* ── 文档列表 ─────────────────────────────────────────────────────────── */

const documents = ref<DocumentSummary[]>([])
const docsLoading = ref(false)
const docsError = ref('')
const selectedDocId = ref<string | null>(null)

/** 列表请求序号：连续点两次刷新时丢弃过期响应，避免旧结果盖掉新结果。 */
let docsSeq = 0

async function loadDocuments(): Promise<void> {
  const token = ++docsSeq
  docsLoading.value = true
  try {
    const result = await listDocuments()
    if (token !== docsSeq) return
    documents.value = Array.isArray(result?.documents) ? result.documents : []
    docsError.value = ''
  } catch (e) {
    if (token !== docsSeq) return
    documents.value = []
    docsError.value = describeError(e, '文档列表加载失败').text
  } finally {
    if (token === docsSeq) docsLoading.value = false
  }
}

/* ── 事实 ─────────────────────────────────────────────────────────────── */

const factsResult = ref<DocumentFactsResult | null>(null)
const factsLoading = ref(false)
const factsError = ref('')
const selectedFactId = ref<string | null>(null)
/** 版本已移动时的页面级提示（由 FactReviewPanel 的 stale 事件写入） */
const staleNotice = ref('')
/** 「定位原页」浮层：null = 关闭；数字 = 焦点引用下标 */
const overlayIndex = ref<number | null>(null)

let factsSeq = 0

const facts = computed<DocumentFact[]>(() =>
  Array.isArray(factsResult.value?.facts) ? (factsResult.value?.facts as DocumentFact[]) : [],
)

const selectedDocument = computed<DocumentSummary | null>(
  () => documents.value.find((d) => d.document_id === selectedDocId.value) ?? null,
)

const selectedFact = computed<DocumentFact | null>(
  () => facts.value.find((f) => f.fact_id === selectedFactId.value) ?? null,
)

const quality = computed(() => factsResult.value?.quality ?? null)
/** 「另有 N 处字段或表头未能确认」：认不出来时为 null（不说成 0）。 */
const unresolved = computed(() => unresolvedCount(quality.value))
const scopeNote = computed(() => extractionScope(quality.value))
const approvalSemanticsNote = computed(() => approvalSemantics(quality.value))

async function loadFacts(documentId: string): Promise<void> {
  const token = ++factsSeq
  factsLoading.value = true
  overlayIndex.value = null
  try {
    const result = await getDocumentFacts(documentId)
    if (token !== factsSeq) return
    factsResult.value = result
    factsError.value = ''
    // 选中的事实仍在新结果里就保留展开，否则收起（避免详情停在一条已不存在的事实上）
    if (selectedFactId.value && !(result.facts ?? []).some((f) => f.fact_id === selectedFactId.value)) {
      selectedFactId.value = null
    }
  } catch (e) {
    if (token !== factsSeq) return
    factsResult.value = null
    selectedFactId.value = null
    factsError.value = describeError(e, '文档事实加载失败').text
  } finally {
    if (token === factsSeq) factsLoading.value = false
  }
}

function selectDocument(documentId: string): void {
  if (selectedDocId.value === documentId) return
  selectedDocId.value = documentId
  factsResult.value = null
  selectedFactId.value = null
  factsError.value = ''
  staleNotice.value = ''
  void loadFacts(documentId)
}

function toggleFact(factId: string): void {
  if (selectedFactId.value === factId) {
    selectedFactId.value = null
    overlayIndex.value = null
    return
  }
  selectedFactId.value = factId
  overlayIndex.value = null
}

/**
 * 复核/审批成功后的字段投影：直接覆盖当前事实的三个状态字段。
 * 刻意**不**重读文档列表：审批只改变这一条事实的复核闸门，
 * 与文档的 searchable / index_state 无关（审批 ≠ 可检索）。
 */
function onFieldState(state: FactFieldState | null): void {
  const fact = selectedFact.value
  if (!fact || !state) return
  fact.latest_review = state.latest_review
  fact.latest_approval = state.latest_approval
  fact.formal_approval_status = state.formal_approval_status
}

/** 版本/状态已变：如实提示并重新加载事实，不做静默重试。 */
function onStale(message: string): void {
  staleNotice.value = message
  if (selectedDocId.value) void loadFacts(selectedDocId.value)
}

async function refresh(): Promise<void> {
  staleNotice.value = ''
  await loadDocuments()
  if (selectedDocId.value) {
    await loadFacts(selectedDocId.value)
  }
}

/* ── 显示工具 ─────────────────────────────────────────────────────────── */

/** 复核列的文案：历史未认证单独标出，绝不显示成一次正常复核。 */
function reviewCellText(fact: DocumentFact): string {
  const review = fact.latest_review
  if (!review) return '未复核'
  if (!review.reviewer_user_id) return '历史未认证'
  return reviewDecisionText(review.decision)
}

/** 引用自带的原文片段（index signature 是 unknown，必须显式收窄后再渲染）。 */
function refText(ref: FactEvidenceRef): string {
  return typeof ref.text === 'string' ? ref.text : ''
}

/** extra=forbid 的 payload 里可能有抽取条件块；只做原样展示，不解释。 */
function conditionsText(fact: DocumentFact): string {
  const value = fact.conditions
  if (value == null) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return ''
  }
}

function rawText(fact: DocumentFact): string {
  const value = fact.raw_value ?? '—'
  return fact.raw_unit ? `${value} ${fact.raw_unit}` : String(value)
}

function normalizedText(fact: DocumentFact): string {
  const unit = fact.normalized_unit ? ` ${fact.normalized_unit}` : ''
  if (fact.normalized_value != null && fact.normalized_value !== '') return `${fact.normalized_value}${unit}`
  const range = fact.normalized_range
  if (range) {
    const left = range.min_inclusive ? '[' : '('
    const right = range.max_inclusive ? ']' : ')'
    return `${left}${range.min}, ${range.max}${right}${unit}`
  }
  return '—'
}

/** 复核链尾 id：提交复核时的 previous_review_id（无记录则为 null）。 */
function latestReviewId(fact: DocumentFact): string {
  return fact.latest_review?.review_id ?? 'null'
}

onMounted(() => {
  void loadDocuments()
})

function backToChat(): void {
  void router.push('/app')
}
</script>

<template>
  <div class="mainpage">
    <SideNav @new-chat="backToChat" />

    <div class="center">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">字段复核</span>
          <span class="tb-note">
            逐条核对抽取是否忠实于原文；复核不覆盖原文声明、不裁定来源权威，审批 ≠ 可检索
          </span>
        </div>
        <button
          class="tb-action"
          type="button"
          :disabled="docsLoading || factsLoading"
          data-testid="fields-refresh"
          @click="refresh"
        >
          {{ docsLoading || factsLoading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="body fields-body">
        <!-- ── 左：文档列表 ── -->
        <section class="fields-docs" data-testid="fields-docs">
          <div class="fields-docs-head">
            <h3 class="fields-h3">文档</h3>
            <span v-if="documents.length" class="fields-count">{{ documents.length }} 份</span>
          </div>
          <!-- 原来是一整段连排、带"这里故意…"这种代码注释口吻，用户反馈太随意。
               改成分条：一条一个结论，蓝点起头，术语加粗。 -->
          <ul class="fields-note" data-testid="fields-searchable-note">
            <li>列表<b>不按可检索性过滤</b>：尚未建立索引的文档，同样可能装着待复核的事实。</li>
            <li><b>审批 ≠ 可检索</b>：审批结论不改变一份文档能否被检索器读到。</li>
          </ul>

          <p v-if="docsLoading" class="fields-hint">正在加载…</p>
          <p v-else-if="docsError" class="fields-error" data-testid="fields-docs-error">{{ docsError }}</p>
          <p v-else-if="!documents.length" class="fields-empty" data-testid="fields-docs-empty">
            没有读到任何已发布文档。若刚上传文件，需要先完成解析与复核才会出现在这里。
          </p>

          <ul v-else class="fields-doc-list" data-testid="fields-doc-list">
            <li v-for="doc in documents" :key="doc.document_id">
              <button
                class="fields-doc-row"
                type="button"
                :class="{ 'is-open': selectedDocId === doc.document_id }"
                :data-testid="`fields-doc-row-${doc.document_id}`"
                @click="selectDocument(doc.document_id)"
              >
                <span class="fields-doc-title" :title="doc.title">{{ doc.title }}</span>
                <span class="fields-doc-meta">
                  {{ doc.collection }} · {{ doc.file_type }}
                  <template v-if="doc.origin"> · {{ doc.origin }}</template>
                </span>
                <span class="fields-doc-meta">
                  v{{ doc.doc_version }}
                  <template v-if="doc.page_count != null"> · {{ doc.page_count }} 页</template>
                  <template v-if="doc.quality_score != null"> · 质量分 {{ doc.quality_score }}</template>
                </span>
                <span class="fields-doc-badges">
                  <span
                    class="fields-badge"
                    :class="doc.searchable ? 'is-ok' : 'is-warn'"
                    :data-testid="`fields-doc-searchable-${doc.document_id}`"
                  >
                    {{ doc.searchable ? '检索器读得到' : '检索不到' }}
                  </span>
                  <span
                    class="fields-badge is-muted"
                    :data-testid="`fields-doc-index-state-${doc.document_id}`"
                  >
                    {{ indexStateText(doc.index_state) }}
                  </span>
                </span>
                <span
                  v-if="indexNoteText(doc.index_note)"
                  class="fields-doc-note"
                  :data-testid="`fields-doc-index-note-${doc.document_id}`"
                >
                  {{ indexNoteText(doc.index_note) }}（{{ doc.index_note }}）
                </span>
              </button>
            </li>
          </ul>
        </section>

        <!-- ── 右：事实与复核 ── -->
        <section class="fields-main" data-testid="fields-main">
          <p v-if="staleNotice" class="fields-stale" data-testid="fields-stale-notice">
            {{ staleNotice }}
          </p>

          <!-- 空态原来是一句长跑句；改成「一句引导 + 一句说明」两行，读起来不费劲。 -->
          <div v-if="!selectedDocId" class="fields-empty fields-empty-big" data-testid="fields-no-doc">
            <p class="fields-empty-lead">从左侧选一份文档</p>
            <p class="fields-empty-sub">
              这里会列出它通过定位校验的全部事实；每条都能打开原页核对定位，并分别记录复核与审批。
            </p>
          </div>

          <template v-else>
            <!-- 文档身份与可检索性：信息，不是过滤条件 -->
            <div v-if="selectedDocument" class="fields-doc-head" data-testid="fields-doc-head">
              <h3 class="fields-h3" data-testid="fields-doc-title">{{ selectedDocument.title }}</h3>
              <p class="fields-meta">
                {{ selectedDocument.collection }} · {{ selectedDocument.file_type }}
                <template v-if="selectedDocument.origin"> · 来源 {{ selectedDocument.origin }}</template>
                · 版本 v{{ selectedDocument.doc_version }}
                <template v-if="selectedDocument.page_count != null">
                  · {{ selectedDocument.page_count }} 页
                </template>
                <template v-if="selectedDocument.quality_score != null">
                  · 质量分 {{ selectedDocument.quality_score }}
                </template>
              </p>
              <p class="fields-meta mono">
                sha256 {{ (factsResult?.source_sha256 || selectedDocument.sha256 || '').slice(0, 16) }}…
                · parser {{ factsResult?.parser_version || selectedDocument.parser_version || '—' }}
                · document_id {{ selectedDocument.document_id }}
              </p>
            </div>

            <AnimatedList tag="ul" class="fields-honesty" data-testid="fields-honesty">
              <li>复核只针对提取忠实度；不覆盖原文声明、不裁定来源权威。</li>
              <li>审批 ≠ 可检索：审批不改变检索器读不读得到这份文档。</li>
              <li>历史未认证记录（reviewer_user_id 为空）不补身份，也不能作为审批依据。</li>
            </AnimatedList>

            <p v-if="factsLoading" class="fields-hint">正在读取事实…</p>
            <p v-else-if="factsError" class="fields-error" data-testid="fields-facts-error">{{ factsError }}</p>

            <template v-else-if="factsResult">
              <!-- 抽取质量：有缺口必须说，没缺口也不许说成「全部核验通过」 -->
              <AnimatedList tag="div" class="fields-quality" data-testid="fields-quality">
                <div class="fields-quality-row">
                  <span class="fields-quality-key">已定位事实</span>
                  <span class="fields-quality-val">{{ factsResult.fact_count }} 条</span>
                </div>
                <div v-if="quality?.extraction_version" class="fields-quality-row">
                  <span class="fields-quality-key">抽取版本</span>
                  <span class="fields-quality-val mono">{{ quality.extraction_version }}</span>
                </div>
                <p
                  v-if="unresolved != null && unresolved > 0"
                  class="fields-quality-gap"
                  data-testid="fields-quality-unresolved"
                >
                  另有 {{ unresolved }} 处字段或表头未能确认：它们没有被抽取成事实，也没有定位可核对。
                </p>
                <p v-else-if="unresolved === 0" class="fields-quality-ok" data-testid="fields-quality-unresolved">
                  抽取侧报告没有未确认的字段或表头（这仍不等于内容正确）。
                </p>
                <p v-else class="fields-quality-unknown" data-testid="fields-quality-unresolved">
                  抽取侧没有给出「未确认字段」的数量，所以无法判断有没有漏抽。
                  没有计数不等于没有缺口。
                </p>
                <p v-if="scopeNote" class="fields-quality-scope" data-testid="fields-quality-scope">
                  抽取口径（服务端原文）：{{ scopeNote }}
                </p>
                <p v-if="approvalSemanticsNote" class="fields-quality-scope">
                  抽取侧的审批口径（服务端原文）：{{ approvalSemanticsNote }}
                </p>
              </AnimatedList>

              <ul v-if="!facts.length" class="fields-note" data-testid="fields-facts-empty">
                <li>这份文档没有通过定位校验的事实。</li>
                <li>这不等于「原文没有内容」：可能是抽取没有产出事实。</li>
                <li>也可能是定位校验没通过 —— 服务端只返回哈希与页框都对得上的事实。</li>
              </ul>

              <template v-else>
                <table class="fields-table" data-testid="fields-fact-table">
                  <thead>
                    <tr>
                      <th>实体</th>
                      <th>指标 / 表头</th>
                      <th>原始值</th>
                      <th>归一值</th>
                      <th>工序 / 批次</th>
                      <th>抽取</th>
                      <th>复核</th>
                      <th>正式审批</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="fact in facts"
                      :key="fact.fact_id"
                      class="fields-fact-row"
                      :class="{ 'is-open': selectedFactId === fact.fact_id }"
                      :data-testid="`fields-fact-row-${fact.fact_id}`"
                      tabindex="0"
                      role="button"
                      @click="toggleFact(fact.fact_id)"
                      @keydown.enter.prevent="toggleFact(fact.fact_id)"
                      @keydown.space.prevent="toggleFact(fact.fact_id)"
                    >
                      <td>
                        {{ fact.entity_name || fact.entity_id || '—' }}
                        <span v-if="fact.entity_type" class="fields-cell-sub">{{ fact.entity_type }}</span>
                      </td>
                      <td>
                        {{ fact.metric || fact.raw_header || '—' }}
                        <span v-if="fact.fact_kind" class="fields-cell-sub">{{ fact.fact_kind }}</span>
                      </td>
                      <td class="mono">{{ rawText(fact) }}</td>
                      <td class="mono">{{ normalizedText(fact) }}</td>
                      <td>
                        {{ fact.operation_no || '—' }}
                        <span v-if="fact.batch_id" class="fields-cell-sub">{{ fact.batch_id }}</span>
                      </td>
                      <td>{{ fact.verification_status || '—' }}</td>
                      <td :class="{ 'is-legacy-cell': fact.latest_review && !fact.latest_review.reviewer_user_id }">
                        {{ reviewCellText(fact) }}
                      </td>
                      <td>
                        <span class="fields-badge" :class="`tone-${formalApprovalTone(fact.formal_approval_status)}`">
                          {{ formalApprovalText(fact.formal_approval_status) }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>

                <!-- ── 详情：证据引用 + 定位原页 + 复核/审批 ── -->
                <section v-if="selectedFact" class="fields-detail" data-testid="fields-fact-panel">
                  <header class="fields-detail-head">
                    <h4 class="fields-h4">
                      {{ selectedFact.entity_name || selectedFact.entity_id || '未命名实体' }} ·
                      {{ selectedFact.metric || selectedFact.raw_header || '未命名指标' }}
                    </h4>
                    <span class="fields-detail-id mono">{{ selectedFact.fact_id }}</span>
                    <button
                      class="fields-link"
                      type="button"
                      data-testid="fields-close-detail"
                      @click="toggleFact(selectedFact.fact_id)"
                    >
                      收起
                    </button>
                  </header>

                  <div class="fields-detail-grid">
                    <div class="fields-kv">
                      <span class="fields-k">原始值</span>
                      <span class="fields-v mono">{{ rawText(selectedFact) }}</span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">归一值</span>
                      <span class="fields-v mono">{{ normalizedText(selectedFact) }}</span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">方法 / 条件</span>
                      <span class="fields-v">
                        {{ selectedFact.method || '—' }}
                        <template v-if="conditionsText(selectedFact)">
                          · {{ conditionsText(selectedFact) }}
                        </template>
                      </span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">版次 / 有效区间</span>
                      <span class="fields-v">
                        {{ selectedFact.source_revision || '—' }} ·
                        {{ selectedFact.valid_from || '未声明' }} ~ {{ selectedFact.valid_to || '未声明' }}
                      </span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">源文件自述审批状态</span>
                      <span class="fields-v">
                        {{ selectedFact.approval_status || '未声明' }}
                        <span class="fields-cell-sub">（原文自己写的，不是本系统结论）</span>
                      </span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">抽取版本 / 载荷哈希</span>
                      <span class="fields-v mono">
                        {{ selectedFact.extraction_version }} ·
                        {{ (selectedFact.payload_sha256 || '').slice(0, 16) }}…
                      </span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">定位版本（四项一起校验）</span>
                      <span class="fields-v mono">
                        v{{ selectedFact.document_version }} · sha
                        {{ (selectedFact.source_sha256 || '').slice(0, 12) }}… · parser
                        {{ selectedFact.parser_version }} · payload
                        {{ (selectedFact.payload_sha256 || '').slice(0, 12) }}…
                      </span>
                    </div>
                    <div class="fields-kv">
                      <span class="fields-k">当前复核链尾</span>
                      <span class="fields-v mono">{{ latestReviewId(selectedFact) }}</span>
                    </div>
                  </div>

                  <!-- 证据引用：每条都能单独「定位原页」 -->
                  <h5 class="fields-h5">证据引用（{{ (selectedFact.evidence_refs || []).length }} 条）</h5>
                  <p
                    v-if="!(selectedFact.evidence_refs || []).length"
                    class="fields-empty"
                    data-testid="fields-refs-empty"
                  >
                    这条事实没有证据引用：无法核对它引自原文哪一段，请不要把它当作已核实。
                  </p>
                  <ul v-else class="fields-refs" data-testid="fields-evidence-refs">
                    <li
                      v-for="(ref, index) in selectedFact.evidence_refs"
                      :key="`${ref.block_id || index}-${index}`"
                      class="fields-ref"
                      :data-testid="`fields-evidence-ref-${index}`"
                    >
                      <div class="fields-ref-head">
                        <span class="fields-ref-role">{{ ref.role || '未声明角色' }}</span>
                        <span class="fields-ref-field">{{ ref.field || '—' }}</span>
                        <span v-if="ref.field_value" class="fields-ref-value">= {{ ref.field_value }}</span>
                        <button
                          class="fields-link"
                          type="button"
                          :data-testid="`fields-locate-${index}`"
                          @click="overlayIndex = index"
                        >
                          定位原页
                        </button>
                      </div>
                      <p v-if="refText(ref)" class="fields-ref-text">{{ refText(ref) }}</p>
                      <p class="fields-ref-meta mono">
                        第 {{ ref.page_no ?? '?' }} 页 ·
                        框选坐标 {{ Array.isArray(ref.bbox) ? ref.bbox.join(', ') : '缺失' }} ·
                        页面尺寸 {{ Array.isArray(ref.page_size) ? ref.page_size.join('×') : '缺失' }} ·
                        {{ ref.coordinate_system || '坐标系未声明' }} ·
                        块 {{ ref.block_id || '—' }}
                      </p>
                    </li>
                  </ul>

                  <FactReviewPanel
                    :document-id="selectedDocId || ''"
                    :fact="selectedFact"
                    @updated="onFieldState"
                    @stale="onStale"
                  />
                </section>
              </template>
            </template>
          </template>
        </section>
      </div>
    </div>

    <!-- 定位原页浮层：判定通过的引用才画框，否则只出标签 -->
    <LocatorOverlay
      v-if="overlayIndex !== null && selectedFact"
      :document-id="selectedDocId || ''"
      :refs="selectedFact.evidence_refs || []"
      :focus-index="overlayIndex"
      @close="overlayIndex = null"
    />
  </div>
</template>

<style scoped>
/* 类名统一 fields- 前缀（外加从 RunsPage 抄来的外壳类名 mainpage/center/topbar/body）：
   Tailwind v4 的 @layer utilities 会生成同名工具类，撞上就会出现「只生成部分属性」
   的怪象——仓库里已经出过两次（.collapse、.result-caption / table-caption）。 */
.mainpage {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--color-void);
}
.center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 28px;
  border-bottom: 1px solid var(--color-line);
}
.tb-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}
/* 标题与「模型接入」页一致：蓝色、15px、600（原来是 16px 灰黑，属离群值） */
.tb-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.tb-note {
  font-size: 14px;
  color: var(--color-text);
}
/* 顶栏按钮统一蓝底白字加粗椭圆（原来是白底细描边） */
.tb-action {
  margin-left: auto;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
}
.tb-action:hover:not(:disabled) {
  background: #1d4ed8;
  color: #ffffff;
}
.tb-action:disabled {
  opacity: 0.6;
  cursor: default;
}
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 28px 40px;
  max-width: 1080px;
  width: 100%;
}
/* 本屏是「左列表 + 右详情」两栏：滚动分别交给两栏，外壳不再整体滚动 */
.fields-body {
  display: flex;
  gap: 18px;
  max-width: none;
  overflow: hidden;
  padding: 16px 24px 18px;
}
.fields-docs {
  flex-shrink: 0;
  width: 306px;
  min-width: 0;
  overflow-y: auto;
  padding-right: 4px;
}
.fields-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding-right: 4px;
}
.fields-docs-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
/* 三级标题统一：蓝色、15px、600（原来是 15 / 14.5 / 13.5 三种灰黑字号） */
.fields-h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.fields-h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.fields-h5 {
  margin: 14px 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.fields-count {
  font-size: 13px;
  color: var(--color-muted);
}
/* 说明改成按条列出：每行一个结论，前面一个蓝点（原来是一整段连排）。 */
.fields-note {
  margin: 8px 0 10px;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--color-text);
}
.fields-note > li {
  position: relative;
  padding-left: 13px;
}
.fields-note > li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.66em;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-accent);
}
.fields-note b {
  font-weight: 600;
  color: var(--color-accent);
}
.fields-doc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fields-doc-row {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  text-align: left;
  font: inherit;
  padding: 9px 11px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.fields-doc-row:hover {
  border-color: rgba(37, 99, 235, 0.4);
}
.fields-doc-row.is-open {
  border-color: var(--color-accent);
  background: rgba(37, 99, 235, 0.06);
}
.fields-doc-title {
  font-size: 14px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fields-doc-meta {
  font-size: 12.5px;
  color: var(--color-muted);
}
.fields-doc-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 2px;
}
.fields-doc-note {
  font-size: 12.5px;
  line-height: 1.5;
  color: #d97706;
}
/* 徽标统一蓝底白字加粗椭圆（原来是白底描边） */
.fields-badge {
  font-size: 12.5px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  white-space: nowrap;
}
.fields-badge.is-ok {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
/* 警示徽标：橙底白字无边框（原来是浅橙底 + 橙字 + 描边）。橙色作为「需注意」
   的语义保留，只把浅底改成实心，与蓝(正常)/红(异常)药丸统一成实心圆角。 */
.fields-badge.is-warn {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: #d97706;
}
/* is-muted / tone-muted 现在与基础样式等价（保留类名，避免动模板） */
.fields-badge.is-muted {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.fields-badge.tone-ok {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.fields-badge.tone-warn {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: #d97706;
}
.fields-badge.tone-bad {
  color: #ffffff; font-weight: 600;
  border-color: transparent;
  background: #e11d48;
}
.fields-badge.tone-muted {
  color: #ffffff;
  font-weight: 600;
  border-color: transparent;
  background: var(--color-accent);
}
.fields-doc-head {
  margin-bottom: 10px;
}
.fields-meta {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text);
  word-break: break-all;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
/* 去框：改成左侧 2px 蓝细线分组，不加底色、不加圆角。与权威页、图纸页、尺寸页的说明块同一套处理。这个块外面包了 Vue Bits 的 AnimatedList，三条逐条淡入。 */
.fields-honesty {
  margin: 0 0 22px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid #d97706;
  border-radius: 0;
  background: none;
  font-size: 13px;
  line-height: 1.85;
  color: var(--color-text);
}
/* 同上：抽取侧报告那块也去框，改左侧蓝细线 + 逐条淡入。 */
.fields-quality {
  margin-bottom: 24px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid var(--color-accent);
  border-radius: 0;
  background: none;
}
.fields-quality-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 3px;
}
.fields-quality-key {
  flex-shrink: 0;
  width: 72px;
  font-size: 13px;
  color: var(--color-muted);
}
.fields-quality-val {
  font-size: 13.5px;
  color: var(--color-text);
}
.fields-quality-gap {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #d97706;
}
.fields-quality-ok,
.fields-quality-unknown,
.fields-quality-scope {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text);
  word-break: break-word;
}
.fields-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}
.fields-table th {
  text-align: left;
  font-weight: 600;
  color: var(--color-muted);
  font-size: 12.5px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-line);
  white-space: nowrap;
}
.fields-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--color-line);
  color: var(--color-body);
  vertical-align: top;
}
.fields-fact-row {
  cursor: pointer;
}
.fields-fact-row:hover {
  background: rgba(37, 99, 235, 0.04);
}
.fields-fact-row.is-open {
  background: rgba(37, 99, 235, 0.07);
}
/* 行是 <tr>（不是按钮），键盘可达就必须自己给出可见焦点环 */
.fields-fact-row:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.45);
  outline-offset: -2px;
}
.fields-cell-sub {
  display: block;
  font-size: 12.5px;
  color: var(--color-text);
}
.is-legacy-cell {
  color: #d97706;
}
.fields-detail {
  margin-top: 14px;
  padding: 14px 16px;
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-ink);
}
.fields-detail-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}
.fields-detail-id {
  font-size: 12.5px;
  color: var(--color-muted);
}
.fields-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 18px;
}
.fields-kv {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.fields-k {
  flex-shrink: 0;
  width: 128px;
  font-size: 12.5px;
  color: var(--color-muted);
}
.fields-v {
  font-size: 13.5px;
  color: var(--color-text);
  word-break: break-all;
}
.fields-refs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fields-ref {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 8px 10px;
}
.fields-ref-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
}
.fields-ref-role {
  font-size: 13.5px;
  font-weight: 600;
  color: #2563eb;
}
.fields-ref-field {
  font-size: 13px;
  color: var(--color-muted);
}
.fields-ref-value {
  font-size: 13.5px;
  color: var(--color-text);
}
.fields-ref-text {
  margin: 5px 0 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--color-body);
  white-space: pre-wrap;
}
.fields-ref-meta {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-text);
  word-break: break-all;
}
/* 「定位到原文 →」：与其它按钮统一成蓝底白字椭圆（原来是白底描边、蓝字） */
.fields-link {
  margin-left: auto;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 3px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: var(--color-accent);
  color: #ffffff;
  cursor: pointer;
  white-space: nowrap;
}
.fields-link:hover {
  background: #1d4ed8;
  color: #ffffff;
}
.fields-hint,
.fields-empty {
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--color-text);
}
.fields-empty {
  padding: 10px 0;
}
.fields-empty-big {
  padding: 28px 0;
  max-width: 560px;
}
/* 空态的两行：第一行是把人叫过来的动作，第二行才是说明。字号与颜色拉开层次。 */
.fields-empty-lead {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-accent);
}
.fields-empty-sub {
  margin: 6px 0 0;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--color-text);
}
.fields-error {
  font-size: 13.5px;
  line-height: 1.75;
  color: #e11d48;
  padding: 10px 0;
}
/* 去框：改左侧 2px 橙细条 + 黑色正文。原来这里是「描边 + 圆角 + 浅底」的卡片，与全站其它提示块不统一；橙作为语义保留在细线上。 */
.fields-stale {
  margin: 0 0 18px;
  padding: 2px 0 2px 16px;
  border: 0;
  border-left: 2px solid #d97706;
  border-radius: 0;
  background: none;
  color: var(--color-text);
}
@media (max-width: 980px) {
  .fields-body {
    flex-direction: column;
    overflow-y: auto;
  }
  .fields-docs,
  .fields-main {
    width: auto;
    overflow: visible;
  }
  .fields-detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
