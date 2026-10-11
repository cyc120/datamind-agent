import { computed, ref } from 'vue'
import { health, modelConfig, testModelConfig } from '../api/client'
import type { ModelConfigView } from '../api/types'

/**
 * 「模型接入」状态只有三件互不蕴含的事，任何一件都不能冒充另一件：
 *
 *   1. 服务健康      ← GET /health            （只说明后端进程活着）
 *   2. 模型配置      ← GET /v1/model/config   （has_key / model / base_url，只说明「配了没有」）
 *   3. 连通性        ← POST /v1/model/config/test（唯一能证明「模型真的能应答」的证据）
 *
 * 后端**不持久化**检测结果（/v1/model/status 这个接口根本不存在，实测 404），
 * 所以第 3 项由前端自己记：成功的/失败的结果连同时间戳写进 localStorage，
 * 刷新页面后仍然显示「最近检测：2026-10-10 14:23 · 通过」；没有记录就老实显示
 * 「未检测」，绝不因为「密钥配了」或「后端起得来」就默认成通过。
 */

/** localStorage 键名（统一 datamind. 前缀，避免与其他页面串味） */
export const CONN_STORAGE_KEY = 'datamind.model.conn.lastTest'

/** 一次连通性检测的落盘记录 */
export interface ConnRecord {
  ok: boolean
  /** 检测完成的本地时间（ISO 8601），用于显示「最近检测：…」 */
  at: string
  /** 后端实测耗时，失败时可能缺失 */
  elapsed_ms: number | null
  /** ok=false 时的机器可读原因（MODEL_NOT_FOUND / MODEL_AUTH_FAILED / REQUEST_FAILED …） */
  error_code: string | null
  /** ok=true 时是上游回复摘录，ok=false 时是失败原因 */
  message: string | null
  model: string | null
  base_url: string | null
}

export type ServiceState = 'checking' | 'ok' | 'down'
export type ConfigState = 'checking' | 'ok' | 'error'
/** untested=没有任何记录 · testing=正在检测 · pass/fail=有记录 · unconfigured=没密钥，测不了 */
export type ConnState = 'untested' | 'testing' | 'pass' | 'fail' | 'unconfigured'

/* 模块级单例状态：顶栏与侧边栏读到的是同一份，不复制成两份。 */
const serviceState = ref<ServiceState>('checking')
const serviceInfo = ref<Record<string, any> | null>(null)
const configState = ref<ConfigState>('checking')
const config = ref<ModelConfigView | null>(null)
const configError = ref('')
const lastTest = ref<ConnRecord | null>(null)
const testing = ref(false)

