<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ModelProfile, ModelProfileInput } from '../../api/types'
import {
  AUTH_STYLE_OPTIONS,
  HAS_KEY_NOTE,
  MODEL_NAME_REQUIRED,
  NO_CLEAR_KEY_NOTE,
  PLAIN_HEADERS_NOTE,
  PROXY_NOTE,
  PROVIDER_PRESETS,
  SECRET_HEADERS_NOTE,
  authStyleUi,
  authStyleWire,
  protocolLabel,
  type AuthStyleUi,
  type ProviderPreset,
} from './messages'

/**
 * 模型接入 —— 右栏编辑表单（新建 / 编辑共用一份）。
 *
 * ═══ 四个密钥规则（本组件的存在理由，改错任何一条都会毁掉用户真实密钥）═══
 *
 * (a) 绝不把 key_masked 放进 api_key 输入框的 value。
 *     输入框永远从空串开始（load() 里 `apiKey.value = ''`），掩码只出现在
 *     placeholder 里（「已保存 xxx，留空表示不修改」）。服务端**没有**任何防护：
 *     把掩码回填并提交，就会把真实密钥永久覆盖成 "sk-abc…wxyz" 这种字符串。
 *
 * (b) 空 api_key = 保持原密钥不变（服务端会 pop 掉空值）。
 *     所以 buildPayload 只在用户真的敲了字符时才带上 api_key 字段；
 *     绝不发送空串（空串与"不修改"在服务端是同一个结果，但显式省略更清楚）。
 *
 * (c) __clear__ 只对**敏感请求头**与**代理**有效，绝不是 api_key 的哨兵。
 *     本表单没有任何「清空密钥」入口：把 "__clear__" 当 api_key 提交，服务端
 *     会把它当普通值存下来（字面量 "__clear__" 变成密钥）。
 *
 * (d) 敏感请求头读取侧只回显**键名**（header_keys），值永远不下发。
 *     所以敏感头每一行都是空值框（留空 = 保持不变），删除必须显式点「删除」，
 *     提交该行的 __clear__ 哨兵。而非敏感头必须**整表提交**：服务端对提交字典里的
 *     非敏感键以提交内容为权威，只发被编辑的那几行会把其余的头全部删掉。
 *
 * 另外：模型名是硬性必填（见 MODEL_NAME_REQUIRED 的注释）。「获取模型列表」例外
 * ——它的用途就是帮你发现模型名，要求先填模型名会变成死循环。
 */

const props = defineProps<{
  /** 服务端下发的可选协议清单（GET /v1/model/profiles -> protocols） */
  protocols: { id: string; label: string }[]
  saving: boolean
}>()

const emit = defineEmits<{
  (e: 'save', payload: ModelProfileInput, activate: boolean): void
  (e: 'probe', payload: ModelProfileInput): void
  (e: 'fetch-models', payload: ModelProfileInput): void
  (e: 'invalid', message: string): void
}>()

/** 非敏感请求头：键值都可见可编辑 */
interface PlainHeaderRow {
  name: string
  value: string
}
/** 敏感请求头：只知道键名；value 为空 = 保持不变，clear = 提交 __clear__ 删除 */
interface SecretHeaderRow {
  name: string
  value: string
  clear: boolean
}

/** 删除哨兵（与服务端 settings_store.CLEAR_SENTINEL 一致） */
const CLEAR_SENTINEL = '__clear__'

/** 请求体用途：不同用途的必填项不同（见 buildPayload 的注释）。 */
type PayloadMode = 'save' | 'probe' | 'models'

