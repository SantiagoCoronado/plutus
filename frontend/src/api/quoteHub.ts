import { openQuoteStream, type QuoteStatus, type QuoteStream, type Tick } from './ws'

/** One websocket for the whole app. The dashboard used to open three sockets
 * (market strip, watchlist, heatmap each ran useQuotes); now every consumer
 * registers here and the hub keeps a single connection subscribed to the
 * union of their symbols, resubscribing in place instead of reconnecting. */

interface Subscriber {
  symbols: Set<string>
  onTick: (tick: Tick) => void
  onStatus?: (status: QuoteStatus) => void
}

export interface QuoteSubscription {
  update(symbols: string[]): void
  unsubscribe(): void
}

const subscribers = new Set<Subscriber>()
let stream: QuoteStream | null = null
let lastStatus: QuoteStatus = 'closed'

const upper = (s: string) => s.toUpperCase()

function union(): string[] {
  const all = new Set<string>()
  for (const sub of subscribers) sub.symbols.forEach((s) => all.add(s))
  return [...all].sort()
}

function handleTick(tick: Tick) {
  const symbol = upper(tick.symbol)
  for (const sub of subscribers) {
    if (sub.symbols.has(symbol)) sub.onTick(tick)
  }
}

function handleStatus(status: QuoteStatus) {
  lastStatus = status
  for (const sub of subscribers) sub.onStatus?.(status)
}

function sync() {
  const symbols = union()
  if (symbols.length === 0) {
    stream?.close()
    stream = null
    lastStatus = 'closed'
    return
  }
  if (stream) {
    stream.setSymbols(symbols)
  } else {
    stream = openQuoteStream(symbols, handleTick, handleStatus)
  }
}

export function subscribeQuotes(
  symbols: string[],
  onTick: (tick: Tick) => void,
  onStatus?: (status: QuoteStatus) => void,
): QuoteSubscription {
  const sub: Subscriber = { symbols: new Set(symbols.map(upper)), onTick, onStatus }
  subscribers.add(sub)
  onStatus?.(lastStatus)
  sync()
  return {
    update(next: string[]) {
      sub.symbols = new Set(next.map(upper))
      sync()
    },
    unsubscribe() {
      subscribers.delete(sub)
      sync()
    },
  }
}
