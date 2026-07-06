import { useEffect, useState } from 'react'
import { openQuoteStream, type QuoteStatus, type Tick } from '../api/ws'

export interface UseQuotesResult {
  quotes: Record<string, Tick>
  status: QuoteStatus
}

/** Subscribe to live quotes for `symbols`, keeping a symbol -> latest-tick map.
 * One websocket per hook instance; re-opens when the symbol set changes. */
export function useQuotes(symbols: string[]): UseQuotesResult {
  const [quotes, setQuotes] = useState<Record<string, Tick>>({})
  const [status, setStatus] = useState<QuoteStatus>('closed')

  // stable dependency: sorted, de-duplicated, uppercased symbol list
  const key = [...new Set(symbols.map((s) => s.toUpperCase()))].sort().join(',')

  useEffect(() => {
    if (!key) {
      setStatus('closed')
      return
    }
    const list = key.split(',')
    const stream = openQuoteStream(
      list,
      (tick) => setQuotes((prev) => ({ ...prev, [tick.symbol.toUpperCase()]: tick })),
      setStatus,
    )
    return () => stream.close()
  }, [key])

  return { quotes, status }
}
