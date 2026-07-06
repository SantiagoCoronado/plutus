import { fmtNum, fmtPct, pctClass, type MarketStripEntry } from '../../api/client'
import { useQuotes } from '../../hooks/useQuotes'

/** Full-width row of market pills, one per MARKET_STRIP entry, live over the WS.
 * '—' until a quote arrives; muted when the tick is a stale daily close. */
export default function MarketStrip({ entries }: { entries: MarketStripEntry[] }) {
  const { quotes } = useQuotes(entries.map((e) => e.symbol))

  return (
    <div className="flex flex-wrap gap-2">
      {entries.map((entry) => {
        const tick = quotes[entry.symbol.toUpperCase()]
        const stale = tick?.source === 'eod'
        // tick change_pct is a percent (1.23); fmtPct wants a fraction
        const change = tick ? tick.change_pct / 100 : null
        return (
          <div
            key={entry.symbol}
            className={`flex min-w-[130px] flex-1 items-center justify-between gap-3 rounded border border-zinc-800 px-3 py-2 ${
              stale ? 'opacity-60' : ''
            }`}
            title={tick ? `${entry.symbol} · ${tick.source}` : entry.symbol}
          >
            <div className="min-w-0">
              <p className="truncate text-[11px] text-zinc-500">{entry.label}</p>
              <p className="font-mono text-sm tabular-nums">{tick ? fmtNum(tick.price) : '—'}</p>
            </div>
            <span className={`font-mono text-xs tabular-nums ${pctClass(change)}`}>
              {change === null ? '—' : fmtPct(change, 2)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
