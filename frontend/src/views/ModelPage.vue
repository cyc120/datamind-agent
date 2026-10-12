<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import SideNav from '../components/SideNav.vue'
import ProfileEditor from '../components/model/ProfileEditor.vue'
import ProfileList from '../components/model/ProfileList.vue'
import ProbePanel from '../components/model/ProbePanel.vue'
import {
  CLEARED_CONFIG_NOTE,
  ENV_FALLBACK_NOTE,
  HAS_KEY_NOTE,
  IMPORT_NOTE,
  describeModelError,
  exportNote,
  protocolLabel,
  sourceText,
  stamp,
  type ModelErrorInfo,
} from '../components/model/messages'
import {
  activateModelProfile,
  createModelProfile,
  deleteModelConfig,
  deleteModelProfile,
  exportModelProfiles,
  getModelProfiles,
  importModelProfiles,
  listModels,
  me,
  modelConfig,
  testModelConfig,
  updateModelProfile,
} from '../api/client'
import type {
  AuthMe,
  ModelConfigView,
  ModelListResult,
  ModelProfile,
  ModelProfileInput,
  ModelProfileListResult,
  ModelTestResult,
} from '../api/types'

/**
 * 模型接入（/model）。
 *
 * 这个视图的存在理由：侧边栏一直有「模型接入」入口，但新前端里它以前只落到
 * 「未迁移」占位页；而后端 `/v1/model/*` 的 12 个路由其实一直都是活的。
 *
 * 三件必须同时正确、且互不冒充的事实（本页把它们分开放）：
 *   1. 配置存在 —— GET /v1/model/profiles、GET /v1/model/config：只说明「配了没有」，
 *      has_key 只等于「密钥字段填过值」，绝不等于密钥有效或模型可用；
 *   2. 连通性   —— POST /v1/model/config/test：**按需**发起，是唯一能证明"能应答"的证据，
 *      而且后端不持久化任何检测结果（本页只在内存里记住这一次 + 时间戳）；
 *   3. 上游模型 —— POST /v1/model/config/models：只说明该密钥能看到哪些模型名。
 *
 * 权限：`/v1/model/*` **全部**路由（包括 GET）都要求 admin，所以 403 PERMISSION_DENIED
 * 是**正常结果**：这里渲染成一段权限说明，而不是堆栈或"加载失败"。
 *
 * 密钥安全：本页遵循四个密钥规则（详见 components/model/ProfileEditor.vue 顶部注释）——
 * 掩码只进 placeholder、空 api_key 表示不修改、__clear__ 只用于敏感头与代理、
 * 敏感头只回显键名且删除必须显式提交哨兵。这里再补一条：**没有任何"清空密钥"的表单动作**；
 * 唯一能清空密钥的是「当前启用配置」卡片里的 DELETE /v1/model/config（兼容单配置面），
 * 它的返回体是窄结构（只有 has_key/key_masked/source），只能当"已清空"读，不能当配置视图。
 */

const router = useRouter()

/** 编辑器的命令式接口（load / applyModel / buildPayload）—— 表单状态在编辑器内部 */
const editor = ref<InstanceType<typeof ProfileEditor> | null>(null)

/* ── 配置列表 ─────────────────────────────────────────────────────────── */
const profiles = ref<ModelProfile[]>([])
const activeId = ref<string | null>(null)
const protocols = ref<{ id: string; label: string }[]>([])
const listLoading = ref(false)
const listLoaded = ref(false)
const listError = ref<ModelErrorInfo | null>(null)

/* ── 当前启用配置（GET /v1/model/config，兼容面）──────────────────────── */
const config = ref<ModelConfigView | null>(null)
const configLoading = ref(false)
const configError = ref('')
const clearedNote = ref('')
const confirmClear = ref(false)
const clearingConfig = ref(false)

/* ── 身份（GET /v1/auth/me，公开路由；只用来把 403 解释清楚）──────────── */
const identity = ref<AuthMe | null>(null)