const editingId = ref<string | null>(null)
/** 仅用于 placeholder 的掩码；**永远不进输入框的 value**（规则 a） */
const loadedKeyMasked = ref('')
const name = ref('')
const protocol = ref('openai')
const baseUrl = ref('')
const model = ref('')
const apiKey = ref('')
const authStyle = ref<AuthStyleUi>('auto')
const maxTokens = ref<string | number>('')
const timeoutS = ref<string | number>('')
const proxy = ref('')
/** 服务端下发的（可能已去 userinfo 的）代理地址：用来判断"用户是否改动过" */
const proxyDisplayed = ref('')
const proxyHasCredentials = ref(false)
const proxyClear = ref(false)
const riskAccepted = ref(false)
const note = ref('')
const plainRows = ref<PlainHeaderRow[]>([])
const secretRows = ref<SecretHeaderRow[]>([])
const missingModel = ref(false)
const formError = ref('')
const presetNotice = ref('')

const isEditing = computed(() => !!editingId.value)
const isAnthropic = computed(() => protocol.value === 'anthropic')

/** 规则 (a)：掩码只出现在 placeholder；输入框的值永远是空串或用户新输入。 */
const apiKeyPlaceholder = computed(() =>
  loadedKeyMasked.value
    ? `已保存 ${loadedKeyMasked.value}，留空表示不修改`
    : '尚未配置密钥；填写后保存即写入',
)

/** 已配置代理里带凭据（浏览器看不到）时，换成不含 user:pass@ 的新地址会丢弃原凭据。 */
const proxyRisk = computed(() => {
  if (!isEditing.value || !proxyHasCredentials.value || proxyClear.value) return false
  const next = proxy.value.trim()
  if (!next || next === proxyDisplayed.value) return false
  return !next.includes('@')
})

/* ── 载入 ─────────────────────────────────────────────────────────────── */

/**
 * 把一份**浏览器安全视图**铺进表单。
 *
 * profile 为 null = 新建。传进来的对象里没有任何凭据值：api_key 只有掩码
 * （has_key/key_masked）、敏感头只有键名（header_keys）、代理可能已去 userinfo。
 * 这里必须预期"看不到值"，而不是替它编一个值。
 */
function load(profile: ModelProfile | null): void {
  editingId.value = profile?.id ?? null
  name.value = profile?.name ?? ''
  protocol.value = profile?.protocol || 'openai'
  baseUrl.value = profile?.base_url ?? ''
  model.value = profile?.model ?? ''
  apiKey.value = '' // ← 规则 (a)：无论 has_key 是什么，输入框一律清空
  loadedKeyMasked.value = profile?.has_key ? profile.key_masked || '' : ''
  authStyle.value = authStyleUi(profile?.auth_style)
  maxTokens.value = profile?.max_tokens != null ? String(profile.max_tokens) : ''
  timeoutS.value = profile?.timeout_s != null ? String(profile.timeout_s) : ''
  proxy.value = profile?.proxy ?? ''
  proxyDisplayed.value = profile?.proxy ?? ''
  proxyHasCredentials.value = !!profile?.proxy_has_credentials
  proxyClear.value = false
  riskAccepted.value = false
  note.value = profile?.note ?? ''
  plainRows.value = Object.entries(profile?.headers || {}).map(([key, value]) => ({
    name: key,
    value: value == null ? '' : String(value),
  }))
  // 规则 (d)：只有键名，值留空（placeholder = 保持不变）
  secretRows.value = (profile?.header_keys || []).map((key) => ({ name: key, value: '', clear: false }))
  missingModel.value = false
  formError.value = ''
  presetNotice.value = ''
}

/** 「获取模型列表」选中的模型名回填：只改这一格，不动其它字段。 */
function applyModel(next: string): void {
  model.value = next
  missingModel.value = false
  formError.value = ''
}

/** 记下模型名缺失的高亮，便于把视线带到出错的字段。 */
function focusModel(): void {
  missingModel.value = true
}

/* ── 预设 ─────────────────────────────────────────────────────────────── */

function applyPreset(preset: ProviderPreset): void {
  protocol.value = preset.protocol
  baseUrl.value = preset.base
  if (preset.model) model.value = preset.model
  if (preset.auth) authStyle.value = preset.auth
  if (!name.value.trim() && preset.name !== '自定义') name.value = preset.name
  formError.value = ''
  missingModel.value = false
  presetNotice.value = `已套用「${preset.name}」预设${preset.hint ? `——${preset.hint}` : ''}。填入 API 密钥后点「测试连接」或「获取模型列表」验证。`
}

