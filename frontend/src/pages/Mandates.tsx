import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiError,
  api,
  relTime,
  type Mandate,
  type ScreenField,
  type SignalInfo,
  type Watchlist,
} from '../api/client'
import MandateForm from '../components/mandates/MandateForm'

const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-zinc-800 text-zinc-400',
  running: 'bg-sky-900/50 text-sky-300',
  done: 'bg-emerald-900/40 text-emerald-300',
  failed: 'bg-red-900/40 text-red-300',
}

const POLL_MS = 2500
const MAX_POLLS = 120

function inTime(iso: string): string {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000
  if (seconds <= 60) return 'due now'
  if (seconds < 3600) return `in ${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `in ${Math.round(seconds / 3600)}h`
  return `in ${Math.round(seconds / 86400)}d`
}

function universeSummary(mandate: Mandate, watchlists: Watchlist[]): string {
  const u = mandate.universe_def
  if (u.type === 'watchlist') {
    const list = watchlists.find((w) => w.id === u.watchlist_id)
    return `watchlist "${list?.name ?? u.watchlist_id}"`
  }
  if (u.type === 'market_cap_floor') return `market cap ≥ $${u.min_market_cap / 1e9}B`
  if (u.type === 'top_by_market_cap') return `top ${u.count} by size`
  return `all ${mandate.asset_class}s`
}

function hitRateLine(mandate: Mandate): string | null {
  const stats = mandate.stats
  if (!stats || stats.candidates_total === 0) return null
  const parts = [`${stats.candidates_total} candidates`]
  if (stats.new) parts.push(`${stats.new} new`)
  if (stats.starred) parts.push(`${stats.starred} starred`)
  if (stats.dismissed) parts.push(`${stats.dismissed} dismissed`)
  if (stats.hit_rate !== null) parts.push(`${Math.round(stats.hit_rate * 100)}% hit rate`)
  return parts.join(' · ')
}

export default function Mandates() {
  const [mandates, setMandates] = useState<Mandate[] | null>(null)
  const [signals, setSignals] = useState<SignalInfo[]>([])
  const [watchlists, setWatchlists] = useState<Watchlist[]>([])
  const [fields, setFields] = useState<ScreenField[]>([])
  const [editing, setEditing] = useState<Mandate | 'new' | null>(null)
  const [runningId, setRunningId] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const cancelled = useRef(false)

  const load = useCallback(async () => {
    setMandates(await api.mandates())
  }, [])

  useEffect(() => {
    cancelled.current = false
    load().catch(() => setMandates([]))
    api.discoverySignals().then(setSignals).catch(() => setSignals([]))
    api.watchlists().then(setWatchlists).catch(() => setWatchlists([]))
    api.screenFields().then(setFields).catch(() => setFields([]))
    return () => {
      cancelled.current = true
    }
  }, [load])

  const runNow = async (mandate: Mandate) => {
    setMessage('')
    setRunningId(mandate.id)
    try {
      await api.runMandate(mandate.id)
      let polls = 0
      const poll = async () => {
        if (cancelled.current) return
        const scans = await api.mandateScans(mandate.id, 1)
        const latest = scans[0]
        if (latest && (latest.status === 'done' || latest.status === 'failed')) {
          setRunningId(null)
          if (latest.status === 'failed') setMessage(`scan failed: ${latest.error ?? ''}`)
          await load()
          return
        }
        polls += 1
        if (polls >= MAX_POLLS) {
          setRunningId(null)
          setMessage('scan is taking a while — refresh to check on it')
          return
        }
        setTimeout(poll, POLL_MS)
      }
      setTimeout(poll, POLL_MS)
    } catch (e) {
      setRunningId(null)
      if (e instanceof ApiError && e.status === 409) {
        setMessage('a scan is already queued or running for that mandate')
      } else {
        setMessage('could not start the scan')
      }
    }
  }

  const toggleActive = async (mandate: Mandate) => {
    await api.patchMandate(mandate.id, { active: !mandate.active })
    await load()
  }

  const remove = async (mandate: Mandate) => {
    await api.deleteMandate(mandate.id)
    await load()
  }

  const sendTestAlert = async () => {
    setMessage('')
    try {
      const { results } = await api.testAlert()
      setMessage(
        results
          .map((r) => `${r.channel}: ${r.ok ? 'sent ✓' : `failed — ${r.error ?? ''}`}`)
          .join(' · '),
      )
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        setMessage('no alert channel configured — set SMTP_* or TELEGRAM_* in .env')
      } else {
        setMessage('test alert failed')
      }
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Mandates</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={sendTestAlert}
            className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            Send test alert
          </button>
          <button
            onClick={() => setEditing('new')}
            className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600"
          >
            New mandate
          </button>
        </div>
      </div>

      {message && (
        <p className="rounded border border-sky-900/60 bg-sky-950/30 px-3 py-2 text-sm text-sky-300">
          {message}
        </p>
      )}

      {mandates === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : mandates.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 p-10 text-center text-sm text-zinc-500">
          No mandates yet. A mandate is a standing instruction — a universe, filter rules,
          signal weights, and a schedule. Scans run automatically and ranked ideas land in
          the <Link to="/inbox" className="text-zinc-300 hover:text-sky-300">Inbox</Link>.
        </div>
      ) : (
        <div className="space-y-3">
          {mandates.map((mandate) => (
            <div
              key={mandate.id}
              className={`rounded border border-zinc-800 p-4 ${mandate.active ? '' : 'opacity-60'}`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{mandate.name}</span>
                    <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                      {mandate.asset_class}
                    </span>
                    {mandate.last_scan && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLES[mandate.last_scan.status] ?? ''}`}
                        title={mandate.last_scan.error ?? undefined}
                      >
                        {mandate.last_scan.status}
                      </span>
                    )}
                    {!mandate.active && <span className="text-xs text-zinc-500">paused</span>}
                  </div>
                  <p className="text-sm text-zinc-400">
                    {universeSummary(mandate, watchlists)}
                    {mandate.rules ? ' · filtered' : ''} ·{' '}
                    <span className="font-mono text-xs">{mandate.schedule}</span>
                    {mandate.active && mandate.next_run_at
                      ? ` · next ${inTime(mandate.next_run_at)}`
                      : ''}
                    {' · alerts '}
                    {mandate.notify}
                  </p>
                  <p className="text-xs text-zinc-500">
                    {hitRateLine(mandate) ?? 'no candidates yet'}
                    {mandate.last_run_at ? ` · last ran ${relTime(mandate.last_run_at)}` : ''}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    onClick={() => runNow(mandate)}
                    disabled={runningId === mandate.id}
                    className="rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
                  >
                    {runningId === mandate.id ? 'Scanning…' : 'Run now'}
                  </button>
                  <button
                    onClick={() => toggleActive(mandate)}
                    className="rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
                  >
                    {mandate.active ? 'Pause' : 'Resume'}
                  </button>
                  <button
                    onClick={() => setEditing(mandate)}
                    className="rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => remove(mandate)}
                    className="text-xs text-zinc-500 hover:text-red-400"
                  >
                    delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <MandateForm
          mandate={editing === 'new' ? null : editing}
          signals={signals}
          watchlists={watchlists}
          fields={fields}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            await load()
          }}
        />
      )}
    </div>
  )
}
