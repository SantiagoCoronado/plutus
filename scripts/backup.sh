#!/bin/sh
# Plutus nightly database backup (Phase 7 M7).
#
# Runs inside a container built from the SAME image as the `db` service
# (timescale/timescaledb:2.28.2-pg16) so pg_dump's version and TimescaleDB
# awareness match the server exactly. A custom-format (-Fc) dump taken this way
# restores cleanly with the timescaledb_pre_restore()/post_restore() dance
# documented in the README "Backups" section.
#
# Dependency-free / BusyBox-safe: only date, sleep, find, mv, du, cut, pg_dump —
# all present in the alpine-based timescaledb image (no `date -d`, no GNU-isms).
#
# Modes:
#   backup.sh          loop forever: sleep until the next 04:00 local, dump, prune
#   backup.sh --now    take one dump + prune, then exit (used by `make backup-now`)

set -eu

BACKUP_DIR=/backups
DB_HOST="${BACKUP_DB_HOST:-db}"
DB_USER="${POSTGRES_USER:-plutus}"
DB_NAME="${POSTGRES_DB:-plutus}"
BACKUP_HOUR=4          # dump at 04:00 in the container's local time (TZ from env)

log() {
  # timestamp in the container's local time; TZ is inherited from the environment
  echo "[backup $(date '+%Y-%m-%d %H:%M:%S %Z')] $*"
}

# Seconds until the next BACKUP_HOUR:00 in local time. Pure integer arithmetic on
# the current wall clock — BusyBox `date` has no `-d`/relative parsing. `${x#0}`
# strips a single leading zero so "08"/"09" don't get read as invalid octal.
seconds_until_backup() {
  h=$(date +%H); m=$(date +%M); s=$(date +%S)
  now=$(( ${h#0} * 3600 + ${m#0} * 60 + ${s#0} ))
  target=$(( BACKUP_HOUR * 3600 ))
  delta=$(( target - now ))
  if [ "$delta" -le 0 ]; then
    delta=$(( delta + 86400 ))
  fi
  echo "$delta"
}

run_dump() {
  ts=$(date +%Y%m%d_%H%M)
  final="$BACKUP_DIR/plutus_${ts}.dump"
  tmp="${final}.tmp"
  # .tmp + atomic rename: a dump killed mid-write leaves only a *.tmp file
  # (swept by prune, ignored by restore), never a plausible-looking partial
  # plutus_*.dump that a restore might trust.
  rm -f "$tmp"
  log "dumping ${DB_NAME}@${DB_HOST} -> ${final}"
  if PGPASSWORD="${PGPASSWORD:-}" pg_dump -Fc -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" > "$tmp"; then
    mv "$tmp" "$final"
    size=$(du -h "$final" | cut -f1)
    log "ok: ${final} (${size})"
  else
    rc=$?
    rm -f "$tmp"
    log "FAILED: pg_dump exited ${rc}; no file written"
    return "$rc"
  fi
}

prune() {
  # retention: drop finished dumps older than 14 days; also sweep stale *.tmp
  # partials left by an interrupted dump.
  find "$BACKUP_DIR" -name 'plutus_*.dump' -type f -mtime +14 -delete 2>/dev/null || true
  find "$BACKUP_DIR" -name 'plutus_*.dump.tmp' -type f -mtime +1 -delete 2>/dev/null || true
  log "pruned dumps older than 14 days"
}

mkdir -p "$BACKUP_DIR"

case "${1:-}" in
  --now)
    log "one-shot backup"
    run_dump
    prune
    log "one-shot done"
    ;;
  "")
    log "backup service started; nightly dump at 0${BACKUP_HOUR}:00 local (TZ=${TZ:-unset})"
    while true; do
      secs=$(seconds_until_backup)
      log "sleeping ${secs}s until next 0${BACKUP_HOUR}:00"
      sleep "$secs"
      run_dump || log "dump failed; retrying at the next scheduled time"
      prune
    done
    ;;
  *)
    echo "usage: backup.sh [--now]" >&2
    exit 2
    ;;
esac
