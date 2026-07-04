const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

export function getToken(): string {
  return localStorage.getItem('plutus_token') ?? ''
}

export function setToken(token: string): void {
  localStorage.setItem('plutus_token', token)
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init?.body) headers.set('Content-Type', 'application/json')

  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!resp.ok) throw new ApiError(resp.status, await resp.text())
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

// --- types -------------------------------------------------------------------

export type AssetClass = 'stock' | 'etf' | 'crypto' | 'forex'

export interface HealthStatus {
  status: 'ok' | 'degraded'
  db: 'ok' | 'error'
  redis: 'ok' | 'error'
  version: string
}

export interface Asset {
  id: number
  symbol: string
  name: string
  asset_class: AssetClass
  exchange: string | null
  currency: string
  meta: Record<string, unknown>
  is_active: boolean
  created_at: string
}

export interface SearchResultItem {
  symbol: string
  name: string
  asset_class: AssetClass
  exchange: string | null
  currency: string
  tracked: boolean
  asset_id: number | null
  provider: string | null
  provider_symbol: string | null
}

export interface Candle {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}

export interface AssetMetrics {
  asset_id: number
  as_of: string
  computed_at: string
  benchmark_symbol: string | null
  extras: {
    bars_available?: number
    mcap_rank?: number
    circulating_supply?: number
    [k: string]: unknown
  }
  [metric: string]: unknown // 50+ nullable numeric columns
}

export interface IndicatorPoint {
  time: number
  value: number
}

export type IndicatorSeries = Record<string, IndicatorPoint[] | Record<string, IndicatorPoint[]>>

export interface FundamentalsRow {
  asset_id: number
  period: string
  report_date: string
  fiscal_year: number | null
  currency: string
  provider: string
  fetched_at: string
  revenue: number | null
  eps: number | null
  fcf: number | null
  gross_margin: number | null
  net_margin: number | null
  roe: number | null
  debt_to_equity: number | null
  pe: number | null
  ps: number | null
  ev_ebitda: number | null
  metrics: Record<string, Record<string, unknown>>
}

export interface NewsItem {
  id: number
  ts: string
  source: string
  headline: string
  url: string
  tickers: string[]
  sentiment: number | null
}

export interface Note {
  id: number
  asset_id: number
  title: string | null
  body_md: string
  source: 'user' | 'ai'
  created_at: string
  updated_at: string
}

export interface WatchlistItem {
  asset_id: number
  symbol: string
  name: string
  asset_class: AssetClass
  added_at: string
  close: number | null
  return_1d: number | null
}

export interface Watchlist {
  id: number
  name: string
  created_at: string
  items: WatchlistItem[]
}

export interface TrackAssetBody {
  symbol: string
  name: string
  asset_class: AssetClass
  exchange?: string | null
  currency?: string
  meta?: Record<string, unknown>
}

// --- screener / backtests -------------------------------------------------------

export type FilterLeaf = {
  field: string
  op: string
  value?: number | [number, number] | { field: string } | null
}
export type FilterNode =
  | FilterLeaf
  | { all: FilterNode[] }
  | { any: FilterNode[] }
  | { not: FilterNode }

export interface ScreenField {
  name: string
  backtestable: boolean
  fundamental: boolean
}

export interface Screen {
  id: number
  name: string
  description: string | null
  asset_class: AssetClass | null
  ast: FilterNode
  created_at: string
  updated_at: string
}

export interface ScreenHit {
  asset_id: number
  symbol: string
  name: string
  asset_class: AssetClass
  as_of: string | null
  values: Record<string, number | null>
}

export interface ScreenRunResult {
  count: number
  columns: string[]
  results: ScreenHit[]
}

export interface AstErrorDetail {
  path?: string
  error: string
  valid_fields?: string[]
  valid_ops?: string[]
}

export interface BacktestStats {
  cagr: number | null
  sharpe: number | null
  max_drawdown: number | null
  win_rate: number | null
  total_return: number | null
  excess_return: number | null
  n_trades: number
  start: string | null
  end: string | null
  bars: number
  benchmark: { cagr: number | null; total_return: number | null; max_drawdown: number | null } | null
  benchmark_symbol?: string
  universe_size?: number
  rebalances?: number
  holding_days?: number
  symbol?: string
}

