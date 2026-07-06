import { Link } from 'react-router-dom'
import { relTime, type HealthLight } from '../../api/client'

const DOT: Record<HealthLight, string> = {
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  red: 'bg-red-500',
}

/** Last scan · ingestion health dot (→ Settings) · armed price-alert count. */
export default function StatusFooter({
  lastScanAt,
  ingestionStatus,
  armedAlerts,
}: {
  lastScanAt: string | null
  ingestionStatus: HealthLight
  armedAlerts: number
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
      <span>{lastScanAt ? `last scan ${relTime(lastScanAt)}` : 'no scans yet'}</span>
      <span className="text-zinc-700">·</span>
      <Link to="/settings" className="flex items-center gap-1.5 hover:text-zinc-300">
        <span className={`inline-block h-2 w-2 rounded-full ${DOT[ingestionStatus]}`} />
        ingestion {ingestionStatus}
      </Link>
      <span className="text-zinc-700">·</span>
      <span>
        {armedAlerts} armed alert{armedAlerts === 1 ? '' : 's'}
      </span>
    </div>
  )
}