/* ── 请求头行操作 ─────────────────────────────────────────────────────── */

function addPlainRow(): void {
  plainRows.value = [...plainRows.value, { name: '', value: '' }]
}
function removePlainRow(index: number): void {
  // 非敏感头整表提交：删掉一行 = 保存后删除该头（见 PLAIN_HEADERS_NOTE）。
  plainRows.value = plainRows.value.filter((_, i) => i !== index)
}
function clearSecretRow(index: number): void {
  // 规则 (d)：删除敏感头唯一通道 = 提交 __clear__（清空输入框只是"保持不变"）。
  secretRows.value = secretRows.value.map((row, i) =>
    i === index ? { ...row, value: '', clear: true } : row,
  )
}
function undoSecretRow(index: number): void {
  secretRows.value = secretRows.value.map((row, i) => (i === index ? { ...row, clear: false } : row))
}
function toggleProxyClear(): void {
  proxyClear.value = !proxyClear.value
  if (proxyClear.value) proxy.value = ''
  riskAccepted.value = false
}

/* ── 构造请求体 ───────────────────────────────────────────────────────── */

interface NumberCheck {
  ok: boolean
  value: number
}

function checkNumber(raw: string | number, label: string, max: number, integer: boolean): NumberCheck {
  // `<input type="number">` 上的 v-model 会由 Vue 转成数字，清空则得到 NaN：
  // 两种形态都要接受，NaN 按"没填"处理（max_tokens=0 表示不限制，timeout_s=0 用默认值）。
  const text = raw == null ? '' : String(raw).trim()
  if (text === '' || text === 'NaN') return { ok: true, value: 0 }
  const value = Number(text)
  // 服务端 ProfileRequest 有 ge/le 边界（422 不带 error_code）：这里先拦一次，
  // 用户看到的是一句准话，而不是 FastAPI 的校验数组。
  if (!Number.isFinite(value) || value < 0 || value > max) {
    formError.value = `${label} 必须是不小于 0、不大于 ${max} 的数字（服务端边界相同）。`
    return { ok: false, value: 0 }
  }
  if (integer && !Number.isInteger(value)) {
    formError.value = `${label} 必须是整数。`
    return { ok: false, value: 0 }
  }
  return { ok: true, value }
}

/**
 * 由表单构造 ProfileRequest。
 *
 * 三种用途的要求不同（服务端的真实约束）：
 *   · save   —— 名称与模型名都必填（后端 400 PROFILE_NAME_REQUIRED；模型名见下方注释）；
 *   · probe  —— 只要模型名（探活不写库，名称对解析没有意义）；
 *   · models —— 两项都不要求：拉列表的目的就是帮你发现模型名，要求先填模型名是死循环。
 * 返回 null 表示本地校验没过，原因写在 formError 里。
 */
