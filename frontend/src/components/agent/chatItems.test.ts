import { describe, expect, it } from 'vitest'
import { applyEvent, type ChatItem } from './chatItems'

const start: ChatItem[] = []

function run(events: [string, Record<string, unknown>][]): ChatItem[] {
  return events.reduce((list, [type, data]) => applyEvent(list, type, data), start)
}

describe('applyEvent', () => {
  it('appends text deltas to the streaming assistant bubble', () => {
    const items = run([
      ['text_delta', { text: 'Hel' }],
      ['text_delta', { text: 'lo' }],
    ])
    expect(items).toEqual([{ kind: 'assistant', text: 'Hello', streaming: true }])
  })

  it('starts a NEW bubble after a tool call ends the previous one', () => {
    const items = run([
      ['text_delta', { text: 'thinking' }],
      ['tool_call', { name: 'get_quote', tool_call_id: 'c1' }],
      ['text_delta', { text: 'answer' }],
    ])
    expect(items.map((i) => i.kind)).toEqual(['assistant', 'tool', 'assistant'])
    expect(items[0]).toMatchObject({ streaming: false })
    expect(items[2]).toMatchObject({ text: 'answer', streaming: true })
  })

  it('matches tool results by call id, not name', () => {
    const items = run([
      ['tool_call', { name: 'get_quote', tool_call_id: 'c1' }],
      ['tool_call', { name: 'get_quote', tool_call_id: 'c2' }],
      ['tool_result', { name: 'get_quote', tool_call_id: 'c2', ok: true, summary: 'second' }],
    ])
    // only c2 resolved; c1 still running — the old code closed BOTH
    expect(items[0]).toMatchObject({ kind: 'tool', callId: 'c1', running: true })
    expect(items[1]).toMatchObject({
      kind: 'tool',
      callId: 'c2',
      running: false,
      ok: true,
      summary: 'second',
    })
  })

  it('without ids, a result closes only the OLDEST running call of that name', () => {
    const items = run([
      ['tool_call', { name: 'get_quote' }],
      ['tool_call', { name: 'get_quote' }],
      ['tool_result', { name: 'get_quote', ok: true }],
    ])
    expect(items[0]).toMatchObject({ running: false, ok: true })
    expect(items[1]).toMatchObject({ running: true })
  })

  it('a confirmation claims exactly one running tool and adds a card', () => {
    const items = run([
      ['tool_call', { name: 'create_mandate', tool_call_id: 'c1' }],
      [
        'confirmation_required',
        { name: 'create_mandate', tool_call_id: 'c1', confirmation_id: 7, arguments: { a: 1 } },
      ],
    ])
    expect(items[0]).toMatchObject({ kind: 'tool', running: false, summary: expect.stringContaining('awaiting') })
    expect(items[1]).toMatchObject({ kind: 'confirmation', id: 7, args: { a: 1 } })
  })

  it('done stops streaming; errors append a bubble', () => {
    const items = run([
      ['text_delta', { text: 'hi' }],
      ['error', { message: 'boom' }],
      ['done', {}],
    ])
    expect(items[0]).toMatchObject({ kind: 'assistant', streaming: false })
    expect(items[1]).toEqual({ kind: 'error', text: 'boom' })
  })

  it('unknown event types are ignored', () => {
    expect(run([['mystery', { x: 1 }]])).toEqual([])
  })
})
