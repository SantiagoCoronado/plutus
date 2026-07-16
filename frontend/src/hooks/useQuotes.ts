import { useEffect, useState } from 'react'
import { subscribeQuotes } from '../api/quoteHub'
import type { QuoteStatus, Tick } from '../api/ws'

export interface UseQuotesResult {
  quotes: Record<string, Tick>
  status: QuoteStatus
}

/** Subscribe to live quotes for `symbols`, keeping a symbol -> latest-tick map.
 * All hook instances share ONE websocket (see quoteHub); a symbol-set change
 * resubscribes in place instead of tearing the socket down.
 *
 * `throttleMs` batches tick-driven re-renders: heavy consumers (the treemap
 * redraws a whole chart per update) pass ~1000 so a busy market can't churn
 * the main thread; 0 = render every tick (strip/watchlist price flashes). */
export function useQuotes(symbols: string[], throttleMs = 0): UseQuotesResult {
  const [quotes, setQuotes] = useState<Record<string, Tick>>({})
  const [status, setStatus] = useState<QuoteStatus>('closed')

  // stable dependency: sorted, de-duplicated, uppercased symbol list
  const key = [...new Set(symbols.map((s) => s.toUpperCase()))].sort().join(',')

  useEffect(() => {
    if (!key) {
      setStatus('closed')
      return
    }
    let pending: Record<string, Tick> = {}
    let timer: ReturnType<typeof setTimeout> | null = null

    const flush = () => {
      timer = null
      const batch = pending
      pending = {}
      setQuotes((prev) => ({ ...prev, ...batch }))
    }

    const onTick = (tick: Tick) => {
      const symbol = tick.symbol.toUpperCase()
      if (throttleMs <= 0) {
        setQuotes((prev) => ({ ...prev, [symbol]: tick }))
        return
      }
      pending[symbol] = tick
      if (timer === null) timer = setTimeout(flush, throttleMs)
    }

    const subscription = subscribeQuotes(key.split(','), onTick, setStatus)
    return () => {
      if (timer !== null) clearTimeout(timer)
      subscription.unsubscribe()
    }
  }, [key, throttleMs])

  return { quotes, status }
}
