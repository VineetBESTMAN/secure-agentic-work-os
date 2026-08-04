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

database_url="$(read_secret "${DATABASE_URL_FILE:?}" "DATABASE_URL")"
bucket="${APP_OBJECT_STORAGE_BUCKET:?}"
prefix="${APP_OBJECT_STORAGE_PREFIX:-workos}/${APP_BACKUP_STATUS_PREFIX:-backups/postgres}"
prefix="${prefix#/}"
prefix="${prefix%/}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="workos-${timestamp}.dump"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
dump_path="$temporary/$backup_name"

aws_args=()
if [[ -n "${APP_OBJECT_STORAGE_ENDPOINT_URL:-}" ]]; then
  aws_args+=(--endpoint-url "$APP_OBJECT_STORAGE_ENDPOINT_URL")
fi

upload_args=()
if [[ "${APP_OBJECT_STORAGE_SSE:-AES256}" == "aws:kms" ]]; then
  upload_args+=(--sse aws:kms --sse-kms-key-id "${APP_OBJECT_STORAGE_KMS_KEY_ID:?}")
else
  upload_args+=(--sse AES256)
fi

pg_dump \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-acl \
  --file "$dump_path" \
  "$database_url"

(
  cd "$temporary"
  sha256sum "$backup_name" > "$backup_name.sha256"
)
printf '{"created_at":"%s","format":"pg_dump_custom","checksum":"sha256"}\n' \
  "$timestamp" > "$dump_path.json"

for path in "$dump_path" "$dump_path.sha256" "$dump_path.json"; do
  aws "${aws_args[@]}" s3 cp "$path" "s3://$bucket/$prefix/$(basename "$path")" \
    "${upload_args[@]}" --only-show-errors
done

aws "${aws_args[@]}" s3api head-object \
  --bucket "$bucket" \
  --key "$prefix/$backup_name" >/dev/null

echo "PostgreSQL backup uploaded and verified: $prefix/$backup_name"
