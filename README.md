# Secure Agentic AI Work OS

Secure Agentic AI Work OS is a working reference implementation of an enterprise AI copilot. It combines document ingestion and retrieval, approval-gated agent workflows, role-based access, auditable tool execution, background jobs, and a standards-compatible MCP security gateway.

The application runs locally with Docker Compose and supports testing with real uploaded documents and provider accounts. High-risk external side effects require a separate approval and execute only through an explicitly connected provider.

## Implemented capabilities

- Organization-aware authentication with rotating refresh sessions and live membership checks
- Organizations, invitations, real user onboarding, tenant-scoped memberships, and stronger RBAC
- Optional per-organization OIDC/SSO with discovery, PKCE, nonce validation, and encrypted client secrets
- Persistent document uploads for `.txt`, `.md`, `.csv`, `.json`, `.eml`, `.pdf`, and `.docx`
- Extraction, chunking, embeddings, semantic search, and claim-level grounded RAG answers
- Central model gateway for structured outputs, retries, timeouts, token limits, provider switching, budgets, and telemetry
- PostgreSQL with `pgvector` and HNSW indexing, plus a SQLite fallback for local development
- Document inspection, metadata editing, re-indexing, deletion, and unsafe-content review
- Prompt-injection detection for uploaded content and tool arguments
- Tenant-configurable DLP detection with audit/quarantine and blocking modes
- Upload size, archive-safety, active-content, executable, and malware-signature scanning
- Optional fail-closed ClamAV INSTREAM scanning for production uploads
- Redis-backed request rate limiting and hardened API response headers
- Versioned encrypted-secret keyrings with tenant-scoped online re-encryption
- Previewable, confirmed, tenant-scoped retention enforcement with audit evidence
- Redis/RQ jobs for uploads, re-indexing, and connector imports
- Constrained LLM or deterministic planning with server-validated MCP tools, scopes, arguments, and approval requirements
- Isolated OpenClaw service integration with tenant-bound, revocable MCP credentials and narrow tool filters
- Durable agent workflows with action state, retries, cancellation, approvals, and idempotency
- Authenticated MCP tools for document search, task creation, data export, Gmail, Calendar, Slack, GitHub, Jira, and Notion actions
- Approval records bound to immutable payload hashes with replay protection
- OAuth authorization-code flows with expiring PKCE state, encrypted tokens, refresh-token rotation, provider revocation, and secure disconnect
- Incremental Gmail, Calendar, Slack, GitHub, Jira, and Notion synchronization with encrypted cursors and RAG document updates
- Signed webhook endpoints with replay protection, provider delivery IDs, and pending-sync state
- Google Drive browsing and selected-file import into the document library
- Policy evaluation, job monitoring, approvals, connector sync, webhook setup, and audit visibility in the React UI
- Persistent RAG, embedding, and MCP runtime telemetry with latency, reliability, and cost summaries
- Persistent RAG evaluation datasets with local-versus-OpenAI quality comparisons
- Daily or monthly cost budgets with warning thresholds and preflight enforcement for priced providers
- Alembic migrations and automated Docker verification
- Production HTTPS proxying, managed-service configuration, encrypted object storage, backups, restore verification, metrics, and alerts
- GitHub Actions checks for tests, migrations, builds, dependency audits, production configuration, image publishing, deployment, and restore verification

## Architecture

### Frontend

- React 19, TypeScript, and Vite
- Responsive operations dashboard for documents, search, workflows, approvals, connectors, jobs, MCP tools, and audit events
- Live API integration with automatic access-token refresh and organization switching

### Backend

- FastAPI with modular route, service, model, and policy layers
- SQLAlchemy persistence managed by Alembic migrations
- Local deterministic embeddings or optional OpenAI embeddings
- Security MCP server exposed through Streamable HTTP
- Production connector framework with provider-specific OAuth, sync, webhook, revocation, and action adapters
- Runtime observability ledger and configurable embedding/generation cost budgets
- Curated RAG quality evaluation with per-case evidence and latency results
- Tenant isolation across documents, workflows, MCP, approvals, connectors, jobs, policies, audit, telemetry, budgets, and RAG evaluations
- Optional OpenClaw Docker overlay with no database, Redis, Docker socket, repository, or host-filesystem access