function buildPayload(mode: PayloadMode = 'save'): ModelProfileInput | null {
  formError.value = ''
  missingModel.value = false

  const requireName = mode === 'save'
  const requireModel = mode === 'save' || mode === 'probe'

  const trimmedName = name.value.trim()
  if (requireName && !trimmedName) {
    formError.value = '配置名称不能为空（服务端会以 PROFILE_NAME_REQUIRED 拒绝）。'
    return null
  }
  const trimmedModel = model.value.trim()
  if (requireModel && !trimmedModel) {
    // 空模型名会被服务端静默回落成默认模型：与本项目调优所用模型不一致，
    // 最终只在下游表现为 SQL_POLICY_BLOCKED，极难归因。这里显式拒绝。
    missingModel.value = true
    formError.value = MODEL_NAME_REQUIRED
    return null
  }

  const tokens = checkNumber(maxTokens.value, 'max_tokens', 200000, true)
  if (!tokens.ok) return null
  const timeout = checkNumber(timeoutS.value, 'timeout_s', 600, false)
  if (!timeout.ok) return null

  const payload: ModelProfileInput = {
    name: trimmedName,
    protocol: protocol.value,
    base_url: baseUrl.value.trim(),
    model: trimmedModel,
    max_tokens: tokens.value,
    timeout_s: timeout.value,
    // auto 在服务端没有对应分支（只认 x-api-key / bearer / both）：
    // 直接提交 "auto" 会发出**不带鉴权头**的请求 → 上游 401。必须映射成 both。
    auth_style: authStyleWire(authStyle.value),
    note: note.value.trim(),
  }

  // 规则 (b)：留空 = 不带这个字段 = 服务端保持原密钥不变。空串同样会被服务端 pop，
  // 但显式省略更不容易被误读成"要清空"。
  const key = apiKey.value.trim()
  if (key) payload.api_key = key

  // 规则 (d)：非敏感头**整表**提交（服务端以提交字典为权威），敏感头只提交
  // 用户改过或显式删除的那些；未提交的敏感头由服务端按键级合并保留。
  const headers: Record<string, string> = {}
  for (const row of plainRows.value) {
    const headerName = row.name.trim()
    if (!headerName) continue
    headers[headerName] = row.value
  }
  for (const row of secretRows.value) {
    if (row.clear) headers[row.name] = CLEAR_SENTINEL
    else if (row.value.trim()) headers[row.name] = row.value
  }
  payload.headers = headers

  // 代理：__clear__ 是唯一清空通道；原样回传服务端下发的（已去 userinfo）地址
  // 不会丢凭据（merge_proxy_value 认识脱敏视图）。
  if (proxyClear.value) {
    payload.proxy = CLEAR_SENTINEL
  } else if (isEditing.value) {
    const next = proxy.value.trim()
    if (next && next !== proxyDisplayed.value) {
      // 只有"保存"才会把新地址写进库里、从而丢掉原凭据；探活/拉列表只是拿它发一次请求，
      // 不该被这条确认拦住（确认文案本身也只谈保存）。
      if (mode === 'save' && proxyRisk.value && !riskAccepted.value) {
        formError.value = '该配置的代理地址里保存着账号口令（浏览器看不到）：用不含凭据的新地址保存会丢弃原凭据，请勾选确认后再保存。'
        return null
      }
      payload.proxy = next
    } else if (!next && proxyDisplayed.value) {
      payload.proxy = CLEAR_SENTINEL // 清空已显示的代理 = 显式删除
    }
  } else if (proxy.value.trim()) {
    payload.proxy = proxy.value.trim()
  }

  if (isEditing.value && editingId.value) {
    // 探活/拉列表时服务端用它选出"要继承密钥与敏感头的已存配置"；
    // 不传的话会回落到**当前启用**配置，编辑非启用配置时会继承错误的密钥。
    payload.profile_id = editingId.value
  }
  return payload
}

/* ── 动作 ─────────────────────────────────────────────────────────────── */

function invalidate(): void {
  if (formError.value) emit('invalid', formError.value)
}

function submit(activate: boolean): void {
  const payload = buildPayload('save')
  if (!payload) {
    invalidate()
    return
  }
  emit('save', payload, activate)
}

function probe(): void {
  const payload = buildPayload('probe')
  if (!payload) {
    invalidate()
    return
  }
  emit('probe', payload)
}

function fetchModels(): void {
  // 这里刻意不要求模型名、也不要求名称：拉列表的目的就是找出可用的模型名。
  const payload = buildPayload('models')
  if (!payload) {
    invalidate()
    return
  }
  emit('fetch-models', payload)
}

defineExpose({ load, applyModel, focusModel, buildPayload })
</script>

