# Plutus

Self-hosted, single-user investment research, opportunity-discovery, and portfolio-tracking
hub for stocks/ETFs, crypto, and forex. **Research and analysis only — no trade execution,
ever.** Full specification: `investment-hub-spec.md` (kept outside the repo).

**Status: Phase 2 complete** — research core (of 7 phases).

| Phase | Scope | Status |
|---|---|---|
| 1 | Docker stack, TimescaleDB, provider adapters, EOD ingestion, symbol API | ✅ |
| 2 | Research core: charts, indicators, `asset_metrics`, watchlists, fundamentals, news, notes | ✅ |
| 3 | Screener + backtesting (VectorBT / Backtrader) | — |
| 4 | Autonomous discovery engine (mandates → scans → ranked inbox) | — |
| 5 | Portfolio tracking (transactions, P&L, TWR/IRR, bank investments) | — |
| 6 | Exchange sync, websockets, hardening | — |
| 7 | AI research agent + MCP control plane | — |

## Stack

FastAPI + SQLAlchemy 2 + Alembic · Celery + Redis · PostgreSQL 16 + TimescaleDB ·
React 18 + TypeScript + Vite + Tailwind v4 · Docker Compose · Python 3.13 via `uv`

## Quickstart

Prerequisites: Docker Desktop, `uv`, Node 20.19+/22.

```sh
cp .env.example .env        # set APP_AUTH_TOKEN to something long and random
                            # free-tier keys: TIINGO_API_KEY + TWELVEDATA_API_KEY (candles),
                            # FMP_API_KEY (fundamentals), FINNHUB_API_KEY (news);
                            # Binance + CoinGecko need no keys

docker compose up --build -d      # db + redis + api + worker + beat
make seed                         # track AAPL, BTC, EURUSD + SPY/UUP benchmarks
make ingest                       # seed + pull daily candles inline (or POST /api/v1/ingestion/run)
cd frontend && npm install && npm run dev   # research UI on :5173
```

API: <http://localhost:8800> (OpenAPI docs at `/api/docs`, health at `/health`).
All `/api/v1/*` endpoints require `Authorization: Bearer $APP_AUTH_TOKEN`.

> Ports: the API maps to host **8800** and Postgres to **5433** to avoid clashing with
> other local stacks. Inside the compose network they remain 8000/5432.

### Dev mode (fast iteration)

```sh
make infra        # db + redis in Docker only
make api          # uvicorn --reload on :8800
make worker       # celery worker (optional; only for queued jobs)
cd frontend && npm install && npm run dev    # Vite on :5173, proxies /api + /health
```

### Tests

```sh
make test              # unit: bucket math, backoff/cache, golden-file normalize, auth
make test-integration  # full pipeline vs real db/redis with mocked provider HTTP (needs `make infra`)
make lint
```

## Verifying the Phase 1 gate

*"Can ingest and query daily candles for AAPL, BTC, EURUSD."*

```sh
make ingest
# per-symbol bar counts (expect AAPL ≈500, BTC ≈365, EURUSD ≈520 on first backfill):
docker compose exec db psql -U plutus -d plutus -c \
  "SELECT a.symbol, count(*) bars, max(ts)::date latest FROM ohlcv o JOIN assets a ON a.id=o.asset_id GROUP BY 1;"
# run history + candles over the API:
curl -H "Authorization: Bearer $TOK" 'localhost:8800/api/v1/ingestion/runs'
curl -H "Authorization: Bearer $TOK" 'localhost:8800/api/v1/assets/1/ohlcv?interval=1d&start=2026-01-01'
```

Nightly ingestion runs via Celery Beat at 03:00/03:10/03:20 America/Mexico_City
(crypto/forex/stocks), incremental and idempotent; every run is logged to `ingestion_runs`.

## Notes & deliberate decisions

- **Stocks store the adjusted series** (Tiingo `adj*` columns) so indicators are
  split/dividend-consistent.
- **Crypto candles are real OHLCV from Binance** (keyless, `data-api.binance.vision`);
  CoinGecko remains the crypto search/metadata source (mcap rank, supply). Note: Binance
  returns HTTP 451 from US IPs — fine from Mexico; a Kraken fallback (720-bar history cap)
  is the documented alternative.
- **Indicators have one source of truth**: the same engine feeds chart series, the nightly
  `asset_metrics` snapshot, and (later) the screener. VWAP is a rolling 20-day
  approximation (true VWAP is intraday) and is labeled as such. Weekly/monthly charts
  compute indicators on resampled bars (Timescale `time_bucket`, computed on read).
- **Benchmarks are ordinary tracked assets** (SPY / BTC / UUP — raw DXY is paid-gated on
  Twelve Data free; `BENCHMARK_FOREX` is env-swappable).
- **redis-py is pinned `<6.5`** — kombu (Celery transport) requires it; do not bump.
- **DB image must stay `timescale/timescaledb` (TSL)** — the `-oss` variant lacks compression.
- Rate limits are enforced client-side: Redis token bucket + hard daily/monthly budgets at
  ~90% of each provider's free tier, plus response caching (bars 12h, quotes 30–60s,
  search 24h, fundamentals 24h, news 10m).
- Beat schedule (America/Mexico_City): EOD 03:00/03:10/03:20 · metrics 03:40 ·
  news every 15 min · fundamentals Sun 04:00.

Market data by Tiingo, Binance, Twelve Data, FMP, and Finnhub — crypto metadata powered by
CoinGecko. Personal use only, no redistribution.
