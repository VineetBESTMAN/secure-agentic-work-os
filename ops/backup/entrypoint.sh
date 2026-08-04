#!/usr/bin/env bash
set -euo pipefail

interval="${BACKUP_INTERVAL_SECONDS:-86400}"
if ! [[ "$interval" =~ ^[0-9]+$ ]] || (( interval < 300 )); then
  echo "BACKUP_INTERVAL_SECONDS must be an integer of at least 300." >&2
  exit 2
fi

while true; do
  if ! /opt/workos/backup.sh; then
    echo "PostgreSQL backup failed; the scheduler will retry after ${interval} seconds." >&2
  fi
  sleep "$interval"
done
