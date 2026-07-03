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
  return resp.json() as Promise<T>
}

export interface HealthStatus {
  status: 'ok' | 'degraded'
  db: 'ok' | 'error'
  redis: 'ok' | 'error'
  version: string
}

export interface SearchResultItem {
  symbol: string
  name: string
  asset_class: 'stock' | 'etf' | 'crypto' | 'forex'
  exchange: string | null
  currency: string
  tracked: boolean
  asset_id: number | null
}

export interface Candle {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}

export const api = {
  // /health sits outside /api/v1 and needs no token
  health: () => fetch('/health').then((r) => r.json() as Promise<HealthStatus>),
  ping: () => request<{ pong: boolean }>('/ping'),
  search: (q: string) =>
    request<{ query: string; results: SearchResultItem[] }>(
      `/assets/search?q=${encodeURIComponent(q)}`,
    ),
  ohlcv: (assetId: number, interval = '1d') =>
    request<{ asset_id: number; interval: string; candles: Candle[] }>(
      `/assets/${assetId}/ohlcv?interval=${interval}`,
    ),
}
