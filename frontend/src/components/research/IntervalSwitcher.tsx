const INTERVALS = ['1d', '1w', '1M'] as const
export type ChartInterval = (typeof INTERVALS)[number]

export default function IntervalSwitcher({
  value,
  onChange,
}: {
  value: ChartInterval
  onChange: (interval: ChartInterval) => void
}) {
  return (
    <div className="flex rounded border border-zinc-700 text-xs">
      {INTERVALS.map((interval) => (
        <button
          key={interval}
          type="button"
          onClick={() => onChange(interval)}
          className={`px-3 py-1 ${
            value === interval ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          {interval.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