### Data and infrastructure

- PostgreSQL stores application state and `pgvector` embeddings in Docker mode
- Redis carries background ingestion jobs to the RQ worker
- Named Docker volumes persist database data and uploaded files
- SQLite and inline jobs provide a low-dependency local development mode
- Production mode requires TLS-enabled managed PostgreSQL and Redis plus encrypted S3-compatible object storage
- Caddy terminates HTTPS, while Prometheus and Alertmanager remain on the internal Compose network

## Repository layout

```text
backend/
  app/
    api/routes/
    core/
    models/
    services/
  alembic/
  tests/
frontend/
  src/
scripts/
docker-compose.yml
```

## Run with Docker

### Prerequisites

- Git
- Docker Desktop with Docker Compose

Clone and start the complete stack:

```bash
git clone https://github.com/VineetBESTMAN/secure-agentic-work-os.git
cd secure-agentic-work-os
docker compose up --build -d
docker compose ps
```

Open `http://127.0.0.1:5173` after all services are healthy.

The base stack starts five containers:

- `frontend`: serves the React application on port `5173`
- `backend`: serves the FastAPI REST and MCP endpoints on port `8000`
- `postgres`: persists application records and vector embeddings
- `redis`: queues background work
- `worker`: processes document and connector jobs from Redis

An optional `openclaw` gateway and a one-shot state-volume initializer are defined in `docker-compose.openclaw.yml`. They start only after an administrator creates a scoped credential.

Stop the stack without deleting persisted volumes:

```bash
docker compose down
```

## Demo accounts

All local demo users use the password `demo-password`.

| Role | Email | Purpose |
| --- | --- | --- |
| Admin | `admin@demo.local` | Upload data, run searches, create workflows, and use MCP tools |
| Manager | `manager@demo.local` | Review and decide approval-gated actions |
| Employee | `employee@demo.local` | Exercise restricted role and scope behavior |

## Test with real data

1. Sign in at `http://127.0.0.1:5173` as `admin@demo.local`.
2. Upload a supported file from the Documents panel.
3. Wait for its ingestion job to reach `completed`.
4. Ask a question whose answer appears in the uploaded file.
5. Inspect the cited source passage and searchable chunks.
6. Edit the document metadata, re-index it, or delete it to test the management lifecycle.
7. Connect a Google Workspace account with Gmail send permission.
8. Create an agent workflow containing `create a task and send a reply`.
9. Sign in as `manager@demo.local`, approve the waiting email action, and confirm the result reports `delivery_mode: provider`.

Uploaded content, extracted chunks, workflows, jobs, approvals, and audit records persist across container restarts through Docker volumes.

## Security model

- Roles and scopes come from the active organization membership and are revalidated on every API and MCP request.
- Access tokens are bound to a server-side session and membership. Refresh tokens are stored only as SHA-256 hashes, rotated on use, and invalidated on logout.
- Suspending a membership immediately blocks its existing sessions; organization switches issue a separately bound session.
- Invitations are single-use, expire automatically, store only a token hash, and require an existing user to confirm their current password.
- Every tenant-owned record carries an organization boundary, and service queries enforce it before role-level visibility rules.
- Policy checks separate an agent's proposed action from actual tool execution.
- High-risk email and export operations require a separate manager approval.
- Requesters cannot approve their own actions.
- Approvals are bound to the exact stored payload through a canonical SHA-256 hash.
- Stable idempotency keys and provider action receipts prevent duplicate local tasks and unsafe automatic retries after ambiguous external deliveries.
- Prompt guards flag suspicious instructions in documents and tool arguments.
- Security-relevant activity is recorded in the audit log.

External actions never fall back to a fake success response. If the required provider is disconnected, expired, or missing permission, the governed execution fails with a reconnect or scope error and retains its audit trail.

## Advanced security controls

Administrators configure DLP, malware scanning, and retention in the **Advanced Security Controls** panel. DLP findings persist only the finding type, a one-way fingerprint, the uploaded-content hash, and safe metadata; matched secrets are never stored in finding records. Audit mode marks the document unsafe so retrieval excludes it, while block mode rejects it before document persistence. The asynchronous upload path performs a malware preflight before writing its staging file.

