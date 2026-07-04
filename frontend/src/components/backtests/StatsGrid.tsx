import { fmtPct, pctClass, type BacktestStats } from '../../api/client'

function Stat({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded border border-zinc-800 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`text-base tabular-nums ${className ?? 'text-zinc-200'}`}>{value}</div>
    </div>
  )
}

export default function StatsGrid({ stats }: { stats: BacktestStats }) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Total return"
          value={fmtPct(stats.total_return)}
          className={pctClass(stats.total_return)}
        />
        <Stat label="CAGR" value={fmtPct(stats.cagr)} className={pctClass(stats.cagr)} />
        <Stat label="Sharpe" value={stats.sharpe === null ? '—' : stats.sharpe.toFixed(2)} />
        <Stat
          label="Max drawdown"
          value={fmtPct(stats.max_drawdown)}
          className="text-red-400"
        />
        <Stat label="Win rate" value={fmtPct(stats.win_rate, 0)} />
        <Stat
          label={`Excess vs ${stats.benchmark_symbol ?? 'benchmark'}`}
          value={fmtPct(stats.excess_return)}
          className={pctClass(stats.excess_return)}
        />
      </div>
      <p className="text-xs text-zinc-600">
        {stats.start} → {stats.end} · {stats.bars} bars
        {stats.universe_size != null && ` · universe ${stats.universe_size}`}
        {stats.rebalances != null &&
          ` · ${stats.rebalances} rebalances every ${stats.holding_days} bars`}
        {stats.n_trades != null && ` · ${stats.n_trades} legs`}
        {stats.benchmark &&
          ` · benchmark total ${fmtPct(stats.benchmark.total_return)} (maxDD ${fmtPct(stats.benchmark.max_drawdown)})`}
      </p>
    </div>
  )
}
