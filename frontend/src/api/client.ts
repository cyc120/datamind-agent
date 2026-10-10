import type {
  ChatPayload,
  TraceEvent,
  EvidenceGraph,
  FinalResult,
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
