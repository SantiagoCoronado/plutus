"""Ingestion health (Phase 7 M5): rolls recent ingestion_runs and the Redis
provider budget counters into one green/amber/red summary for the Settings
page and the dashboard status footer. See app/health/aggregate.py."""
