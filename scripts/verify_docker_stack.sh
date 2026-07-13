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

migration_revision="$(docker compose exec -T backend python -m alembic current --check-heads)"
echo "Database migration head is active: ${migration_revision}"

token="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@demo.local","password":"demo-password"}' \
  http://127.0.0.1:8000/api/auth/login \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")"

queued_job_id="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"provider":"google","items":[{"filename":"docker-smoke-note.txt","content":"Docker smoke note: Acme renewal requires manager approval before any external contract summary is sent.","mime_type":"text/plain","classification":"internal","owner_team":"platform"}]}' \
  http://127.0.0.1:8000/api/connectors/import/async \
  | python -c "import json,sys; print(json.load(sys.stdin)['job']['job_id'])")"

import_status="queued"
for _ in $(seq 1 120); do
  job_json="$(curl --fail --silent \
    -H "Authorization: Bearer ${token}" \
    "http://127.0.0.1:8000/api/jobs/${queued_job_id}")"
  import_status="$(python -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$job_json")"
  if [[ "$import_status" == "completed" || "$import_status" == "failed" ]]; then
    break
  fi
  sleep 1
done

if [[ "$import_status" != "completed" ]]; then
  echo "Async connector import smoke test did not complete successfully: ${import_status}" >&2
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

task_execution="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"tool_name":"create_task","arguments":{"title":"Verify Docker Security MCP","description":"Created by the repeatable stack verification."}}' \
  http://127.0.0.1:8000/api/mcp/executions)"
task_status="$(python -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$task_execution")"
task_id="$(python -c "import json,sys; print(json.load(sys.stdin)['result'].get('task_id',''))" <<<"$task_execution")"
if [[ "$task_status" != "completed" || -z "$task_id" ]]; then
  echo "Security MCP task execution did not persist successfully." >&2
  exit 1
fi

email_execution="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"tool_name":"send_email","arguments":{"to":"client@example.com","subject":"Docker Security MCP verification","body":"This approved message must remain simulated."}}' \
  http://127.0.0.1:8000/api/mcp/executions)"
email_status="$(python -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$email_execution")"
approval_id="$(python -c "import json,sys; print(json.load(sys.stdin).get('approval_id',''))" <<<"$email_execution")"
execution_id="$(python -c "import json,sys; print(json.load(sys.stdin)['execution_id'])" <<<"$email_execution")"
if [[ "$email_status" != "pending_approval" || -z "$approval_id" ]]; then
  echo "Security MCP email execution did not enter approval state." >&2
  exit 1
fi

manager_token="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -d '{"email":"manager@demo.local","password":"demo-password"}' \
  http://127.0.0.1:8000/api/auth/login \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")"
curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${manager_token}" \
  -d '{"approved":true}' \
  "http://127.0.0.1:8000/api/approvals/${approval_id}/decision" >/dev/null

approved_execution="$(curl --fail --silent \
  -H "Authorization: Bearer ${manager_token}" \
  "http://127.0.0.1:8000/api/mcp/executions/${execution_id}")"
approved_status="$(python -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$approved_execution")"
delivery_mode="$(python -c "import json,sys; print(json.load(sys.stdin)['result'].get('delivery_mode',''))" <<<"$approved_execution")"
if [[ "$approved_status" != "completed" || "$delivery_mode" != "simulated" ]]; then
  echo "Security MCP approval did not resume the exact simulated email execution." >&2
  exit 1
fi

echo "Backend API, Postgres, Redis worker, RAG, and Security MCP smoke tests passed."
wait_http "http://127.0.0.1:5173" "Frontend preview"
echo "Docker stack verification passed."
echo "Open http://127.0.0.1:5173 and sign in with admin@demo.local / demo-password."
echo "Services are still running. Use 'docker compose down' when you want to stop them."
