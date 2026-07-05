import { getToken } from './client'

export interface SseEvent {
  type: string
  data: Record<string, unknown>
}

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

/** POST + parse the SSE response. EventSource can't send the bearer header,
 * so this reads the body stream by hand (precedent: backtestReportBlob). */
export async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`stream failed (${resp.status}): ${await resp.text()}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'

  const handleLine = (line: string) => {
    if (line.startsWith(':')) return // heartbeat comment
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      try {
        onEvent({ type: eventName, data: JSON.parse(line.slice(5).trim()) })
      } catch {
        // malformed frame — skip rather than kill the stream
      }
      eventName = 'message'
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    lines.forEach(handleLine)
  }
  if (buffer) handleLine(buffer)
}