/* ── 交互状态 ─────────────────────────────────────────────────────────── */
const editing = ref<ModelProfile | null>(null)
const saving = ref(false)
const busyId = ref<string | null>(null)
const deletingId = ref<string | null>(null)
const probingId = ref<string | null>(null)
const notice = ref<{ kind: 'ok' | 'bad' | 'info'; text: string } | null>(null)

/* ── 探活 / 模型列表（都在本页内存里，服务端不保存）──────────────────── */
const probing = ref(false)
const testResult = ref<ModelTestResult | null>(null)
const testError = ref('')
const testAt = ref<string | null>(null)
const testTarget = ref('')
const modelsLoading = ref(false)
const models = ref<ModelListResult | null>(null)
const modelsError = ref('')
const modelsAt = ref<string | null>(null)

/* ── 导入 ─────────────────────────────────────────────────────────────── */
const importOpen = ref(false)
const importText = ref('')
const importFileName = ref('')
const importing = ref(false)

const ROLE_LABEL: Record<string, string> = {
  viewer: '浏览',
  reviewer: '复核',
  approver: '审批',
  admin: '管理',
}

const roles = computed<string[]>(() => {
  const raw = identity.value?.user?.roles
  return Array.isArray(raw) ? raw.map((r) => String(r)) : []
})

/** admin 通过所有角色门；这里只用来提前解释权限，不作为放行依据（服务端才是权威）。 */
const isAdmin = computed(() => roles.value.includes('admin'))

const identityText = computed(() => {
  const user = identity.value?.user
  if (!user) return '未登录（匿名）'
  const label = roles.value.length
    ? roles.value.map((r) => `${r}${ROLE_LABEL[r] ? `（${ROLE_LABEL[r]}）` : ''}`).join(' / ')
    : '无角色'
  return `${user.display_name || user.username || '未知用户'} · ${label}`
})

/** 401/403 都是权限事实：整页改成权限说明，不再显示任何读不到的界面。 */
const permissionBlocked = computed(
  () => listError.value?.permission === 'denied' || listError.value?.permission === 'login',
)

/* ── 读取 ─────────────────────────────────────────────────────────────── */

function applyList(res: ModelProfileListResult | null): void {
  profiles.value = Array.isArray(res?.items) ? res.items : []
  activeId.value = res?.active ?? null
  if (res?.protocols?.length) protocols.value = res.protocols
}

/**
 * 拉配置列表。
 *
 * 刻意**不**在这里重载编辑器：刷新按钮不应该吞掉用户正在填的内容。
 * 写操作（保存/删除/清空）之后由各自的处理函数负责把编辑器对齐服务端安全视图。
 */
async function loadProfiles(): Promise<void> {
  listLoading.value = true
  try {
    applyList(await getModelProfiles())
    listError.value = null
  } catch (e) {
    listError.value = describeModelError(e, '接入配置加载失败')
    profiles.value = []
    activeId.value = null
  } finally {
    listLoading.value = false
    listLoaded.value = true
  }
}

async function loadConfig(): Promise<void> {
  configLoading.value = true
  try {
    config.value = await modelConfig()
    configError.value = ''
  } catch (e) {
    config.value = null
    configError.value = describeModelError(e, '当前启用配置读取失败').text
  } finally {
    configLoading.value = false
  }
}

async function loadIdentity(): Promise<void> {
  try {
    identity.value = await me()
  } catch {
    identity.value = null
  }
}

async function refresh(): Promise<void> {
  notice.value = null
  await loadIdentity()
  await Promise.all([loadProfiles(), loadConfig()])
}

/* ── 编辑器同步 ───────────────────────────────────────────────────────── */

/**
 * 用服务端最新下发的**安全视图**重载编辑器。
 * 这一步同时保证四个密钥规则在写操作之后依然成立：
 * api_key 输入框重新变空（只剩掩码 placeholder）、敏感头只剩键名、代理可能被去 userinfo。
 */
