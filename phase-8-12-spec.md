# Plutus — Phases 8–12: remediation & morning brief

Spec sheet for the work identified by the 2026-07-10 full-project audit (4-track review:
backend correctness, security, frontend, ops/hygiene), plus one new feature: a single
consolidated **morning brief** notification. Follows the same phase/milestone/gate
structure as phases 1–7.

| Phase | Scope | Priority |
|---|---|---|
| 8 | Ledger correctness — Bitso sync, lot accounting, bank interest, CSV locale, FX | P0 — corrupts money data silently |
| 9 | Security & network hardening — port bindings, sidecar auth, infra credentials | P0 — LAN-exposed control plane |
| 10 | Reliability & ops — CI, restart policies, failure alerts, prod frontend, off-host backups | P1 |
| 11 | Frontend correctness & UX hardening — confirmation cards, error states, races, tests | P1 |
| 12 | Morning brief — one consolidated daily notification | P2 — feature |

Severity labels below come from the audit; every finding carries its file anchor.

---

## Phase 8 — Ledger correctness (P0)

Transactions are the source of truth; these bugs make the source of truth wrong.

### M1 — Bitso sync: never lose rows behind the cursor
**Bugs.** `app/exchanges/sync.py:207-221, 243-257` — pending fundings/withdrawals are
skipped but the cursor commits past them; when they complete they are unreachable
(`sort=asc, marker=` never returns them again). Same pattern for unknown-symbol trades
(`sync.py:184-186`): skipped, cursor advanced, unrecoverable.

**Spec.**
- Only advance `last_funding_id` / `last_withdrawal_id` past rows in a **terminal**
  status (`complete`, `cancelled`, `failed`). A `pending` row halts cursor advancement
  for that stream (later completed rows behind it are picked up next sync).
- Persist skipped non-terminal / unknown-symbol items in a `sync_skips` table
  (account, stream, external_id, raw payload, reason, resolved_at). Each sync retries
  unresolved skips first.
- Add `POST /api/v1/exchanges/accounts/{id}/resync?from=<date|beginning>` — cursor
  reset with full re-walk; `external_id` dedup already guarantees zero duplicates.
- Surface unresolved skips as a count on the Settings exchange card.

**Gate.** Integration test: funding pending on sync 1 → completes → appears after sync 2.
Unknown-symbol trade + asset tracked later + resync → trade lands in ledger.

### M2 — Bitso sync: fee currency
**Bug.** `sync.py:279-291` stores `fees` with `currency=quote`; Bitso charges buy-side
fees in the **major** (received) asset. Every synced buy overstates holdings by the fee,
pollutes the cash ledger with foreign units, and corrupts cost basis.

**Spec.**
- Record `fees_currency` from the API (`bitso.py:125-126` already fetches it).
- Fee in quote currency → keep current behavior. Fee in major → reduce received
  `quantity` by the fee (net-received model, cost basis unchanged in quote terms) and
  store the fee leg for audit. Document the model in the transaction `note`.
- One-time repair task for already-synced trades (recompute from raw Bitso data via M1
  resync).

**Gate.** Unit test per fee-currency case; integration test asserts net BTC quantity and
MXN cash effect on a recorded BTC/MXN buy fixture.

### M3 — Lot engine: duplicate `lot_links` oversell
**Bug.** `app/portfolio/lots.py:207-225` — per-link checks read `lot.remaining` before
any decrement, so `[{lot A, 10}, {lot A, 10}]` against a 10-unit lot passes strict
validation; remaining goes negative and realized P&L is overstated. Reachable via API
and agent write tools.

**Spec.** Aggregate links per lot id before validation (or decrement a working copy
inside the planning loop). Strict mode rejects any aggregate `> lot.remaining`.

**Gate.** Unit test: the exact duplicate-links payload is rejected with a 422; existing
specific-ID tests stay green.

### M4 — Bank investments: auto-renew must not rewrite history
**Bug.** `app/portfolio/maturities.py:52-62` + `interest.py:81-89` — rollover mutates
`principal`/`start_date` in place; `daily_value_series` returns 0 before the new
`start_date`, so charts show a retroactive cliff and TWR books the whole principal as
return.

