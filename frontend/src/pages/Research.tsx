import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  api,
  type Asset,
  type AssetMetrics,
  type Candle,
  type IndicatorSeries,
} from '../api/client'
import DeepDiveButton from '../components/agent/DeepDiveButton'
import ChartContainer from '../components/research/ChartContainer'
import FundamentalsTab from '../components/research/FundamentalsTab'
import IndicatorPicker from '../components/research/IndicatorPicker'
import IntervalSwitcher, { type ChartInterval } from '../components/research/IntervalSwitcher'
import NewsTab from '../components/research/NewsTab'
import NotesTab from '../components/research/NotesTab'
import StatsPanel from '../components/research/StatsPanel'
import WatchlistButton from '../components/research/WatchlistButton'

const DEFAULT_INDICATORS = new Set(['sma_20', 'sma_50', 'volume', 'rsi_14'])

type TabKey = 'fundamentals' | 'news' | 'notes'

export default function Research() {
  const { id } = useParams()
  const assetId = Number(id)

  const [asset, setAsset] = useState<Asset | null>(null)
  const [metrics, setMetrics] = useState<AssetMetrics | null>(null)
  const [candles, setCandles] = useState<Candle[]>([])
  const [series, setSeries] = useState<IndicatorSeries>({})
  const [interval, setInterval] = useState<ChartInterval>('1d')
  const [selected, setSelected] = useState<Set<string>>(new Set(DEFAULT_INDICATORS))
  const [tab, setTab] = useState<TabKey>('notes')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setAsset(null)
    setMetrics(null)
    setError(null)
    api
      .asset(assetId)
      .then((a) => {
        setAsset(a)
        setTab(a.asset_class === 'stock' || a.asset_class === 'etf' ? 'fundamentals' : 'news')
      })
      .catch((e) => setError(String(e)))
    api.metrics(assetId).then(setMetrics).catch(() => setMetrics(null))
  }, [assetId])

  const indicatorKeys = useMemo(
    () => [...selected].filter((k) => k !== 'volume'),
    [selected],
  )

  useEffect(() => {
    if (!asset) return
    api.ohlcv(assetId, interval).then((r) => setCandles(r.candles)).catch(() => setCandles([]))
  }, [assetId, interval, asset])

  useEffect(() => {
    if (!asset) return
    if (indicatorKeys.length === 0) {
      setSeries({})
      return
    }
    api
      .indicators(assetId, indicatorKeys, interval)
      .then((r) => setSeries(r.series))
      .catch(() => setSeries({}))
  }, [assetId, interval, asset, indicatorKeys])

  if (error) return <p className="text-sm text-red-400">Failed to load asset: {error}</p>
  if (!asset) return <p className="text-sm text-zinc-500">Loading…</p>

  const tabs: { key: TabKey; label: string; show: boolean }[] = [
    {
      key: 'fundamentals',
      label: 'Fundamentals',
      show: asset.asset_class === 'stock' || asset.asset_class === 'etf',
    },
    { key: 'news', label: 'News', show: true },
    { key: 'notes', label: 'Notes', show: true },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold">{asset.symbol}</h1>
          <span className="text-sm text-zinc-400">{asset.name}</span>
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-400">
            {asset.asset_class}
          </span>
          {asset.exchange && <span className="text-xs text-zinc-600">{asset.exchange}</span>}
        </div>
        <div className="flex items-center gap-2">
          <DeepDiveButton assetId={assetId} />
          <WatchlistButton assetId={assetId} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_280px]">
        <div className="rounded border border-zinc-800 p-3">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <IndicatorPicker
              selected={selected}
              onToggle={(key) =>
                setSelected((prev) => {
                  const next = new Set(prev)
                  if (next.has(key)) next.delete(key)
                  else next.add(key)
                  return next
                })
              }
            />
            <IntervalSwitcher value={interval} onChange={setInterval} />
          </div>
          {candles.length > 0 ? (
            <ChartContainer
              candles={candles}
              series={series}
              showVolume={selected.has('volume')}
              showRsi={selected.has('rsi_14')}
              showMacd={selected.has('macd')}
            />
          ) : (
            <div className="flex h-96 items-center justify-center text-sm text-zinc-600">
              No candles yet — the backfill may still be running.
            </div>
          )}
        </div>
        <StatsPanel asset={asset} metrics={metrics} />
      </div>

      <div className="rounded border border-zinc-800">
        <div className="flex gap-1 border-b border-zinc-800 px-3 pt-2">
          {tabs
            .filter((t) => t.show)
            .map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`rounded-t px-3 py-1.5 text-sm ${
                  tab === t.key
                    ? 'border-x border-t border-zinc-800 bg-zinc-950 text-zinc-100'
                    : 'text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {t.label}
              </button>
            ))}
        </div>
        <div className="p-4">
          {tab === 'fundamentals' && <FundamentalsTab assetId={assetId} />}
          {tab === 'news' && <NewsTab asset={asset} />}
          {tab === 'notes' && <NotesTab assetId={assetId} />}
        </div>
      </div>
    </div>
  )
}
