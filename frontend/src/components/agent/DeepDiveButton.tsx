import { useState } from 'react'
import { ApiError, getToken } from '../../api/client'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

async function requestDeepDive(body: {
  asset_id?: number
  candidate_id?: number
}): Promise<{ conversation_id: number; symbol: string }> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const resp = await fetch(`${API_BASE}/agent/deep-dives`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new ApiError(resp.status, await resp.text())
  return resp.json()
}

/** Queue an AI deep-dive; the memo lands as an AI-labeled note on the asset. */
export default function DeepDiveButton({
  assetId,
  candidateId,
  compact = false,
}: {
  assetId?: number
  candidateId?: number
  compact?: boolean
}) {
  const [state, setState] = useState<'idle' | 'queueing' | 'queued' | 'error'>('idle')

  const queue = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    if (state === 'queueing' || state === 'queued') return
    setState('queueing')
    try {
      await requestDeepDive(
        candidateId !== undefined ? { candidate_id: candidateId } : { asset_id: assetId },
      )
      setState('queued')
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 4000)
    }
  }

  const label =
    state === 'queued'
      ? compact
        ? 'memo queued ✓'
        : 'Memo queued — appears in Notes in ~1 min'
      : state === 'queueing'
        ? 'queueing…'
        : state === 'error'
          ? 'failed — retry?'
          : compact
            ? 'AI memo'
            : 'AI deep-dive'

  return (
    <button
      className={`rounded border border-violet-900 bg-violet-950/50 text-violet-300 hover:bg-violet-900/50 disabled:opacity-60 ${
        compact ? 'px-1.5 py-0.5 text-[11px]' : 'px-3 py-1.5 text-sm'
      }`}
      disabled={state === 'queueing' || state === 'queued'}
      onClick={queue}
      title="Run an AI research loop (overview → fundamentals → news → signal history) and write a memo note"
    >
      {label}
    </button>
  )
}