function reloadEditor(profile: ModelProfile | null): void {
  editing.value = profile
  editor.value?.load(profile)
}

function reloadEditorFromList(fallbackId: string | null = null): void {
  const id = fallbackId || editing.value?.id || null
  if (!id) {
    reloadEditor(null)
    return
  }
  reloadEditor(profiles.value.find((p) => p.id === id) || null)
}

function newProfile(): void {
  reloadEditor(null)
  notice.value = null
}

function edit(profile: ModelProfile): void {
  reloadEditor(profile)
  notice.value = { kind: 'info', text: `正在编辑「${profile.name || '未命名配置'}」：密钥与敏感头的值不下发，留空即保持原值。` }
}

function onInvalid(message: string): void {
  notice.value = { kind: 'bad', text: message }
}

function onPickModel(name: string): void {
  editor.value?.applyModel(name)
  notice.value = { kind: 'info', text: `已把模型名「${name}」填入表单；点「保存」后才会写入配置。` }
}

/* ── 保存 / 启用 / 删除 ───────────────────────────────────────────────── */

async function save(payload: ModelProfileInput, activate: boolean): Promise<void> {
  saving.value = true
  notice.value = null
  const targetId = editing.value?.id || null
  try {
    const saved = targetId
      ? await updateModelProfile(targetId, payload)
      : await createModelProfile(payload)

    // create/update 的返回体里 active 恒为 false（后端 public_profile 的默认参数），
    // 不能据此判断"刚保存的成了当前启用"——只有列表接口里的 active 可信。
    let activateNote = ''
    let activateFailed = false
    if (activate) {
      try {
        applyList(await activateModelProfile(saved.id))
        activateNote = '并已启用'
      } catch (e) {
        activateFailed = true
        activateNote = `但启用失败：${describeModelError(e, '启用失败').text}`
      }
    }

    await loadProfiles()
    reloadEditorFromList(saved.id)
    await loadConfig()

    const keyState = saved.has_key
      ? `${saved.key_masked || '已填写（掩码缺失）'}`
      : '未填写'
    notice.value = {
      kind: activateFailed ? 'bad' : 'ok',
      text: `配置「${saved.name || '未命名配置'}」已保存${activateNote}。密钥状态：${keyState} —— 掩码只说明填过值，不代表有效。`,
    }
  } catch (e) {
    notice.value = { kind: 'bad', text: describeModelError(e, '保存失败').text }
  } finally {
    saving.value = false
  }
}

async function activate(profile: ModelProfile): Promise<void> {
  busyId.value = profile.id
  try {
    applyList(await activateModelProfile(profile.id))
    await loadConfig()
    notice.value = {
      kind: 'ok',
      text: `已把「${profile.name || '未命名配置'}」设为当前启用配置；后续问数立即使用它。`,
    }
  } catch (e) {
    notice.value = { kind: 'bad', text: describeModelError(e, '启用失败').text }
  } finally {
    busyId.value = null
  }
}

async function remove(profile: ModelProfile): Promise<void> {
  deletingId.value = profile.id
  try {
    applyList(await deleteModelProfile(profile.id))
    if (editing.value?.id === profile.id) reloadEditor(null)
    await loadConfig()
    notice.value = {
      kind: 'info',
      text: `配置「${profile.name || '未命名配置'}」已删除。删除后当前启用配置可能换人，以列表里的「启用中」为准。`,
    }
  } catch (e) {
    notice.value = { kind: 'bad', text: describeModelError(e, '删除失败').text }
  } finally {
    deletingId.value = null
  }
}

/**
 * 清空「当前启用配置」的密钥字段（DELETE /v1/model/config）。
 *
 * 这个接口只清空 app_settings 里**当前启用那条配置**的 api_key（条目本身保留，
 * 端点与模型不变），其他配置不受影响。它的返回体是窄结构，只说明"已清空"，
 * 因此这里绝不把它并进 config（那不是配置视图），而是把原文念出来 + 重新读一次配置。
 */