<template>
  <section class="mpe" data-testid="model-editor">
    <div class="mpe-head">
      <h2 class="mpe-title" data-testid="model-editor-title">
        {{ isEditing ? '编辑配置' : '新建配置' }}
      </h2>
      <span class="mpe-sub">
        {{ isEditing ? '密钥与敏感头的真实值不会下发：留空即保持原值' : '选一个预设可少填几项' }}
      </span>
    </div>

    <div class="mpe-presets" data-testid="model-presets">
      <button
        v-for="preset in PROVIDER_PRESETS"
        :key="preset.name"
        class="mpe-preset"
        type="button"
        :data-testid="`model-preset-${preset.name}`"
        @click="applyPreset(preset)"
      >
        {{ preset.name }}
      </button>
    </div>
    <p v-if="presetNotice" class="mpe-hint is-info" data-testid="model-preset-notice">{{ presetNotice }}</p>

    <div class="mpe-grid">
      <label class="mpe-field">
        <span class="mpe-label">配置名称</span>
        <input v-model="name" class="mpe-input" type="text" maxlength="120" data-testid="model-field-name" />
      </label>

      <label class="mpe-field">
        <span class="mpe-label">协议</span>
        <select v-model="protocol" class="mpe-input" data-testid="model-field-protocol">
          <option v-for="opt in props.protocols" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
          <option v-if="!props.protocols.length" value="openai">OpenAI 兼容</option>
          <option v-if="!props.protocols.length" value="anthropic">Anthropic Messages</option>
        </select>
      </label>

      <label class="mpe-field is-wide">
        <span class="mpe-label">接口地址（base_url）</span>
        <input
          v-model="baseUrl"
          class="mpe-input"
          type="text"
          maxlength="500"
          placeholder="填到 /v1 即可，粘贴完整端点会自动截断"
          data-testid="model-field-base-url"
        />
        <span class="mpe-hint">
          服务端会补版本段（/v1、/api/paas/v4、/api/v3 都能直接填）。地址与已存配置不同源时，
          探活会以 400 MODEL_KEY_NOT_INHERITED 拒绝继承已存密钥。
        </span>
      </label>

      <label class="mpe-field is-wide">
        <span class="mpe-label">模型名（必填）</span>
        <input
          v-model="model"
          class="mpe-input"
          :class="{ 'is-missing': missingModel }"
          type="text"
          maxlength="200"
          placeholder="例如 deepseek-chat；可先点「获取模型列表」"
          data-testid="model-field-model"
        />
        <span v-if="missingModel" class="mpe-hint is-bad" data-testid="model-model-missing">
          {{ MODEL_NAME_REQUIRED }}
        </span>
      </label>

      <label class="mpe-field is-wide">
        <span class="mpe-label">API 密钥</span>
        <input
          v-model="apiKey"
          class="mpe-input"
          type="password"
          autocomplete="off"
          :placeholder="apiKeyPlaceholder"
          data-testid="model-field-api-key"
        />
        <span class="mpe-hint">
          留空 = 保持原密钥不变；填新值 = 覆盖。掩码只出现在上面的 placeholder 里，
          绝不会被回填进输入框（回填提交会把真实密钥覆盖成掩码字符串）。
        </span>
        <span class="mpe-hint is-warn">{{ NO_CLEAR_KEY_NOTE }}</span>
      </label>

      <label class="mpe-field" :class="{ 'is-dim': !isAnthropic }">
        <span class="mpe-label">鉴权方式（仅 Anthropic 有意义）</span>
        <select
          v-model="authStyle"
          class="mpe-input"
          :disabled="!isAnthropic"
          data-testid="model-field-auth-style"
        >
          <option v-for="opt in AUTH_STYLE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <span class="mpe-hint">
          {{
            isAnthropic
              ? '「自动」提交成服务端的 both：同时发 x-api-key 与 Authorization: Bearer。'
              : 'OpenAI 兼容协议固定发 Authorization: Bearer，这一项会被服务端忽略。'
          }}
        </span>
      </label>

      <label class="mpe-field">
        <span class="mpe-label">max_tokens</span>
        <input
          v-model="maxTokens"
          class="mpe-input"
          type="number"
          min="0"
          max="200000"
          step="1"
          placeholder="0 = 不限制"
          data-testid="model-field-max-tokens"
        />
      </label>

      <label class="mpe-field">
        <span class="mpe-label">timeout_s</span>
        <input
          v-model="timeoutS"
          class="mpe-input"
          type="number"
          min="0"
          max="600"
          step="1"
          placeholder="0 = 用默认（75s）"
          data-testid="model-field-timeout-s"
        />
      </label>

      <label class="mpe-field is-wide">
        <span class="mpe-label">备注（note）</span>
        <textarea
          v-model="note"
          class="mpe-input mpe-textarea"
          maxlength="500"
          rows="2"
          placeholder="只给同一批配置做人类可读的标记；不参与请求"
          data-testid="model-field-note"
        />
      </label>
    </div>

    <div class="mpe-section">
      <div class="mpe-section-head">
        <h3 class="mpe-h3">普通请求头</h3>
        <button class="mpe-btn" type="button" data-testid="model-header-add" @click="addPlainRow">加一行</button>
      </div>
      <p class="mpe-hint">{{ PLAIN_HEADERS_NOTE }}</p>
      <p v-if="!plainRows.length" class="mpe-hint is-muted" data-testid="model-headers-empty">
        没有非敏感请求头。
      </p>
      <div v-for="(row, index) in plainRows" :key="`plain-${index}`" class="mpe-row" data-testid="model-header-row">
        <input
          v-model="row.name"
          class="mpe-input mpe-key"
          type="text"
          placeholder="头名称"
          data-testid="model-header-name"
        />
        <input
          v-model="row.value"
          class="mpe-input"
          type="text"
          placeholder="头值"
          data-testid="model-header-value"
        />
        <button
          class="mpe-btn is-danger"
          type="button"
          data-testid="model-header-remove"
          @click="removePlainRow(index)"
        >
          删除
        </button>
      </div>
    </div>

    <div class="mpe-section">
      <h3 class="mpe-h3">敏感请求头（值不下发，只回显键名）</h3>
      <p class="mpe-hint">{{ SECRET_HEADERS_NOTE }}</p>
      <p v-if="!secretRows.length" class="mpe-hint is-muted" data-testid="model-secrets-empty">
        没有已保存的敏感请求头。
      </p>
      <div v-for="(row, index) in secretRows" :key="`secret-${row.name}`" class="mpe-row" data-testid="model-secret-row">
        <code class="mpe-key is-code" data-testid="model-secret-name">{{ row.name }}</code>
        <input
          v-model="row.value"
          class="mpe-input"
          type="password"
          autocomplete="off"
          :disabled="row.clear"
          :placeholder="row.clear ? '将提交 __clear__ 删除' : '保持不变'"
          data-testid="model-secret-value"
        />
        <button
          v-if="!row.clear"
          class="mpe-btn is-danger"
          type="button"
          data-testid="model-secret-clear"
          @click="clearSecretRow(index)"
        >
          删除
        </button>
        <button v-else class="mpe-btn" type="button" data-testid="model-secret-undo" @click="undoSecretRow(index)">
          撤销删除
        </button>
      </div>
    </div>

    <div class="mpe-section">
      <h3 class="mpe-h3">代理</h3>
      <p class="mpe-hint">{{ PROXY_NOTE }}</p>
      <div class="mpe-row">
        <input
          v-model="proxy"
          class="mpe-input"
          type="text"
          maxlength="300"
          :disabled="proxyClear"
          :placeholder="proxyClear ? '将提交 __clear__ 删除代理' : '留空 = 本机直连；例：http://127.0.0.1:7897'"
          data-testid="model-field-proxy"
        />
        <button class="mpe-btn is-danger" type="button" data-testid="model-proxy-clear" @click="toggleProxyClear">
          {{ proxyClear ? '撤销删除代理' : '删除代理' }}
        </button>
      </div>
      <p v-if="proxyRisk" class="mpe-hint is-warn" data-testid="model-proxy-risk">
        新地址不含 user:pass@，而已保存的代理里有凭据：保存会丢掉原凭据。
        <label class="mpe-check">
          <input v-model="riskAccepted" type="checkbox" data-testid="model-proxy-risk-accept" />
          我确认丢弃原代理凭据
        </label>
      </p>
    </div>

    <p v-if="formError" class="mpe-error" data-testid="model-editor-error">{{ formError }}</p>

    <div class="mpe-actions">
      <button
        class="mpe-btn is-primary"
        type="button"
        :disabled="props.saving"
        data-testid="model-save"
        @click="submit(false)"
      >
        {{ props.saving ? '保存中…' : isEditing ? '保存' : '新建' }}
      </button>
      <button
        class="mpe-btn is-primary"
        type="button"
        :disabled="props.saving"
        data-testid="model-save-activate"
        @click="submit(true)"
      >
        {{ props.saving ? '保存中…' : '保存并启用' }}
      </button>
      <button class="mpe-btn" type="button" :disabled="props.saving" data-testid="model-probe" @click="probe">
        测试连接
      </button>
      <button
        class="mpe-btn"
        type="button"
        :disabled="props.saving"
        data-testid="model-fetch-models"
        @click="fetchModels"
      >
        获取模型列表
      </button>
    </div>

    <p class="mpe-hint is-muted">
      当前表单：{{ protocolLabel(protocol, props.protocols) }} · {{ HAS_KEY_NOTE }}
    </p>
  </section>