**Spec.** Terms become append-only: rollover closes the current term (row with
`start/end/principal/rate`) and opens the next with capitalized principal.
`daily_value_series` walks terms so the series is continuous across renewals; interest
capitalization is *not* an external flow (it is return).

**Gate.** Unit test: value series across a renewal is continuous and monotone under
positive rates; TWR over a window spanning the rollover equals the accrued rate, not
principal/0.

### M5 — CSV import: Spanish locale
**Bugs.** `app/portfolio/csv_import.py:231` — `replace(",", "")` turns `1,5` into `15.0`
(silent 10×) and mangles `1.234,56`; `:256` — `pd.to_datetime` without `dayfirst`
misreads `02/03/2025`. The importer explicitly targets Spanish exports.

**Spec.** Add `number_format` (`1,234.56` | `1.234,56`) and `date_order`
(`auto|dayfirst|monthfirst`) to the import mapping; sniff a default from the sample and
show it in the wizard; Bitso preset pins the correct values. Ambiguous dates under
`auto` become per-row errors, not guesses.

**Gate.** Golden-file test with a decimal-comma, day-first fixture; `1,5` imports as 1.5.

### M6 — FX & valuation consistency
**Bugs.** Realized P&L converted at report-date FX (`valuation.py:107-112`); transfer
flows valued at `quantity × price` but sync writes `price=None` → fake account-level
TWR gains/losses (`valuation.py:290,529-531` + `sync.py` `_transfer_record`); FX rate
lookups silently fall back to 1.0 and accept arbitrarily old closes (`valuation.py:428`).

**Spec.**
- Realized P&L converts at the **sale-date** rate; report-date conversion only for
  unrealized.
- Transfers value at the valuation-date market price when carried cost is absent.
- `fx_rate` gets a staleness bound (`FX_MAX_STALE_DAYS`, default 7); beyond it the
  report returns an explicit warning entry instead of a silent 1.0 blend.

**Gate.** Unit tests for each; a report with a missing rate carries a machine-readable
warning, never a 1.0-converted total.

### M7 — Beat-layer concurrency discipline
**Bugs.** No lock/expiry anywhere: backlogged `evaluate_price_alerts` runs double-fire
alerts (`worker/celery_app.py:88` + `alerts/evaluate.py:69-98`); "Sync now" + nightly
sync interleave and break Bitso nonce monotonicity (`bitso.py:62-66`); maturity rollover
assumes one runner. Also `quote:last:<SYMBOL>` is keyed by bare symbol
(`quotes/publisher.py:19-27`) — ticker collisions can fire the wrong alert.

**Spec.**
- Redis lock helper (`SET NX EX`) wrapping the alert evaluator, per-account exchange
  sync, and the maturity task; `expires=` on every beat entry ≤ its cadence.
- Quote keys become `quote:last:<asset_class>:<symbol>`; alert evaluator matches on
  asset id, not symbol text.

**Gate.** Unit test: second concurrent evaluator invocation no-ops; quote key collision
test (stock + crypto same ticker) fires only the right alert.

---

## Phase 9 — Security & network hardening (P0)

Single-user self-hosted hub: nothing belongs on `0.0.0.0`.

### M1 — Bind published ports to loopback
`docker-compose.yml` publishes 8800, 5433, 6379, 8787 on all interfaces. Change every
mapping to `127.0.0.1:<host>:<container>`; drop the sidecar's 8787 publish from the
default file (only the native-dev override needs it — document a compose override file
for that case).
**Gate.** `docker compose ps` shows only `127.0.0.1` bindings; LAN scan finds nothing.

