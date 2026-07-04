import { fmtNum, fmtPct, pctClass, type Asset, type AssetMetrics } from '../../api/client'

function num(metrics: AssetMetrics, key: string): number | null {
  const value = metrics[key]
  return typeof value === 'number' ? value : null
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-sm tabular-nums">{children}</span>
    </div>
  )
}

function RangeBar({ low, high, close }: { low: number; high: number; close: number }) {
  const pct = Math.min(100, Math.max(0, ((close - low) / (high - low || 1)) * 100))
  return (
    <div className="mt-1">
      <div className="relative h-1.5 rounded bg-zinc-800">
        <div className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-sky-400" style={{ left: `${pct}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-zinc-600 tabular-nums">
        <span>{fmtNum(low)}</span>
        <span>{fmtNum(high)}</span>
      </div>
    </div>
  )
}

export default function StatsPanel({ asset, metrics }: { asset: Asset; metrics: AssetMetrics | null }) {
  if (!metrics) {
    return (
      <div className="rounded border border-zinc-800 p-4 text-sm text-zinc-500">
        Metrics not computed yet — trigger an ingestion run or wait for tonight's refresh.
      </div>
    )
  }
  const close = num(metrics, 'close')
  const ret1d = num(metrics, 'return_1d')
  const high52 = num(metrics, 'high_52w')
  const low52 = num(metrics, 'low_52w')
  const isStock = asset.asset_class === 'stock' || asset.asset_class === 'etf'
  const isCrypto = asset.asset_class === 'crypto'
  const isForex = asset.asset_class === 'forex'

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="flex items-baseline gap-3">
        <span className="text-2xl font-semibold tabular-nums">{fmtNum(close, isForex ? 4 : 2)}</span>
        <span className={`text-sm tabular-nums ${pctClass(ret1d)}`}>
          {ret1d !== null && ret1d >= 0 ? '+' : ''}
          {fmtPct(ret1d)}
        </span>
        <span className="text-xs text-zinc-600">as of {metrics.as_of}</span>
      </div>

      {high52 !== null && low52 !== null && close !== null && (
        <div className="mt-3">
          <span className="text-xs text-zinc-500">52-week range</span>
          <RangeBar low={low52} high={high52} close={close} />
        </div>
      )}

      <div className="mt-3 divide-y divide-zinc-900">
        {!isForex && <Row label="Volume (avg 20d)">{fmtNum(num(metrics, 'volume_avg_20'), 0)}</Row>}
        {num(metrics, 'market_cap') !== null && (
          <Row label="Market cap">{fmtNum(num(metrics, 'market_cap'), 0)}</Row>
        )}
        {isCrypto && metrics.extras.mcap_rank !== undefined && (
          <Row label="Mcap rank">#{metrics.extras.mcap_rank}</Row>
        )}
        {isCrypto && metrics.extras.circulating_supply !== undefined && (
          <Row label="Circulating supply">{fmtNum(metrics.extras.circulating_supply ?? null, 0)}</Row>
        )}
        {isStock && (
          <>
            <Row label="P/E">{fmtNum(num(metrics, 'pe'))}</Row>
            <Row label="P/S">{fmtNum(num(metrics, 'ps'))}</Row>
            <Row label="EV/EBITDA">{fmtNum(num(metrics, 'ev_ebitda'))}</Row>
            <Row label="Gross margin">{fmtPct(num(metrics, 'gross_margin'), 1)}</Row>
            <Row label="Net margin">{fmtPct(num(metrics, 'net_margin'), 1)}</Row>
            <Row label="Revenue growth (yoy)">
              <span className={pctClass(num(metrics, 'revenue_growth_yoy'))}>
                {fmtPct(num(metrics, 'revenue_growth_yoy'), 1)}
              </span>
            </Row>
          </>
        )}
        <Row label="RSI (14)">{fmtNum(num(metrics, 'rsi_14'), 1)}</Row>
        <Row label="Volatility (20d, ann.)">{fmtPct(num(metrics, 'volatility_20'), 1)}</Row>
        <Row label="ADX (14)">{fmtNum(num(metrics, 'adx_14'), 1)}</Row>
        <Row label="vs 52w high">
          <span className={pctClass(num(metrics, 'dist_52w_high'))}>
            {fmtPct(num(metrics, 'dist_52w_high'), 1)}
          </span>
        </Row>
        {metrics.benchmark_symbol && (
          <Row label={`RS 3m vs ${metrics.benchmark_symbol}`}>
            <span className={pctClass(num(metrics, 'rs_3m'))}>{fmtPct(num(metrics, 'rs_3m'), 1)}</span>
          </Row>
        )}
      </div>
    </div>
  )
}
