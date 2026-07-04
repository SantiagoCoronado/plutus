import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type SearchResultItem } from '../api/client'

export default function SearchBox() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResultItem[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([])
      return
    }
    const handle = setTimeout(() => {
      api
        .search(query.trim())
        .then((r) => {
          setResults(r.results)
          setOpen(true)
        })
        .catch(() => setResults([]))
    }, 300)
    return () => clearTimeout(handle)
  }, [query])

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  async function pick(item: SearchResultItem) {
    setOpen(false)
    setQuery('')
    if (item.tracked && item.asset_id) {
      navigate(`/asset/${item.asset_id}`)
      return
    }
    setBusy(true)
    try {
      const meta =
        item.provider && item.provider_symbol
          ? { provider_symbols: { [item.provider]: item.provider_symbol } }
          : {}
      const asset = await api.trackAsset({
        symbol: item.symbol,
        name: item.name,
        asset_class: item.asset_class,
        exchange: item.exchange,
        currency: item.currency,
        meta,
      })
      navigate(`/asset/${asset.id}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        placeholder="Search symbols… (AAPL, bitcoin, EUR/USD)"
        className="w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 focus:border-zinc-500 focus:outline-none"
      />
      {open && results.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-80 w-full overflow-auto rounded border border-zinc-700 bg-zinc-900 shadow-xl">
          {results.map((item, i) => (
            <li key={`${item.symbol}-${item.asset_class}-${i}`}>
              <button
                type="button"
                disabled={busy}
                onClick={() => pick(item)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-zinc-800"
              >
                <span>
                  <span className="font-medium">{item.symbol}</span>{' '}
                  <span className="text-zinc-400">{item.name.slice(0, 40)}</span>
                </span>
                <span className="flex items-center gap-2 text-xs">
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-400">
                    {item.asset_class}
                  </span>
                  {item.tracked ? (
                    <span className="text-emerald-400">tracked</span>
                  ) : (
                    <span className="text-zinc-500">+ track</span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
