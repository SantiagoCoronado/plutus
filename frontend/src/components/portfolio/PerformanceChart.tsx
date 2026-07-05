import { useEffect, useState } from 'react'
import { api, fmtPct, pctClass, type PerformancePeriod, type PerformanceReport } from '../../api/client'
import EquityChart from '../backtests/EquityChart'

const PERIODS: PerformancePeriod[] = ['1m', '3m', '6m', 'ytd', '1y', 'all']

export default function PerformanceChart({
  currency,
  refreshKey,
}: {
  currency: string
  refreshKey: number
}) {
  const [period, setPeriod] = useState<PerformancePeriod>('1y')
  const [report, setReport] = useState<PerformanceReport | null>(null)

  useEffect(() => {
    api.portfolioPerformance(period, currency).then(setReport).catch(() => setReport(null))
  }, [period, currency, refreshKey])

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-4">
          <h2 className="text-sm font-semibold text-zinc-300">Performance</h2>
          {report && (
            <span className="text-xs text-zinc-500">
              return{' '}
              <span className={pctClass(report.twr)}>{fmtPct(report.twr)}</span>
              {report.twr_annualized !== null && (
                <>
                  {' · '}annualized{' '}
                  <span className={pctClass(report.twr_annualized)}>
                    {fmtPct(report.twr_annualized)}
                  </span>
                </>
              )}
              {report.irr !== null && (
                <>
                  {' · '}money-weighted{' '}
                  <span className={pctClass(report.irr)}>{fmtPct(report.irr)}</span>
                </>
              )}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {PERIODS.map((option) => (
            <button
              key={option}
              onClick={() => setPeriod(option)}
              className={`rounded px-2 py-0.5 text-xs ${
                period === option
                  ? 'bg-zinc-800 text-zinc-200'
                  : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      {report === null || report.indexed.length === 0 ? (
        <p className="py-10 text-center text-sm text-zinc-600">
          no valued history in this window yet
        </p>
      ) : (
        <EquityChart
          portfolio={report.indexed}
          benchmark={report.benchmark?.indexed ?? []}
          benchmarkLabel={report.benchmark?.symbol}
        />
      )}
    </div>
  )
}