async function clearActiveKey(): Promise<void> {
  clearingConfig.value = true
  try {
    const narrow = await deleteModelConfig()
    clearedNote.value = `服务端返回：has_key=${String(narrow.has_key)} · key_masked="${narrow.key_masked}" · source=${narrow.source}。${CLEARED_CONFIG_NOTE}`
    await loadProfiles()
    await loadConfig()
    reloadEditorFromList()
    notice.value = { kind: 'info', text: '已清空当前启用配置的密钥字段（配置条目仍在，其他配置未受影响）。' }
  } catch (e) {
    clearedNote.value = ''
    notice.value = { kind: 'bad', text: describeModelError(e, '清空密钥失败').text }
  } finally {
    clearingConfig.value = false
    confirmClear.value = false
  }
}

/* ── 探活 / 模型列表 ─────────────────────────────────────────────────── */

/** 探活只影响"探活结果"面板；结果带时间戳留在内存里，服务端不保存。 */
async function runProbe(payload: ModelProfileInput, target: string): Promise<void> {
  probing.value = true
  testResult.value = null
  testError.value = ''
  testTarget.value = target
  try {
    testResult.value = await testModelConfig(payload)
  } catch (e) {
    // 请求本身失败（403/CSRF/跨源 400/网络）也如实显示，不静默。
    testError.value = describeModelError(e, '探活请求失败').text
  } finally {
    testAt.value = new Date().toISOString()
    probing.value = false
  }
}

async function probeProfile(profile: ModelProfile): Promise<void> {
  probingId.value = profile.id
  try {
    await runProbe(
      { profile_id: profile.id },
      `配置「${profile.name || '未命名配置'}」（${profile.model || '未填模型'}）`,
    )
  } finally {
    probingId.value = null
  }
}

async function probeForm(payload: ModelProfileInput): Promise<void> {
  const target = editing.value
    ? `配置「${editing.value.name || '未命名配置'}」的表单当前内容（未保存也会按表单探测；密钥与敏感头留空时由服务端按键级合并补回）`
    : '新建表单的当前内容（尚未保存）'
  await runProbe(payload, target)
}

async function fetchModels(payload: ModelProfileInput): Promise<void> {
  modelsLoading.value = true
  models.value = null
  modelsError.value = ''
  try {
    // 注意：这个接口在 HTTP 200 里用 {ok:false,error_code,message} 报错，
    // 只有 HTTP 层失败才会抛异常——两种都要显示（见 ProbePanel）。
    models.value = await listModels(payload)
  } catch (e) {
    modelsError.value = describeModelError(e, '获取模型列表失败').text
  } finally {
    modelsAt.value = new Date().toISOString()
    modelsLoading.value = false
  }
}

/* ── 导出 / 导入 ─────────────────────────────────────────────────────── */

/** 导出永远 include_keys=false：本页只提供不含密钥的导出。 */
async function exportProfiles(): Promise<void> {
  try {
    const data = await exportModelProfiles(false)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `datamind-profiles-${new Date().toISOString().slice(0, 10)}.json`
    document.body.append(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    notice.value = {
      kind: 'ok',
      text: exportNote(Array.isArray(data.profiles) ? data.profiles.length : 0, !!data.include_keys),
    }
  } catch (e) {
    notice.value = { kind: 'bad', text: describeModelError(e, '导出失败').text }
  }
}

function toggleImport(): void {
  importOpen.value = !importOpen.value
}

async function onImportFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importFileName.value = file.name
  try {
    importText.value = await file.text()
  } catch {
    notice.value = { kind: 'bad', text: '读取所选文件失败，可以直接把 JSON 粘贴到下面的输入框。' }
  }
  // 清空 input：同一个文件改过之后还能再次选择。
  input.value = ''
}

