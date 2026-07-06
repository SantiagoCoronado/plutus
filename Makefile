.PHONY: infra infra-down api worker beat seed ingest test test-integration lint up down logs guard-tasa

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
