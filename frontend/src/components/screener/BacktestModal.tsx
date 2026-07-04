import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError, type AssetClass, type FilterNode } from '../../api/client'

interface Props {
  ast: FilterNode
  assetClass: AssetClass
  screenId: number | null
  onClose: () => void
}

export default function BacktestModal({ ast, assetClass, screenId, onClose }: Props) {
  const navigate = useNavigate()
  const [holdingDays, setHoldingDays] = useState('20')
  const [benchmark, setBenchmark] = useState('SPY')
  const [feesPct, setFeesPct] = useState('0')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const backtest = await api.createScreenBacktest({
        ...(screenId != null ? { screen_id: screenId } : { ast }),
        asset_class: assetClass,
        holding_days: Number(holdingDays) || 20,
        benchmark: benchmark.trim() || 'SPY',
        fees_pct: (Number(feesPct) || 0) / 100,
        ...(start ? { start } : {}),
        ...(end ? { end } : {}),
      })
      navigate(`/backtests/${backtest.id}`)
    } catch (e) {
      if (e instanceof ApiError) {
        try {
          const detail = JSON.parse(e.message).detail
          const first = detail?.errors?.[0]
          setError(first ? `${first.path ?? ''} ${first.error}` : e.message)
        } catch {
          setError(e.message)
        }
      } else setError(String(e))
      setSubmitting(false)
    }
  }

  const field = (label: string, control: React.ReactNode) => (
    <label className="flex items-center justify-between gap-3 text-sm text-zinc-400">
      {label}
      {control}
    </label>
  )
  const inputClass =
    'w-36 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200 focus:border-zinc-500 focus:outline-none'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-96 space-y-3 rounded-lg border border-zinc-700 bg-zinc-950 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-medium text-zinc-200">Backtest this screen</h2>
        <p className="text-xs text-zinc-500">
          Rebalances every N bars into equal weights, next-bar execution, vs benchmark
          buy&nbsp;&amp;&nbsp;hold. Fundamental fields are not point-in-time and are rejected.
        </p>
        {field(
          'Holding period (bars)',
          <input type="number" min={1} max={126} value={holdingDays} onChange={(e) => setHoldingDays(e.target.value)} className={inputClass} />,
        )}
        {field(
          'Benchmark symbol',
          <input value={benchmark} onChange={(e) => setBenchmark(e.target.value.toUpperCase())} className={inputClass} />,
        )}
        {field(
          'Fees per fill (%)',
          <input type="number" min={0} max={5} step="0.05" value={feesPct} onChange={(e) => setFeesPct(e.target.value)} className={inputClass} />,
        )}
        {field(
          'Start (optional)',
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={inputClass} />,
        )}
        {field(
          'End (optional)',
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={inputClass} />,
        )}
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-zinc-700 px-3 py-1 text-sm text-zinc-400 hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={submitting}
            onClick={submit}
            className="rounded bg-sky-700 px-3 py-1 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
          >
            {submitting ? 'Starting…' : 'Run backtest'}
          </button>
        </div>
      </div>
    </div>
  )
}
