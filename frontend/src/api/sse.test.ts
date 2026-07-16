import { describe, expect, it } from 'vitest'
import { makeSseParser, type SseEvent } from './sse'

function collect(lines: string[]): SseEvent[] {
  const events: SseEvent[] = []
  const parse = makeSseParser((event) => events.push(event))
  lines.forEach(parse)
  parse('')
  return events
}

describe('makeSseParser', () => {
  it('dispatches a single-line frame with its event name', () => {
    const events = collect(['event: text_delta', 'data: {"text":"hi"}', ''])
    expect(events).toEqual([{ type: 'text_delta', data: { text: 'hi' } }])
  })

  it('concatenates consecutive data: lines before parsing (SSE spec)', () => {
    // pretty-printed JSON arrives as one data: line per source line; the old
    // parser JSON.parsed each line alone and silently dropped the frame
    const events = collect(['event: done', 'data: {"text":', 'data: "hello"}', ''])
    expect(events).toEqual([{ type: 'done', data: { text: 'hello' } }])
  })

  it('defaults the event name to message and resets it per frame', () => {
    const events = collect([
      'data: {"a":1}',
      '',
      'event: named',
      'data: {"b":2}',
      '',
      'data: {"c":3}',
      '',
    ])
    expect(events.map((e) => e.type)).toEqual(['message', 'named', 'message'])
  })

  it('ignores heartbeat comments without disturbing an open frame', () => {
    const events = collect(['event: tick', ': ping', 'data: {"n":1}', ''])
    expect(events).toEqual([{ type: 'tick', data: { n: 1 } }])
  })

  it('skips malformed JSON rather than throwing', () => {
    const events = collect(['data: {not json', '', 'data: {"ok":true}', ''])
    expect(events).toEqual([{ type: 'message', data: { ok: true } }])
  })

  it('tolerates CRLF line endings', () => {
    const events = collect(['event: tick\r', 'data: {"n":2}\r', '\r'])
    expect(events).toEqual([{ type: 'tick', data: { n: 2 } }])
  })

  it('strips at most one leading space after data:', () => {
    const events = collect(['data: {"s":" padded"}', ''])
    expect(events).toEqual([{ type: 'message', data: { s: ' padded' } }])
  })
})
