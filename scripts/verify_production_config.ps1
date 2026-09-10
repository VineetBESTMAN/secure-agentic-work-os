param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("workos-production-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null

try {
    $files = @{
        "app-secret-key" = "production-jwt-secret-at-least-thirty-two-characters"
        "encryption-keyring.json" = '{"primary":"production-encryption-key-at-least-thirty-two-characters"}'
        "database-url" = "postgresql://workos:password@managed-postgres:5432/workos?sslmode=require"
        "redis-url" = "rediss://:password@managed-redis:6380/0"
        "metrics-token" = "metrics-token-at-least-24-characters"
        "restore-admin-url" = "postgresql://admin:password@managed-postgres:5432/postgres?sslmode=require"
        "restore-database-url" = "postgresql://admin:password@managed-postgres:5432/workos_restore_verify?sslmode=require"
    }
    foreach ($entry in $files.GetEnumerator()) {
        [System.IO.File]::WriteAllText((Join-Path $temporary $entry.Key), $entry.Value)
    }

    $env:APP_DOMAIN = "workos.example.com"
    $env:ACME_EMAIL = "operations@example.com"
    $env:APP_ACTIVE_ENCRYPTION_KEY_ID = "primary"
    $env:APP_OBJECT_STORAGE_BUCKET = "workos-production"
    $env:APP_SECRET_KEY_FILE_HOST = Join-Path $temporary "app-secret-key"
    $env:APP_ENCRYPTION_KEYRING_FILE_HOST = Join-Path $temporary "encryption-keyring.json"
    $env:DATABASE_URL_FILE_HOST = Join-Path $temporary "database-url"
    $env:REDIS_URL_FILE_HOST = Join-Path $temporary "redis-url"
    $env:METRICS_TOKEN_FILE_HOST = Join-Path $temporary "metrics-token"
    $env:RESTORE_VERIFY_ADMIN_URL_FILE_HOST = Join-Path $temporary "restore-admin-url"
    $env:RESTORE_VERIFY_DATABASE_URL_FILE_HOST = Join-Path $temporary "restore-database-url"

    docker compose -f (Join-Path $root "docker-compose.production.yml") --profile restore config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Production Compose validation failed." }

    docker run --rm --entrypoint promtool -v "${root}/ops/monitoring:/etc/prometheus:ro" -v "${temporary}/metrics-token:/run/secrets/metrics_token:ro" prom/prometheus:v3.5.0 check config /etc/prometheus/prometheus.yml
    if ($LASTEXITCODE -ne 0) { throw "Prometheus configuration validation failed." }
    docker run --rm --entrypoint promtool -v "${root}/ops/monitoring:/etc/prometheus:ro" prom/prometheus:v3.5.0 check rules /etc/prometheus/alerts.yml
    if ($LASTEXITCODE -ne 0) { throw "Prometheus rules validation failed." }
    docker run --rm --entrypoint amtool -v "${root}/ops/monitoring/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" prom/alertmanager:v0.28.1 check-config /etc/alertmanager/alertmanager.yml
    if ($LASTEXITCODE -ne 0) { throw "Alertmanager configuration validation failed." }
    docker run --rm --entrypoint caddy -e APP_DOMAIN=$env:APP_DOMAIN -e ACME_EMAIL=$env:ACME_EMAIL -v "${root}/ops/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.4-alpine validate --config /etc/caddy/Caddyfile
    if ($LASTEXITCODE -ne 0) { throw "Caddy configuration validation failed." }

    Write-Output "Production Compose, Prometheus, Alertmanager, and Caddy configuration passed."
}
finally {
    $resolvedTemporary = [System.IO.Path]::GetFullPath($temporary)
    $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedTemporary.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not (Split-Path -Leaf $resolvedTemporary).StartsWith('workos-production-')) {
        throw 'Refusing cleanup outside the verification temporary directory.'
    }
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
