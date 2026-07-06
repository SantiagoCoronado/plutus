import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtNum, fmtPct, pctClass, type WatchlistItem } from '../../api/client'
import { useQuotes } from '../../hooks/useQuotes'
import Sparkline from '../Sparkline'

/** The Default watchlist with a live quote, day change, and 7-day sparkline per row. */
export default function WatchlistPanel() {
  const [items, setItems] = useState<WatchlistItem[] | null>(null)
  const [spark, setSpark] = useState<Record<number, number[]>>({})

  useEffect(() => {
    let cancelled = false
    api
      .watchlists()
      .then((lists) => {
        const preferred = lists.find((l) => l.name === 'Default') ?? lists[0]
        const list = preferred?.items ?? []
        if (cancelled) return
        setItems(list)
        // 7-day close series per row (bounded: a hand-curated list)
        list.forEach((item) => {
          api
            .ohlcv(item.asset_id, '1d')
            .then((res) => {
              if (cancelled) return
              const closes = res.candles.slice(-7).map((c) => c.close)
              setSpark((prev) => ({ ...prev, [item.asset_id]: closes }))
            })
            .catch(() => {})
        })
      })
      .catch(() => setItems([]))
    return () => {
      cancelled = true
    }
  }, [])

  const symbols = useMemo(() => (items ?? []).map((i) => i.symbol), [items])
  const { quotes } = useQuotes(symbols)

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-300">Watchlist</h2>
        <Link to="/watchlists" className="text-xs text-sky-400 hover:text-sky-300">
          manage →
        </Link>
      </div>

      {items === null ? (
        <p className="py-6 text-center text-sm text-zinc-600">Loading…</p>
      ) : items.length === 0 ? (
        <p className="py-6 text-center text-sm text-zinc-600">Watchlist is empty.</p>
      ) : (
        <ul className="divide-y divide-zinc-900">
          {items.map((item) => {
            const tick = quotes[item.symbol.toUpperCase()]
            const live = tick !== undefined
            const price = live ? tick.price : item.close
            // live change_pct is a percent; fmtPct wants a fraction
            const change = live ? tick.change_pct / 100 : item.return_1d
            return (
              <li key={item.asset_id} className="flex items-center gap-3 py-2">
                <Link
                  to={`/asset/${item.asset_id}`}
                  className="w-16 shrink-0 font-medium hover:text-sky-300"
                >
                  {item.symbol}
                </Link>
                <Sparkline values={spark[item.asset_id] ?? []} width={70} height={22} />
                <span className="ml-auto font-mono text-sm tabular-nums text-zinc-300">
                  <span
                    className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                      live ? 'bg-emerald-400' : 'bg-zinc-700'
                    }`}
                    title={live ? `live · ${tick.source}` : 'end-of-day close'}
                  />
                  {fmtNum(price)}
                </span>
                <span className={`w-16 text-right font-mono text-xs tabular-nums ${pctClass(change)}`}>
                  {fmtPct(change)}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
