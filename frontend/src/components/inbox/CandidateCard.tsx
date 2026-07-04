import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, relTime, type Candidate, type CandidateSignal } from '../../api/client'
import Sparkline from '../Sparkline'

export function ScoreBadge({ score }: { score: number }) {
  const cls =
    score >= 80
      ? 'border-emerald-800 bg-emerald-900/40 text-emerald-300'
      : score >= 60
        ? 'border-sky-800 bg-sky-900/40 text-sky-300'
        : 'border-zinc-700 bg-zinc-800 text-zinc-400'
  return (
    <div
      className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border text-lg font-semibold tabular-nums ${cls}`}
      title={`score ${score.toFixed(1)}`}
    >
      {Math.round(score)}
    </div>
  )
}

function SignalChip({ signal }: { signal: CandidateSignal }) {
  const cls = signal.triggered
    ? 'border-sky-800 bg-sky-950 text-sky-300'
    : 'border-zinc-800 bg-zinc-900 text-zinc-500'
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[11px] ${cls}`}>
      {signal.label} · {Math.round(signal.score)}
    </span>
  )
}

function historyLine(candidate: Candidate): string | null {
  const checks = candidate.context.history_check ?? {}
  for (const signal of candidate.signals) {
    const check = checks[signal.key]
    const fwd = check?.fwd?.['20d']
    if (check && fwd) {
      const sign = fwd.median >= 0 ? '+' : ''
      const win = fwd.win_rate === null ? '—' : `${Math.round(fwd.win_rate * 100)}%`
      return `After ${check.n_triggers} past signal${check.n_triggers === 1 ? '' : 's'}: ${sign}${(fwd.median * 100).toFixed(1)}% median 20-day move, ${win} win rate`
    }
  }
  return null
}

export default function CandidateCard({
  candidate,
  onChanged,
}: {
  candidate: Candidate
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const starred = candidate.status === 'starred'
  const history = historyLine(candidate)
  const sparkValues = (candidate.context.chart ?? []).map(([, value]) => value)

  const setStatus = async (status: Candidate['status']) => {
    setBusy(true)
    try {
      await api.patchCandidate(candidate.id, status)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  const markReviewed = () => {
    if (candidate.status === 'new') api.patchCandidate(candidate.id, 'reviewed').catch(() => {})
  }

  return (
    <div className="flex items-start gap-4 rounded border border-zinc-800 p-4 hover:border-zinc-700">
      <ScoreBadge score={candidate.score} />
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-baseline gap-2">
          <Link
            to={`/asset/${candidate.asset_id}`}
            onClick={markReviewed}
            className="font-semibold hover:text-sky-300"
          >
            {candidate.symbol}
          </Link>
          <span className="truncate text-sm text-zinc-500">{candidate.name}</span>
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
            {candidate.asset_class}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {candidate.signals.map((signal) => (
            <SignalChip key={signal.key} signal={signal} />
          ))}
        </div>
        {history ? (
          <p className="text-xs text-zinc-400">{history}</p>
        ) : (
          <p className="text-xs text-zinc-600">No history check for this signal</p>
        )}
        <p className="text-xs text-zinc-600">
          {candidate.mandate_name} · {relTime(candidate.created_at)}
        </p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <Sparkline values={sparkValues} width={110} height={28} />
        <div className="flex items-center gap-2">
          <button
            onClick={() => setStatus(starred ? 'new' : 'starred')}
            disabled={busy}
            className={`rounded border px-2 py-1 text-xs disabled:opacity-50 ${
              starred
                ? 'border-amber-600 bg-amber-950 text-amber-300'
                : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
            }`}
          >
            {starred ? '★ starred' : '☆ star'}
          </button>
          {candidate.status !== 'dismissed' && (
            <button
              onClick={() => setStatus('dismissed')}
              disabled={busy}
              className="text-xs text-zinc-500 hover:text-red-400 disabled:opacity-50"
            >
              dismiss
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
