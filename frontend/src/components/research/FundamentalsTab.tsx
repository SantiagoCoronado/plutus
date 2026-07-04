import { useEffect, useState } from 'react'
import { api, fmtNum, fmtPct, type FundamentalsRow } from '../../api/client'
import Sparkline from '../Sparkline'

type Extractor = (row: FundamentalsRow) => number | null

const LINE_ITEMS: { label: string; get: Extractor; fmt: (v: number | null) => string }[] = [
  { label: 'Revenue', get: (r) => r.revenue, fmt: (v) => fmtNum(v, 0) },
  {
    label: 'Net income',
    get: (r) => (typeof r.metrics.income?.netIncome === 'number' ? r.metrics.income.netIncome : null),
    fmt: (v) => fmtNum(v, 0),
  },
  { label: 'EPS (diluted)', get: (r) => r.eps, fmt: (v) => fmtNum(v) },
  { label: 'Free cash flow', get: (r) => r.fcf, fmt: (v) => fmtNum(v, 0) },
  { label: 'Gross margin', get: (r) => r.gross_margin, fmt: (v) => fmtPct(v, 1) },
  { label: 'Net margin', get: (r) => r.net_margin, fmt: (v) => fmtPct(v, 1) },
  { label: 'ROE', get: (r) => r.roe, fmt: (v) => fmtPct(v, 1) },
  { label: 'Debt / equity', get: (r) => r.debt_to_equity, fmt: (v) => fmtNum(v) },
  { label: 'P/E', get: (r) => r.pe, fmt: (v) => fmtNum(v, 1) },
  { label: 'P/S', get: (r) => r.ps, fmt: (v) => fmtNum(v, 1) },
  { label: 'EV/EBITDA', get: (r) => r.ev_ebitda, fmt: (v) => fmtNum(v, 1) },
]

export default function FundamentalsTab({ assetId }: { assetId: number }) {
  const [rows, setRows] = useState<FundamentalsRow[] | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = () => api.fundamentals(assetId).then(setRows).catch(() => setRows([]))

  useEffect(() => {
    setRows(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetId])

  async function refresh() {
    setRefreshing(true)
    try {
      await api.refreshFundamentals(assetId)
      // the worker fills the table asynchronously — poll a few times
      for (let i = 0; i < 6; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2500))
        const fresh = await api.fundamentals(assetId)
        if (fresh.length > (rows?.length ?? 0)) {
          setRows(fresh)
          break
        }
      }
      await load()
    } finally {
      setRefreshing(false)
    }
  }

  if (rows === null) return <p className="text-sm text-zinc-500">Loading…</p>

  if (rows.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        <p>No fundamentals stored yet (ETFs have no statements; stocks fill in with the weekly refresh).</p>
        <button
          type="button"
          onClick={refresh}
          disabled={refreshing}
          className="mt-3 rounded border border-zinc-700 px-3 py-1 text-zinc-300 hover:bg-zinc-800"
        >
          {refreshing ? 'Refreshing…' : 'Fetch now'}
        </button>
      </div>
    )
  }

  const ordered = [...rows].sort((a, b) => a.report_date.localeCompare(b.report_date))
  const years = ordered.map((r) => r.fiscal_year ?? Number(r.report_date.slice(0, 4)))

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-zinc-500">
          Annual statements ({ordered[0].provider}), fiscal {years[0]}–{years[years.length - 1]}
        </span>
        <button
          type="button"
          onClick={refresh}
          disabled={refreshing}
          className="rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
        >
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
              <th className="py-2 pr-4 font-normal">Line item</th>
              {years.map((y) => (
                <th key={y} className="px-3 py-2 text-right font-normal tabular-nums">
                  FY{y}
                </th>
              ))}
              <th className="px-3 py-2 text-right font-normal">Trend</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-900">
            {LINE_ITEMS.map((item) => {
              const values = ordered.map((r) => item.get(r))
              if (values.every((v) => v === null)) return null
              return (
                <tr key={item.label}>
                  <td className="py-1.5 pr-4 text-zinc-400">{item.label}</td>
                  {values.map((v, i) => (
                    <td key={i} className="px-3 py-1.5 text-right tabular-nums">
                      {item.fmt(v)}
                    </td>
                  ))}
                  <td className="px-3 py-1.5 text-right">
                    <Sparkline values={values} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