### M2 — Sidecar authentication
`sidecar/server.mjs` accepts any `POST /chat/stream` unauthenticated while holding
`CLAUDE_CODE_OAUTH_TOKEN` and the real `APP_AUTH_TOKEN` (`tools.mjs:57-64`) — full
read-tool blast radius plus subscription burn, bypassing the daily token budget
(`loop.py:98`), and write-tool reach via guessable autonomous conversation ids
(`agent.py:233-237`).
**Spec.** Shared secret `SIDECAR_SHARED_SECRET` (required, fail-closed at boot like
`APP_AUTH_TOKEN`): sidecar rejects requests without the matching
`Authorization: Bearer` header; `claude_sidecar.py:72-75` sends it. Add to
`.env.example` with generation instructions.
**Gate.** Unauthenticated `POST /chat/stream` → 401; sidecar refuses to start without
the secret; integration path (app → sidecar → tools) still green.

### M3 — Infra credentials
Redis has no password (it is the Celery broker — enqueue-anything on the LAN); Postgres
ships with the documented default `plutus` and no boot guard.
**Spec.** `requirepass ${REDIS_PASSWORD}` on the redis service and credentialed
`REDIS_URL` everywhere (app, worker, beat, quotes). App startup warns loudly (or
refuses, behind `ALLOW_DEFAULT_DB_PASSWORD=true` escape hatch) when
`POSTGRES_PASSWORD=plutus`.
**Gate.** `redis-cli ping` without auth fails; boot with default pg password prints the
warning.

