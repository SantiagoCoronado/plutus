# Plutus

Self-hosted, single-user investment research, opportunity-discovery, and portfolio-tracking
hub for stocks/ETFs, crypto, and forex. **Research and analysis only — no trade execution,
ever.** Full specification: `investment-hub-spec.md` (kept outside the repo).

**Status: Phase 7 complete** — exchange sync, live quotes, price alerts, backups, hardening (all 7 phases shipped).

| Phase | Scope | Status |
|---|---|---|
| 1 | Docker stack, TimescaleDB, provider adapters, EOD ingestion, symbol API | ✅ |
| 2 | Research core: charts, indicators, `asset_metrics`, watchlists, fundamentals, news, notes | ✅ |
| 3 | Screener (filter AST) + backtesting (VectorBT / Backtrader + quantstats) | ✅ |
| 4 | Autonomous discovery engine (mandates → scans → ranked inbox + alerts) | ✅ |
| 5 | Portfolio tracking (transactions, P&L, TWR/IRR, bank investments) + fundamentals signal pack | ✅ |
| 6 | AI research agent (candidate memos, strategy-from-content translator) + MCP control plane | ✅ |
| 7 | Exchange sync (Bitso), live quotes, per-asset price alerts, dashboard, backups, hardening | ✅ |

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

docker compose up --build -d      # db + redis + api + worker + beat + quotes + backup + agent-sidecar
make seed                         # track AAPL, BTC, EURUSD + SPY/QQQ/UUP/ETH benchmarks & strip
make ingest                       # seed + pull daily candles inline (or POST /api/v1/ingestion/run)
cd backend && uv run python -m app.ingestion.universe   # optional: ~100 large caps + top crypto,
                                  # 5y backfill (paced by rate limits, ~2h; resumable)
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

## Screener & backtesting (Phase 3)

- **Screener** (`/screener`): rule builder emits a JSON filter AST
  (`{"all": [{"field": "rsi_14", "op": "<", "value": 30}, ...]}`) evaluated against the
  nightly `asset_metrics` snapshot. `any`/`not` nesting and column-vs-column comparisons
  (`close > sma_200`) are available through the JSON editor. Screens are saveable and
  runnable on demand (scheduling arrives with Phase 4 mandates). SQL NULL semantics: an
  asset with a missing metric never matches, even under `not`.
- **Screen backtest** (VectorBT): replays the AST on point-in-time panels recomputed from
  OHLCV, rebalancing every N bars into equal weights with **next-bar execution** (signal
  at close(t), fill at open(t+1)). Fundamental fields are rejected — only latest annual
  snapshots are stored, so screening history on them would be look-ahead. Stats: CAGR,
  Sharpe, max drawdown, win rate, excess return vs benchmark buy & hold.
- **Strategy backtest** (Backtrader): entry/exit condition ASTs on one asset, with
  `crosses_above`/`crosses_below`, optional stop-loss/take-profit (checked on close,
  exits next open), position sizing, and a quantstats HTML report saved to the shared
  `artifacts` volume and served at `GET /backtests/{id}/report`.
- Backtests run in the worker; `POST /backtests/{screen,strategy}` returns a pollable
  row (`queued → running → done|failed`).

## Discovery engine (Phase 4)

- **Mandates** (`/mandates`): standing scan instructions — a universe (whole class /
  watchlist / market-cap floor / top-N by size), an optional screener-AST coarse filter,
  a 5-field cron schedule (local time), per-signal weights, and alert settings. A beat
  dispatcher checks every active mandate's cron every 5 minutes and enqueues due scans;
  "Run now" triggers one immediately without moving the standing schedule.
- **Scan funnel**: universe → coarse filter (one indexed query over `asset_metrics`) →
  vectorized signal analysis over the full 5-year bar window → weighted 0–100 composite
  score. A candidate needs `score ≥ min_score` *and* at least one triggered signal.
  Fine analysis is DB + pandas only — scans never call providers, so rate budgets are
  untouchable. A ~100-asset scan runs in about a second.
- **Signal library** (`app/discovery/signals.py`): price breakout, trend cross (50/200),
  oversold RSI, momentum vs peers (cross-sectional percentile), stretched below trend,
  cheap vs own 5y history (annual fundamentals), unusual volume, crypto drawdown from
  the all-time high, dip in an uptrend. Earnings surprise and forex rate-differential
  are deferred (no data source on the free provider tiers).
