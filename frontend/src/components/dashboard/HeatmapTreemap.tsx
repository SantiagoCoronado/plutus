import * as echarts from 'echarts/core'
import { TreemapChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  fmtNum,
  type HeatmapMode,
  type HeatmapTile,
  type HeatmapTimeframe,
} from '../../api/client'
import { api } from '../../api/client'
import { useQuotes } from '../../hooks/useQuotes'
import { fmtMoney } from '../portfolio/shared'

// tree-shaken: only the treemap chart + tooltip + canvas renderer, not all of echarts
echarts.use([TreemapChart, TooltipComponent, CanvasRenderer])

const MODES: { key: HeatmapMode; label: string }[] = [
  { key: 'portfolio', label: 'My portfolio' },
  { key: 'watchlist', label: 'Watchlist' },
  { key: 'market', label: 'Market' },
]
const TIMEFRAMES: HeatmapTimeframe[] = ['1D', '1W', '1M', 'YTD']

const NEUTRAL = [63, 63, 70] // zinc-700, the "0% change" tile
const GREEN = [22, 163, 74] // green-600, clamped at +3%
const RED = [220, 38, 38] // red-600, clamped at -3%
const CLAMP = 3 // ±3% diverging clamp (spec §9.1)

function lerp(a: number[], b: number[], t: number): string {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

/** change is a PERCENT (1.23 = +1.23%). Exported for unit tests. */
export function tileColor(change: number): string {
  const t = Math.max(-CLAMP, Math.min(CLAMP, change)) / CLAMP
  return t >= 0 ? lerp(NEUTRAL, GREEN, t) : lerp(NEUTRAL, RED, -t)
}

function pctLabel(change: number): string {
  return `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`
}

const CLASS_LABELS: Record<string, string> = {
  stock: 'Stocks',
  etf: 'ETFs',
  crypto: 'Crypto',
  forex: 'Forex',
}

interface LeafNode {
  name: string
  value: number
  itemStyle: { color: string }
  _tile: HeatmapTile
  _change: number
}

/** Group tiles by asset class into the nested shape echarts treemap wants. */
function buildData(
  tiles: HeatmapTile[],
  quotes: Record<string, { change_pct: number }>,
  live: boolean,
) {
  const groups = new Map<string, LeafNode[]>()
  for (const tile of tiles) {
    const tick = live ? quotes[tile.symbol.toUpperCase()] : undefined
    const change = tick ? tick.change_pct : tile.change_pct
    const leaf: LeafNode = {
      name: tile.symbol,
      value: tile.size,
      itemStyle: { color: tileColor(change) },
      _tile: tile,
      _change: change,
    }
    const bucket = groups.get(tile.asset_class)
    if (bucket) bucket.push(leaf)
    else groups.set(tile.asset_class, [leaf])
  }
  return [...groups.entries()].map(([key, children]) => ({
    name: CLASS_LABELS[key] ?? key,
    children,
    itemStyle: { color: '#18181b', gapWidth: 1 },
  }))
}

export default function HeatmapTreemap({ currency }: { currency: string }) {
  const [mode, setMode] = useState<HeatmapMode>('portfolio')
  const [timeframe, setTimeframe] = useState<HeatmapTimeframe>('1D')
  const [tiles, setTiles] = useState<HeatmapTile[] | null>(null)
  const [failed, setFailed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const navigate = useNavigate()
  const navRef = useRef(navigate)
  navRef.current = navigate

  useEffect(() => {
    let cancelled = false
    setTiles(null)
    setFailed(false)
    api
      .dashboardHeatmap(mode, timeframe, currency)
      .then((res) => {
        if (!cancelled) setTiles(res.tiles)
      })
      .catch(() => {
        // a failed load is an ERROR, not "no positions yet"
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [mode, timeframe, currency])

  // 1D tiles recolor live from the quote stream; bounded modes only (market is huge).
  // 1s throttle: a busy market must not rebuild the chart per tick.
  const live = timeframe === '1D' && mode !== 'market'
  const liveSymbols = useMemo(
    () => (live ? (tiles ?? []).map((t) => t.symbol) : []),
    [live, tiles],
  )
  const { quotes } = useQuotes(liveSymbols, 1000)

  // init once: create the chart, wire click-through + a resize observer
  useEffect(() => {
    if (!containerRef.current) return
    const chart = echarts.init(containerRef.current)
    chartRef.current = chart
    chart.on('click', (params) => {
      const tile = (params.data as { _tile?: HeatmapTile } | undefined)?._tile
      if (tile) navRef.current(`/asset/${tile.asset_id}`)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  // full (re)draw only when the tile set itself changes
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (!tiles || tiles.length === 0) {
      chart.clear()
      return
    }
    chart.setOption(
      {
        tooltip: {
          borderColor: '#3f3f46',
          backgroundColor: '#18181b',
          textStyle: { color: '#e4e4e7', fontSize: 12 },
          formatter: (p: { data?: { _tile?: HeatmapTile; _change?: number }; name: string }) => {
            const tile = p.data?._tile
            if (!tile) return p.name
            const rows = [
              `<b>${tile.symbol}</b> ${tile.name ?? ''}`,
              tile.sector ? `sector ${tile.sector}` : null,
              `price ${fmtNum(tile.price)}`,
              `change ${pctLabel(p.data?._change ?? tile.change_pct)}`,
              `weight ${tile.weight_pct === null ? '—' : `${tile.weight_pct.toFixed(1)}%`}`,
            ].filter(Boolean)
            if (tile.pnl !== null) rows.push(`P&L ${fmtMoney(tile.pnl)}`)
            return rows.join('<br/>')
          },
        },
        series: [
          {
            type: 'treemap',
            roam: false,
            nodeClick: false,
            breadcrumb: { show: false },
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            width: '100%',
            height: '100%',
            itemStyle: { borderColor: '#18181b', borderWidth: 1, gapWidth: 1 },
            upperLabel: {
              show: true,
              height: 18,
              color: '#a1a1aa',
              fontSize: 11,
            },
            label: {
              show: true,
              overflow: 'truncate',
              color: '#fafafa',
              fontSize: 11,
              lineHeight: 14,
              formatter: (p: { name: string; data: { _change?: number } }) =>
                p.data?._change === undefined
                  ? p.name
                  : `${p.name}\n${pctLabel(p.data._change)}`,
            },
            levels: [
              { itemStyle: { borderWidth: 0, gapWidth: 1 } },
              { itemStyle: { gapWidth: 1 } },
            ],
            data: buildData(tiles, quotes, live),
          },
        ],
      },
      { notMerge: true },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps -- quotes drive the
    // cheap merged update below; rebuilding the whole option per tick was the bug
  }, [tiles, live])

  // live ticks patch only the series data, merged — never a full rebuild
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !live || !tiles || tiles.length === 0) return
    chart.setOption({ series: [{ data: buildData(tiles, quotes, live) }] })
  }, [quotes, tiles, live])

  const empty = tiles !== null && tiles.length === 0 && !failed

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-300">Heatmap</h2>
        <div className="flex items-center gap-3">
          <div className="flex gap-1">
            {MODES.map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className={`rounded px-2 py-0.5 text-xs ${
                  mode === m.key ? 'bg-zinc-800 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="flex gap-1">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`rounded px-2 py-0.5 text-xs ${
                  timeframe === tf ? 'bg-zinc-800 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="relative h-[420px] w-full">
        <div ref={containerRef} className="h-full w-full" />
        {empty && (
          <div className="absolute inset-0 flex items-center justify-center text-center text-sm text-zinc-600">
            {mode === 'portfolio'
              ? 'No open positions to map yet.'
              : mode === 'watchlist'
                ? 'Your watchlist is empty — star assets from their research page.'
                : 'No tracked assets yet.'}
          </div>
        )}
        {tiles === null && !failed && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-zinc-600">
            Loading…
          </div>
        )}
        {failed && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-red-400">
            Couldn't load the heatmap — is the API reachable?
          </div>
        )}
      </div>
    </div>
  )
}
