/**
 * 模型接入 —— 预设、文案与错误解释的单一来源。
 *
 * WHY 单独一个纯模块（与 components/fields/messages.ts 同一套写法）：
 *   本屏有两类「说错就害人」的话：
 *     1. 密钥/连通性的口径 —— `has_key` 只等于「密钥字段填过值」，
 *        绝不能渲染成「密钥有效」或「模型可用」；
 *     2. 错误解释 —— `/v1/model/*` 整段要求 admin（**包括 GET**），
 *        所以 403 PERMISSION_DENIED 是**正常结果**，必须被讲成权限事实，
 *        而不是「加载失败/网络错误」让人反复重试。
 *   把判定集中在这里，视图层就不会各自发明一套说法。
 */

import { formatStamp } from '../../composables/useModelAccess'

/* ── 提供方预设 ─────────────────────────────────────────────────────────
   与旧版控制台（web/app.js 的 PRESETS）逐条对齐的 10 项。
   **刻意不含 localhost:4000 / 127.0.0.1:4000**：那个本地 New API 网关已随
   「移除本地网关依赖，统一为直连上游」整体删除，再放一个指过去的预设等于给用户
   一个必然连不上的地址（旧版正是因为内置网关才出现「填好密钥却 502/挂起」）。
   「自建网关」「自定义」的地址故意留空：由用户填自己的网关地址，本仓库不内置。 */

export type ProtocolId = 'openai' | 'anthropic'
/** 表单里的鉴权风格：auto 是 UI 说法，提交时要映射成服务端的 both（见 authStyleWire）。 */
export type AuthStyleUi = 'auto' | 'x-api-key' | 'bearer'