async function runImport(): Promise<void> {
  const raw = importText.value.trim()
  if (!raw) {
    notice.value = { kind: 'bad', text: '请先选择导出文件，或把导出文件里的 JSON 粘贴进来。' }
    return
  }
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    notice.value = { kind: 'bad', text: 'JSON 解析失败：请粘贴合法的导出文件原文（{"profiles":[…]} 或数组）。' }
    return
  }
  const list = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.profiles) ? parsed.profiles : null
  if (!list || !list.length) {
    notice.value = { kind: 'bad', text: '没找到可导入的配置：文件应为导出结果（含 profiles 数组）或直接是一个数组。' }
    return
  }
  importing.value = true
  try {
    // activate_first 固定 false：导入**不得**改变当前启用配置（合同要求）。
    const res = await importModelProfiles({ profiles: list, activate_first: false })
    applyList(res)
    await loadConfig()
    notice.value = {
      kind: 'ok',
      text: `已导入 ${res.imported} 套配置（提交 ${list.length} 条；服务端最多处理 50 条）。${IMPORT_NOTE}`,
    }
    importOpen.value = false
    importText.value = ''
    importFileName.value = ''
  } catch (e) {
    notice.value = { kind: 'bad', text: describeModelError(e, '导入失败').text }
  } finally {
    importing.value = false
  }
}

/* ── 生命周期 ─────────────────────────────────────────────────────────── */

/** 「新建会话」入口与其它页一致：回问数与问答页。 */
function backToChat(): void {
  void router.push('/app')
}

onMounted(async () => {
  // 先读身份（公开路由，匿名也返回 200），这样 403 的说明里能带上"当前是谁、什么角色"。
  await loadIdentity()
  await Promise.all([loadProfiles(), loadConfig()])
})
</script>

