import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { fmtNum, type ScreenRunResult } from '../../api/client'

interface Props {
  result: ScreenRunResult
}

export default function ResultsTable({ result }: Props) {
  const [sortBy, setSortBy] = useState<string>('symbol')
  const [descending, setDescending] = useState(false)

  const rows = useMemo(() => {
    const copy = [...result.results]
    copy.sort((a, b) => {
      const av = sortBy === 'symbol' ? a.symbol : (a.values[sortBy] ?? Number.NEGATIVE_INFINITY)
      const bv = sortBy === 'symbol' ? b.symbol : (b.values[sortBy] ?? Number.NEGATIVE_INFINITY)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return descending ? -cmp : cmp
    })
    return copy
  }, [result, sortBy, descending])

  const toggleSort = (column: string) => {
    if (sortBy === column) setDescending(!descending)
    else {
      setSortBy(column)
      setDescending(column !== 'symbol')
    }
  }

  if (result.count === 0) {
    return (
      <p className="rounded border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-600">
        No matches. Assets with missing metrics never match — run metrics refresh if the
        universe was just backfilled.
      </p>
    )
  }

  const arrow = (column: string) => (sortBy === column ? (descending ? ' ↓' : ' ↑') : '')

  return (
    <div className="overflow-x-auto rounded border border-zinc-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500">
            <th
              className="cursor-pointer px-4 py-2 font-normal hover:text-zinc-300"
              onClick={() => toggleSort('symbol')}
            >
              Symbol{arrow('symbol')}
            </th>
            <th className="px-4 py-2 font-normal">Name</th>
            {result.columns.map((column) => (
              <th
                key={column}
                className="cursor-pointer px-4 py-2 text-right font-normal hover:text-zinc-300"
                onClick={() => toggleSort(column)}
              >
                {column}
                {arrow(column)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-900">
          {rows.map((hit) => (
            <tr key={hit.asset_id} className="hover:bg-zinc-900/50">
              <td className="px-4 py-2">
                <Link to={`/asset/${hit.asset_id}`} className="font-medium hover:text-sky-300">
                  {hit.symbol}
                </Link>
                <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {hit.asset_class}
                </span>
              </td>
              <td className="max-w-56 truncate px-4 py-2 text-zinc-400">{hit.name}</td>
              {result.columns.map((column) => (
                <td key={column} className="px-4 py-2 text-right tabular-nums">
                  {fmtNum(hit.values[column], 4)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-zinc-900 px-4 py-2 text-xs text-zinc-600">
        {result.count} match{result.count === 1 ? '' : 'es'}
        {result.results[0]?.as_of && ` · metrics as of ${result.results[0].as_of}`}
      </p>
    </div>
  )
}