The built-in scanner rejects the EICAR test signature, executable content, executable archive members, unsafe archive expansion, malformed archives, and active PDF content. A production deployment can use ClamAV's `INSTREAM` protocol:

```text
APP_MALWARE_SCANNER_MODE=clamav
APP_MALWARE_FAIL_CLOSED=true
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
```

Rate limits use hashed principals and return standard limit and retry headers. Local development defaults to an in-memory backend; multi-instance deployments must use Redis:

```text
APP_RATE_LIMIT_BACKEND=redis
APP_RATE_LIMIT_REQUESTS=120
APP_RATE_LIMIT_AUTH_REQUESTS=10
APP_RATE_LIMIT_UPLOAD_REQUESTS=20
```

Encrypted connector tokens, OAuth verifiers, webhook secrets, synchronization cursors, and OIDC secrets use a versioned envelope. Configure a JSON keyring with the old key retained during rotation, choose the active key, inspect the tenant's rotation status, then explicitly rotate from the administration panel:

```text
APP_ENCRYPTION_KEYRING={"2026-07":"old-secret","2026-10":"new-secret"}
APP_ACTIVE_ENCRYPTION_KEY_ID=2026-10
```

Key IDs and counts are visible to administrators; key material is never returned by the API. Existing unversioned ciphertext remains decryptable through the legacy application secret and can be migrated online.

Retention starts disabled. Administrators first save category-specific periods, inspect the tenant-only preview, and explicitly confirm enforcement. Each run deletes at most `APP_RETENTION_BATCH_SIZE` expired records per category and writes a fresh audit event. It does not delete documents, users, memberships, workflows, approvals, connector accounts, or uploaded files.

For secret-file injection through Docker/Kubernetes secrets or a dedicated secret manager sidecar, use:

```text
APP_SECRET_KEY_FILE=/run/secrets/workos-jwt-key
APP_ENCRYPTION_KEYRING_FILE=/run/secrets/workos-encryption-keyring
```

When `APP_ENV=production`, startup rejects the demo JWT key, missing encryption keyring, non-Redis rate limiting, disabled malware scanning, or non-fail-closed malware configuration.

## Organizations, invitations, and SSO

Every user can belong to multiple organizations with a separate role and scope set in each. Use the Organization & Identity panel to create a workspace and switch the active tenant. Admins can invite users, suspend or reactivate memberships, and configure an optional OIDC provider. Managers can inspect members and invitation status without changing membership security.

New users accept an invitation with a display name and a password of at least 12 characters. Existing users enter their current password when accepting an invitation for another organization. The invitation response exposes the raw token once so it can be delivered through a secure channel; only its hash is persisted.

OIDC sign-in is opt-in per organization. Register this callback pattern with the identity provider:

```text
http://127.0.0.1:8000/api/auth/oidc/{provider_id}/callback
```

The authorization flow uses discovery, authorization-code PKCE, a single-use hashed state, nonce checking, issuer/audience/signature validation, and a verified email that must already have an active organization membership. Configure the public callback base with `APP_OIDC_REDIRECT_BASE_URL`.

## Security MCP server

The Streamable HTTP MCP endpoint is:

```text
http://127.0.0.1:8000/protocol/mcp
```

It accepts the same JWT bearer token as the REST API and exposes these structured tools:

- `search_documents` runs with the server-owned `documents:read` scope.
- `create_task` persists a task with the server-owned `tasks:write` scope.
- `send_email` sends through Gmail after a separate approval.
- `create_calendar_event` creates a Google Calendar event after approval.
- `send_slack_message` posts through Slack after approval.
- `create_github_issue`, `create_jira_issue`, and `create_notion_page` create provider records after approval.
- `export_data` creates an approval request before returning accessible document metadata.

The server determines each required scope, validates and prompt-scans arguments, stores a canonical argument hash, and audits the execution. A client-supplied scope cannot reduce these controls.

## Agent workflows

Each workflow action is a durable database record. Document search and task creation execute through the MCP gateway, while email pauses in `waiting_for_approval`. A manager decision updates the hash-bound MCP execution, sends through the connected Gmail account, and resumes the parent workflow automatically.

