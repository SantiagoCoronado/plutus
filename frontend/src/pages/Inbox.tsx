import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type AssetClass,
  type Candidate,
  type CandidateStatus,
  type CandidateSummary,
  type Mandate,
} from '../api/client'
import CandidateCard from '../components/inbox/CandidateCard'

const STATUSES: { key: CandidateStatus | ''; label: string }[] = [
  { key: 'new', label: 'New' },
  { key: 'reviewed', label: 'Reviewed' },
  { key: 'starred', label: 'Starred' },
  { key: 'dismissed', label: 'Dismissed' },
  { key: '', label: 'All' },
]

const selectClass =
  'rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm focus:border-zinc-500 focus:outline-none'

export default function Inbox() {
  const [candidates, setCandidates] = useState<Candidate[] | null>(null)
  const [summary, setSummary] = useState<CandidateSummary | null>(null)
  const [mandates, setMandates] = useState<Mandate[]>([])
  const [status, setStatus] = useState<CandidateStatus | ''>('new')
  const [mandateId, setMandateId] = useState<number | ''>('')
  const [assetClass, setAssetClass] = useState<AssetClass | ''>('')
  const [order, setOrder] = useState<'score' | 'newest'>('score')

  const load = useCallback(async () => {
    const [list, counts] = await Promise.all([
      api.candidates({ status, mandate_id: mandateId, asset_class: assetClass, order }),
      api.candidatesSummary(),
    ])
    setCandidates(list)
    setSummary(counts)
  }, [status, mandateId, assetClass, order])

  useEffect(() => {
    setCandidates(null)
    load().catch(() => setCandidates([]))
  }, [load])

  useEffect(() => {
    api.mandates().then(setMandates).catch(() => setMandates([]))
  }, [])

  const count = (key: CandidateStatus | '') => {
    if (!summary) return null
    if (key === '') return Object.values(summary.by_status).reduce((a, b) => a + b, 0)
    return summary.by_status[key]
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Research inbox</h1>
        <Link to="/mandates" className="text-sm text-zinc-400 hover:text-sky-300">
          Manage mandates →
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          {STATUSES.map(({ key, label }) => (
            <button
              key={label}
              onClick={() => setStatus(key)}
              className={`rounded px-2.5 py-1 text-sm ${
                status === key
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {label}
              {count(key) !== null && (
                <span className="ml-1 text-xs text-zinc-500">{count(key)}</span>
              )}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <select
            value={mandateId}
            onChange={(e) => setMandateId(e.target.value ? Number(e.target.value) : '')}
            className={selectClass}
          >
            <option value="">all mandates</option>
            {mandates.map((mandate) => (
              <option key={mandate.id} value={mandate.id}>
                {mandate.name}
              </option>
            ))}
          </select>
          <select
            value={assetClass}
            onChange={(e) => setAssetClass(e.target.value as AssetClass | '')}
            className={selectClass}
          >
            <option value="">all classes</option>
            <option value="stock">stock</option>
            <option value="etf">etf</option>
            <option value="crypto">crypto</option>
            <option value="forex">forex</option>
          </select>
          <select
            value={order}
            onChange={(e) => setOrder(e.target.value as 'score' | 'newest')}
            className={selectClass}
          >
            <option value="score">top score</option>
            <option value="newest">newest</option>
          </select>
        </div>
      </div>

      {candidates === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : candidates.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 p-10 text-center text-sm text-zinc-500">
          No {status === '' ? '' : `${status} `}ideas here yet — mandates run on their own
          schedule, or trigger one from the{' '}
          <Link to="/mandates" className="text-zinc-300 hover:text-sky-300">
            Mandates page
          </Link>
          .
        </div>
      ) : (
        <div className="space-y-2">
          {candidates.map((candidate) => (
            <CandidateCard key={candidate.id} candidate={candidate} onChanged={load} />
          ))}
        </div>
      )}
    </div>
  )
}