</template>

<style scoped>
/* 类名统一 mpe- 前缀，避开 Tailwind v4 @layer utilities 的同名工具类。 */
.mpe {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px 16px;
}
.mpe-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.mpe-title {
  margin: 0;
  font-size: 15px;
  color: var(--color-text);
}
.mpe-sub {
  font-size: 13px;
  color: var(--color-muted);
}
.mpe-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 10px 0 4px;
}
.mpe-preset {
  font: inherit;
  font-size: 13px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-ink);
  color: var(--color-body);
  cursor: pointer;
}
.mpe-preset:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.mpe-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
  margin-top: 12px;
}
.mpe-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.mpe-field.is-wide {
  grid-column: 1 / -1;
}
.mpe-field.is-dim {
  opacity: 0.6;
}
.mpe-label {
  font-size: 13px;
  color: var(--color-muted);
}
.mpe-input {
  font: inherit;
  font-size: 14px;
  padding: 7px 9px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-text);
  min-width: 0;
}
.mpe-input:focus {
  outline: none;
  border-color: var(--color-accent);
}
.mpe-input.is-missing {
  border-color: #e11d48;
  background: rgba(225, 29, 72, 0.05);
}
.mpe-input:disabled {
  background: var(--color-ink);
  color: var(--color-muted);
}
.mpe-textarea {
  resize: vertical;
}
.mpe-hint {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--color-muted);
}
.mpe-hint.is-warn {
  color: #d97706;
}
.mpe-hint.is-bad {
  color: #e11d48;
}
.mpe-hint.is-info {
  color: var(--color-accent);
}
.mpe-hint.is-muted {
  opacity: 0.85;
}
.mpe-section {
  margin-top: 14px;
  border-top: 1px dashed var(--color-line);
  padding-top: 10px;
}
.mpe-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.mpe-h3 {
  margin: 0 0 4px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--color-text);
}
.mpe-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}
.mpe-key {
  flex: 0 0 180px;
  min-width: 0;
}
.mpe-key.is-code {
  font-size: 13px;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--color-ink);
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mpe-check {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 6px;
}
.mpe-error {
  margin: 12px 0 0;
  font-size: 13.5px;
  color: #e11d48;
  line-height: 1.6;
}
.mpe-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.mpe-btn {
  font: inherit;
  font-size: 13.5px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.mpe-btn:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.mpe-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.mpe-btn.is-primary {
  border-color: rgba(37, 99, 235, 0.45);
  background: rgba(37, 99, 235, 0.08);
  color: var(--color-accent);
}
.mpe-btn.is-danger {
  color: #e11d48;
  border-color: rgba(225, 29, 72, 0.3);
}
@media (max-width: 860px) {
  .mpe-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .mpe-key {
    flex-basis: 120px;
  }
}
</style>