The UI displays action attempts, approval IDs, MCP execution IDs, results, and failures. Workflows support safe resume, up to three retry attempts after failure, and cancellation. Idempotency keys reuse an existing execution instead of creating duplicate side effects.

## Secure OpenClaw integration

OpenClaw connects to Secure Work OS as a Streamable HTTP MCP client. It receives a dedicated credential tied to one organization and an explicit subset of `documents:read`, `tasks:write`, `email:send`, and `connectors:act`. Work OS stores only the SHA-256 token hash. The raw token is returned once, expires, can be rotated or revoked immediately, and is not accepted by the normal REST API.

OpenClaw tool calls still enter the same MCP gateway as every other client. Server-owned scopes, prompt-safety checks, organization isolation, policy evaluation, immutable argument hashes, human approval, provider execution, audit events, and runtime observations remain authoritative. An approval-gated request is revalidated against the active OpenClaw client before it can resume.

The optional Docker overlay uses the pinned official OpenClaw image. Its root filesystem is read-only, Linux capabilities are dropped, `no-new-privileges` is enabled, and its generated MCP configuration lives in tmpfs. The container receives two read-only secret files, one read-only bootstrap file, and a named OpenClaw state volume. It does not receive application environment variables, database credentials, Redis access, the Docker socket, repository directories, or uploads. Its only application network is an internal bridge shared with the backend. A networkless one-shot initializer grants the non-root OpenClaw user access to the named volume; it receives no secrets.

To configure it:

