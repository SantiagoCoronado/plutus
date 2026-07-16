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
  const parse = makeSseParser(onEvent)
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    lines.forEach(parse)
  }
  if (buffer) parse(buffer)
  parse('') // flush a frame the stream ended without terminating
}

// Per the SSE spec, consecutive `data:` lines concatenate with "\n" and the
// event dispatches on the BLANK line — a JSON payload containing a newline
// arrives as several data: lines and must be reassembled before parsing.
// Module-level state would leak across streams, so the parser is a factory.
export function makeSseParser(onEvent: (event: SseEvent) => void) {
  let eventName = 'message'
  let dataLines: string[] = []

  return (line: string) => {
    line = line.endsWith('\r') ? line.slice(0, -1) : line
    if (line === '') {
      if (dataLines.length > 0) {
        try {
          onEvent({ type: eventName, data: JSON.parse(dataLines.join('\n')) })
        } catch {
          // malformed frame — skip rather than kill the stream
        }
      }
      eventName = 'message'
      dataLines = []
      return
    }
    if (line.startsWith(':')) return // heartbeat comment
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      const value = line.slice(5)
      dataLines.push(value.startsWith(' ') ? value.slice(1) : value)
    }
  }
}