export interface BacktestSummary {
  id: number
  kind: 'screen' | 'strategy'
  status: 'queued' | 'running' | 'done' | 'failed'
  screen_id: number | null
  stats: BacktestStats | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface Backtest extends BacktestSummary {
  params: Record<string, unknown>
  equity_curve: { portfolio: [string, number][]; benchmark: [string, number][] } | null
  trade_list: unknown[] | null
  artifact_path: string | null
  started_at: string | null
}

export interface StrategyTrade {
  entry_ts: string
  exit_ts: string
  entry_price: number | null
  exit_price: number | null
  pnl: number
  pnl_pct: number | null
  bars_held: number
}

export interface ScreenHolding {
  date: string
  symbols: string[]
}

export interface ScreenBacktestBody {
  screen_id?: number
  ast?: FilterNode
  asset_class?: AssetClass
  holding_days?: number
  start?: string
  end?: string
  benchmark?: string
  fees_pct?: number
}

export interface StrategyBacktestBody {
  asset_id: number
  entry: FilterNode
  exit: FilterNode
  stop_loss_pct?: number | null
  take_profit_pct?: number | null
  position_size_pct?: number
  cash?: number
  fees_pct?: number
  start?: string
  end?: string
}

// --- api ----------------------------------------------------------------------

export const api = {
  // /health sits outside /api/v1 and needs no token
  health: () => fetch('/health').then((r) => r.json() as Promise<HealthStatus>),
  ping: () => request<{ pong: boolean }>('/ping'),

  search: (q: string) =>
    request<{ query: string; results: SearchResultItem[] }>(
      `/assets/search?q=${encodeURIComponent(q)}`,
    ),
  trackAsset: (body: TrackAssetBody) =>
    request<Asset>('/assets', { method: 'POST', body: JSON.stringify(body) }),
  asset: (id: number) => request<Asset>(`/assets/${id}`),
  metrics: (id: number) => request<AssetMetrics>(`/assets/${id}/metrics`),
  ohlcv: (id: number, interval: string) =>
    request<{ asset_id: number; interval: string; candles: Candle[] }>(
      `/assets/${id}/ohlcv?interval=${interval}`,
    ),
  indicators: (id: number, keys: string[], interval: string) =>
    request<{ asset_id: number; interval: string; series: IndicatorSeries }>(
      `/assets/${id}/indicators?keys=${keys.join(',')}&interval=${interval}`,
    ),

  fundamentals: (id: number) => request<FundamentalsRow[]>(`/assets/${id}/fundamentals`),
  refreshFundamentals: (id: number) =>
    request<{ task_id: string }>(`/assets/${id}/fundamentals/refresh`, { method: 'POST' }),
  news: (id: number, days = 7) => request<NewsItem[]>(`/assets/${id}/news?days=${days}`),

  notes: (assetId: number) => request<Note[]>(`/assets/${assetId}/notes`),
  createNote: (assetId: number, body: { title?: string | null; body_md: string }) =>
    request<Note>(`/assets/${assetId}/notes`, { method: 'POST', body: JSON.stringify(body) }),
  updateNote: (assetId: number, noteId: number, body: { title?: string | null; body_md?: string }) =>
    request<Note>(`/assets/${assetId}/notes/${noteId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteNote: (assetId: number, noteId: number) =>
    request<void>(`/assets/${assetId}/notes/${noteId}`, { method: 'DELETE' }),

  watchlists: () => request<Watchlist[]>('/watchlists'),
  createWatchlist: (name: string) =>
    request<Watchlist>('/watchlists', { method: 'POST', body: JSON.stringify({ name }) }),
  deleteWatchlist: (id: number) => request<void>(`/watchlists/${id}`, { method: 'DELETE' }),
  addWatchlistItem: (watchlistId: number, assetId: number) =>
    request<unknown>(`/watchlists/${watchlistId}/items`, {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId }),
    }),
  removeWatchlistItem: (watchlistId: number, assetId: number) =>
    request<void>(`/watchlists/${watchlistId}/items/${assetId}`, { method: 'DELETE' }),

  screenFields: () => request<ScreenField[]>('/screens/fields'),
  screens: () => request<Screen[]>('/screens'),
  createScreen: (body: {
    name: string
    description?: string | null
    asset_class?: AssetClass | null
    ast: FilterNode
  }) => request<Screen>('/screens', { method: 'POST', body: JSON.stringify(body) }),
  updateScreen: (
    id: number,
    body: { name: string; description?: string | null; asset_class?: AssetClass | null; ast: FilterNode },
  ) => request<Screen>(`/screens/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteScreen: (id: number) => request<void>(`/screens/${id}`, { method: 'DELETE' }),
  runScreen: (body: { ast: FilterNode; asset_class?: AssetClass | null; limit?: number }) =>
    request<ScreenRunResult>('/screens/run', { method: 'POST', body: JSON.stringify(body) }),
  runSavedScreen: (id: number) =>
    request<ScreenRunResult>(`/screens/${id}/run`, { method: 'POST' }),

  backtests: (kind?: 'screen' | 'strategy') =>
    request<BacktestSummary[]>(`/backtests${kind ? `?kind=${kind}` : ''}`),
  backtest: (id: number) => request<Backtest>(`/backtests/${id}`),
  createScreenBacktest: (body: ScreenBacktestBody) =>
    request<BacktestSummary>('/backtests/screen', { method: 'POST', body: JSON.stringify(body) }),
  createStrategyBacktest: (body: StrategyBacktestBody) =>
    request<BacktestSummary>('/backtests/strategy', { method: 'POST', body: JSON.stringify(body) }),
  deleteBacktest: (id: number) => request<void>(`/backtests/${id}`, { method: 'DELETE' }),
  backtestReportBlob: async (id: number): Promise<Blob> => {
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const resp = await fetch(`${API_BASE}/backtests/${id}/report`, { headers })
    if (!resp.ok) throw new ApiError(resp.status, await resp.text())
    return resp.blob()
  },
}

// --- shared formatting helpers -------------------------------------------------

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return value.toFixed(digits)
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function pctClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'text-zinc-500'
  return value >= 0 ? 'text-emerald-400' : 'text-red-400'
}

export function relTime(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