<template>
  <div class="mainpage">
    <SideNav @new-chat="backToChat" />

    <div class="center">
      <header class="topbar">
        <div class="tb-left">
          <span class="tb-title">模型接入</span>
          <span class="tb-note">这里只回答「配了没有」；密钥是否有效、模型能否应答要靠探活证明</span>
        </div>
        <button class="tb-action" type="button" :disabled="listLoading || configLoading" @click="refresh">
          {{ listLoading || configLoading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <div class="body">
        <p v-if="notice" class="mp-notice" :class="`tone-${notice.kind}`" data-testid="model-notice">
          {{ notice.text }}
        </p>

        <!-- ── 权限（401/403）：这是正常结果，不是加载失败 ────────────────── -->
        <section v-if="permissionBlocked" class="mp-panel mp-panel-perm" data-testid="model-permission">
          <h2 class="mp-h2">模型接入需要管理员身份</h2>
          <p class="mp-line" data-testid="model-permission-text">{{ listError?.text }}</p>
          <p class="mp-line">
            <code>/v1/model/*</code> 的全部 12 个路由——<strong>包括读取</strong>——都要求
            <code>admin</code> 角色（配置视图带接口地址与密钥掩码，中间件把整个前缀归到 admin）。
            所以匿名或 viewer / reviewer / approver 身份拿到
            <code>403 PERMISSION_DENIED</code> 是设计如此；拿到 401 则说明会话已失效。
          </p>
          <p class="mp-line">当前身份：{{ identityText }}（admin 会通过全部权限门）</p>
          <p class="mp-line mp-hint">
            本页不会因为读不到配置就假装"没有配置"：现在显示的就是"读不到"，而不是"未配置"。
          </p>
          <button class="mp-btn" type="button" :disabled="listLoading" data-testid="model-permission-retry" @click="refresh">
            重新读取
          </button>
        </section>

        <!-- ── 其它加载错误（503 / 网络 / 422 等）──────────────────────── -->
        <section v-else-if="listError" class="mp-panel mp-panel-bad" data-testid="model-error">
          <h2 class="mp-h2">接入配置读取失败</h2>
          <p class="mp-line">{{ listError.text }}</p>
          <p class="mp-line mp-hint">
            错误码：<code>{{ listError.code || '(无)' }}</code>
            <span v-if="listError.status"> · HTTP {{ listError.status }}</span>
          </p>
          <button class="mp-btn" type="button" :disabled="listLoading" data-testid="model-error-retry" @click="refresh">
            重试
          </button>
        </section>

        <!-- 首次读取完成前不显示任何内容态：profiles=[] 只是"还没读到"，不是"没有配置"。
             把空列表渲染成"还没有任何接入配置"正是本仓库最反对的越权结论。 -->
        <p v-else-if="!listLoaded" class="mp-hint" data-testid="model-loading">
          正在读取接入配置…
        </p>

        <template v-else>
          <!-- ── 当前启用配置（GET /v1/model/config；source / environment_fallback 解释配置来源）── -->
          <section class="mp-panel" data-testid="model-active-config">
            <div class="mp-panel-head">
              <h2 class="mp-h2">当前启用配置</h2>
              <span class="mp-chip" data-testid="model-identity">身份：{{ identityText }}</span>
              <span v-if="isAdmin" class="mp-chip tone-ok" data-testid="model-admin-badge">admin</span>
            </div>

            <p v-if="configLoading" class="mp-hint">正在读取…</p>
            <p v-else-if="configError" class="mp-bad" data-testid="model-active-error">{{ configError }}</p>
            <template v-else-if="config">
              <p class="mp-line" data-testid="model-active-summary">
                {{ config.name || '未命名配置' }} ·
                {{ protocolLabel(config.protocol, protocols) }} ·
                模型 {{ config.model || '(未填模型)' }} ·
                地址 {{ config.base_url || '(未填)' }}
              </p>
              <p class="mp-line" data-testid="model-active-key">
                密钥：{{ config.has_key ? `已填写 ${config.key_masked || ''}` : '未填写' }}{{ config.has_key ? ' —— ' : '。' }}{{ HAS_KEY_NOTE }}
              </p>
              <p class="mp-line">
                配置来源：{{ sourceText(config.source) }}
                <span v-if="config.updated_at"> · 更新于 {{ stamp(config.updated_at) }}</span>
              </p>
              <p v-if="config.environment_fallback" class="mp-warn" data-testid="model-env-fallback">
                {{ ENV_FALLBACK_NOTE }}
              </p>
              <p v-if="config.has_proxy" class="mp-line mp-hint">
                代理：{{ config.proxy || '(地址不下发或为空)' }}
                {{ config.proxy_has_credentials ? ' · 地址里带账号口令（凭据不下发）' : '' }}
              </p>
              <p v-if="(config.header_keys || []).length" class="mp-line mp-hint">
                敏感请求头：{{ (config.header_keys || []).join('、') }}（只回显键名，值不下发）
              </p>
            </template>
            <p v-else class="mp-hint" data-testid="model-active-empty">没有读到当前启用配置。</p>

            <p class="mp-line mp-hint" data-testid="model-clear-note">
              这是单配置兼容面（DELETE /v1/model/config）的动作：只清空<strong>当前启用</strong>那条配置的
              api_key 字段，配置条目与端点/模型都保留，其他配置不受影响。右侧的配置编辑表单里
              <strong>刻意没有</strong>「清空密钥」入口——__clear__ 只对敏感请求头与代理有效，
              把它当 api_key 提交会把字面量存成密钥。
            </p>

            <div class="mp-actions">
              <template v-if="!confirmClear">
                <button
                  class="mp-btn is-danger"
                  type="button"
                  :disabled="clearingConfig || !config?.has_key"
                  data-testid="model-clear-active-key"
                  @click="confirmClear = true"
                >
                  清空启用配置的密钥
                </button>
              </template>
              <template v-else>
                <span class="mp-confirm" data-testid="model-clear-active-key-ask">
                  确认清空「当前启用配置」的密钥字段？配置条目保留，其他配置不受影响。
                </span>
                <button
                  class="mp-btn is-danger"
                  type="button"
                  :disabled="clearingConfig"
                  data-testid="model-clear-active-key-confirm"
                  @click="clearActiveKey"
                >
                  {{ clearingConfig ? '清空中…' : '确认清空' }}
                </button>
                <button class="mp-btn" type="button" data-testid="model-clear-active-key-cancel" @click="confirmClear = false">
                  取消
                </button>
              </template>
            </div>
            <p v-if="clearedNote" class="mp-line mp-hint" data-testid="model-cleared-note">{{ clearedNote }}</p>
          </section>

          <!-- ── 工具条：新建 / 导出（不含密钥）/ 导入（文件或粘贴，activate_first:false）── -->
          <div class="mp-tools">
            <button class="mp-btn is-primary" type="button" data-testid="model-new" @click="newProfile">新建配置</button>
            <button class="mp-btn" type="button" data-testid="model-export" @click="exportProfiles">导出（不含密钥）</button>
            <button class="mp-btn" type="button" data-testid="model-import-toggle" @click="toggleImport">
              {{ importOpen ? '收起导入' : '导入配置' }}
            </button>
          </div>

          <section v-if="importOpen" class="mp-panel" data-testid="model-import-panel">
            <h2 class="mp-h2">导入配置</h2>
            <p class="mp-hint">{{ IMPORT_NOTE }}</p>
            <input
              class="mp-file"
              type="file"
              accept=".json,application/json"
              data-testid="model-import-file"
              @change="onImportFile"
            />
            <textarea
              v-model="importText"
              class="mp-textarea"
              rows="5"
              placeholder='或直接把导出文件里的 JSON 粘贴到这里：{"profiles":[…]} 或 […]'
              data-testid="model-import-text"
            />
            <div class="mp-actions">
              <button class="mp-btn is-primary" type="button" :disabled="importing" data-testid="model-import-run" @click="runImport">
                {{ importing ? '导入中…' : '开始导入' }}
              </button>
              <span v-if="importFileName" class="mp-hint">已选择文件：{{ importFileName }}</span>
            </div>
          </section>

          <!-- ── 左：配置列表；右：编辑器 + 探活面板 ─────────────────────── -->
          <div class="mp-grid">
            <div class="mp-col">
              <h2 class="mp-h2">已保存的配置（{{ profiles.length }}）</h2>
              <p class="mp-hint">
                列表里的协议 / has_key / active 都是服务端下发的安全视图：没有密钥值，也没有敏感头的值。
              </p>
              <p v-if="!profiles.length" class="mp-empty" data-testid="model-empty">
                还没有任何接入配置。点右上「新建配置」，选一个预设、填入 API 密钥后「保存并启用」。
                <span v-if="config?.environment_fallback || config?.source === 'environment'">
                  注意：环境变量里似乎有兜底密钥，问数可能仍在工作；但那些密钥不在本页的配置里，
                  这里既看不到也不能证明它当前生效。
                </span>
              </p>
              <ProfileList
                v-else
                :items="profiles"
                :active="activeId"
                :protocols="protocols"
                :busy-id="busyId"
                :deleting-id="deletingId"
                :editing-id="editing?.id ?? null"
                :probing-id="probingId"
                @edit="edit"
                @activate="activate"
                @probe="probeProfile"
                @remove="remove"
              />
            </div>

            <div class="mp-col">
              <ProfileEditor
                ref="editor"
                :protocols="protocols"
                :saving="saving"
                @save="save"
                @probe="probeForm"
                @fetch-models="fetchModels"
                @invalid="onInvalid"
              />
              <ProbePanel
                :probing="probing"
                :test-result="testResult"
                :test-error="testError"
                :test-at="testAt"
                :test-target="testTarget"
                :models-loading="modelsLoading"
                :models="models"
                :models-error="modelsError"
                :models-at="modelsAt"
                @pick-model="onPickModel"
              />
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 外壳沿用 RunsPage.vue（mainpage/center/topbar/body + tb-*）；
   本视图自己的类一律 mp- 前缀：Tailwind v4 的 @layer utilities 会生成同名类，
   撞上就会出现「只声明了部分属性、其余被工具类接管」的怪象（仓库里已出过两次事故）。 */
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
.tb-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.tb-note {
  font-size: 12.5px;
  color: var(--color-muted);
}
.tb-action {
  margin-left: auto;
  font: inherit;
  font-size: 12.5px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.tb-action:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.tb-action:disabled {
  opacity: 0.6;
  cursor: default;
}
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 28px 48px;
  width: 100%;
  max-width: 1320px;
}
.mp-notice {
  margin: 0 0 14px;
  font-size: 12.5px;
  line-height: 1.7;
  padding: 9px 12px;
  border-radius: 10px;
  border: 1px solid var(--color-line);
  color: var(--color-body);
}
.mp-notice.tone-ok {
  border-color: rgba(22, 163, 74, 0.35);
  background: rgba(22, 163, 74, 0.06);
}
.mp-notice.tone-bad {
  border-color: rgba(225, 29, 72, 0.3);
  background: rgba(225, 29, 72, 0.05);
  color: #e11d48;
}
.mp-notice.tone-info {
  border-color: rgba(37, 99, 235, 0.3);
  background: rgba(37, 99, 235, 0.05);
  color: var(--color-body);
}
.mp-panel {
  border: 1px solid var(--color-line);
  border-radius: 12px;
  background: var(--color-panel);
  padding: 14px 16px;
  margin-bottom: 14px;
}
.mp-panel-perm {
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.05);
}
.mp-panel-bad {
  border-color: rgba(225, 29, 72, 0.3);
  background: rgba(225, 29, 72, 0.04);
}
.mp-panel-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.mp-h2 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}
.mp-panel-head .mp-h2 {
  margin: 0;
}
.mp-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-line);
  color: var(--color-muted);
}
.mp-chip.tone-ok {
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.35);
  background: rgba(22, 163, 74, 0.08);
}
.mp-line {
  margin: 6px 0 0;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--color-body);
  word-break: break-word;
}
.mp-hint {
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-muted);
}
.mp-warn {
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: #d97706;
}
.mp-bad {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: #e11d48;
  line-height: 1.7;
}
.mp-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.mp-confirm {
  font-size: 11.5px;
  color: #e11d48;
}
.mp-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.mp-btn {
  font: inherit;
  font-size: 12px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-body);
  cursor: pointer;
}
.mp-btn:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.mp-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.mp-btn.is-primary {
  border-color: rgba(37, 99, 235, 0.45);
  background: rgba(37, 99, 235, 0.08);
  color: var(--color-accent);
}
.mp-btn.is-danger {
  color: #e11d48;
  border-color: rgba(225, 29, 72, 0.3);
}
.mp-file {
  display: block;
  margin-top: 8px;
  font: inherit;
  font-size: 12px;
  color: var(--color-body);
}
.mp-textarea {
  display: block;
  width: 100%;
  margin-top: 8px;
  font: inherit;
  font-size: 12px;
  line-height: 1.6;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--color-line);
  background: var(--color-panel);
  color: var(--color-text);
  resize: vertical;
}
.mp-textarea:focus {
  outline: none;
  border-color: var(--color-accent);
}
.mp-grid {
  display: grid;
  grid-template-columns: minmax(0, 380px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.mp-col {
  min-width: 0;
}
.mp-empty {
  margin: 8px 0 0;
  padding: 22px 0;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--color-muted);
}
code {
  font-size: 11.5px;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--color-ink);
  color: var(--color-text);
}
@media (max-width: 980px) {
  .mp-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