1. Start the base stack and sign in as an organization administrator.
2. In **OpenClaw MCP Integration**, create a client with the minimum required scopes. Save the one-time token.
3. From PowerShell, run the secret setup helper and paste the token when prompted:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/configure_openclaw_secrets.ps1
```

4. Prove the official OpenClaw client can authenticate and discover only the configured tool filter:

```powershell
docker compose -f docker-compose.yml -f docker-compose.openclaw.yml --profile openclaw-tools run --rm openclaw-cli mcp probe secure-work-os --json
```

5. Start the isolated gateway and inspect its health:

```powershell
docker compose -f docker-compose.yml -f docker-compose.openclaw.yml --profile openclaw up -d
docker compose -f docker-compose.yml -f docker-compose.openclaw.yml ps
```

The gateway port is not published to the host while the service uses the internal-only MCP network. Check it through Docker's health status and logs. The setup helper prints its randomly generated gateway token once. `OPENCLAW_WORKOS_TOOLS` must stay aligned with the scopes granted by Work OS; the default exposes only `search_documents` and `create_task`.

## Background jobs

Uploads, re-index requests, and connector imports return a job ID immediately. The RQ worker then performs extraction, chunking, embedding, and persistence. Jobs report `queued`, `running`, `completed`, or `failed` status in the dashboard and retry worker failures up to three times.

Local development can execute these operations inline without Redis:

```text
APP_ASYNC_JOBS_ENABLED=false
APP_ASYNC_JOBS_FALLBACK_SYNC=true
```

## Embeddings and retrieval

The default deterministic local embedding provider makes upload, search, and citations work without an API key:

```text
APP_EMBEDDING_PROVIDER=local
APP_VECTOR_DIMENSIONS=384
```

OpenAI embeddings can be enabled through environment variables:

```text
APP_EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
APP_VECTOR_DIMENSIONS=384
```

The configured OpenAI model receives a request for 384 output dimensions to match the pgvector schema. Re-index uploaded documents after changing the embedding provider or vector dimensions.

## Grounded answers, model gateway, and constrained planning

Retrieved passages can be turned into natural answers through the central model gateway. The answer schema requires every factual claim to cite a retrieved chunk ID and include an exact supporting quote; the server rejects unknown IDs or quotes absent from the cited passage before rendering numbered markers. Retrieved document content is treated as untrusted data. If model access is disabled, unavailable, over budget, or returns invalid grounding, an extractive deterministic answer is returned instead.

The same gateway provides structured outputs, explicit provider/model selection, request timeouts, bounded retries, output-token limits, cost preflight, and runtime telemetry. OpenAI Responses requests set `store=false`. Deterministic mode remains the default and requires no API key:

```text
APP_MODEL_PROVIDER=deterministic
APP_GROUNDED_ANSWERS_ENABLED=true
APP_LLM_PLANNER_ENABLED=false
```

To enable OpenAI grounded generation and constrained planning, set:

```text
APP_MODEL_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_GENERATION_MODEL=gpt-5.6
APP_LLM_PLANNER_ENABLED=true
APP_MODEL_MAX_INPUT_TOKENS=16000
OPENAI_GENERATION_TIMEOUT_SECONDS=30
OPENAI_GENERATION_MAX_RETRIES=2
OPENAI_GENERATION_MAX_OUTPUT_TOKENS=1200
```

Set the current input and output prices explicitly with `OPENAI_GENERATION_INPUT_COST_PER_MILLION_TOKENS` and `OPENAI_GENERATION_OUTPUT_COST_PER_MILLION_TOKENS`. A zero value records usage and latency without accruing an estimated provider cost.

The LLM planner is proposal-only. Its allowed tool list is filtered by the active membership scopes, and the server validates every action against the MCP input schema. Scope and approval values are copied from server-owned tool definitions. Actual execution still occurs only through the MCP gateway, policies, immutable payload hashes, approvals, idempotency, audit records, and runtime telemetry. `GET /api/models/status` exposes non-secret gateway configuration to authenticated users.

## RAG quality evaluation

Admins and managers can create persistent evaluation datasets from accessible, prompt-safe document chunks. Each answerable case declares expected document or chunk IDs, evidence facts, and a reference answer; unanswerable control cases declare no expected evidence. Dataset settings control corpus document IDs, top-K retrieval, and the minimum similarity score.

The RAG Quality Evaluation dashboard runs the same dataset against local and OpenAI embeddings without re-indexing or modifying stored documents. Each provider run records:

- retrieval accuracy from expected evidence recall
- citation correctness from the precision of returned citations
- groundedness from expected facts supported by retrieved excerpts
- a hallucination proxy that flags unsupported citations and citations on unanswerable cases
- average, P95, and corpus-index embedding latency

OpenAI comparisons use the configured model and cost-budget enforcement. If `OPENAI_API_KEY` is absent, the OpenAI run is persisted as `skipped` while the local run still completes. Evaluation corpora are bounded by `APP_RAG_EVALUATION_MAX_CHUNKS`, which defaults to 500.

```text
GET  /api/rag-evaluations/datasets
POST /api/rag-evaluations/datasets
POST /api/rag-evaluations/datasets/{dataset_id}/runs
GET  /api/rag-evaluations/runs
GET  /api/rag-evaluations/runs/{run_id}
```

## Runtime governance and cost budgets

RAG queries, embedding batches, model generation, agent planning, and terminal MCP tool executions write structured runtime observations with a shared trace ID, actor, provider/model label, outcome, latency, usage units, and estimated cost. Admins and managers can inspect 24-hour or custom-window summaries in the Runtime Governance dashboard or through:

```text
GET /api/observability/summary?hours=24
GET /api/observability/events?hours=24&limit=200
GET /api/observability/budgets
```

Admins can create, update, or remove daily and monthly budgets under `/api/observability/budgets`. Enabled budgets are checked before priced embedding and generation requests. Provider prices change over time, so the repository deliberately does not hard-code a price; configure the current rates explicitly:

```text
OPENAI_EMBEDDING_COST_PER_MILLION_TOKENS=0
OPENAI_GENERATION_INPUT_COST_PER_MILLION_TOKENS=0
OPENAI_GENERATION_OUTPUT_COST_PER_MILLION_TOKENS=0
APP_DEFAULT_DAILY_COST_LIMIT_USD=5
```

The deterministic local embedding provider records latency and usage with zero provider cost. Telemetry write failures never interrupt the governed operation, while an exceeded enabled budget blocks the priced request before it reaches the provider.

## Production connectors and real actions

The Connectors panel supports account authorization, incremental synchronization, webhook endpoint setup, token status, reconnect, revocation, and persisted live validation. Configure each OAuth application with this callback pattern:

```text
http://127.0.0.1:8000/api/connectors/{provider}/callback
```

Set the matching client credentials in the root `.env` file:

```text
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_PUBSUB_SERVICE_ACCOUNT=
GOOGLE_PUBSUB_AUDIENCE=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=
JIRA_CLIENT_ID=
JIRA_CLIENT_SECRET=
```

Enable the API products and OAuth scopes shown by the provider's connector card:

| Provider | Incremental data | Approval-gated actions |
| --- | --- | --- |
| Google Workspace | Gmail and Calendar, plus selected Drive imports | Gmail send and Calendar event creation |
| Slack | Channel messages | Channel message send |
| GitHub | Issues visible to the connected user | Repository issue creation |
| Jira | Cloud issues | Issue creation |
| Notion | Shared pages | Page creation |

Tokens and refresh tokens are encrypted at rest. Expiring access tokens refresh automatically when the provider issued a refresh token. Disconnect always removes local credentials and also calls the provider's revocation API where one is available.

Sync cursors are encrypted and scoped to the organization, connector, and resource. Changed items update their existing RAG documents in place; provider deletion events are recorded without automatically deleting retained workspace documents.

Webhook creation returns a callback URL and a signing secret exactly once. GitHub, Google, and Jira can be registered remotely when the required target is supplied through the API. Slack and Notion callback URLs are configured in their app consoles. Incoming deliveries require a provider signature or channel token, reject stale Slack timestamps, deduplicate delivery IDs, and mark the corresponding resource for incremental sync.

For Gmail notifications, create an authenticated Pub/Sub push subscription for the returned callback URL. Set `GOOGLE_PUBSUB_SERVICE_ACCOUNT` to the exact push-auth service-account email and set `GOOGLE_PUBSUB_AUDIENCE` to the configured OIDC audience; when omitted, the audience defaults to the callback URL. Gmail deliveries are accepted only after the Google-signed OIDC token, issuer, audience, and service-account email are verified. Calendar watch notifications continue to use their high-entropy `X-Goog-Channel-Token`.

Set the externally reachable webhook base before registering provider webhooks:

```text
APP_CONNECTOR_WEBHOOK_BASE_URL=https://work-os.example.com/api/connectors/webhooks
```

Use **Validate live** after connecting an account. It performs read-only identity and minimum-result API probes, compares granted scopes, and reports existing sync, signed-webhook, approval-gated action, disconnect, and revocation evidence. Validation records contain only safe metadata such as status, latency, counts, scope names, timestamps, and identity-match results; access tokens and provider content are never returned or persisted. Administrators can use **Validate + refresh** to explicitly exercise refresh-token rotation.

A provider remains `incomplete` until its real developer application and account lifecycle have produced the required evidence. To complete validation without bypassing governance:

1. Authorize the provider from the Connectors panel and run **Validate live**.
2. Run each supported resource sync and configure the signed webhook callback in the provider console.
3. Deliver a real signed webhook event.
4. Request each external action through MCP and have a different manager approve it.
5. Run **Validate + refresh** when the provider issued a refresh token.
6. Disconnect once to prove local credential wiping and remote revocation, then reconnect for continued use.

The validator never sends messages or creates provider records itself. It recognizes external actions only from completed connector receipts joined to an MCP execution with a recorded approval.

## Production deployment and operations

`docker-compose.production.yml` runs the application behind Caddy with automatic HTTPS, immutable read-only application containers, fail-closed ClamAV scanning, protected Prometheus metrics, Alertmanager rules, scheduled PostgreSQL backups, and an isolated restore-verification job. PostgreSQL, Redis, and S3-compatible object storage are external managed services; the production stack does not publish their ports or create disposable replacements.

Provision these dependencies before deployment:

- a DNS record for the application domain pointing to the deployment host, with inbound TCP 80/443 and UDP 443 permitted
- PostgreSQL with TLS and a connection URL containing `sslmode=require`, `verify-ca`, or `verify-full`
- Redis with TLS and a `rediss://` connection URL
- a private S3-compatible bucket with versioning, lifecycle policy, server-side encryption, and a least-privilege workload identity
- separate PostgreSQL administration and scratch-database URLs for restore verification

