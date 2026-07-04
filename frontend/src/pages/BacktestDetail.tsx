import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  relTime,
  type Backtest,
  type ScreenHolding,
  type StrategyTrade,
} from '../api/client'
import EquityChart from '../components/backtests/EquityChart'
import StatsGrid from '../components/backtests/StatsGrid'
import { HoldingsLog, StrategyTradesTable } from '../components/backtests/TradesTable'

const POLL_MS = 2500
const MAX_POLLS = 120 // ~5 min, then ask for a manual refresh

export default function BacktestDetail() {
  const { id } = useParams()
  const backtestId = Number(id)
  const [backtest, setBacktest] = useState<Backtest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pollTimeout, setPollTimeout] = useState(false)
  const [reportUrl, setReportUrl] = useState<string | null>(null)
  const pollCount = useRef(0)

  useEffect(() => {
    let cancelled = false
    pollCount.current = 0
    setBacktest(null)
    setPollTimeout(false)
    setError(null)

    const poll = async () => {
      try {
        const fresh = await api.backtest(backtestId)
        if (cancelled) return
        setBacktest(fresh)
        if (fresh.status === 'queued' || fresh.status === 'running') {
          pollCount.current += 1
          if (pollCount.current >= MAX_POLLS) {
            setPollTimeout(true)
            return
          }
          setTimeout(poll, POLL_MS)
        }
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    }
    poll()
    return () => {
      cancelled = true
    }
  }, [backtestId])

  // object URLs leak unless revoked
  useEffect(() => () => {
    if (reportUrl) URL.revokeObjectURL(reportUrl)
  }, [reportUrl])

  const openReport = async () => {
    const blob = await api.backtestReportBlob(backtestId)
    const url = URL.createObjectURL(blob)
    setReportUrl(url)
    window.open(url, '_blank', 'noopener')
  }

  if (error) return <p className="text-sm text-red-400">Failed to load backtest: {error}</p>
  if (!backtest) return <p className="text-sm text-zinc-500">Loading…</p>

  const inFlight = backtest.status === 'queued' || backtest.status === 'running'

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/backtests" className="text-sm text-zinc-500 hover:text-zinc-300">
          ← Backtests
        </Link>
        <h1 className="text-xl font-semibold">
          #{backtest.id} · {backtest.kind} backtest
          {backtest.stats?.symbol ? ` · ${backtest.stats.symbol}` : ''}
        </h1>
        <span className="text-xs text-zinc-600">created {relTime(backtest.created_at)}</span>
      </div>

      {inFlight && (
        <div className="rounded border border-sky-900/60 bg-sky-950/30 px-4 py-3 text-sm text-sky-300">
          {backtest.status === 'queued' ? 'Waiting for the worker…' : 'Running…'}
          {pollTimeout && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="ml-3 underline hover:text-sky-200"
            >
              still going — refresh manually
            </button>
          )}
        </div>
      )}

      {backtest.status === 'failed' && (
        <div className="rounded border border-red-900/60 bg-red-950/30 px-4 py-3 text-sm text-red-300">
          <span className="font-medium">Failed:</span> {backtest.error}
        </div>
      )}

      {backtest.status === 'done' && backtest.stats && (
        <>
          <StatsGrid stats={backtest.stats} />

          {backtest.equity_curve && backtest.equity_curve.portfolio.length > 1 && (
            <div className="rounded border border-zinc-800 p-3">
              <EquityChart
                portfolio={backtest.equity_curve.portfolio}
                benchmark={backtest.equity_curve.benchmark ?? []}
              />
            </div>
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-zinc-300">
                {backtest.kind === 'strategy' ? 'Trades' : 'Holdings by rebalance'}
              </h2>
              {backtest.kind === 'strategy' && backtest.artifact_path && (
                <button
                  type="button"
                  onClick={openReport}
                  className="rounded border border-zinc-700 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
                >
                  Open quantstats report ↗
                </button>
              )}
            </div>
            {backtest.kind === 'strategy' ? (
              <StrategyTradesTable trades={(backtest.trade_list ?? []) as StrategyTrade[]} />
            ) : (
              <HoldingsLog holdings={(backtest.trade_list ?? []) as ScreenHolding[]} />
            )}
          </div>
        </>
      )}
    </div>
  )
}
