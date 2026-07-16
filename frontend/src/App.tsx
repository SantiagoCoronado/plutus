import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, getToken, onUnauthorized, setToken, type HealthStatus } from './api/client'
import AttributionFooter from './components/AttributionFooter'
import ErrorBoundary from './components/ErrorBoundary'
import SearchBox from './components/SearchBox'

function TokenBanner() {
  const [invalid, setInvalid] = useState(false)

  useEffect(() => onUnauthorized(() => setInvalid(true)), [])
  // a freshly saved token clears the banner on the next successful call;
  // cheapest signal we have is any location/token change re-probe via /health
  useEffect(() => {
    if (!invalid) return
    const id = setInterval(() => {
      api.health().then(() => {
        // /health is unauthenticated — probe a real endpoint quietly
        api.agentConversations().then(() => setInvalid(false)).catch(() => {})
      })
    }, 5000)
    return () => clearInterval(id)
  }, [invalid])

  if (!invalid) return null
  return (
    <div className="border-b border-amber-900 bg-amber-950/60 px-6 py-2 text-xs text-amber-300">
      The API rejected your token — paste the correct <span className="font-mono">APP_AUTH_TOKEN</span>{' '}
      into the token field above and save. Pages will show errors until then.
    </div>
  )
}

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
  const location = useLocation()
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
            <NavLink to="/portfolio" className={navClass}>
              Portfolio
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
            <NavLink to="/mandates" className={navClass}>
              Mandates
            </NavLink>
            <NavLink to="/agent" className={navClass}>
              Agent
            </NavLink>
            <NavLink to="/settings" className={navClass}>
              Settings
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
      <TokenBanner />
      <main className="flex-1 p-6">
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
      <AttributionFooter />
    </div>
  )
}
