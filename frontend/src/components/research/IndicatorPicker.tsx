const OVERLAYS: { key: string; label: string }[] = [
  { key: 'sma_20', label: 'SMA 20' },
  { key: 'sma_50', label: 'SMA 50' },
  { key: 'sma_200', label: 'SMA 200' },
  { key: 'ema_12', label: 'EMA 12' },
  { key: 'ema_26', label: 'EMA 26' },
  { key: 'ema_50', label: 'EMA 50' },
  { key: 'bbands', label: 'Bollinger' },
  { key: 'vwap_20', label: 'VWAP (20d)' },
]

const PANES: { key: string; label: string }[] = [
  { key: 'rsi_14', label: 'RSI' },
  { key: 'macd', label: 'MACD' },
  { key: 'volume', label: 'Volume' },
]

function Chip({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
        active
          ? 'border-sky-600 bg-sky-950 text-sky-300'
          : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
      }`}
    >
      {label}
    </button>
  )
}

export default function IndicatorPicker({
  selected,
  onToggle,
}: {
  selected: Set<string>
  onToggle: (key: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {OVERLAYS.map((o) => (
        <Chip
          key={o.key}
          label={o.label}
          active={selected.has(o.key)}
          onClick={() => onToggle(o.key)}
        />
      ))}
      <span className="mx-1 text-zinc-700">|</span>
      {PANES.map((p) => (
        <Chip
          key={p.key}
          label={p.label}
          active={selected.has(p.key)}
          onClick={() => onToggle(p.key)}
        />
      ))}
    </div>
  )
}
