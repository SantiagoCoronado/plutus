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

// --- discovery types -----------------------------------------------------------

export type CandidateStatus = 'new' | 'reviewed' | 'starred' | 'dismissed'
export type NotifyMode = 'off' | 'instant' | 'digest'

export type UniverseDef =
  | { type: 'class' }
  | { type: 'watchlist'; watchlist_id: number }
  | { type: 'market_cap_floor'; min_market_cap: number }
  | { type: 'top_by_market_cap'; count: number }

export interface MandateStats {
  candidates_total: number
  new: number
  starred: number
  dismissed: number
  hit_rate: number | null
}

export interface LastScan {
  id: number
  status: string
  finished_at: string | null
  error: string | null
}

export interface Mandate {
  id: number
  name: string
  description: string | null
  asset_class: AssetClass
  universe_def: UniverseDef
  rules: FilterNode | null
  schedule: string
  score_weights: Record<string, number>
  min_score: number
  notify_min_score: number | null
  max_candidates: number
  cooldown_days: number
  notify: NotifyMode
  active: boolean
  last_run_at: string | null
  created_at: string
  updated_at: string
  next_run_at: string | null
  stats: MandateStats | null
  last_scan: LastScan | null
}

export interface MandateBody {
  name: string
  description?: string | null
  asset_class: AssetClass
  universe_def: UniverseDef
  rules?: FilterNode | null
  schedule: string
  score_weights: Record<string, number>
  min_score?: number
  notify_min_score?: number | null
  max_candidates?: number
  cooldown_days?: number
  notify?: NotifyMode
  active?: boolean
}