Copy `.env.production.example` to the ignored `.env.production`, create each file under `.secrets/production/`, and keep those files outside version control. Use a JSON encryption keyring whose active key matches `APP_ACTIVE_ENCRYPTION_KEY_ID`. The production Compose file expects a host or workload identity for S3; if static credentials are unavoidable, mount the supported object-storage credential files through a private Compose override.

Validate the production artifacts without starting the application:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_production_config.ps1
```

After provisioning the managed services and secrets, deploy with:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml up -d --wait
docker compose --env-file .env.production -f docker-compose.production.yml ps
```

The public liveness and dependency-readiness endpoints are `/health` and `/ready`. `/metrics` is reachable only on the internal backend network and requires the bearer token mounted into Prometheus. Admins and managers can inspect the same sanitized dependency and backup state in the **Production Operations** panel.

### Object storage migration

The document migration adds storage metadata and preserves every existing upload. When moving an installation from local files to S3, first configure the destination and run the migration helper in dry-run mode from `backend/`:

```bash
python ../scripts/migrate_object_storage.py
python ../scripts/migrate_object_storage.py --execute
```

Each object is uploaded, read back, and SHA-256 verified before its database record changes. Local source files are deliberately retained, so rollback does not depend on deleting or reconstructing existing data.

### Backups, restoration, monitoring, and deployment automation

