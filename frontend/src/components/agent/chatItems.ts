import type { AgentConfirmation, AgentMessage } from '../../api/client'

export type ChatItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming?: boolean }
  | {
      kind: 'tool'
      name: string
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
