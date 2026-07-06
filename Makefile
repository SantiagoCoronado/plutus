.PHONY: infra infra-down api worker beat quotes seed ingest test test-integration lint up down logs guard-tasa backup-now backup-list restore

RESTORE_DB := plutus_restore_check

GUARD_DIRS := backend/app backend/worker frontend/src sidecar scripts

# --- Native dev (fast iteration): db+redis in Docker, backend/frontend on host ---
infra:            ## start db + redis only
	docker compose up -d db redis

infra-down:
	docker compose stop db redis

api:              ## run FastAPI natively with reload
	cd backend && uv run uvicorn app.main:app --reload --port 8800

worker:           ## run Celery worker natively
	cd backend && uv run celery -A worker.celery_app worker -l info --concurrency=2

beat:             ## run Celery beat natively
	cd backend && uv run celery -A worker.celery_app beat -l info --schedule /tmp/plutus-celerybeat-schedule

quotes:           ## run the live-quote streamer natively
	cd backend && uv run python -m app.quotes.streamer

migrate:          ## apply migrations
	cd backend && uv run alembic upgrade head

seed:             ## upsert AAPL, BTC, EURUSD
	cd backend && uv run python -m app.ingestion.seed

ingest:           ## seed + run EOD ingestion inline (no worker needed)
	cd backend && uv run python -m app.ingestion.seed --ingest

test:             ## unit tests (no services needed)
	cd backend && uv run pytest

test-integration: ## integration tests (needs `make infra` first)
	cd backend && uv run pytest -m integration

lint:
	cd backend && uv run ruff check .

# read-only guarantee: the forbidden trading string must appear nowhere in source
# (-I skips binaries; node_modules holds vendored deps we don't own)
guard-tasa:       ## fail if the forbidden string leaks into source
	@dirs="$(foreach d,$(GUARD_DIRS),$(wildcard $(d)))"; \
	if grep -riI --exclude-dir=node_modules tasa $$dirs >/dev/null 2>&1; then \
		echo "guard-tasa: forbidden string found:"; \
		grep -rniI --exclude-dir=node_modules tasa $$dirs; exit 1; \
	else \
		echo "guard-tasa: clean"; \
	fi

# --- Full stack ---
up:               ## build + start the full compose stack
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

# --- Backups (Phase 7) ---
# Nightly 04:00 dumps land in the `backups` volume; retention is 14 days. See the
# README "Backups" section for the restore procedure and the TimescaleDB dance.

# One-shot dump on demand. We `exec` into the already-running backup container
# rather than `run` a fresh one: it's unambiguous (no entrypoint/CMD arg juggling),
# writes to the very same /backups volume the nightly loop uses, and doesn't spin
# up a throwaway container or restart dependencies. Requires the stack to be up
# (`make up`); the sleeping nightly loop is untouched.
backup-now:       ## take a database dump right now (needs the stack up)
	docker compose exec backup /bin/sh /backup.sh --now

# List existing dumps. A one-off container is the only way to see inside the
# `backups` named volume from the host; --no-deps skips starting db just to `ls`.
backup-list:      ## list dumps in the backups volume
	docker compose run --rm --no-deps --entrypoint /bin/sh backup \
		-c 'ls -lh /backups/plutus_*.dump 2>/dev/null || echo "no backups yet"'

# Verify a dump restores. Non-destructive: restores into a NEW scratch database
# ($(RESTORE_DB)); the live `plutus` db is never touched. Runs the full TimescaleDB
# restore dance (pre_restore -> pg_restore -> post_restore) from a one-off backup
# container, which alone mounts /backups and reaches `db` over the compose network.
restore:          ## verify a dump restores into a scratch db: make restore FILE=plutus_YYYYMMDD_HHMM.dump
	@test -n "$(FILE)" || { echo "usage: make restore FILE=plutus_YYYYMMDD_HHMM.dump   (list with: make backup-list)"; exit 1; }
	@echo ">> Verifying /backups/$(FILE) by restoring into scratch db '$(RESTORE_DB)'. Live 'plutus' is NOT touched."
	docker compose run --rm --no-deps --entrypoint /bin/sh backup -c '\
		set -e; \
		export PGPASSWORD="$${POSTGRES_PASSWORD:-plutus}"; \
		test -f /backups/$(FILE) || { echo "no such dump: /backups/$(FILE)"; exit 1; }; \
		dropdb  -h db -U plutus --if-exists $(RESTORE_DB); \
		createdb -h db -U plutus $(RESTORE_DB); \
		psql -h db -U plutus -d $(RESTORE_DB) -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"; \
		psql -h db -U plutus -d $(RESTORE_DB) -v ON_ERROR_STOP=1 -c "SELECT timescaledb_pre_restore();"; \
		pg_restore -Fc -h db -U plutus -d $(RESTORE_DB) --no-owner /backups/$(FILE); \
		psql -h db -U plutus -d $(RESTORE_DB) -v ON_ERROR_STOP=1 -c "SELECT timescaledb_post_restore();"; \
		echo "restore OK -> $(RESTORE_DB)"'
	@echo ">> Scratch db '$(RESTORE_DB)' now holds the restored data. Inspect it with:"
	@echo "     docker compose exec db psql -U plutus -d $(RESTORE_DB) -c '\\dt'"
	@echo ">> Drop the scratch db when done:"
	@echo "     docker compose exec db psql -U plutus -d plutus -c 'DROP DATABASE $(RESTORE_DB);'"
	@echo ">> REAL in-place restore into live 'plutus' (DESTRUCTIVE, not automated): stop app+worker+beat+quotes,"
	@echo "   then run the same pre_restore -> pg_restore -> post_restore steps against -d plutus with this same image/version."
