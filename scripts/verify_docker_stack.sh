#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker was not found. Install Docker Desktop, start it, then rerun this script." >&2
  exit 1
fi

wait_http() {
  local url="$1"
  local name="$2"
  local timeout="${3:-120}"
  local end=$((SECONDS + timeout))

  until curl --fail --silent --show-error "$url" >/dev/null 2>&1; do
    if (( SECONDS >= end )); then
      echo "$name did not become reachable at $url within $timeout seconds." >&2
      exit 1
    fi
    sleep 2
  done

  echo "$name is reachable at $url"
}

echo "Building and starting Docker Compose services..."
docker compose up --build -d

wait_http "http://127.0.0.1:8000/health" "Backend health"

token="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.local","password":"demo-password"}' \
  http://127.0.0.1:8000/api/auth/login \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")"

import_status="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"provider":"google","items":[{"filename":"docker-smoke-note.txt","content":"Docker smoke note: Acme renewal requires manager approval before any external contract summary is sent.","mime_type":"text/plain","classification":"internal","owner_team":"platform"}]}' \
  http://127.0.0.1:8000/api/connectors/import \
  | python -c "import json,sys; print(json.load(sys.stdin)['job']['status'])")"

if [[ "$import_status" != "completed" ]]; then
  echo "Connector import smoke test did not complete successfully." >&2
  exit 1
fi

citations_count="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"question":"What approval is required for the Acme renewal?"}' \
  http://127.0.0.1:8000/api/documents/query \
  | python -c "import json,sys; print(len(json.load(sys.stdin)['citations']))")"

if [[ "$citations_count" -lt 1 ]]; then
  echo "RAG smoke test did not return a citation." >&2
  exit 1
fi

echo "Backend API, Postgres persistence, connector import, and RAG smoke tests passed."
wait_http "http://127.0.0.1:5173" "Frontend preview"
echo "Docker stack verification passed."
echo "Open http://127.0.0.1:5173 and sign in with admin@demo.local / demo-password."
echo "Services are still running. Use 'docker compose down' when you want to stop them."
