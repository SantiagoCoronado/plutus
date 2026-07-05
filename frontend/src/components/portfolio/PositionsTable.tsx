import { Link } from 'react-router-dom'
import { fmtPct, pctClass, type PositionsReport } from '../../api/client'
import { fmtMoney } from './shared'

export default function PositionsTable({
  report,
  currency,
}: {
  report: PositionsReport
  currency: string
}) {
  if (report.positions.length === 0 && report.cash.length === 0) {
    return (
      <div className="rounded border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-500">
        No positions yet — add accounts and transactions below, or import a CSV.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {report.warnings.length > 0 && (
        <div className="rounded border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
          {report.warnings.slice(0, 3).map((warning, i) => (
            <p key={i}>{String(warning.warning ?? JSON.stringify(warning))}</p>
          ))}
          {report.warnings.length > 3 && <p>… and {report.warnings.length - 3} more</p>}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-zinc-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
              <th className="px-3 py-2 font-normal">asset</th>
              <th className="px-3 py-2 font-normal">account</th>
              <th className="px-3 py-2 text-right font-normal">quantity</th>
              <th className="px-3 py-2 text-right font-normal">avg cost</th>
              <th className="px-3 py-2 text-right font-normal">last price</th>
              <th className="px-3 py-2 text-right font-normal">value ({currency})</th>
              <th className="px-3 py-2 text-right font-normal">unrealized</th>
              <th className="px-3 py-2 text-right font-normal">weight</th>
            </tr>
          </thead>
          <tbody>
            {report.positions.map((position) => (
              <tr
                key={`${position.account_id}-${position.asset_id}`}
                className="border-b border-zinc-900 hover:bg-zinc-900/50"
              >
                <td className="px-3 py-2">
                  <Link to={`/asset/${position.asset_id}`} className="hover:text-sky-300">
                    <span className="font-semibold">{position.symbol}</span>
                    {position.name && (
                      <span className="ml-2 hidden text-xs text-zinc-500 lg:inline">
                        {position.name}
                      </span>
                    )}
                  </Link>
                </td>
                <td className="px-3 py-2 text-xs text-zinc-500">{position.account_name}</td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {position.quantity.toLocaleString(undefined, { maximumFractionDigits: 8 })}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-zinc-400">
                  {fmtMoney(position.average_cost_native)}
                  <span className="ml-1 text-zinc-600">{position.native_currency}</span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-zinc-400">
                  {fmtMoney(position.last_price)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {fmtMoney(position.value)}
                </td>
                <td
                  className={`px-3 py-2 text-right font-mono text-xs ${pctClass(position.unrealized_pnl)}`}
                >
                  {fmtMoney(position.unrealized_pnl)}
                  {position.unrealized_pnl_pct !== null && (
                    <span className="ml-1">({fmtPct(position.unrealized_pnl_pct, 1)})</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-zinc-400">
                  {fmtPct(position.weight, 1)}
                </td>
              </tr>
            ))}
            {report.cash.map((cash) => (
              <tr
                key={`cash-${cash.account_id}-${cash.currency}`}
                className="border-b border-zinc-900 text-zinc-400"
              >
                <td className="px-3 py-2">
                  <span className="text-xs">cash · {cash.currency}</span>
                </td>
                <td className="px-3 py-2 text-xs text-zinc-500">{cash.account_name}</td>
                <td className="px-3 py-2 text-right font-mono text-xs">{fmtMoney(cash.amount)}</td>
                <td className="px-3 py-2" colSpan={2} />
                <td className="px-3 py-2 text-right font-mono text-xs">{fmtMoney(cash.value)}</td>
                <td className="px-3 py-2" colSpan={2} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
