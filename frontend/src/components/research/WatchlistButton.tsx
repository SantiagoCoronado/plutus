import { useEffect, useState } from 'react'
import { api, type Watchlist } from '../../api/client'

export default function WatchlistButton({ assetId }: { assetId: number }) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([])
  const [busy, setBusy] = useState(false)

  const refresh = () => api.watchlists().then(setWatchlists).catch(() => {})

  useEffect(() => {
    refresh()
  }, [assetId])

  const target = watchlists.find((w) => w.name === 'Default') ?? watchlists[0]
  const starred = target?.items.some((item) => item.asset_id === assetId) ?? false

  async function toggle() {
    if (!target || busy) return
    setBusy(true)
    try {
      if (starred) {
        await api.removeWatchlistItem(target.id, assetId)
      } else {
        await api.addWatchlistItem(target.id, assetId)
      }
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={!target || busy}
      title={starred ? `Remove from ${target?.name}` : `Add to ${target?.name}`}
      className={`rounded border px-2.5 py-1 text-sm transition-colors ${
        starred
          ? 'border-amber-600 bg-amber-950 text-amber-300'
          : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
      }`}
    >
      {starred ? '★ watching' : '☆ watch'}
    </button>
  )
}
