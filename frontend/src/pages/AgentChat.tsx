import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, ApiError, relTime, type AgentConversation } from '../api/client'
import { streamEvents } from '../api/sse'
import { applyEvent, itemsFromServer, type ChatItem } from '../components/agent/chatItems'

const inputClass =
  'w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-sky-700'

export default function AgentChat() {
  const [conversations, setConversations] = useState<AgentConversation[]>([])
  const [active, setActive] = useState<AgentConversation | null>(null)
  const [items, setItems] = useState<ChatItem[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadConversations = useCallback(() => {
    api.agentConversations().then(setConversations)
  }, [])

  useEffect(loadConversations, [loadConversations])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items])

  const openConversation = async (conversation: AgentConversation) => {
    abortRef.current?.abort()
    setStreaming(false)
    const detail = await api.agentConversation(conversation.id)
    setActive(detail.conversation)
    setItems(itemsFromServer(detail.messages, detail.pending_confirmations))
  }

  const newConversation = async () => {
    const conversation = await api.createAgentConversation()
    setConversations((list) => [conversation, ...list])
    setActive(conversation)
    setItems([])
  }

  const removeConversation = async (id: number) => {
    if (!window.confirm('Delete this conversation? Its transcript is gone for good.')) return
    try {
      await api.deleteAgentConversation(id)
    } catch (e) {
      setItems((list) => [
        ...list,
        { kind: 'error', text: `delete failed: ${e instanceof Error ? e.message : String(e)}` },
      ])
      return
    }
    setConversations((list) => list.filter((c) => c.id !== id))
    if (active?.id === id) {
      setActive(null)
      setItems([])
    }
  }

  const toggleAutonomous = async () => {
    if (!active) return
    const next = await api.patchAgentConversation(active.id, {
      autonomous: !active.autonomous,
    })
    setActive(next)
  }

  const send = async () => {
    const text = draft.trim()
    if (!text || streaming) return
    let conversation = active
    if (!conversation) {
      conversation = await api.createAgentConversation()
      setConversations((list) => [conversation!, ...list])
      setActive(conversation)
    }
    setDraft('')
    setStreaming(true)
    setItems((list) => [...list, { kind: 'user', text }])

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamEvents(
        `/agent/conversations/${conversation.id}/messages`,
        { content: text },
        (event) => setItems((list) => applyEvent(list, event.type, event.data)),
        controller.signal,
      )
    } catch (e) {
      // switching conversations aborts the active stream on purpose — that
      // cancellation must not surface as an error bubble in the NEW chat
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setItems((list) => [
          ...list,
          { kind: 'error', text: e instanceof Error ? e.message : String(e) },
        ])
      }
    } finally {
      setStreaming(false)
      loadConversations()
    }
  }

  // in-flight guard: React state alone can't stop a double-click that lands
  // before the re-render, and a write action must never fire twice
  const resolvingRef = useRef<Set<number>>(new Set())

  const patchConfirmation = (
    id: number,
    patch: Partial<Extract<ChatItem, { kind: 'confirmation' }>>,
  ) =>
    setItems((list) =>
      list.map((item) =>
        item.kind === 'confirmation' && item.id === id ? { ...item, ...patch } : item,
      ),
    )

  const resolveConfirmation = async (id: number, approve: boolean) => {
    if (resolvingRef.current.has(id)) return
    resolvingRef.current.add(id)
    patchConfirmation(id, { resolving: true })
    try {
      const resolution = approve
        ? await api.approveConfirmation(id)
        : await api.rejectConfirmation(id)
      patchConfirmation(id, {
        resolving: false,
        resolved:
          resolution.status === 'error' ? 'failed' : (resolution.status as 'approved' | 'rejected'),
        resolution: resolution.result_summary ?? resolution.error,
      })
    } catch (e) {
      // whatever happened server-side, this card is no longer safely clickable
      const already = e instanceof ApiError && e.status === 409
      patchConfirmation(id, {
        resolving: false,
        resolved: 'failed',
        resolution: already
          ? 'already resolved elsewhere — reopen the conversation for the outcome'
          : e instanceof Error
            ? e.message
            : String(e),
      })
    } finally {
      resolvingRef.current.delete(id)
    }
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      <aside className="flex w-60 shrink-0 flex-col rounded border border-zinc-800">
        <button
          className="m-2 rounded bg-sky-700 px-3 py-1.5 text-sm hover:bg-sky-600"
          onClick={newConversation}
        >
          New chat
        </button>
        <div className="flex-1 overflow-y-auto">
          {conversations.map((conversation) => (
            <div
              key={conversation.id}
              role="button"
              tabIndex={0}
              className={`group flex cursor-pointer items-center justify-between px-3 py-2 text-xs hover:bg-zinc-900 focus:bg-zinc-900 focus:outline-none ${
                active?.id === conversation.id ? 'bg-zinc-900 text-zinc-100' : 'text-zinc-400'
              }`}
              onClick={() => openConversation(conversation)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  openConversation(conversation)
                }
              }}
            >
              <div className="min-w-0">
                <p className="truncate">{conversation.title ?? 'untitled'}</p>
                <p className="text-[10px] text-zinc-600">{relTime(conversation.updated_at)}</p>
              </div>
              <button
                aria-label="delete conversation"
                className="hidden text-zinc-600 hover:text-red-400 group-hover:block group-focus-within:block"
                onClick={(e) => {
                  e.stopPropagation()
                  removeConversation(conversation.id)
                }}
              >
                ×
              </button>
            </div>
          ))}
          {conversations.length === 0 && (
            <p className="p-3 text-xs text-zinc-600">No conversations yet.</p>
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col rounded border border-zinc-800">
        <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold">
              {active?.title ?? 'Research agent'}
            </h1>
            {active?.provider && (
              <p className="text-[10px] text-zinc-500">
                {active.provider}
                {active.model ? ` · ${active.model}` : ''}
              </p>
            )}
          </div>
          {active && (
            <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
              <input
                type="checkbox"
                checked={active.autonomous}
                onChange={toggleAutonomous}
                className="accent-violet-500"
              />
              autonomous
              {active.autonomous && (
                <span className="rounded bg-violet-950 px-1.5 py-0.5 text-[10px] text-violet-300">
                  writes run without asking
                </span>
              )}
            </label>
          )}
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {items.length === 0 && !streaming && (
            <div className="mt-12 text-center text-sm text-zinc-600">
              <p className="mb-2">Ask about any tracked asset, your portfolio, or the Inbox.</p>
              <p className="text-xs">
                “How is AAPL looking?” · “What's in my inbox worth reading?” ·
                “Create a mandate for oversold large caps”
              </p>
            </div>
          )}
          {items.map((item, index) => (
            <ChatItemView
              key={index}
              item={item}
              onResolve={resolveConfirmation}
            />
          ))}
          {streaming && items[items.length - 1]?.kind !== 'assistant' && (
            <p className="text-xs text-zinc-600">thinking…</p>
          )}
          <div ref={bottomRef} />
        </div>

        <footer className="border-t border-zinc-800 p-3">
          <div className="flex gap-2">
            <textarea
              className={`${inputClass} max-h-40 min-h-10 resize-y`}
              rows={1}
              placeholder="Ask the research agent…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
            />
            <button
              className="rounded bg-sky-700 px-4 text-sm hover:bg-sky-600 disabled:opacity-40"
              disabled={streaming || !draft.trim()}
              onClick={send}
            >
              {streaming ? '…' : 'Send'}
            </button>
          </div>
          <p className="mt-1 text-[10px] text-zinc-600">
            AI-generated analysis, informational only — never investment advice. Write
            actions ask for your approval unless autonomous mode is on.
          </p>
        </footer>
      </section>
    </div>
  )
}

