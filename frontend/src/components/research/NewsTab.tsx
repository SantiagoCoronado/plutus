import { useEffect, useState } from 'react'
import { api, relTime, type Asset, type NewsItem } from '../../api/client'

export default function NewsTab({ asset }: { asset: Asset }) {
  const [items, setItems] = useState<NewsItem[] | null>(null)

  useEffect(() => {
    setItems(null)
    api.news(asset.id, 14).then(setItems).catch(() => setItems([]))
  }, [asset.id])

  if (asset.asset_class === 'crypto' || asset.asset_class === 'forex') {
    return (
      <p className="text-sm text-zinc-500">
        News isn't available for this asset class yet — company news covers stocks/ETFs.
      </p>
    )
  }
  if (items === null) return <p className="text-sm text-zinc-500">Loading…</p>
  if (items.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        No headlines in the last 14 days (the news job pulls every 15 minutes while the worker
        runs).
      </p>
    )
  }

  return (
    <ul className="divide-y divide-zinc-900">
      {items.map((item) => (
        <li key={item.id} className="py-2.5">
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-zinc-200 hover:text-sky-300"
          >
            {item.headline}
          </a>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-zinc-500">
            <span className="rounded bg-zinc-800 px-1.5 py-0.5">{item.source}</span>
            <span>{relTime(item.ts)}</span>
          </div>
        </li>
      ))}
    </ul>
  )
}
