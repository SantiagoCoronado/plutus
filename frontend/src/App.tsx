import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api, getToken, setToken, type HealthStatus } from './api/client'
import SearchBox from './components/SearchBox'

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
  const label = failed ? 'api unreachable' : (health?.status ?? '…')

  return (
    <span className="flex items-center gap-2 text-xs text-zinc-400" title={`db:${health?.db} redis:${health?.redis}`}>
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`} />
      {label}
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
        className="w-36 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:border-zinc-500 focus:outline-none"
      />
      <button
        type="submit"
        className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
      >
        {saved ? '✓' : 'save'}
      </button>
    </form>
  )
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded px-3 py-1.5 text-sm ${isActive ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'}`

export default function App() {
  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      <header className="flex items-center justify-between gap-4 border-b border-zinc-800 px-6 py-3">
        <div className="flex items-center gap-5">
          <NavLink to="/" className="text-lg font-semibold tracking-wide">
            Plutus
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navClass}>
              Dashboard
            </NavLink>
            <NavLink to="/inbox" className={navClass}>
              Inbox
            </NavLink>
            <NavLink to="/watchlists" className={navClass}>
              Watchlists
            </NavLink>
            <NavLink to="/screener" className={navClass}>
              Screener
            </NavLink>
            <NavLink to="/backtests" className={navClass}>
              Backtests
            </NavLink>
          </nav>
        </div>
        <div className="max-w-md flex-1">
          <SearchBox />
        </div>
        <div className="flex items-center gap-4">
          <TokenInput />
          <HealthBadge />
        </div>
      </header>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
      <footer className="px-6 py-4 text-xs text-zinc-600">
        Market data by Tiingo, Binance, Twelve Data, FMP, Finnhub — crypto metadata powered by{' '}
        <a href="https://www.coingecko.com" target="_blank" rel="noopener" className="underline">
          CoinGecko
        </a>
        . Informational only — not financial advice.
      </footer>
    </div>
  )
}