- **Auto-context**: every candidate ships its evidence — a metrics snapshot at trigger
  time, a sparkline payload, and a *history check*: forward 5/20/60-day returns after
  past triggers of the same signal on the same asset ("after 13 past signals: +3.1%
  median 20-day move, 62% win rate").
- **Research Inbox** (`/inbox`): ranked card queue, filterable by status / mandate /
  class. Star/dismiss feed per-mandate hit-rate stats (starred ÷ voted) so weights can
  be tuned by hand — deliberately no ML re-ranking.
- **Dedup**: a scan never re-nominates an asset while its previous candidate is still
  unreviewed, nor within the mandate's cooldown window (default 7 days).
- **Alerts**: email (SMTP) and Telegram, both env-optional (`SMTP_*`, `TELEGRAM_*` in
  `.env`). Instant mode sends one message per scan batching candidates above the alert
  threshold; digest mode sends a daily 08:00 summary. Every attempt is audited in
  `notifications`; `POST /api/v1/mandates/test-alert` (or the button on the Mandates
  page) verifies channels.

## Portfolio tracking (Phase 5)

- **Transactions are the source of truth** (`/portfolio`): positions, lots, and P&L are
  derived on every read — nothing stored can drift. Nine transaction types cover buys,
  sells, cash moves, dividends/interest/fees, and transfers between accounts
  (`transfer_in` carries the original cost so basis survives a Bitso → Ledger move).
- **Lot accounting**: first-in-first-out by default; a sell can name its exact buy lots
  (`lot_links`) for specific-ID matching. Writes are validated by a strict lot replay —
  overselling and edits that would orphan a later sell are rejected up front.
- **Multi-currency**: every amount is stored in its native currency; reports convert at
  the valuation date via tracked forex closes (USDMXN, EURUSD — no separate rates
  pipeline). The UI has a USD⇄MXN toggle; a missing rate degrades to a warning.
- **Performance**: daily portfolio value series feeds time-weighted return (flows land
  end-of-day) and money-weighted return (XIRR); the chart shows the portfolio vs SPY
  indexed to 100.
- **Bank investments** (spec §7.4): fixed-term deposits (pagarés/CETES-style) and
  interest-bearing demand balances, with ACT/360 or ACT/365 day counts, simple/monthly/
  daily compounding, tiered rates (`15% up to 25k, 5% above`), caps, and auto-renew
  (accrued interest capitalizes into the principal at rollover). A daily beat task flips
  matured investments and sends reminders `MATURITY_REMINDER_DAYS` ahead through the
  alert channels — deduped per investment per maturity date.
- **CSV import**: paste or upload; delimiter sniffing, English/Spanish header
  suggestions, a data-driven Bitso preset, per-row errors that never block good rows,
  and content-hash dedup so re-imports count as `skipped_duplicates`.
- **Fundamentals signal pack** for discovery mandates: *Financially healthy*
  (Piotroski-style nine-check score over the last two annual statements — checks skip
  rather than fail when a field is missing) and *Quality at a fair price*
  (Magic-Formula-style rank of earnings yield × return on invested capital vs the
  universe). Both stock-only; coverage grows as the weekly fundamentals rotation fills.

## AI research agent (Phase 6)

- **One tool registry, three surfaces** (`app/llm/tooldefs.py`): 21 tools (13 read /
  8 write) wrapping the hub's own services are served identically to the in-app chat,
  Celery research tasks, and the MCP server — they can never drift. Every call goes
  through one executor (validation → tier gate → confirmation gate → audit row in
  `agent_tool_calls`); a "recent agent actions" feed on the Settings page shows all of
  it. Hard-coded exclusions (editing/deleting transactions, touching LLM settings or
  alert channels, deleting mandates/watchlists/notes, anything trade-shaped) simply do
  not exist as tools — no configuration can expose them.
- **User-selectable LLM provider** (Settings page, no restart needed): default
  **claude-subscription** — a small Node sidecar (`sidecar/`) wraps
  `@anthropic-ai/claude-agent-sdk` so the agent runs on your Claude plan: run
  `claude setup-token`, paste the result as `CLAUDE_CODE_OAUTH_TOKEN` in `.env`,
  `docker compose up -d agent-sidecar`. Never set `ANTHROPIC_API_KEY` in the sidecar's
  environment (it would silently switch billing to the API; the server deletes it
  defensively). Alternatives: `anthropic-api`, `openai`, `google`, `openrouter`, and
  `ollama` (local) — API keys entered in Settings are Fernet-encrypted at rest
  (`FERNET_KEY` in `.env`) and shown masked.
- **Agent chat** (`/agent`): streaming conversations over hub data with tool-call
  chips. Write-tier actions render as **confirmation cards** — the model proposes,
  you approve or reject — unless you flip a conversation to autonomous mode. Every
  reply is AI-labeled; the system prompt forbids trade recommendations phrased as
  instructions.
- **Research memos**: an "AI deep-dive" button on any asset page / Inbox card (and a
  nightly 08:15 task over the top new candidates above their mandate's alert
  threshold) runs a structured loop — overview → fundamentals → news → signal
  backtests — and writes an AI-labeled note ending with a mandatory
  informational-only disclaimer. Memos link back to their Inbox candidate.
- **Strategy-from-content translator** (spec §13.5, "Test a strategy" on the
  Backtests page): paste an article/transcript/your own words; the LLM translates it
  into the *existing* strategy AST — never free-form code — and must present a
  **fidelity report** first: its plain-English understanding plus everything the
  daily-bar engine *cannot* express (intraday rules, leverage, options legs…). The
  backtest runs only after you confirm; results come back as a plain-English verdict
  vs buy-and-hold. Candlestick patterns (engulfing, hammer, shooting star, doji) are
  legal condition vocabulary — pure OHLC math, kept out of the screener snapshot.
- **MCP control plane**: `claude mcp add plutus -- uv --directory ~/plutus/backend run plutus-mcp`
  gives terminal Claude Code the same registry over stdio ("create a mandate for
  oversold large caps and run it now"). `MCP_TOOL_TIER=read` locks it to queries;
  writes are audited with `source=mcp`. Requires the compose db/redis reachable on
  localhost (the root `.env` dev values).
- **Guardrails**: `AGENT_DAILY_TOKEN_BUDGET` (default 500k) is a hard daily cap
  summed over every surface; tool loops cap at `AGENT_MAX_TOOL_ITERATIONS` (15);
  tool outputs are size-clipped; scheduled tasks skip-and-log when the budget is
  spent. The app is fully usable with the AI layer unconfigured.

## Live quotes, price alerts & exchange sync (Phase 7)

- **Live quotes** (`/ws/quotes`): the `quotes` compose service is a persistent
  streamer — Binance's public websocket for crypto plus a market-hours-gated poller
  for stocks/forex — fanned out to the browser over one websocket via Redis pub/sub.
  The dashboard market strip, watchlist, and heatmap tick live; when no live quote is
  available the UI falls back to the latest daily close. Intraday prices live in Redis
  only and never touch the `ohlcv` hypertable.
- **Price alerts** (bell icon on any asset, or via the agent): per-asset above/below
  threshold rules. A minute-cadence evaluator fires on an actual **crossing** (not a
  level), delivers once through the same email/Telegram channels as discovery alerts
  (`notifications` kind `price_alert`), then disarms — one-shot with explicit re-arm,
  so a flapping price can't spam you.
- **Exchange sync — Bitso, read-only** (Settings → connect, then "Sync now" on the
  exchange account): pulls your real trades, fundings, and withdrawals into the ledger,
  resuming from a saved cursor and deduping via `external_id` so re-syncs create zero
  duplicates. The client is **structurally GET-only** (no order/trade endpoints exist in
  the code) — research and analysis only, never execution. Keys are entered in Settings
  and Fernet-encrypted at rest; a nightly beat job re-syncs linked accounts.
- **Dashboard** (spec §9.1): live market strip, four metric cards (value, today's P&L,
  YTD TWR vs SPY, new candidates), an ECharts red→green heatmap treemap
  (portfolio / watchlist / market × 1D/1W/1M/YTD), the research inbox preview, watchlist,
  allocation donut, latest AI brief, and a status footer with ingestion health, last scan,
  and armed-alert count.
- **Ingestion health** (Settings + dashboard footer): per-provider last-success,
  staleness vs expected cadence, and daily/monthly budget usage rolled up to a
  green/amber/red status.

## Backups

The `backup` compose service takes a nightly **`pg_dump` at 04:00 local** (container
`TZ`, default `America/Mexico_City`) into the `backups` Docker volume, keeping **14 days**
of history. It runs the **same `timescale/timescaledb:2.28.2-pg16` image as the database**,
so pg_dump's version and TimescaleDB extension exactly match the server — a hard
requirement for a clean restore. Dumps are custom-format (`-Fc`) and written via a
`.tmp` + atomic-rename, so an interrupted dump never leaves a partial `plutus_*.dump`.

```sh
make backup-now     # take a dump right now (stack must be up)
make backup-list    # list dumps in the backups volume
```

**Restore.** TimescaleDB needs a pre/post-restore dance, and the restore must use the
same timescaledb image/version the dump was taken with. `make restore` verifies a dump
by restoring it into a throwaway `plutus_restore_check` database — the live `plutus`
database is never touched:

```sh
make restore FILE=plutus_20260706_0400.dump
```

Under the hood (and the manual procedure for a real in-place restore into `plutus`,
after stopping `app`/`worker`/`beat`/`quotes`):

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;   -- into the fresh, empty target db
SELECT timescaledb_pre_restore();
-- (shell) pg_restore -Fc -d <db> <file>
SELECT timescaledb_post_restore();
```

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
- Beat schedule (America/Mexico_City): EOD 03:00/03:10/03:20 · metrics 06:30 ·
  news every 15 min · fundamentals Sun 06:30 (stalest-first, capped at 32 assets/run to
  respect the FMP day budget — the ~100-stock universe rotates over ~3 weeks) ·
  discovery dispatcher every 5 min · alert digest 08:00 · AI research memos 08:15 ·
  maturity check 08:30. Schedule mandates after 06:30 so scans read fresh metrics.
- **The ~100-stock nightly ingestion paces at Tiingo's bucket (~80s/symbol, ≈2.3h)** —
  ingest tasks carry 4h Celery time limits, and the metrics beat runs after the window.
- Backtest guardrails: point-in-time panels only (no forward-fill of signals), next-bar
  fills, and a PIT-consistency test that pins panel math to the live snapshot engine.

Market data by Tiingo, Binance, Twelve Data, FMP, and Finnhub — crypto metadata powered by
CoinGecko. Personal use only, no redistribution.
