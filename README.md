# Secure Agentic AI Work OS

Secure Agentic AI Work OS is a working reference implementation of an enterprise AI copilot. It combines document ingestion and retrieval, approval-gated agent workflows, role-based access, auditable tool execution, background jobs, and a standards-compatible MCP security gateway.

The application runs locally with Docker Compose and supports testing with real uploaded documents. High-risk side effects remain safely simulated.

## Implemented capabilities

- JWT authentication with `admin`, `manager`, and `employee` roles
- Persistent document uploads for `.txt`, `.md`, `.csv`, `.json`, `.eml`, `.pdf`, and `.docx`
- Extraction, chunking, embeddings, semantic search, and cited RAG answers
- PostgreSQL with `pgvector` and HNSW indexing, plus a SQLite fallback for local development
- Document inspection, metadata editing, re-indexing, deletion, and unsafe-content review
- Prompt-injection detection for uploaded content and tool arguments
- Redis/RQ jobs for uploads, re-indexing, and connector imports
- Durable agent workflows with action state, retries, cancellation, approvals, and idempotency
- Authenticated MCP tools for document search, task creation, email simulation, and data export
- Approval records bound to immutable payload hashes with replay protection
- Google Drive OAuth browsing and selected-file import into the document library
- Policy evaluation, job monitoring, approvals, connector status, and audit visibility in the React UI
- Alembic migrations and automated Docker verification
- GitHub Actions checks for backend tests, migration round trips, frontend builds, and dependency audits

## Architecture

### Frontend

- React 19, TypeScript, and Vite
- Responsive operations dashboard for documents, search, workflows, approvals, connectors, jobs, MCP tools, and audit events
- Live API integration with JWT-authenticated requests

### Backend

- FastAPI with modular route, service, model, and policy layers
- SQLAlchemy persistence managed by Alembic migrations
- Local deterministic embeddings or optional OpenAI embeddings
- Security MCP server exposed through Streamable HTTP
- OAuth connector framework with a working Google Drive file flow

### Data and infrastructure

- PostgreSQL stores application state and `pgvector` embeddings in Docker mode
- Redis carries background ingestion jobs to the RQ worker
- Named Docker volumes persist database data and uploaded files
- SQLite and inline jobs provide a low-dependency local development mode

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

The stack starts five containers:

- `frontend`: serves the React application on port `5173`
- `backend`: serves the FastAPI REST and MCP endpoints on port `8000`
- `postgres`: persists application records and vector embeddings
- `redis`: queues background work
- `worker`: processes document and connector jobs from Redis

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
7. Create an agent workflow containing `create a task and send a reply`.
8. Sign in as `manager@demo.local` and approve the waiting email action.
9. Confirm the workflow completes and the email result reports `delivery_mode: simulated`.

Uploaded content, extracted chunks, workflows, jobs, approvals, and audit records persist across container restarts through Docker volumes.

## Security model

- Roles and JWT scopes restrict API and MCP operations.
- Policy checks separate an agent's proposed action from actual tool execution.
- High-risk email and export operations require a separate manager approval.
- Requesters cannot approve their own actions.
- Approvals are bound to the exact stored payload through a canonical SHA-256 hash.
- Stable idempotency keys prevent duplicate tasks or simulated deliveries during retries.
- Prompt guards flag suspicious instructions in documents and tool arguments.
- Security-relevant activity is recorded in the audit log.

Email delivery is intentionally simulated. The application does not send an external email during the approval demo.

## Security MCP server

The Streamable HTTP MCP endpoint is:

```text
http://127.0.0.1:8000/protocol/mcp
```

It accepts the same JWT bearer token as the REST API and exposes four structured tools:

- `search_documents` runs with the server-owned `documents:read` scope.
- `create_task` persists a task with the server-owned `tasks:write` scope.
- `send_email` creates an approval request and completes in simulation mode after approval.
- `export_data` creates an approval request before returning accessible document metadata.

The server determines each required scope, validates and prompt-scans arguments, stores a canonical argument hash, and audits the execution. A client-supplied scope cannot reduce these controls.

## Agent workflows

Each workflow action is a durable database record. Document search and task creation execute through the MCP gateway, while email pauses in `waiting_for_approval`. A manager decision updates the hash-bound MCP execution and resumes the parent workflow automatically.

The UI displays action attempts, approval IDs, MCP execution IDs, results, and failures. Workflows support safe resume, up to three retry attempts after failure, and cancellation. Idempotency keys reuse an existing execution instead of creating duplicate side effects.

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

## Google Drive OAuth

The Google connector can browse recent Drive files and import selected supported files into the RAG document library.

1. Create a Google Cloud OAuth client and enable the Google Drive API.
2. Register `http://127.0.0.1:8000/api/connectors/google/callback` as an authorized redirect URI.
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in a root `.env` file.
4. Restart the Docker stack.
5. Authorize Google Workspace from the Connectors panel, browse files, and import the selected items.

The repository also contains OAuth configuration endpoints for GitHub, Slack, Notion, and Jira. Their UI status remains `not_configured` unless matching client credentials are supplied.

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

The verification scripts build the stack, check the Alembic revision, sign in, import a queued sample, query PostgreSQL/pgvector, execute the MCP approval lifecycle, complete an approval-resumed workflow, and confirm the frontend is reachable.

GitHub Actions run backend tests, an Alembic upgrade/downgrade round trip, the frontend production build, and `npm audit --audit-level=moderate` on pushes to `main` and on pull requests.

## Public repository safety

This repository is configured for local demonstration, not direct internet exposure. The Docker defaults include known demo credentials, a development database password, and `APP_SECRET_KEY=change-me`. Keep `.env` files and provider secrets untracked, and replace all default secrets before exposing any service outside the local machine.