export interface Scan {
  id: number
  mandate_id: number
  status: 'queued' | 'running' | 'done' | 'failed'
  stats: Record<string, unknown> | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface SignalInfo {
  key: string
  label: string
  description: string
  asset_classes: AssetClass[]
  needs_volume: boolean
  supports_history_check: boolean
}

export interface CandidateSignal {
  key: string
  label: string
  score: number
  weight: number
  triggered: boolean
  evidence: Record<string, unknown>
}

export interface HistoryHorizon {
  n: number
  median: number
  win_rate: number | null
}

export interface HistoryCheck {
  n_triggers: number
  fwd: Record<string, HistoryHorizon>
}

export interface CandidateContext {
  snapshot?: Record<string, number | string | null>
  history_check?: Record<string, HistoryCheck>
  chart?: [string, number][]
  memo_note_id?: number
}

export interface Candidate {
  id: number
  mandate_id: number
  mandate_name: string
  asset_id: number
  symbol: string
  name: string
  asset_class: AssetClass
  ts: string
  score: number
  status: CandidateStatus
  signals: CandidateSignal[]
  context: CandidateContext
  created_at: string
}

export interface MandateCandidateSummary {
  mandate_id: number
  mandate_name: string
  new: number
  starred: number
  dismissed: number
  hit_rate: number | null
}

export interface CandidateSummary {
  by_status: Record<CandidateStatus, number>
  by_mandate: MandateCandidateSummary[]
}

// --- portfolio (Phase 5) -------------------------------------------------------

export type AccountType = 'brokerage' | 'exchange' | 'wallet' | 'bank' | 'manual'
export type TransactionType =
  | 'buy'
  | 'sell'
  | 'deposit'
  | 'withdrawal'
  | 'dividend'
  | 'interest'
  | 'fee'
  | 'transfer_in'
  | 'transfer_out'

export interface Account {
  id: number
  name: string
  type: AccountType
  provider: string | null
  currency: string
  note: string | null
  is_active: boolean
  created_at: string
  cash_balances: { currency: string; amount: number }[]
  transactions_count: number
  bank_investments_count: number
}

export interface AccountBody {
  name: string
  type: AccountType
  provider?: string | null
  currency?: string
  note?: string | null
  is_active?: boolean
}

export interface Transaction {
  id: number
  account_id: number
  asset_id: number | null
  type: TransactionType
  ts: string
  quantity: number
  price: number | null
  fees: number
  currency: string
  note: string | null
  external_id: string | null
  lot_links: { buy_transaction_id: number; quantity: number }[] | null
  created_at: string
  account_name: string | null
  symbol: string | null
}

export interface TransactionBody {
  account_id: number
  asset_id?: number | null
  type: TransactionType
  ts: string
  quantity: number
  price?: number | null
  fees?: number
  currency: string
  note?: string | null
  lot_links?: { buy_transaction_id: number; quantity: number }[] | null
}

export interface RateTier {
  up_to: number | null
  annual_rate: number
}

export interface BankInvestment {
  id: number
  account_id: number
  name: string
  kind: 'demand' | 'fixed_term'
  principal: number
  currency: string
  annual_rate: number
  rate_tiers: RateTier[] | null
  day_count: 'act360' | 'act365'
  compounding: 'daily' | 'monthly' | 'at_maturity'
  start_date: string
  term_days: number | null
  maturity_date: string | null
  cap_amount: number | null
  auto_renew: boolean
  status: 'active' | 'matured' | 'closed'
  note: string | null
  created_at: string
  updated_at: string
  accrued_interest: number
  current_value: number
  projected_maturity_value: number | null
  days_to_maturity: number | null
  effective_annual_rate: number
  account_name: string | null
}

export interface BankInvestmentBody {
  account_id: number
  name: string
  kind: 'demand' | 'fixed_term'
  principal: number
  currency?: string
  annual_rate: number
  rate_tiers?: RateTier[] | null
  day_count?: 'act360' | 'act365'
  compounding?: 'daily' | 'monthly' | 'at_maturity'
  start_date: string
  term_days?: number | null
  cap_amount?: number | null
  auto_renew?: boolean
  status?: 'active' | 'matured' | 'closed'
  note?: string | null
}

export interface Position {
  account_id: number
  account_name: string | null
  asset_id: number
  symbol: string
  name: string | null
  asset_class: string | null
  quantity: number
  average_cost_native: number | null
  cost_currency: string | null
  native_currency: string
  last_price: number | null
  market_value_native: number | null
  value: number | null
  cost_basis: number | null
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
  realized_pnl: number | null
  weight: number | null
}

export interface PositionsReport {
  as_of: string
  currency: string
  totals: {
    value: number | null
    positions_value: number | null
    cash_value: number | null
    bank_value: number | null
    cost_basis: number | null
    unrealized_pnl: number | null
    unrealized_pnl_pct: number | null
    realized_pnl: number | null
  }
  positions: Position[]
  cash: {
    account_id: number
    account_name: string | null
    currency: string
    amount: number | null
    value: number | null
  }[]
  bank_investments: {
    id: number
    account_id: number
    account_name: string | null
    name: string
    kind: string
    currency: string
    principal: number
    accrued_interest: number | null
    value_native: number | null
    value: number | null
    maturity_date: string | null
    status: string
  }[]
  warnings: Record<string, unknown>[]
}

export type PerformancePeriod = '1m' | '3m' | '6m' | 'ytd' | '1y' | 'all'

export interface PerformanceReport {
  currency: string
  period: string
  start: string
  end: string
  twr: number | null
  twr_annualized: number | null
  irr: number | null
  series: [string, number][]
  indexed: [string, number][]
  benchmark: { symbol: string; indexed: [string, number][] } | null
  flows: [string, number][]
}

export interface AllocationReport {
  as_of: string
  currency: string
  by: string
  total: number | null
  groups: { key: string; value: number | null; weight: number | null }[]
}

export interface CsvPreview {
  columns: string[]
  sample_rows: Record<string, string>[]
  row_count: number
  preset: string | null
  suggested_mapping: Record<string, string>
}

export interface CsvCommitResult {
  created: number
  skipped_duplicates: number
  errors: { row?: number; error: string }[]
}

// --- agent layer (Phase 6) ---

export type LLMProviderName =
  | 'claude-subscription'
  | 'anthropic-api'
  | 'openai'
  | 'google'
  | 'openrouter'
  | 'ollama'

export interface LLMSettings {
  provider: LLMProviderName
  model: string
  keys: Record<string, string | null>
  sidecar: { url: string; reachable: boolean; auth_ok: boolean }
  daily_token_budget: number
  fernet_configured: boolean
}

export interface TestConnectionResult {
  ok: boolean
  provider: string
  detail: string
}

export interface AgentUsage {
  date: string
  tokens_used: number
  daily_token_budget: number
  remaining: number
}

export interface AgentAction {
  id: number
  conversation_id: number | null
  source: 'app' | 'task' | 'mcp'
  tier: 'read' | 'write'
  name: string
  arguments: Record<string, unknown>
  status: string
  result_summary: string | null
  error: string | null
  created_at: string
}

export interface AgentToolCallRef {
  id: string
  name: string
  arguments: Record<string, unknown>
}

export interface AgentMessage {
  id: number
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string | null
  tool_calls: AgentToolCallRef[] | null
  tool_call_id: string | null
  tool_name: string | null
  tool_result: Record<string, unknown> | null
  provider: string | null
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  created_at: string
}

export interface AgentConversation {
  id: number
  kind: 'chat' | 'task' | 'translate'
  title: string | null
  autonomous: boolean
  status: string
  provider: string | null
  model: string | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface AgentConfirmation {
  id: number
  name: string
  arguments: Record<string, unknown>
  result_summary: string | null
  status: string
  created_at: string
}

export interface ConversationDetail {
  conversation: AgentConversation
  messages: AgentMessage[]
  pending_confirmations: AgentConfirmation[]
}

export interface ConfirmationResolution {
  ok: boolean
  status: string
  result_summary: string | null
  error: string | null
  result: unknown
}

export function getCurrency(): string {
  return localStorage.getItem('plutus_currency') ?? 'USD'
}

export function setCurrency(currency: string): void {
  localStorage.setItem('plutus_currency', currency)
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
  mandates: () => request<Mandate[]>('/mandates'),
  mandate: (id: number) => request<Mandate>(`/mandates/${id}`),
  createMandate: (body: MandateBody) =>
    request<Mandate>('/mandates', { method: 'POST', body: JSON.stringify(body) }),
  updateMandate: (id: number, body: MandateBody) =>
    request<Mandate>(`/mandates/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  patchMandate: (id: number, body: { active?: boolean; notify?: NotifyMode }) =>
    request<Mandate>(`/mandates/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteMandate: (id: number) => request<void>(`/mandates/${id}`, { method: 'DELETE' }),
  runMandate: (id: number) => request<Scan>(`/mandates/${id}/scan`, { method: 'POST' }),
  mandateScans: (id: number, limit = 20) =>
    request<Scan[]>(`/mandates/${id}/scans?limit=${limit}`),
  discoverySignals: () => request<SignalInfo[]>('/mandates/signals'),
  testAlert: () =>
    request<{ results: { channel: string; ok: boolean; error: string | null }[] }>(
      '/mandates/test-alert',
      { method: 'POST' },
    ),

  candidates: (
    params: {
      status?: CandidateStatus | ''
      mandate_id?: number | ''
      asset_class?: AssetClass | ''
      order?: 'score' | 'newest'
      limit?: number
    } = {},
  ) => {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.mandate_id) query.set('mandate_id', String(params.mandate_id))
    if (params.asset_class) query.set('asset_class', params.asset_class)
    if (params.order) query.set('order', params.order)
    if (params.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<Candidate[]>(`/candidates${qs ? `?${qs}` : ''}`)
  },
  candidatesSummary: () => request<CandidateSummary>('/candidates/summary'),
  patchCandidate: (id: number, status: CandidateStatus) =>
    request<Candidate>(`/candidates/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),

  backtestReportBlob: async (id: number): Promise<Blob> => {
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const resp = await fetch(`${API_BASE}/backtests/${id}/report`, { headers })
    if (!resp.ok) throw new ApiError(resp.status, await resp.text())
    return resp.blob()
  },

  // --- portfolio (Phase 5) ---
  accounts: () => request<Account[]>('/accounts'),
  createAccount: (body: AccountBody) =>
    request<Account>('/accounts', { method: 'POST', body: JSON.stringify(body) }),
  updateAccount: (id: number, body: AccountBody) =>
    request<Account>(`/accounts/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteAccount: (id: number) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),

  transactions: (params: {
    account_id?: number
    asset_id?: number
    type?: string
    limit?: number
    offset?: number
  }) => {
    const qs = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) qs.set(key, String(value))
    }
    return request<{ items: Transaction[]; total: number }>(`/transactions?${qs}`)
  },
  createTransaction: (body: TransactionBody) =>
    request<Transaction>('/transactions', { method: 'POST', body: JSON.stringify(body) }),
  updateTransaction: (id: number, body: TransactionBody) =>
    request<Transaction>(`/transactions/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteTransaction: (id: number) => request<void>(`/transactions/${id}`, { method: 'DELETE' }),

  bankInvestments: () => request<BankInvestment[]>('/bank-investments'),
  createBankInvestment: (body: BankInvestmentBody) =>
    request<BankInvestment>('/bank-investments', { method: 'POST', body: JSON.stringify(body) }),
  updateBankInvestment: (id: number, body: BankInvestmentBody) =>
    request<BankInvestment>(`/bank-investments/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteBankInvestment: (id: number) =>
    request<void>(`/bank-investments/${id}`, { method: 'DELETE' }),

  portfolioPositions: (currency: string) =>
    request<PositionsReport>(`/portfolio/positions?currency=${currency}`),
  portfolioPerformance: (period: PerformancePeriod, currency: string) =>
    request<PerformanceReport>(`/portfolio/performance?period=${period}&currency=${currency}`),
  portfolioAllocation: (by: string, currency: string) =>
    request<AllocationReport>(`/portfolio/allocation?by=${by}&currency=${currency}`),

  csvPreview: (content: string) =>
    request<CsvPreview>('/portfolio/import/csv/preview', {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  csvCommit: (body: { account_id: number; content: string; mapping: Record<string, string> }) =>
    request<CsvCommitResult>('/portfolio/import/csv/commit', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  agentConversations: (kind = 'chat') =>
    request<AgentConversation[]>(`/agent/conversations?kind=${kind}`),
  createAgentConversation: (title?: string) =>
    request<AgentConversation>('/agent/conversations', {
      method: 'POST',
      body: JSON.stringify({ title: title ?? null }),
    }),
  agentConversation: (id: number) =>
    request<ConversationDetail>(`/agent/conversations/${id}`),
  patchAgentConversation: (id: number, body: { title?: string; autonomous?: boolean }) =>
    request<AgentConversation>(`/agent/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteAgentConversation: (id: number) =>
    request<void>(`/agent/conversations/${id}`, { method: 'DELETE' }),
  approveConfirmation: (id: number) =>
    request<ConfirmationResolution>(`/agent/confirmations/${id}/approve`, { method: 'POST' }),
  rejectConfirmation: (id: number) =>
    request<ConfirmationResolution>(`/agent/confirmations/${id}/reject`, { method: 'POST' }),

  agentSettings: () => request<LLMSettings>('/agent/settings'),
  updateAgentSettings: (body: {
    provider?: LLMProviderName
    model?: string
    keys?: Record<string, string>
  }) => request<LLMSettings>('/agent/settings', { method: 'PUT', body: JSON.stringify(body) }),
  testAgentConnection: (provider?: LLMProviderName) =>
    request<TestConnectionResult>('/agent/settings/test', {
      method: 'POST',
      body: JSON.stringify({ provider: provider ?? null }),
    }),
  agentUsage: () => request<AgentUsage>('/agent/usage'),
  agentActions: (filters?: { source?: string; tier?: string; limit?: number }) => {
    const params = new URLSearchParams()
    if (filters?.source) params.set('source', filters.source)
    if (filters?.tier) params.set('tier', filters.tier)
    if (filters?.limit) params.set('limit', String(filters.limit))
    const qs = params.toString()
    return request<AgentAction[]>(`/agent/actions${qs ? `?${qs}` : ''}`)
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
