import { setCurrency } from '../../api/client'

const CURRENCIES = ['USD', 'MXN'] as const

export default function CurrencyToggle({
  currency,
  onChange,
}: {
  currency: string
  onChange: (currency: string) => void
}) {
  return (
    <div className="flex overflow-hidden rounded border border-zinc-700 text-xs">
      {CURRENCIES.map((option) => (
        <button
          key={option}
          onClick={() => {
            setCurrency(option)
            onChange(option)
          }}
          className={`px-3 py-1.5 ${
            currency === option
              ? 'bg-zinc-800 text-zinc-100'
              : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          {option}
        </button>
      ))}
    </div>
  )
}
