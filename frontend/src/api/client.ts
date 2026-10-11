import type {
  ChatPayload,
  TraceEvent,
  EvidenceGraph,
  FinalResult,
  ModelConfigView,
  ModelTestResult,
} from './types'

// 统一走相对路径：开发时由 Vite 代理到 127.0.0.1:8000，生产时与后端同源。
export async function health(): Promise<Record<string, any>> {
  const res = await fetch('/health')
  if (!res.ok) throw new Error(`health ${res.status}`)
  return res.json()
}

export async function listRuns(limit = 20): Promise<{ runs: any[] }> {
  const res = await fetch(`/v1/runs?limit=${limit}`)
  if (!res.ok) throw new Error(`listRuns ${res.status}`)
  return res.json()
}

export async function getEvidence(runId: string): Promise<EvidenceGraph> {
  const res = await fetch(`/v1/runs/${runId}/evidence`)
  if (!res.ok) throw new Error(`evidence ${res.status}`)
  return res.json()
}

export async function getEvents(runId: string): Promise<{ events: TraceEvent[] }> {
  const res = await fetch(`/v1/runs/${runId}/events`)
  if (!res.ok) throw new Error(`events ${res.status}`)
  return res.json()
}

/**
 * CSRF：0.39.0 起写操作（含 POST /v1/chat/stream）必须带 X-CSRF-Token，
 * 令牌由 GET /v1/auth/me 下发，写请求通过同源凭据与服务端 CSRF 校验，
 * 否则问答请求会以 403 CSRF_REQUIRED 失败。
 */
let csrfToken: string | null = null

export async function csrf(): Promise<string> {
  if (csrfToken) return csrfToken
  const res = await fetch('/v1/auth/me')
  if (!res.ok) throw new Error(`auth/me ${res.status}`)
  const data = await res.json()
  csrfToken = data?.csrf_token ?? ''
  return csrfToken ?? ''
}

/** 流式提问：POST /v1/chat/stream，手动解析 SSE（EventSource 不支持 POST）。 */
export async function streamChat(
  payload: ChatPayload,
  onEvent: (ev: TraceEvent) => void,
): Promise<FinalResult> {
  const token = await csrf()
  const res = await fetch('/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
    body: JSON.stringify(payload),
  })

  if (!res.ok || !res.body) {
    let detail = ''
    try {
      const j = await res.json()
      detail = typeof j?.detail === 'string' ? j.detail : JSON.stringify(j)
    } catch {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let final: FinalResult | null = null

  const processChunk = (chunk: string) => {
    let data: string | null = null
    for (const line of chunk.split('\n')) {
      if (line.startsWith('data:')) data = line.slice(5).trim()
    }
    if (!data) return
    const ev: TraceEvent = JSON.parse(data)
    onEvent(ev)
    if (
      ev.event_type === 'answer.completed' ||
      ev.event_type === 'answer.abstained' ||
      ev.event_type === 'answer.clarification' ||
      ev.event_type === 'run.failed'
    ) {
      final = ev.output_summary as unknown as FinalResult
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (raw.trim()) processChunk(raw)
    }
  }
  if (buffer.trim()) processChunk(buffer)

  if (!final) throw new Error('未收到最终结果（流可能中断）')
  return final
}

/**
 * 当前启用配置：GET /v1/model/config。
 * 它属于 SENSITIVE_READ_PREFIXES，中间件会做同源校验——必须与后端同源调用
 * （所以开发时也走 vite 代理，不要直连另一个端口的绝对地址）。
 * 返回体只说明「配了没有」，不含连通性：has_key 为 true 只等于密钥已填写。
 */
export async function modelConfig(): Promise<ModelConfigView> {
  const res = await fetch('/v1/model/config')
  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      detail = typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? j)
    } catch {
      /* ignore */
    }
    throw new Error(detail || `model/config HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * 连通性检测：POST /v1/model/config/test（后端 api.py::probe_chat）。
 * 请求体就是 ProfileRequest，全部字段可选；**空对象 `{}` 即合法输入**——
 * resolve_profile 会取「当前启用配置 + 环境变量 + 默认值」解析出的 profile，
 * 与 GET /v1/model/config 同一份来源。想测别的目标可传
 * {profile_id} 或 {base_url, model, api_key, protocol}。
 * 这是写操作：需要 CSRF 令牌，且服务端做同源校验。
 * 注意它**不会写库、也不落盘**任何检测结果——持久化由前端自己负责。
 */
export async function testModelConfig(
  body: Record<string, any> = {},
): Promise<ModelTestResult> {
  const token = await csrf()
  const res = await fetch('/v1/model/config/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      const d = j?.detail
      detail = typeof d === 'string' ? d : (d?.error_code || d?.message || JSON.stringify(d ?? j))
    } catch {
      /* ignore */
    }
    const err = new Error(detail || `model/config/test HTTP ${res.status}`)
    ;(err as any).errorCode = `HTTP_${res.status}`
    throw err
  }
  return res.json()
}
