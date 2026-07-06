import { useCallback, useEffect, useState } from 'react'
import { api, fmtNum, relTime, type HealthLight, type IngestionHealth } from '../../api/client'
import { buttonClass } from '../portfolio/shared'

const dotClass: Record<HealthLight, string> = {
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  red: 'bg-red-500',
}

const badgeClass: Record<HealthLight, string> = {
  green: 'bg-emerald-950 text-emerald-300',
  amber: 'bg-amber-950 text-amber-300',
  red: 'bg-red-950 text-red-300',
}

export default function IngestionHealthSection() {
  const [health, setHealth] = useState<IngestionHealth | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api
      .ingestionHealth()
      .then(setHealth)
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  if (!health) return null

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="flex items-center gap-2 text-lg font-semibold">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${dotClass[health.status]}`} />
          Ingestion health
        </h1>
        <button className={buttonClass} disabled={loading} onClick={load}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="overflow-x-auto rounded border border-zinc-800">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-zinc-500">
              <th className="px-3 py-2 font-normal">job</th>
              <th className="px-3 py-2 font-normal">provider</th>
              <th className="px-3 py-2 font-normal">last run</th>
              <th className="px-3 py-2 font-normal">last success</th>
              <th className="px-3 py-2 font-normal">status</th>
            </tr>
          </thead>
          <tbody>
            {health.jobs.map((job) => (
              <tr key={job.job_name} className="border-b border-zinc-900">
                <td className="px-3 py-2 font-mono">{job.job_name}</td>
                <td className="px-3 py-2 text-zinc-400">{job.provider ?? '—'}</td>
                <td className="px-3 py-2 whitespace-nowrap text-zinc-500">
                  {job.last_run_at ? relTime(job.last_run_at) : '—'}
                  {job.last_status && (
                    <span className="ml-1 text-zinc-400">({job.last_status})</span>
                  )}
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-zinc-500">
                  {job.last_success_at ? relTime(job.last_success_at) : '—'}
                </td>
                <td className="px-3 py-2">
                  <span className={`rounded px-1.5 py-0.5 ${badgeClass[job.staleness]}`}>
                    {job.staleness}
                  </span>
                  {job.note && <span className="ml-1 text-zinc-500">{job.note}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {health.budgets.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-zinc-300">Provider budgets</h2>
          <div className="space-y-3 rounded border border-zinc-800 p-4">
            {health.budgets.map((budget) => (
              <div key={`${budget.provider}-${budget.window}`}>
                <div className="mb-1 flex justify-between text-xs text-zinc-400">
                  <span>
                    {budget.provider} <span className="text-zinc-500">({budget.window})</span>
                  </span>
                  <span>
                    {fmtNum(budget.used, 0)} of {fmtNum(budget.budget, 0)} calls
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded bg-zinc-900">
                  <div
                    className={`h-full ${budget.pct > 90 ? 'bg-red-500' : budget.pct > 70 ? 'bg-amber-500' : 'bg-sky-600'}`}
                    style={{ width: `${Math.min(100, budget.pct)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