function ChatItemView({
  item,
  onResolve,
}: {
  item: ChatItem
  onResolve: (id: number, approve: boolean) => void
}) {
  if (item.kind === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-sky-950/60 px-3 py-2 text-sm">
          {item.text}
        </div>
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="flex">
        <div className="max-w-[85%] rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2">
          <span className="mb-1 inline-block rounded bg-violet-950 px-1.5 py-0.5 text-[10px] text-violet-300">
            AI
          </span>
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
          </div>
        </div>
      </div>
    )
  }
  if (item.kind === 'tool') {
    return (
      <div className="flex items-center gap-2 pl-2 text-xs text-zinc-500">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            item.running ? 'animate-pulse bg-amber-400' : item.ok ? 'bg-emerald-500' : 'bg-red-500'
          }`}
        />
        <span className="font-mono">{item.name}</span>
        {item.summary && <span>· {item.summary}</span>}
        {item.error && <span className="text-red-400">· {item.error}</span>}
      </div>
    )
  }
  if (item.kind === 'confirmation') {
    return (
      <div className="rounded border border-amber-900/60 bg-amber-950/20 p-3">
        <p className="text-xs font-semibold text-amber-300">
          Proposed action: <span className="font-mono">{item.name}</span>
        </p>
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-zinc-950 p-2 text-[11px] text-zinc-400">
          {JSON.stringify(item.args, null, 2)}
        </pre>
        {item.resolved ? (
          <p
            className={`mt-2 text-xs ${
              item.resolved === 'approved'
                ? 'text-emerald-400'
                : item.resolved === 'failed'
                  ? 'text-red-400'
                  : 'text-zinc-500'
            }`}
          >
            {item.resolved}
            {item.resolution ? ` — ${item.resolution}` : ''}
          </p>
        ) : (
          <div className="mt-2 flex gap-2">
            <button
              className="rounded bg-emerald-800 px-3 py-1 text-xs hover:bg-emerald-700 disabled:opacity-40"
              disabled={item.resolving}
              onClick={() => onResolve(item.id, true)}
            >
              {item.resolving ? '…' : 'Approve'}
            </button>
            <button
              className="rounded bg-zinc-800 px-3 py-1 text-xs hover:bg-zinc-700 disabled:opacity-40"
              disabled={item.resolving}
              onClick={() => onResolve(item.id, false)}
            >
              Reject
            </button>
          </div>
        )}
      </div>
    )
  }
  return (
    <div className="rounded border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
      {item.text}
    </div>
  )
}
