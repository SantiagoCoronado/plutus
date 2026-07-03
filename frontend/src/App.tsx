import { useEffect, useState } from 'react'
import { api, getToken, setToken, type HealthStatus } from './api/client'
import Dashboard from './pages/Dashboard'

function HealthBadge() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const poll = () =>
      api
        .health()
        .then((h) => {
          setHealth(h)
          setFailed(false)
        })
        .catch(() => setFailed(true))
    poll()
    const id = setInterval(poll, 15_000)
    return () => clearInterval(id)
  }, [])

  const color = failed
    ? 'bg-red-500'
    : health?.status === 'ok'
      ? 'bg-emerald-500'
      : health
        ? 'bg-amber-500'
        : 'bg-zinc-600'
  const label = failed ? 'api unreachable' : (health?.status ?? 'connecting…')

  return (
    <span className="flex items-center gap-2 text-sm text-zinc-400">
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`} />
      {label}
      {health && <span className="text-zinc-600">db:{health.db} redis:{health.redis}</span>}
    </span>
  )
}

function TokenInput() {
  const [value, setValue] = useState(getToken())
  const [saved, setSaved] = useState(false)

  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        setToken(value.trim())
        setSaved(true)
        setTimeout(() => setSaved(false), 1500)
      }}
    >
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="API token"
        className="w-44 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200 placeholder-zinc-600 focus:border-zinc-500 focus:outline-none"
      />
      <button
        type="submit"
        className="rounded border border-zinc-700 px-2 py-1 text-sm text-zinc-300 hover:bg-zinc-800"
      >
        {saved ? 'saved ✓' : 'save'}
      </button>
    </form>
  )
}

export default function App() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="flex items-center justify-between border-b border-zinc-800 px-6 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-wide">Plutus</h1>
          <span className="text-xs text-zinc-500">investment hub</span>
        </div>
        <div className="flex items-center gap-6">
          <TokenInput />
          <HealthBadge />
        </div>
      </header>
      <main className="p-6">
        <Dashboard />
      </main>
      <footer className="px-6 py-4 text-xs text-zinc-600">
        Market data by Tiingo, CoinGecko and Twelve Data. Informational only — not financial
        advice.
      </footer>
    </div>
  )
}
