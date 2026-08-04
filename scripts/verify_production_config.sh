#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

printf '%s' 'production-jwt-secret-at-least-thirty-two-characters' > "$temporary/app-secret-key"
printf '%s' '{"primary":"production-encryption-key-at-least-thirty-two-characters"}' > "$temporary/encryption-keyring.json"
printf '%s' 'postgresql://workos:password@managed-postgres:5432/workos?sslmode=require' > "$temporary/database-url"
printf '%s' 'rediss://:password@managed-redis:6380/0' > "$temporary/redis-url"
printf '%s' 'metrics-token-at-least-24-characters' > "$temporary/metrics-token"
printf '%s' 'postgresql://admin:password@managed-postgres:5432/postgres?sslmode=require' > "$temporary/restore-admin-url"
printf '%s' 'postgresql://admin:password@managed-postgres:5432/workos_restore_verify?sslmode=require' > "$temporary/restore-database-url"

export APP_DOMAIN="workos.example.com"
export ACME_EMAIL="operations@example.com"
export APP_ACTIVE_ENCRYPTION_KEY_ID="primary"
export APP_OBJECT_STORAGE_BUCKET="workos-production"
export APP_SECRET_KEY_FILE_HOST="$temporary/app-secret-key"
export APP_ENCRYPTION_KEYRING_FILE_HOST="$temporary/encryption-keyring.json"
export DATABASE_URL_FILE_HOST="$temporary/database-url"
export REDIS_URL_FILE_HOST="$temporary/redis-url"
export METRICS_TOKEN_FILE_HOST="$temporary/metrics-token"
export RESTORE_VERIFY_ADMIN_URL_FILE_HOST="$temporary/restore-admin-url"
export RESTORE_VERIFY_DATABASE_URL_FILE_HOST="$temporary/restore-database-url"

docker compose -f "$root/docker-compose.production.yml" --profile restore config --quiet
docker run --rm \
  --entrypoint promtool \
  -v "$root/ops/monitoring:/etc/prometheus:ro" \
  -v "$temporary/metrics-token:/run/secrets/metrics_token:ro" \
  prom/prometheus:v3.5.0 \
  check config /etc/prometheus/prometheus.yml
docker run --rm \
  --entrypoint promtool \
  -v "$root/ops/monitoring:/etc/prometheus:ro" \
  prom/prometheus:v3.5.0 \
  check rules /etc/prometheus/alerts.yml
docker run --rm \
  --entrypoint amtool \
  -v "$root/ops/monitoring/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
  prom/alertmanager:v0.28.1 \
  check-config /etc/alertmanager/alertmanager.yml
docker run --rm \
  --entrypoint caddy \
  -e APP_DOMAIN="$APP_DOMAIN" \
  -e ACME_EMAIL="$ACME_EMAIL" \
  -v "$root/ops/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.11.4-alpine \
  validate --config /etc/caddy/Caddyfile

echo "Production Compose, Prometheus, Alertmanager, and Caddy configuration passed."
