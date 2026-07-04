import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtNum, fmtPct, pctClass, type Watchlist } from '../api/client'

export default function Watchlists() {
  const [watchlists, setWatchlists] = useState<Watchlist[] | null>(null)
  const [newName, setNewName] = useState('')

  const load = () => api.watchlists().then(setWatchlists).catch(() => setWatchlists([]))

  useEffect(() => {
    load()
  }, [])

  if (watchlists === null) return <p className="text-sm text-zinc-500">Loading…</p>

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Watchlists</h1>
        <form
          className="flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            if (!newName.trim()) return
            await api.createWatchlist(newName.trim())
            setNewName('')
            await load()
          }}
        >
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New watchlist name"
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm focus:border-zinc-500 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded border border-zinc-700 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            Create
          </button>
        </form>
      </div>

      {watchlists.map((watchlist) => (
        <section key={watchlist.id} className="rounded border border-zinc-800">
          <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
            <h2 className="text-sm font-medium">{watchlist.name}</h2>
            {watchlist.name !== 'Default' && (
              <button
                type="button"
                onClick={async () => {
                  await api.deleteWatchlist(watchlist.id)
                  await load()
                }}
                className="text-xs text-zinc-500 hover:text-red-400"
              >
                delete list
              </button>
            )}
          </header>
          {watchlist.items.length === 0 ? (
            <p className="px-4 py-3 text-sm text-zinc-600">
              Empty — star assets from their research page.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-zinc-500">
                  <th className="px-4 py-2 font-normal">Symbol</th>
                  <th className="px-4 py-2 font-normal">Name</th>
                  <th className="px-4 py-2 text-right font-normal">Close</th>
                  <th className="px-4 py-2 text-right font-normal">1d</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-900">
                {watchlist.items.map((item) => (
                  <tr key={item.asset_id} className="hover:bg-zinc-900/50">
                    <td className="px-4 py-2">
                      <Link to={`/asset/${item.asset_id}`} className="font-medium hover:text-sky-300">
                        {item.symbol}
                      </Link>
                      <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                        {item.asset_class}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-zinc-400">{item.name}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{fmtNum(item.close)}</td>
                    <td className={`px-4 py-2 text-right tabular-nums ${pctClass(item.return_1d)}`}>
                      {fmtPct(item.return_1d)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={async () => {
                          await api.removeWatchlistItem(watchlist.id, item.asset_id)
                          await load()
                        }}
                        className="text-xs text-zinc-500 hover:text-red-400"
                      >
                        remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ))}
    </div>
  )
}
