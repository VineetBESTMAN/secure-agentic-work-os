#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.reliability.yml"
PROJECT_NAME="${RELIABILITY_PROJECT_NAME:-secure-work-os-reliability-${GITHUB_RUN_ID:-local-$$}}"

if [[ ! "$PROJECT_NAME" =~ ^secure-work-os-reliability-[a-zA-Z0-9_-]+$ ]]; then
  echo "Refusing to run: RELIABILITY_PROJECT_NAME must start with secure-work-os-reliability-." >&2
  exit 2
fi

DUMP_FILE="$(mktemp "${TMPDIR:-/tmp}/workos-reliability-XXXXXX.sql")"
cleanup() {
  rm -f "$DUMP_FILE"
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

compose() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

compose up -d --wait postgres-reliability redis-reliability

compose exec -T postgres-reliability psql \
  -v ON_ERROR_STOP=1 -U reliability -d workos_reliability <<'SQL'
CREATE TABLE recovery_evidence (
  evidence_id integer PRIMARY KEY,
  checksum text NOT NULL
);
INSERT INTO recovery_evidence
SELECT value, md5(value::text)
FROM generate_series(1, 100) AS value;
SQL

compose exec -T postgres-reliability pg_dump \
  -U reliability -d workos_reliability --no-owner --no-privileges > "$DUMP_FILE"
test -s "$DUMP_FILE"

# Recreate only this guarded project's tmpfs-backed database container.
compose stop postgres-reliability
compose rm -f postgres-reliability
compose up -d --wait postgres-reliability
compose exec -T postgres-reliability psql \
  -v ON_ERROR_STOP=1 -U reliability -d workos_reliability < "$DUMP_FILE"

RESTORED_COUNT="$(
  compose exec -T postgres-reliability psql \
    -At -U reliability -d workos_reliability \
    -c "SELECT COUNT(*) FROM recovery_evidence WHERE checksum = md5(evidence_id::text);" \
    | tr -d '\r[:space:]'
)"
if [[ "$RESTORED_COUNT" != "100" ]]; then
  echo "Recovery verification failed: expected 100 rows, found $RESTORED_COUNT." >&2
  exit 1
fi

if [[ "$(compose exec -T redis-reliability redis-cli ping | tr -d '\r[:space:]')" != "PONG" ]]; then
  echo "Redis reliability dependency did not recover." >&2
  exit 1
fi

echo "Isolated PostgreSQL restore verified with 100 checksummed rows."
