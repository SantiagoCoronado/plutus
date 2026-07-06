import { getToken } from './client'

export interface Tick {
  symbol: string
  price: number
  change_pct: number
  ts: string
  source: string
}

export type QuoteStatus = 'open' | 'closed' | 'error'

export interface QuoteStream {
  close(): void
  setSymbols(symbols: string[]): void
}

const BACKOFF_START_MS = 1000
const BACKOFF_MAX_MS = 15000

/** Open one live-quote websocket to /ws/quotes. Sibling of sse.ts; the browser
 * can't send the bearer header on a websocket, so the token rides as ?token=.
 * Auto-reconnects with capped backoff and re-subscribes on reopen. */
export function openQuoteStream(
  symbols: string[],
  onTick: (tick: Tick) => void,
  onStatus?: (status: QuoteStatus) => void,
): QuoteStream {
  let ws: WebSocket | null = null
  let current = [...symbols]
  let backoff = BACKOFF_START_MS
  let closedByUser = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  const subscribe = () => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'subscribe', symbols: current }))
    }
  }

  const connect = () => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    const url = `${proto}//${window.location.host}/ws/quotes?token=${encodeURIComponent(token)}`
    ws = new WebSocket(url)

    ws.onopen = () => {
      backoff = BACKOFF_START_MS
      onStatus?.('open')
      subscribe()
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'tick') {
          onTick({
            symbol: msg.symbol,
            price: msg.price,
            change_pct: msg.change_pct,
            ts: msg.ts,
            source: msg.source,
          })
        }
      } catch {
        // ignore malformed frames
      }
    }
    ws.onerror = () => onStatus?.('error')
    ws.onclose = () => {
      onStatus?.('closed')
      if (closedByUser) return
      reconnectTimer = setTimeout(connect, backoff)
      backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
    }
  }

  connect()

  return {
    close() {
      closedByUser = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    },
    setSymbols(next: string[]) {
      current = [...next]
      subscribe()
    },
  }
}
