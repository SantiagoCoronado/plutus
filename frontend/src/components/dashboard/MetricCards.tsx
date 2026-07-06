import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { fmtPct, pctClass, relTime, type Dashboard } from '../../api/client'
import { fmtMoney } from '../portfolio/shared'
import Sparkline from '../Sparkline'

function Card({
  label,
  children,
  to,
}: {
  label: string
  children: ReactNode
  to?: string
}) {
  const body = (
    <div className="h-full rounded border border-zinc-800 px-3 py-2 hover:border-zinc-700">
      <p className="text-[11px] text-zinc-500">{label}</p>
      {children}
    </div>
  )
  return to ? (
    <Link to={to} className="block">
      {body}
    </Link>
  ) : (
    body
  )
}

/** The 4 metric cards (spec §9.1): value+sparkline, today's P&L, YTD vs SPY, candidates. */
export default function MetricCards({ data }: { data: Dashboard }) {
  const { portfolio, ytd, candidates, last_scan_at } = data

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Card label={`portfolio value (${portfolio.currency})`}>
        <div className="flex items-end justify-between gap-2">
          <p className="font-mono text-lg">{fmtMoney(portfolio.value)}</p>
          <Sparkline values={portfolio.series_30d.map((p) => p.value)} width={100} height={28} />
        </div>
      </Card>

      <Card label="today's P&L">
        <p className={`font-mono text-lg ${pctClass(portfolio.day_pnl)}`}>
          {portfolio.day_pnl === null ? '—' : fmtMoney(portfolio.day_pnl)}
        </p>
        <p className={`font-mono text-xs ${pctClass(portfolio.day_pnl_pct)}`}>
          {portfolio.day_pnl_pct === null ? '' : fmtPct(portfolio.day_pnl_pct, 2)}
        </p>
      </Card>

      <Card label="YTD return (TWR)">
        <p className={`font-mono text-lg ${pctClass(ytd.twr_pct)}`}>
          {ytd.twr_pct === null ? '—' : fmtPct(ytd.twr_pct, 1)}
        </p>
        <p className="font-mono text-xs text-zinc-500">
          vs {ytd.benchmark_symbol}{' '}
          <span className={pctClass(ytd.benchmark_return_pct)}>
            {ytd.benchmark_return_pct === null ? '—' : fmtPct(ytd.benchmark_return_pct, 1)}
          </span>
        </p>
      </Card>

      <Card label="opportunity inbox" to="/inbox">
        <p className="font-mono text-lg">{candidates.new_count} new candidates</p>
        <p className="text-xs text-zinc-500">
          {last_scan_at ? `last scan ${relTime(last_scan_at)}` : 'no scans yet'}
        </p>
      </Card>
    </div>
  )
}