let bootstrapped = false

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** 2026-10-10T06:23:11.000Z → 「2026-10-10 14:23」（本地时区，分钟精度） */
export function formatStamp(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 读取落盘记录；结构不完整/被改坏一律当「没有记录」，宁可显示未检测也不假装通过。 */
function readStored(): ConnRecord | null {
  try {
    const raw = localStorage.getItem(CONN_STORAGE_KEY)
    if (!raw) return null
    const p = JSON.parse(raw)
    if (!p || typeof p !== 'object') return null
    if (typeof p.ok !== 'boolean' || typeof p.at !== 'string') return null
    if (Number.isNaN(new Date(p.at).getTime())) return null
    return {
      ok: p.ok,
      at: p.at,
      elapsed_ms: typeof p.elapsed_ms === 'number' ? p.elapsed_ms : null,
      error_code: typeof p.error_code === 'string' ? p.error_code : null,
      message: typeof p.message === 'string' ? p.message : null,
      model: typeof p.model === 'string' ? p.model : null,
      base_url: typeof p.base_url === 'string' ? p.base_url : null,
    }
  } catch {
    return null
  }
}

function persist(rec: ConnRecord): void {
  try {
    localStorage.setItem(CONN_STORAGE_KEY, JSON.stringify(rec))
  } catch {
    /* 隐私模式/存储被禁：写不进去不影响本次会话内的显示 */
  }
}

const connState = computed<ConnState>(() => {
  if (testing.value) return 'testing'
  if (configState.value === 'ok' && config.value && config.value.has_key === false) {
    return 'unconfigured'
  }
  if (!lastTest.value) return 'untested'
  return lastTest.value.ok ? 'pass' : 'fail'
})

/** 没有任何密钥时不允许（也无法）发起检测 */
const canTest = computed(() => !testing.value && config.value?.has_key !== false)

async function refresh(): Promise<void> {
  const tasks: Promise<void>[] = [
    (async () => {
      try {
        const h = await health()
        serviceInfo.value = h
        serviceState.value = h?.status === 'ok' ? 'ok' : 'down'
      } catch {
        serviceInfo.value = null
        serviceState.value = 'down'
      }
    })(),
    (async () => {
      try {
        const c = await modelConfig()
        config.value = c
        configState.value = 'ok'
        configError.value = ''
      } catch (e: any) {
        config.value = null
        configState.value = 'error'
        configError.value = e?.message || '读取失败'
      }
    })(),
  ]
  await Promise.all(tasks)
}

/**
 * 发起一次真实检测并把结果记下来。
 * 返回落盘记录，便于调用方（如测试脚本）断言；不可检测时返回 null。
 */
async function runTest(): Promise<ConnRecord | null> {
  if (testing.value) return null
  if (config.value && config.value.has_key === false) return null

  testing.value = true
  let rec: ConnRecord
  try {
    // 空 body：让后端按「当前启用配置」解析 profile，与 GET /v1/model/config 同一来源。
    const res = await testModelConfig({})
    rec = {
      ok: !!res.ok,
      at: new Date().toISOString(),
      elapsed_ms: typeof res.elapsed_ms === 'number' ? res.elapsed_ms : null,
      error_code: res.error_code ?? null,
      message: res.ok ? (res.reply ?? null) : (res.message ?? null),
      model: res.model ?? config.value?.model ?? null,
      base_url: res.base_url ?? config.value?.base_url ?? null,
    }
  } catch (e: any) {
    // 请求本身失败（网络/权限/CSRF）也是「检测失败」，如实记下来而不是静默吞掉。
    rec = {
      ok: false,
      at: new Date().toISOString(),
      elapsed_ms: null,
      error_code: e?.errorCode || 'REQUEST_FAILED',
      message: e?.message || '检测请求失败',
      model: config.value?.model ?? null,
      base_url: config.value?.base_url ?? null,
    }
  } finally {
    testing.value = false
  }
  lastTest.value = rec
  persist(rec)
  return rec
}

/** 页面加载时调用一次：读回上次检测结果，并刷新服务健康与模型配置。 */
async function bootstrap(): Promise<void> {
  if (bootstrapped) return
  bootstrapped = true
  if (!lastTest.value) lastTest.value = readStored()
  await refresh()
}

export function useModelAccess() {
  const serviceText = computed(() =>
    serviceState.value === 'ok' ? '在线' : serviceState.value === 'down' ? '离线' : '检测中',
  )

  const configText = computed(() => {
    if (configState.value === 'checking') return '读取中'
    if (configState.value === 'error') return '读取失败'
    return config.value?.has_key ? '已配置密钥' : '未配置密钥'
  })

  const connText = computed(() => {
    switch (connState.value) {
      case 'testing':
        return '检测中…'
      case 'pass':
        return '检测通过'
      case 'fail':
        return '检测失败'
      case 'unconfigured':
        return '未检测'
      default:
        return '未检测'
    }
  })

  /** 状态点语义，沿用仓库既有取值：成功 #16a34a / 警告 #d97706 / 失败 #e11d48 */
  const serviceDot = computed(() =>
    serviceState.value === 'ok' ? 'ok' : serviceState.value === 'down' ? 'down' : 'checking',
  )
  const configDot = computed(() => {
    if (configState.value === 'checking') return 'checking'
    if (configState.value === 'error') return 'down'
    return config.value?.has_key ? 'ok' : 'warn'
  })
  const connDot = computed(() => {
    switch (connState.value) {
      case 'testing':
        return 'checking'
      case 'pass':
        return 'ok'
      case 'fail':
        return 'down'
      default:
        return 'idle'
    }
  })

  /** 「最近检测：2026-10-10 14:23 · 通过」——没有记录就明说未检测 */
  const lastTestText = computed(() => {
    const rec = lastTest.value
    if (!rec) return '最近检测：未检测'
    const verdict = rec.ok ? '通过' : '失败'
    const cost = rec.elapsed_ms != null ? ` · ${Math.round(rec.elapsed_ms)}ms` : ''
    return `最近检测：${formatStamp(rec.at)} · ${verdict}${cost}`
  })

  /** 检测的依据细节：成功给上游回复摘要，失败给 error_code + 原因 */
  const lastTestDetail = computed(() => {
    const rec = lastTest.value
    if (!rec) return ''
    if (rec.ok) return rec.message ? `上游回复：${rec.message}` : ''
    return [rec.error_code, rec.message].filter(Boolean).join(' · ')
  })

  /** 不能测的原因（没密钥时把话说清楚，而不是给个点了没反应的按钮） */
  const testBlockedReason = computed(() => {
    if (configState.value === 'error') {
      return `模型配置读取失败：${configError.value}`
    }
    if (config.value && config.value.has_key === false) {
      return '未配置密钥，无法检测连通性'
    }
    return ''
  })

  return {
    // 服务健康
    serviceState,
    serviceInfo,
    serviceText,
    serviceDot,
    // 模型配置
    configState,
    config,
    configError,
    configText,
    configDot,
    // 连通性
    connState,
    lastTest,
    lastTestText,
    lastTestDetail,
    connText,
    connDot,
    testing,
    canTest,
    testBlockedReason,
    // 动作
    bootstrap,
    refresh,
    runTest,
  }
}
