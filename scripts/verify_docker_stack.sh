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

model_status="$(curl --fail --silent \
  -H "Authorization: Bearer ${token}" \
  http://127.0.0.1:8000/api/models/status)"
model_ready="$(python -c "import json,sys; value=json.load(sys.stdin); print(value['configured'] and value['grounded_answers_enabled'])" <<<"$model_status")"
if [[ "$model_ready" != "True" ]]; then
  echo "Model gateway status did not report the deterministic grounded-answer path." >&2
  exit 1
fi

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

smoke_document_id="$(python -c "import json,sys; print(json.load(sys.stdin)['result']['document_ids'][0])" <<<"$job_json")"
organizations_json="$(curl --fail --silent \
  -H "Authorization: Bearer ${token}" \
  http://127.0.0.1:8000/api/organizations)"
verification_organization_id="$(python -c "import json,sys; print(next((o['organization_id'] for o in json.load(sys.stdin) if o['slug']=='docker-verification'), ''))" <<<"$organizations_json")"
if [[ -z "$verification_organization_id" ]]; then
  verification_organization_id="$(curl --fail --silent \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${token}" \
    -d '{"name":"Docker Verification","slug":"docker-verification"}' \
    http://127.0.0.1:8000/api/organizations \
    | python -c "import json,sys; print(json.load(sys.stdin)['organization_id'])")"
fi
tenant_session="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d "{\"organization_id\":\"${verification_organization_id}\"}" \
  http://127.0.0.1:8000/api/auth/switch-organization)"
tenant_token="$(python -c "import json,sys; print(json.load(sys.stdin)['access_token'])" <<<"$tenant_session")"
tenant_refresh_token="$(python -c "import json,sys; print(json.load(sys.stdin)['refresh_token'])" <<<"$tenant_session")"
tenant_documents="$(curl --fail --silent \
  -H "Authorization: Bearer ${tenant_token}" \
  http://127.0.0.1:8000/api/documents/library)"
tenant_contains_smoke="$(python -c "import json,sys; expected=sys.argv[1]; print(any(d['document_id']==expected for d in json.load(sys.stdin)))" "$smoke_document_id" <<<"$tenant_documents")"
if [[ "$tenant_contains_smoke" == "True" ]]; then
  echo "A default-organization document crossed the Docker tenant boundary." >&2
  exit 1
fi
tenant_policy_count="$(curl --fail --silent \
  -H "Authorization: Bearer ${tenant_token}" \
  http://127.0.0.1:8000/api/policies \
  | python -c "import json,sys; print(len(json.load(sys.stdin)))")"
if [[ "$tenant_policy_count" -lt 3 ]]; then
  echo "The Docker verification organization was not seeded with governance policies." >&2
  exit 1
fi
curl --fail --silent \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"${tenant_refresh_token}\"}" \
  http://127.0.0.1:8000/api/auth/refresh >/dev/null
replay_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"${tenant_refresh_token}\"}" \
  http://127.0.0.1:8000/api/auth/refresh)"
if [[ "$replay_status" != "401" ]]; then
  echo "A used refresh token was accepted a second time (HTTP ${replay_status})." >&2
  exit 1
fi

query="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"question":"What approval is required for the Acme renewal?"}' \
  http://127.0.0.1:8000/api/documents/query)"
citations_count="$(python -c "import json,sys; print(len(json.load(sys.stdin)['citations']))" <<<"$query")"
grounded_answer="$(python -c "import json,re,sys; value=json.load(sys.stdin); print(value['grounded'] and bool(re.search(r'\[\d+\]', value['answer'])))" <<<"$query")"

if [[ "$citations_count" -lt 1 || "$grounded_answer" != "True" ]]; then
  echo "Grounded RAG smoke test did not return a citation-marked answer." >&2
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
  -d '{"tool_name":"send_email","arguments":{"to":"client@example.com","subject":"Docker Security MCP verification","body":"This verification message must never be sent."}}' \
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
  -d '{"approved":false}' \
  "http://127.0.0.1:8000/api/approvals/${approval_id}/decision" >/dev/null

approved_execution="$(curl --fail --silent \
  -H "Authorization: Bearer ${manager_token}" \
  "http://127.0.0.1:8000/api/mcp/executions/${execution_id}")"
approved_status="$(python -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$approved_execution")"
delivery_mode="$(python -c "import json,sys; print(json.load(sys.stdin)['result'].get('delivery_mode',''))" <<<"$approved_execution")"
if [[ "$approved_status" != "rejected" || -n "$delivery_mode" ]]; then
  echo "Security MCP rejection did not prevent the real provider email action." >&2
  exit 1
fi

workflow="$(curl --fail --silent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${token}" \
  -d '{"prompt":"Find the Acme renewal policy and create a verification task"}' \
  http://127.0.0.1:8000/api/agent/workflows)"
workflow_id="$(python -c "import json,sys; print(json.load(sys.stdin)['workflow_id'])" <<<"$workflow")"
workflow_status="$(python -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$workflow")"
workflow_validated="$(python -c "import json,sys; print(json.load(sys.stdin)['plan']['validated'])" <<<"$workflow")"
workflow_email_count="$(python -c "import json,sys; print(sum(a['tool_name'] == 'send_email' for a in json.load(sys.stdin)['actions']))" <<<"$workflow")"
if [[ "$workflow_status" != "completed" || "$workflow_validated" != "True" || "$workflow_email_count" -ne 0 ]]; then
  echo "Agent workflow did not complete its provider-free safe actions." >&2
  exit 1
fi

echo "Backend API, tenant isolation, session rotation, Postgres, Redis worker, RAG, governed provider actions, and workflow smoke tests passed."
wait_http "http://127.0.0.1:5173" "Frontend preview"
echo "Docker stack verification passed."
echo "Open http://127.0.0.1:5173 and sign in with admin@demo.local / demo-password."
echo "Services are still running. Use 'docker compose down' when you want to stop them."