export interface ProviderPreset {
  name: string
  protocol: ProtocolId
  /** 留空 = 由用户填写（自建网关 / 中转站） */
  base: string
  model: string
  auth?: AuthStyleUi
  /** 套用预设时要一并带给用户的口径说明 */
  hint?: string
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  { name: 'DeepSeek 官方', protocol: 'openai', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  {
    name: 'Anthropic 官方',
    protocol: 'anthropic',
    base: 'https://api.anthropic.com',
    model: 'claude-sonnet-4-5',
    auth: 'x-api-key',
  },
  {
    name: 'Claude 中转',
    protocol: 'anthropic',
    base: '',
    model: 'claude-sonnet-4-5',
    auth: 'bearer',
    hint: '中转站地址按其文档填写（通常与官方不同源）；地址不同源时服务端不会把已存密钥继承过去。',
  },
  { name: '阿里云百炼', protocol: 'openai', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { name: '智谱 AI', protocol: 'openai', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-plus' },
  { name: '月之暗面', protocol: 'openai', base: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { name: '硅基流动', protocol: 'openai', base: 'https://api.siliconflow.cn/v1', model: 'deepseek-ai/DeepSeek-V3' },
  { name: '火山方舟', protocol: 'openai', base: 'https://ark.cn-beijing.volces.com/api/v3', model: '' },
  { name: '自建网关', protocol: 'openai', base: '', model: '', hint: '填你自己网关的地址；本仓库不再内置本地 4000 网关。' },
  { name: '自定义', protocol: 'openai', base: '', model: '' },
]

/* ── 协议与鉴权 ─────────────────────────────────────────────────────── */

/** 服务端 GET /v1/model/profiles 下发的 protocols 是权威来源，这里只做兜底显示。 */
export const PROTOCOL_FALLBACK: Record<string, string> = {
  openai: 'OpenAI 兼容',
  anthropic: 'Anthropic Messages',
}

export function protocolLabel(protocol?: string | null, options?: { id: string; label: string }[]): string {
  if (!protocol) return '未知协议'
  const hit = (options || []).find((o) => o.id === protocol)
  return hit?.label || PROTOCOL_FALLBACK[protocol] || protocol
}

export interface AuthStyleOption {
  value: AuthStyleUi
  label: string
  note: string
}

/**
 * 表单只给三个选项。**auto 提交时必须写成 both**：
 * 服务端 providers.headers_for 只认 `x-api-key` / `bearer` / `both`，
 * 收到字面量 "auto" 时两个 if 都不命中，请求会**不带任何鉴权头**发出去，
 * 表现为上游 401 —— 一个几乎无法从界面看出来的坑。
 * 反方向：读取侧 historical 默认值是 "both"，展示成 auto（两种都发）。
 */
export const AUTH_STYLE_OPTIONS: AuthStyleOption[] = [
  { value: 'auto', label: '自动（两种都发）', note: '等价于服务端 both：同时发 x-api-key 与 Authorization: Bearer，兼容官方与中转。' },
  { value: 'x-api-key', label: 'x-api-key', note: '只发 x-api-key 头（Anthropic 官方口径）。' },
  { value: 'bearer', label: 'Authorization: Bearer', note: '只发 Authorization: Bearer（多数中转站口径）。' },
]

/** 已存值 → 表单显示值。未知/空值一律按 auto（= 服务端 both）显示，而不是编一个别的东西。 */
export function authStyleUi(authStyle?: string | null): AuthStyleUi {
  const value = (authStyle || '').trim().toLowerCase()
  if (value === 'x-api-key') return 'x-api-key'
  if (value === 'bearer') return 'bearer'
  return 'auto'
}

/** 表单值 → 提交值。见 AUTH_STYLE_OPTIONS 的注释：auto 必须落成 both。 */
export function authStyleWire(ui: AuthStyleUi): string {
  return ui === 'auto' ? 'both' : ui
}

/* ── 配置来源 ───────────────────────────────────────────────────────── */

export const SOURCE_TEXT: Record<string, string> = {
  app_settings: '应用设置（数据库 app_settings 里保存的接入配置）',
  environment: '环境变量兜底（页面没有可用配置，解析时落到了 NEW_API_* 环境变量）',
  unset: '未配置（应用设置与环境变量都没有可用密钥）',
}

export function sourceText(source?: string | null): string {
  if (!source) return '未知来源'
  return SOURCE_TEXT[source] || source
}

/** environment_fallback 只说「环境里存在 NEW_API_KEY」，不等于当前生效的就是它。 */
export const ENV_FALLBACK_NOTE =
  '检测到环境变量 NEW_API_KEY 存在（environment_fallback=true）：它可能在页面没有可用配置时被当作兜底密钥使用；但这里读不到它的值，也无法证明当前生效的就是它。'

/* ── 诚实口径（每一条都对应界面上必须出现的一句实话）─────────────── */

/** has_key 的真实含义：填过值 ≠ 有效 ≠ 可用 */
export const HAS_KEY_NOTE =
  'has_key 只表示「密钥字段填过值」：它既不表示密钥有效，也不表示模型可用。唯一能证明模型真的能应答的是「测试连接」的成功结果。'

/** 探活结果不落库（后端没有 /v1/model/status 这类接口，实测 404） */
export const PROBE_NOT_PERSISTED =
  '探活结果只在本页浏览器的内存里（带时间戳），刷新即消失：服务端不持久化任何检测结果。一次通过只证明「这一刻、这个地址、这个模型」能应答，不证明密钥长期有效，也不证明该模型适合本项目的问数链路。'

/** 模型名必须显式填写（服务端会静默回落默认模型） */
export const MODEL_NAME_REQUIRED =
  '请先填写模型名：留空时服务端会静默回落到默认模型，而它与本项目调优使用的模型生成的 SQL 写法不同，失败时只在下游表现为 SQL_POLICY_BLOCKED，极难归因。可先「获取模型列表」再选一个。'

/** 跨源探活被拒（400 MODEL_KEY_NOT_INHERITED） */
export const CROSS_ORIGIN_NOTE =
  '接口地址与已保存配置不同源（host/端口不同）时，服务端拒绝把已存密钥继承过去（400 MODEL_KEY_NOT_INHERITED）——这是防外泄，不是故障。要在另一个来源探活，请在该配置里显式填入这个来源自己的密钥，或把地址改回同源。'

/** DELETE /v1/model/config 的返回体是窄结构，只说明密钥被清空 */
export const CLEARED_CONFIG_NOTE =
  'DELETE /v1/model/config 的返回体只有 {has_key,key_masked,source} 三个键，只说明「密钥已清空」；它不是完整配置视图，不能拿它当配置读回。它清空的是**当前启用配置**的密钥字段（配置条目保留），其他配置不受影响，也不是删除配置。'

/** 四个密钥规则里最重要的一条：本屏不提供任何「清空密钥」表单动作 */
export const NO_CLEAR_KEY_NOTE =
  '本页（配置编辑表单）不提供「清空密钥」动作：__clear__ 只对敏感请求头和代理有效，把它当作 api_key 提交会把字面量 "__clear__" 存成密钥。留空表示保持原密钥不变。'

export const SECRET_HEADERS_NOTE =
  '敏感请求头（authorization / x-api-key / cookie…）的值保存在服务端，接口只回显键名。留空 = 保持不变（不会用空值覆盖真实凭据）；要删除必须点「删除」，它会为这一行提交 __clear__ 哨兵。'

export const PLAIN_HEADERS_NOTE =
  '非敏感请求头以本表为准：保存时整表提交，删掉一行就等于删除该头（服务端对非敏感键以提交内容为权威）。只发被编辑的那几行会删掉其余的头。'

export const PROXY_NOTE =
  '代理留空 = 本机直连、远端跟随系统代理。已保存的代理地址里若带 user:pass@，浏览器看不到凭据：原样保留即不改动；改成不含凭据的新地址会丢弃原凭据；要删除代理请点「删除代理」（提交 __clear__）。'

export const IMPORT_NOTE =
  '导入按 ccswitch 风格搬迁：同名不覆盖，一律作为新配置写入，最多 50 条。activate_first 固定为 false —— 导入不会改变你当前的启用配置。导出文件默认不含密钥；若文件里本来就没有密钥（或敏感头已被剔除），导入后需要逐条重新填写。'

/** 导出（不含密钥）的证明口径 */
export function exportNote(count: number, includeKeys: boolean): string {
  return `已导出 ${count} 套配置；返回体里的 include_keys=${String(includeKeys)} —— false 表示这份文件不含 api_key，敏感请求头已剔除，代理地址里的 user:pass@ 已去掉。本页只提供不含密钥的导出。`
}

/* ── 错误解释 ───────────────────────────────────────────────────────── */

export interface ModelErrorInfo {
  /** 后端 error_code；FastAPI 自身的 422 没有码，归一成 VALIDATION_ERROR */
  code: string
  status: number | null
  /** 可直接显示的中文（含错误码本身，便于和服务端日志对照） */
  text: string
  /** 401/403：这是权限事实，不是「加载失败」 */
  permission: 'login' | 'denied' | null
}

/** 每个码一句准话。来源：api.py / providers.py / settings_store.py（照抄不改写含义）。 */
export const CODE_TEXT: Record<string, string> = {
  // 权限与身份：/v1/model/* 整段（含 GET）都要求 admin
  PERMISSION_DENIED:
    '权限不足（PERMISSION_DENIED）：模型接入的全部接口——包括读取——都要求 admin 角色，这是正常的权限答复，不是页面坏了。',
  LOGIN_REQUIRED: '需要登录（LOGIN_REQUIRED）：请先以管理员身份登录，再回到本页。',
  SESSION_INVALID: '会话已失效（SESSION_INVALID）：请重新登录。',
  CSRF_REQUIRED: '缺少或失效的 CSRF 令牌（CSRF_REQUIRED）：写操作需要 X-CSRF-Token，请刷新页面后重试。',
  ORIGIN_NOT_ALLOWED:
    '请求来源不被允许（ORIGIN_NOT_ALLOWED）：模型接口做同源校验，必须由本页（同源）发起，不能用绝对地址直连别的端口。',
  VALIDATION_ERROR:
    '请求未通过服务端校验（FastAPI 422，没有 error_code）：字段缺失或格式不符，请检查后重试。',
  // 配置本身
  PROFILE_NAME_REQUIRED: '配置名称不能为空（PROFILE_NAME_REQUIRED）。',
  PROFILE_NOT_FOUND: '该接入配置不存在或已被删除（PROFILE_NOT_FOUND），请刷新后重试。',
  DATABASE_UNAVAILABLE: '元数据库不可用（DATABASE_UNAVAILABLE）：配置读写都依赖 app_settings。',
  MODEL_KEY_NOT_INHERITED: `跨来源探活被拒绝（MODEL_KEY_NOT_INHERITED）。${CROSS_ORIGIN_NOTE}`,
  // 探活/拉列表时上游给出的码
  MODEL_NAME_MISSING: `缺少模型名（MODEL_NAME_MISSING）。${MODEL_NAME_REQUIRED}`,
  MODEL_BASE_URL_MISSING: '接口地址为空（MODEL_BASE_URL_MISSING）：解析后没有可用的 base_url。',
  MODEL_KEY_MISSING:
    '没有可用密钥（MODEL_KEY_MISSING）：当前启用配置没有密钥，环境变量里也没有兜底密钥。',
  MODEL_AUTH_FAILED: '上游拒绝了这个密钥（MODEL_AUTH_FAILED，401/403）：密钥无效、过期或没有该模型权限。',
  MODEL_BALANCE_EMPTY: '上游账户额度不足（MODEL_BALANCE_EMPTY，402 或余额类提示）：请充值后重试。',
  MODEL_NOT_FOUND: '接口或模型不存在（MODEL_NOT_FOUND，404）：检查接口地址与模型名。',
  MODEL_RATE_LIMITED: '上游限流（MODEL_RATE_LIMITED，429）：稍后重试。',
  MODEL_TIMEOUT: '调用超时（MODEL_TIMEOUT）：可调大「超时」后再试。',
  MODEL_UNREACHABLE: '连不上上游（MODEL_UNREACHABLE）：检查接口地址、网络与代理设置。',
  MODEL_INVALID_RESPONSE: '上游没有返回 JSON（MODEL_INVALID_RESPONSE）：地址可能指到了非模型接口。',
  MODEL_EMPTY_RESPONSE: '上游返回了空内容（MODEL_EMPTY_RESPONSE）：模型可能存在但这次没有产出。',
  MODEL_UPSTREAM_ERROR: '上游返回了错误（MODEL_UPSTREAM_ERROR）：见服务端原文。',
}

/**
 * 把任意异常翻成一句可显示的中文。
 *
 * 两个必须显式处理的形状（GLOBAL CONTRACT）：
 *   1. `{"detail":{"error_code":"CODE"}}` —— client.ts 的 apiError 已经把码放进
 *      Error.errorCode；
 *   2. FastAPI 自身的 422（pydantic / Query 边界）**没有 error_code**，apiError 归一成
 *      VALIDATION_ERROR，这里按码显示，不再假装它是业务码。
 *   另外 client.ts 里 testModelConfig 只把 HTTP 状态塞进 errorCode（没有 .status），
 *   所以这里把 `HTTP_403` 归一成 PERMISSION_DENIED，否则 403 会被讲成「请求失败」。
 */
export function describeModelError(e: unknown, fallback: string): ModelErrorInfo {
  const err = e as { errorCode?: unknown; status?: unknown; message?: unknown } | null
  let code = typeof err?.errorCode === 'string' ? err.errorCode : ''
  let status = typeof err?.status === 'number' ? err.status : null
  const raw = typeof err?.message === 'string' ? err.message : ''

  const httpMatch = /^HTTP_(\d{3})$/.exec(code)
  if (httpMatch) {
    const parsed = Number(httpMatch[1])
    if (status === null) status = parsed
    if (parsed === 401) code = 'LOGIN_REQUIRED'
    else if (parsed === 403) code = 'PERMISSION_DENIED'
    else if (parsed === 422) code = 'VALIDATION_ERROR'
  }

  const permission: ModelErrorInfo['permission'] =
    code === 'PERMISSION_DENIED' ? 'denied' : code === 'LOGIN_REQUIRED' || code === 'SESSION_INVALID' ? 'login' : null

  if (code && CODE_TEXT[code]) {
    // apiError 把 detail.details 拼在码后面（`CODE {json}`）：有额外细节就附上，
    // 只有码本身时不再重复一遍（否则每句话后面都拖一个同义括号）。
    const details = raw.startsWith(code) ? raw.slice(code.length).trim() : ''
    return {
      code,
      status,
      text: details ? `${CODE_TEXT[code]}（服务端原文：${details}）` : CODE_TEXT[code],
      permission,
    }
  }
  if (!code && status === 422) {
    return {
      code: 'VALIDATION_ERROR',
      status,
      text: `${CODE_TEXT.VALIDATION_ERROR}${raw ? `（${raw}）` : ''}`,
      permission,
    }
  }
  if (code) {
    return { code, status, text: `${fallback}（${code}${raw && raw !== code ? `：${raw}` : ''}）`, permission }
  }
  return { code: '', status, text: raw ? `${fallback}：${raw}` : fallback, permission }
}

/** 探针接口（test/models）在 HTTP 200 里用 {ok:false,error_code,message} 报错。 */
export function probeFailureText(errorCode?: string | null, message?: string | null): string {
  const code = (errorCode || '').trim()
  const detail = (message || '').trim()
  if (code && CODE_TEXT[code]) {
    return detail && !CODE_TEXT[code].includes(detail) ? `${CODE_TEXT[code]}（上游原文：${detail}）` : CODE_TEXT[code]
  }
  if (code) return `探测失败（${code}）${detail ? `：${detail}` : ''}`
  return detail || '探测失败：服务端没有给出错误码与原因。'
}

/** 客户端时间戳：探活/拉列表都是本页自己的记录，用本机时区显示到分钟。 */
export function stamp(iso?: string | null): string {
  if (!iso) return ''
  return formatStamp(iso)
}