The backup service creates compressed `pg_dump` archives, SHA-256 checksums, and manifests, uploads them with server-side encryption, and verifies the stored object. Set `BACKUP_INTERVAL_SECONDS` and `APP_BACKUP_RPO_SECONDS` to the required operating objectives. Test the newest backup only against the dedicated scratch database:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml --profile restore run --rm restore-verify
```

The verifier checks the checksum, restores with error-on-failure semantics, confirms the target database identity, reads the Alembic revision and document count, then removes only the explicitly named scratch database. Never point either restore-verification secret at the live application database.

Prometheus alert rules cover backend availability, readiness, HTTP 5xx rate, p95 latency, and missing or stale backups. The checked-in Alertmanager receiver intentionally has no external destination; add an operator-owned override with the desired paging or messaging credentials before production launch.

The deployment workflow publishes commit-addressed backend and frontend images with provenance and SBOM metadata. To enable its environment-gated SSH deployment and weekly restore test, configure the GitHub `production` environment with `PRODUCTION_SSH_HOST`, `PRODUCTION_SSH_USER`, `PRODUCTION_DEPLOY_PATH`, `PRODUCTION_SSH_PRIVATE_KEY`, and `PRODUCTION_SSH_KNOWN_HOSTS`; set `PRODUCTION_DEPLOY_ENABLED=true` and `PRODUCTION_APP_URL` as repository environment variables. The target directory must contain this repository, `.env.production`, and its secret files.

## Local development

Docker is the primary full-stack path. The backend and frontend can also run directly for development.

Backend on Windows Git Bash:

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend in a second terminal:

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

Without a PostgreSQL `DATABASE_URL`, the backend uses SQLite at `backend/data/workos.db`. Uploaded files are stored in `backend/data/uploads/`, and queued operations run inline by default.

## Database migrations

Application startup runs `alembic upgrade head` before demo data is seeded. Run migration commands from `backend/`:

```bash
python -m alembic current
python -m alembic history
python -m alembic upgrade head
```

Create and validate a schema revision with:

```bash
python -m alembic revision -m "describe the schema change"
python -m alembic upgrade head
python -m alembic downgrade -1
python -m alembic upgrade head
```

Back up persistent data before executing a downgrade against a populated environment.

## Verification and CI

Run the complete Docker smoke test from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_docker_stack.ps1
```

or:

```bash
bash scripts/verify_docker_stack.sh
```

The verification scripts build the stack, check the Alembic revision, sign in, import a queued sample, query PostgreSQL/pgvector, verify that a rejected provider action never executes, complete a provider-free workflow, and confirm the frontend is reachable.

GitHub Actions run backend tests, an Alembic upgrade/downgrade round trip, frontend and production-image builds, dependency audits, and production Compose/proxy/monitoring validation on pushes to `main` and on pull requests.

## Public repository safety

The default `docker-compose.yml` is configured for local demonstration, not direct internet exposure. It includes known demo credentials, a development database password, `APP_SECRET_KEY=change-me`, and a development encryption keyring. Keep `.env`, `.env.production`, `.secrets`, and provider credentials untracked. The separate production configuration deliberately refuses these defaults and requires HTTPS, managed TLS services, encrypted object storage, protected metrics, and external secret files before startup.
