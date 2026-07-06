import { Link } from 'react-router-dom'
import type { DashboardCandidate } from '../../api/client'

// same thresholds as CandidateCard's ScoreBadge, compact pill form
function ScorePill({ score }: { score: number }) {
  const cls =
    score >= 80
      ? 'border-emerald-800 bg-emerald-900/40 text-emerald-300'
      : score >= 60
        ? 'border-sky-800 bg-sky-900/40 text-sky-300'
        : 'border-zinc-700 bg-zinc-800 text-zinc-400'
  return (
    <span
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded border text-sm font-semibold tabular-nums ${cls}`}
      title={`score ${score.toFixed(1)}`}
    >
      {Math.round(score)}
    </span>
  )
}

/** Top-5 candidates as compact rows; the whole panel links through to the Inbox. */
export default function InboxPreview({ candidates }: { candidates: DashboardCandidate[] }) {
  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-300">Opportunity inbox</h2>
        <Link to="/inbox" className="text-xs text-sky-400 hover:text-sky-300">
          view all →
        </Link>
      </div>

      {candidates.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-600">
          No candidates yet — mandates surface them overnight.
        </p>
      ) : (
        <ul className="divide-y divide-zinc-900">
          {candidates.map((candidate) => (
            <li key={candidate.id}>
              <Link
                to="/inbox"
                className="flex items-center gap-3 py-2 hover:bg-zinc-900/50"
              >
                <ScorePill score={candidate.score} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="font-semibold">{candidate.symbol}</span>
                    <span className="truncate text-xs text-zinc-500">{candidate.name}</span>
                  </div>
                  <p className="truncate text-xs text-zinc-500">
                    {candidate.mandate_name}
                    {candidate.signals_summary.length > 0 &&
                      ` · ${candidate.signals_summary.join(', ')}`}
                  </p>
                </div>
                <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {candidate.asset_class}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