### M4 — WebSocket token out of the URL
`ws_quotes.py:29-31` authenticates `?token=` — the single global credential lands in
proxy logs and browser history (frontend sends it from `ws.ts:44`).
**Spec.** `POST /api/v1/ws-ticket` (auth'd) mints a 30-second single-use ticket
(random, stored in Redis); `/ws/quotes?ticket=` consumes it. Frontend fetches a ticket
per (re)connect.
**Gate.** Old `?token=` path removed; expired/reused ticket → close code 4401; UI
reconnect obtains a fresh ticket.

---

## Phase 10 — Reliability & operations (P1)

### M1 — Restart policies & healthchecks
`db`, `redis`, `app`, `worker`, `beat`, `agent-sidecar` have no `restart:` — a reboot
silently kills ingestion, alerts, and backups. `worker`/`beat`/`quotes` have no
healthcheck (a hung worker looks healthy).
**Spec.** `restart: unless-stopped` on every service; healthchecks:
`celery inspect ping` (worker), schedule-file mtime probe (beat), Redis heartbeat key
freshness written by the streamer (quotes). Add default `logging` options (json-file,
`max-size: 10m`, `max-file: 3`) on all services.
**Gate.** `docker compose restart` + host-reboot simulation brings the full stack back
with no manual step.

### M2 — CI
No CI of any kind; `make guard-tasa` — the grep enforcing "no trade execution, ever" —
runs only by hand.
**Spec.** `.github/workflows/ci.yml`: backend job (`uv sync`, `make lint`, `make test`,
`make guard-tasa`, `alembic check` against a service TimescaleDB) + frontend job
(`npm ci`, `tsc --noEmit` — add a `typecheck` script, `oxlint`, `npm run build`) +
weekly `pip-audit` / `npm audit` job. Add Dependabot for pip, npm, and Docker images.
**Gate.** CI green on main; a PR reintroducing a `place_order`-shaped symbol fails
`guard-tasa` in CI.

### M3 — Failure notifications (prereq for Phase 12)
Structured logs exist, but nothing pushes: a failed nightly ingestion, dead beat, or
crashed streamer notifies no one despite working email/Telegram channels
(`discovery/notify.py`).
**Spec.**
- Celery `task_failure` signal handler → `deliver(kind="task_failure", ...)`, deduped
  per (task, day).
- Watchdog beat task (hourly): ingestion staleness beyond expected cadence
  (`health/aggregate.py` already computes verdicts), quotes heartbeat stale, last
  backup older than 26 h → `deliver(kind="watchdog", ...)`.
- Both respect a per-kind daily cap to avoid storms.
**Gate.** Kill the streamer → watchdog notification within an hour; failing ingest task
→ one `task_failure` notification, not one per retry.

### M4 — Off-host backups
Dumps land in a named volume on the same disk as `pgdata`.
**Spec.** Post-dump hook in `scripts/backup.sh`: `rclone copy` to a configurable remote
(`BACKUP_REMOTE`, optional — skipped with a log line when unset); nightly result feeds
M3's watchdog (success heartbeat + failure notification). Document `BACKUP_DB_HOST`
and add both to `.env.example`.
**Gate.** `make backup-now` with a remote configured lands the dump off-host; unset
remote logs a skip, not an error.

### M5 — Production frontend
`npm run build` output is served by nothing — the "self-hosted hub" UI exists only
while a Vite dev server runs.
**Spec.** Multi-stage `frontend/Dockerfile` (node build → nginx serving `dist/`,
proxying `/api`, `/health`, `/ws` to `app:8000`); new `web` compose service published
on `127.0.0.1:8443` (or 8080); `FRONTEND_ORIGIN` updated accordingly. Dev flow
(`npm run dev`) unchanged.
**Gate.** Fresh `docker compose up -d` serves the full UI with zero terminals open;
README quickstart updated.

### M6 — Env & docs hygiene
Add `CLAUDE_SIDECAR_URL_OVERRIDE` (the active native-sidecar knob) and `BACKUP_DB_HOST`
to `.env.example`; document the Redis-ephemeral tradeoff (queued jobs vanish on
restart) and the `redis<6.5` kombu pin re-check rule in README notes.

---

## Phase 11 — Frontend correctness & UX hardening (P1)

### M1 — Confirmation cards: idempotent, honest
`AgentChat.tsx:95-112, 368-381` — Approve/Reject have no in-flight guard or disabled
state (double-click = two write POSTs; approve-then-reject can race) and no try/catch
(failures leave the card actionable with unknown server state).
**Spec.** Disable both buttons on click; in-flight flag; catch → show error state on
the card with the server's resolution status re-fetched; backend rejects a second
resolution of the same confirmation id with 409 (verify — add if missing).
**Gate.** Double-click fires exactly one POST; simulated 500 shows an error card, not a
clickable one.

### M2 — Error states & global 401
Portfolio failure renders eternal "Loading…" (`Portfolio.tsx:71-73`); errors disguise as
empty states (`Research.tsx:59`, `HeatmapTreemap.tsx:105`, `SearchBox.tsx`); no
ErrorBoundary; no global 401 handling.
**Spec.** `client.ts` throws a typed `ApiError`; 401 sets a global "token invalid"
banner pointing at Settings; every page renders distinct loading / error-with-retry /
empty states; top-level `ErrorBoundary` per route in `App.tsx`.
**Gate.** Stop the API → every page shows an error state with retry; wrong token →
banner, not "No candles yet".

### M3 — Stale-fetch cancellation
Races on rapid navigation/toggles (`Research.tsx:38-72`, `Dashboard.tsx:18-24`,
`Portfolio.tsx:60-73`, `HeatmapTreemap.tsx:100-106`) — the `cancelled`-flag pattern in
`WatchlistPanel.tsx:12-37` applied everywhere (or `AbortController` per effect).
**Gate.** Fast `/asset/1 → /asset/2` navigation never renders asset 1's data; currency
toggle A→B→A always displays A's figures.

### M4 — Quotes transport & rendering efficiency
`ws.ts:69-74` reconnects forever with no close-code inspection (wrong token = hammering
every 1–15 s); Dashboard opens three sockets (`useQuotes` per panel);
`QuoteStream.setSymbols` is dead code — symbol changes tear down the socket; treemap
rebuilds fully per tick (`HeatmapTreemap.tsx:135-202`).
**Spec.** Single shared quote socket (context/provider) with reference-counted symbol
subscriptions using `setSymbols`; stop reconnecting on auth close codes (surface the
M2 banner); throttle treemap updates to 1 Hz with merged `setOption`. Also fix the
mid-stream conversation-switch spurious AbortError bubble (`AgentChat.tsx:30-36,84-88`)
and `tool_result` matching by name only (`AgentChat.tsx:263-274` — match by call id).
**Gate.** Dashboard shows one WS connection; auth-failed socket stops after N attempts;
CPU profile of a ticking dashboard shows no per-tick full treemap rebuild.

### M5 — Frontend test harness & typecheck
Zero tests, no typecheck script.
**Spec.** Vitest + `npm run test` + `npm run typecheck` (`tsc --noEmit`), both wired
into Phase 10 M2 CI. First targets (pure logic): SSE line parser (`sse.ts` — also fix
multi-line `data:` frame handling per spec while under test), `applyEvent`/
`itemsFromServer` (chat reducer), `parseServerErrors`, `tileColor`.
**Gate.** ≥ those five modules covered; CI runs them.

### M6 — Modal & a11y polish (low)
`shared.tsx:33-58` modal: `role="dialog"`, focus trap, Escape, and no
close-on-backdrop-click while the CSV wizard has unsaved mapping state. Keyboardable
conversation list rows; delete (×) gets a confirm.

---

## Phase 12 — Morning brief (feature, P2)

**Goal.** One notification per morning containing everything the hub currently says in
separate messages — instead of digest 08:00 + memos 08:15 + maturity 08:30 (+ the new
Phase 10 failure/watchdog kinds), a single consolidated brief.

### Design
- **New beat task** `send_morning_brief` at **08:45 America/Mexico_City** (after all
  daily producers have run; env `MORNING_BRIEF_TIME` if we want it movable).
- **Collection, not new plumbing.** Producers keep writing their artifacts (candidates,
  memo notes, maturity flags, `notifications` audit rows). The brief composes from the
  DB over the window *since the last successful brief* (`kind="morning_brief"`,
  `ok=true` — same look-back pattern as `send_digest`, `notify.py:173-223`).
- **Suppression switch.** `MORNING_BRIEF_ENABLED` (default `true`): when on, the
  daily-cadence senders (`send_digest`, memo notification, maturity reminders, daily
  watchdog rollups) write their content/audit rows with `channel="brief"` instead of
  delivering individually — the brief is the single delivery. Real-time kinds (price
  alerts, instant scan alerts, task failures) still send immediately **and** are
  recapped in the brief. When off, behavior is exactly today's.
- **Message sections** (any empty section is omitted):
  1. **Portfolio** — value, day P&L, YTD TWR (from the dashboard aggregates).
  2. **Discovery** — new candidates above threshold since last brief, grouped by
     mandate (reuses `candidate_line`).
  3. **AI research** — memos written overnight (title + asset + one-liner).
  4. **Maturities** — bank investments maturing within `MATURITY_REMINDER_DAYS`.
  5. **Price alerts** — alerts that fired in the window (recap; they already sent
     instantly) + currently armed count.
  6. **System** — ingestion health rollup (`health/aggregate.py` verdict), last backup
     age, any task failures in the window.
- **Delivery** via existing `deliver(session, kind="morning_brief", ...)` — email gets
  full sections, Telegram a compact variant (existing channel functions; both already
  audited in `notifications`).
- **Idempotent & catch-up.** Redis lock (Phase 8 M7 helper); if the host was down at
  08:45, the next beat catch-up run covers the full gap window since the last
  successful brief — nothing is lost, nothing repeats.
- **Settings UI**: toggle, time display, and a "Send test brief" button
  (`POST /api/v1/notifications/test-brief`), mirroring the existing test-alert flow.
- **Empty day**: configurable `MORNING_BRIEF_ON_QUIET` (`skip` | `send`) — default
  `send` a one-line "all quiet, systems green" so silence remains a signal of breakage,
  not of a quiet market.

### Gate
- With the flag on: exactly one outbound notification per morning across email +
  Telegram; digest/memo/maturity content verifiably inside it; `notifications` shows
  one `morning_brief` row and zero individual daily rows.
- With the flag off: byte-identical behavior to Phase 7.
- Downtime test: stack down over 08:45 → next morning's brief covers 48 h.

---

## Suggested execution order

8.M1–M3 (money bugs) → 9.M1–M2 (exposure) → 10.M1–M2 (restart + CI, so everything after
is regression-guarded) → remaining Phase 8 → Phase 9 M3–M4 → Phase 10 M3–M6 → Phase 11
→ Phase 12 (depends on 10.M3 watchdog kinds and 8.M7 lock helper).
