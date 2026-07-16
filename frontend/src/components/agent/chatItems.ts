import type { AgentConfirmation, AgentMessage } from '../../api/client'

export type ChatItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming?: boolean }
  | {
      kind: 'tool'
      name: string
      callId?: string | null
      running?: boolean
      ok?: boolean
      summary?: string | null
      error?: string | null
    }
  | {
      kind: 'confirmation'
      id: number
      name: string
      args: Record<string, unknown>
      summary?: string | null
      resolving?: boolean
      resolved?: 'approved' | 'rejected' | 'failed'
      resolution?: string | null
    }
  | { kind: 'error'; text: string }

/** Rebuild the display transcript from persisted rows + pending confirmations. */
export function itemsFromServer(
  messages: AgentMessage[],
  pending: AgentConfirmation[],
): ChatItem[] {
  const items: ChatItem[] = []
  for (const message of messages) {
    if (message.role === 'user') {
      items.push({ kind: 'user', text: message.content ?? '' })
    } else if (message.role === 'assistant') {
      if (message.content) items.push({ kind: 'assistant', text: message.content })
    } else if (message.role === 'tool') {
      const status = (message.tool_result?.status as string) ?? 'ok'
      if (status === 'needs_confirmation') continue // the card carries this
      items.push({
        kind: 'tool',
        name: message.tool_name ?? 'tool',
        ok: status === 'ok',
        error: status === 'error' ? String(message.tool_result?.error ?? '') : null,
        summary: null,
      })
    }
  }
  for (const confirmation of pending) {
    items.push({
      kind: 'confirmation',
      id: confirmation.id,
      name: confirmation.name,
      args: confirmation.arguments,
      summary: confirmation.result_summary,
    })
  }
  return items
}

/** Fold one SSE event into the transcript. Pure — unit-tested directly. */
export function applyEvent(
  list: ChatItem[],
  type: string,
  data: Record<string, unknown>,
): ChatItem[] {
  switch (type) {
    case 'text_delta': {
      const last = list[list.length - 1]
      if (last?.kind === 'assistant' && last.streaming) {
        return [
          ...list.slice(0, -1),
          { ...last, text: last.text + String(data.text ?? '') },
        ]
      }
      return [...list, { kind: 'assistant', text: String(data.text ?? ''), streaming: true }]
    }
    case 'tool_call':
      return [
        ...list.map((item) =>
          item.kind === 'assistant' ? { ...item, streaming: false } : item,
        ),
        {
          kind: 'tool',
          name: String(data.name),
          callId: data.tool_call_id != null ? String(data.tool_call_id) : null,
          running: true,
        },
      ]
    case 'tool_result': {
      // Match by call id when the provider sends one — two parallel calls to
      // the same tool must not both be closed by the first result. Fallback:
      // the OLDEST running item with that name, never all of them.
      const callId = data.tool_call_id != null ? String(data.tool_call_id) : null
      const index = list.findIndex(
        (item) =>
          item.kind === 'tool' &&
          item.running &&
          (callId && item.callId ? item.callId === callId : item.name === data.name),
      )
      if (index === -1) return list
      const item = list[index] as Extract<ChatItem, { kind: 'tool' }>
      return [
        ...list.slice(0, index),
        {
          ...item,
          running: false,
          ok: Boolean(data.ok),
          summary: (data.summary as string) ?? null,
          error: (data.error as string) ?? null,
        },
        ...list.slice(index + 1),
      ]
    }
    case 'confirmation_required': {
      const callId = data.tool_call_id != null ? String(data.tool_call_id) : null
      let claimed = false
      return [
        ...list.map((item) => {
          if (
            !claimed &&
            item.kind === 'tool' &&
            item.running &&
            (callId && item.callId ? item.callId === callId : item.name === data.name)
          ) {
            claimed = true
            return { ...item, running: false, ok: true, summary: 'proposed — awaiting approval' }
          }
          return item
        }),
        {
          kind: 'confirmation',
          id: Number(data.confirmation_id),
          name: String(data.name),
          args: (data.arguments as Record<string, unknown>) ?? {},
          summary: (data.summary as string) ?? null,
        },
      ]
    }
    case 'error':
      return [...list, { kind: 'error', text: String(data.message ?? 'unknown error') }]
    case 'done':
      return list.map((item) =>
        item.kind === 'assistant' ? { ...item, streaming: false } : item,
      )
    default:
      return list
  }
}
