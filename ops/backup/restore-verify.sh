#!/usr/bin/env bash
set -euo pipefail

read_secret() {
  local path="$1"
  local label="$2"
  if [[ ! -s "$path" ]]; then
    echo "$label secret file is missing or empty." >&2
    exit 2
  fi
  tr -d '\r\n' < "$path"
}

admin_url="$(read_secret "${RESTORE_VERIFY_ADMIN_URL_FILE:?}" "restore admin URL")"
restore_url="$(read_secret "${RESTORE_VERIFY_DATABASE_URL_FILE:?}" "restore database URL")"
database_name="${RESTORE_VERIFY_DATABASE_NAME:-workos_restore_verify}"
if ! [[ "$database_name" =~ ^[a-zA-Z][a-zA-Z0-9_]{1,47}_restore_verify$ ]]; then
  echo "RESTORE_VERIFY_DATABASE_NAME must be a dedicated *_restore_verify database." >&2
  exit 2
fi

restore_target="${restore_url%%\?*}"
restore_target="${restore_target##*/}"
admin_target="${admin_url%%\?*}"
admin_target="${admin_target##*/}"
if [[ "$restore_target" != "$database_name" ]]; then
  echo "The restore URL must target RESTORE_VERIFY_DATABASE_NAME exactly." >&2
  exit 2
fi
if [[ "$admin_target" == "$database_name" ]]; then
  echo "The restore admin URL must connect to a different administration database." >&2
  exit 2
fi

bucket="${APP_OBJECT_STORAGE_BUCKET:?}"
prefix="${APP_OBJECT_STORAGE_PREFIX:-workos}/${APP_BACKUP_STATUS_PREFIX:-backups/postgres}"
prefix="${prefix#/}"
prefix="${prefix%/}"
temporary="$(mktemp -d)"
trap 'psql "$admin_url" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$database_name\" WITH (FORCE);" >/dev/null 2>&1 || true; rm -rf "$temporary"' EXIT

aws_args=()
if [[ -n "${APP_OBJECT_STORAGE_ENDPOINT_URL:-}" ]]; then
  aws_args+=(--endpoint-url "$APP_OBJECT_STORAGE_ENDPOINT_URL")
fi

latest_key="$(
  aws "${aws_args[@]}" s3 ls "s3://$bucket/$prefix/" --recursive |
    awk '$4 ~ /\.dump$/ {print $1 " " $2 " " $4}' |
    sort |
    tail -n 1 |
    awk '{print $3}'
)"
if [[ -z "$latest_key" ]]; then
  echo "No PostgreSQL backup dump is available for restore verification." >&2
  exit 3
fi

dump_path="$temporary/$(basename "$latest_key")"
aws "${aws_args[@]}" s3 cp "s3://$bucket/$latest_key" "$dump_path" --only-show-errors
aws "${aws_args[@]}" s3 cp "s3://$bucket/$latest_key.sha256" "$dump_path.sha256" --only-show-errors
(
  cd "$temporary"
  sha256sum -c "$(basename "$dump_path").sha256"
)

psql "$admin_url" -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"$database_name\" WITH (FORCE);"
psql "$admin_url" -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE \"$database_name\";"
pg_restore --exit-on-error --no-owner --no-acl --dbname "$restore_url" "$dump_path"

restored_name="$(psql "$restore_url" -v ON_ERROR_STOP=1 -Atc 'SELECT current_database();')"
if [[ "$restored_name" != "$database_name" ]]; then
  echo "Restore target mismatch; refusing to validate an unexpected database." >&2
  exit 4
fi
schema_revision="$(psql "$restore_url" -v ON_ERROR_STOP=1 -Atc 'SELECT version_num FROM alembic_version;')"
document_count="$(psql "$restore_url" -v ON_ERROR_STOP=1 -Atc 'SELECT COUNT(*) FROM documents;')"

echo "Restore verification passed for $latest_key"
echo "Schema revision: $schema_revision; documents: $document_count"
