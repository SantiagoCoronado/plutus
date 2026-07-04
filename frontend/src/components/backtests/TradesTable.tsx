import {
  fmtNum,
  fmtPct,
  pctClass,
  type ScreenHolding,
  type StrategyTrade,
} from '../../api/client'

export function StrategyTradesTable({ trades }: { trades: StrategyTrade[] }) {
  if (trades.length === 0)
    return <p className="text-sm text-zinc-600">No trades were triggered in this window.</p>
  return (
    <div className="overflow-x-auto rounded border border-zinc-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500">
            <th className="px-3 py-2 font-normal">Entry</th>
            <th className="px-3 py-2 font-normal">Exit</th>
            <th className="px-3 py-2 text-right font-normal">Entry px</th>
            <th className="px-3 py-2 text-right font-normal">Exit px</th>
            <th className="px-3 py-2 text-right font-normal">P&L</th>
            <th className="px-3 py-2 text-right font-normal">P&L %</th>
            <th className="px-3 py-2 text-right font-normal">Bars</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-900">
          {trades.map((trade, i) => (
            <tr key={i} className="hover:bg-zinc-900/50">
              <td className="px-3 py-1.5">{trade.entry_ts}</td>
              <td className="px-3 py-1.5">{trade.exit_ts}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(trade.entry_price)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(trade.exit_price)}</td>
              <td className={`px-3 py-1.5 text-right tabular-nums ${pctClass(trade.pnl)}`}>
                {fmtNum(trade.pnl)}
              </td>
              <td className={`px-3 py-1.5 text-right tabular-nums ${pctClass(trade.pnl_pct)}`}>
                {fmtPct(trade.pnl_pct)}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{trade.bars_held}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function HoldingsLog({ holdings }: { holdings: ScreenHolding[] }) {
  if (holdings.length === 0) return <p className="text-sm text-zinc-600">No rebalances logged.</p>
  return (
    <div className="max-h-80 overflow-y-auto rounded border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-zinc-950">
          <tr className="text-left text-xs text-zinc-500">
            <th className="px-3 py-2 font-normal">Rebalance date</th>
            <th className="px-3 py-2 font-normal">Holdings</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-900">
          {holdings.map((entry) => (
            <tr key={entry.date} className="hover:bg-zinc-900/50">
              <td className="px-3 py-1.5 align-top tabular-nums">{entry.date}</td>
              <td className="px-3 py-1.5">
                {entry.symbols.length === 0 ? (
                  <span className="text-zinc-600">cash</span>
                ) : (
                  entry.symbols.join(', ')
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
