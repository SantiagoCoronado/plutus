import { useEffect, useState } from 'react'
import { api, fmtPct, type AllocationReport } from '../../api/client'
import { fmtMoney } from './shared'

const MODES = [
  { key: 'asset_class', label: 'Class' },
  { key: 'currency', label: 'Currency' },
  { key: 'account', label: 'Account' },
]

// zinc-friendly categorical palette
const COLORS = ['#38bdf8', '#34d399', '#fbbf24', '#a78bfa', '#fb7185', '#22d3ee', '#a3e635']

const SIZE = 180
const RADIUS = 70
const STROKE = 26
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

export default function AllocationDonut({
  currency,
  refreshKey,
}: {
  currency: string
  refreshKey: number
}) {
  const [by, setBy] = useState('asset_class')
  const [report, setReport] = useState<AllocationReport | null>(null)

  useEffect(() => {
    api.portfolioAllocation(by, currency).then(setReport).catch(() => setReport(null))
  }, [by, currency, refreshKey])

  const groups = report?.groups ?? []
  let offset = 0
  const arcs = groups.map((group, i) => {
    const weight = group.weight ?? 0
    const arc = {
      color: COLORS[i % COLORS.length],
      dash: `${weight * CIRCUMFERENCE} ${CIRCUMFERENCE}`,
      offset: -offset * CIRCUMFERENCE,
    }
    offset += weight
    return arc
  })

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-300">Allocation</h2>
        <div className="flex gap-1">
          {MODES.map((mode) => (
            <button
              key={mode.key}
              onClick={() => setBy(mode.key)}
              className={`rounded px-2 py-0.5 text-xs ${
                by === mode.key ? 'bg-zinc-800 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      {groups.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-600">nothing to allocate yet</p>
      ) : (
        <div className="flex items-center gap-5">
          <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} className="shrink-0">
            <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
              {arcs.map((arc, i) => (
                <circle
                  key={i}
                  cx={SIZE / 2}
                  cy={SIZE / 2}
                  r={RADIUS}
                  fill="none"
                  stroke={arc.color}
                  strokeWidth={STROKE}
                  strokeDasharray={arc.dash}
                  strokeDashoffset={arc.offset}
                />
              ))}
            </g>
            <text
              x="50%"
              y="47%"
              textAnchor="middle"
              className="fill-zinc-200 text-sm font-semibold"
            >
              {fmtMoney(report?.total)}
            </text>
            <text x="50%" y="58%" textAnchor="middle" className="fill-zinc-500 text-[10px]">
              {currency}
            </text>
          </svg>
          <ul className="min-w-0 flex-1 space-y-1.5 text-xs">
            {groups.map((group, i) => (
              <li key={group.key} className="flex items-center gap-2">
                <span
                  className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ backgroundColor: COLORS[i % COLORS.length] }}
                />
                <span className="truncate text-zinc-300">{group.key}</span>
                <span className="ml-auto text-zinc-500">{fmtMoney(group.value)}</span>
                <span className="w-12 text-right text-zinc-400">{fmtPct(group.weight, 1)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
