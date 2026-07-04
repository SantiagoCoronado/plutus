import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtPct, pctClass, relTime, type BacktestSummary } from '../api/client'

const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-zinc-800 text-zinc-400',
  running: 'bg-sky-900/50 text-sky-300',
  done: 'bg-emerald-900/40 text-emerald-300',
  failed: 'bg-red-900/40 text-red-300',
}

export default function Backtests() {
  const [backtests, setBacktests] = useState<BacktestSummary[] | null>(null)
  const [kind, setKind] = useState<'' | 'screen' | 'strategy'>('')

  const load = (k: '' | 'screen' | 'strategy') =>
    api
      .backtests(k || undefined)
      .then(setBacktests)
      .catch(() => setBacktests([]))

  useEffect(() => {
    load(kind)
  }, [kind])

  if (backtests === null) return <p className="text-sm text-zinc-500">Loading…</p>

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Backtests</h1>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as '' | 'screen' | 'strategy')}
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm focus:border-zinc-500 focus:outline-none"
        >
          <option value="">all kinds</option>
          <option value="screen">screen</option>
          <option value="strategy">strategy</option>
        </select>
      </div>

      {backtests.length === 0 ? (
        <p className="rounded-lg border border-dashed border-zinc-800 p-10 text-center text-sm text-zinc-500">
          No backtests yet — run one from the Screener page, or POST /backtests/strategy for a
          single-asset entry/exit strategy.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-zinc-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500">
                <th className="px-4 py-2 font-normal">#</th>
                <th className="px-4 py-2 font-normal">Kind</th>
                <th className="px-4 py-2 font-normal">Status</th>
                <th className="px-4 py-2 font-normal">Target</th>
                <th className="px-4 py-2 text-right font-normal">Total return</th>
                <th className="px-4 py-2 text-right font-normal">Sharpe</th>
                <th className="px-4 py-2 text-right font-normal">Max DD</th>
                <th className="px-4 py-2 font-normal">Created</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {backtests.map((backtest) => (
                <tr key={backtest.id} className="hover:bg-zinc-900/50">
                  <td className="px-4 py-2">
                    <Link to={`/backtests/${backtest.id}`} className="font-medium hover:text-sky-300">
                      {backtest.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-zinc-400">{backtest.kind}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLES[backtest.status]}`}
                    >
                      {backtest.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-zinc-400">
                    {backtest.stats?.symbol ??
                      (backtest.stats?.universe_size != null
                        ? `${backtest.stats.universe_size} assets`
                        : '—')}
                  </td>
                  <td
                    className={`px-4 py-2 text-right tabular-nums ${pctClass(backtest.stats?.total_return)}`}
                  >
                    {fmtPct(backtest.stats?.total_return)}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">
                    {backtest.stats?.sharpe == null ? '—' : backtest.stats.sharpe.toFixed(2)}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-red-400">
                    {fmtPct(backtest.stats?.max_drawdown)}
                  </td>
                  <td className="px-4 py-2 text-zinc-500">{relTime(backtest.created_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={async () => {
                        await api.deleteBacktest(backtest.id)
                        await load(kind)
                      }}
                      className="text-xs text-zinc-600 hover:text-red-400"
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
