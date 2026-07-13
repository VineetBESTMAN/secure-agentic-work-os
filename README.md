# Secure Agentic AI Work OS

Secure Agentic AI Work OS is a portfolio-grade starter for an enterprise AI copilot that can search company knowledge, summarize emails, propose actions, and enforce approvals before sensitive tool execution.

## Why this project matters

This repository is designed to demonstrate the engineering patterns companies want from modern AI systems:

- Retrieval-augmented answers over internal documents with citations
- Role-based access and policy-aware retrieval
- Approval-gated agent workflows instead of unchecked tool execution
- Standards-compatible MCP tool routing behind a security gateway
- Prompt injection detection, audit logging, and action traceability

## Architecture

### Frontend

- React + TypeScript + Vite
- Tailwind-ready project structure
- Dashboard-focused UI shell for search, approvals, and audit visibility

### Backend

- FastAPI application with modular route groups
- JWT utilities and role-aware request handling
- Alembic-versioned SQLite and PostgreSQL persistence for application state
- Real upload ingestion for `.txt`, `.md`, `.csv`, `.json`, `.eml`, `.pdf`, and `.docx`
- Local embedding RAG over uploaded document chunks with citations
- Optional PostgreSQL + `pgvector` storage with HNSW vector indexing
- Document management for source inspection, metadata edits, delete, and re-index
- Unsafe document review for prompt-injection flagged uploads
- Policy engine with default document-access, tool-approval, and prompt-safety rules
- Redis/RQ background workers for document ingestion, reindexing, and connector imports
- Persisted agent workflow plans for approval-backed automation
- CI and Docker scaffolding for production-oriented validation
- Approval workflow service for high-risk actions
- Authenticated MCP Streamable HTTP server with server-owned scopes and structured tools
- Approval-bound tool executions with immutable payload hashes and replay protection
- Prompt guard for suspicious content detection
- OAuth-ready connector endpoints for Google Workspace, GitHub, Slack, Notion, and Jira
- Audit service for immutable-style activity trails

### Data and infra

- PostgreSQL for app state
- `pgvector` storage with HNSW indexing for embeddings
- Redis queues for ingestion workers and future caching
- Docker Compose starter for local development

## Repository layout

```text
backend/
  app/
    api/routes/
    core/
    models/
    services/
  tests/
frontend/
  src/
```

## Initial product scope

### Phase 1

- Login and JWT-based session handling
- Roles: `admin`, `manager`, `employee`
- Document upload metadata model
- RAG query endpoint with citations
- Agent workflow endpoint that produces approval-gated action plans
- MCP gateway endpoint that enforces scoped permissions

### Phase 2

- OAuth integrations for Gmail, Google Drive, Calendar, Slack, Jira, GitHub
- Queue-backed async summarization and ingestion
- Full policy engine and admin rules
- Cost, latency, and model observability
- End-to-end audit dashboard

## Security goals

This starter treats security as a first-class product feature:

- Reject unapproved high-risk actions
- Restrict tool calls by user role and scope
- Flag prompt-injection patterns from documents and emails
- Keep auditable records of queries, decisions, and tool execution
- Separate agent intent from actual side-effecting operations

## Getting started

### Backend

1. Create a virtual environment.
2. Install dependencies from `backend/pyproject.toml`.
3. Run `uvicorn app.main:app --reload` from `backend/`.

### Frontend

1. Install dependencies in `frontend/`.
2. Run `npm run dev`.

## Testing with real data

1. Open `http://127.0.0.1:5173`.
2. Sign in with `admin@demo.local` and `demo-password`.
3. Upload a `.txt`, `.md`, `.csv`, `.json`, `.eml`, `.pdf`, or `.docx` file.
4. Ask a question about the uploaded content.
5. Review the cited passages, document library, source chunks, approvals, connector status, and audit trail.
6. Use document actions to view chunks, edit metadata, re-index, or delete documents.
7. Import a Google Drive-style note from the connector panel and watch the job list.
8. Create an agent workflow and review the planned actions plus approval state.
9. Use the Security MCP Console to run document search or create a persistent task.

## Security MCP server

The standards-compatible MCP endpoint is:

```text
http://127.0.0.1:8000/protocol/mcp
```

It uses the same JWT bearer token as the REST API and currently advertises four structured tools:

- `search_documents` runs immediately with `documents:read`.
- `create_task` persists a task immediately with `tasks:write`.
- `send_email` requires separate human approval and completes in safe simulation mode.
- `export_data` requires separate human approval before returning accessible document metadata.

The server determines each required scope; a client-supplied scope cannot downgrade security. Every request is validated and prompt-scanned, persisted with a canonical SHA-256 argument hash, and audited. Approval resumes only the exact stored payload, requesters cannot approve their own action, and repeat decisions are rejected.

To test the complete approval lifecycle in the UI:

1. Sign in as `admin@demo.local` with `demo-password`.
2. Run `send_email` from the Security MCP Console.
3. Sign out, then sign in as `manager@demo.local` with `demo-password`.
4. Approve the pending action in the Approvals panel.
5. Confirm that the execution changes to `completed` with `delivery_mode: simulated`; no external email is sent.

Uploaded files and searchable chunks persist in `backend/data/workos.db` and `backend/data/uploads/`.

## Background ingestion workers

The UI sends uploads, reindex requests, and manual connector imports to queued endpoints. Each request immediately returns a job ID, and the UI polls the job while an RQ worker performs extraction, chunking, embedding, and database persistence.

Docker enables workers automatically:

```bash
docker compose up --build -d
docker compose ps
```

The stack includes a dedicated `worker` service connected to Redis, PostgreSQL, and the shared upload volume. Jobs move through `queued`, `running`, `completed`, or `failed`, report progress in the dashboard, and retry worker failures up to three times.

For local development without Redis, queued endpoints execute inline by default:

```text
APP_ASYNC_JOBS_ENABLED=false
APP_ASYNC_JOBS_FALLBACK_SYNC=true
```

To run a local worker, set `APP_ASYNC_JOBS_ENABLED=true`, start Redis, and run this from `backend/`:

```bash
rq worker --url redis://127.0.0.1:6379/0 ingestion
```

## PostgreSQL and pgvector mode

SQLite is still available as a zero-setup local fallback. To run the vector database path:

1. Start Postgres with pgvector.

```bash
docker compose up -d postgres
```

2. Create a `.env` file in the repo root with:

```text
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agentic_work_os
APP_VECTOR_DIMENSIONS=384
```

3. Restart the backend.

```bash
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

When `DATABASE_URL` is set to PostgreSQL, the backend creates the `vector` extension, stores chunk embeddings in a `vector(384)` column, and ranks citations with cosine distance.

## Database migrations

Alembic owns the SQLite and PostgreSQL schema. Application startup runs `alembic upgrade head` before demo data is seeded, so existing installations are adopted without deleting documents and fresh installations receive the complete schema.

Run migration commands from `backend/`:

```bash
python -m alembic current
python -m alembic history
python -m alembic upgrade head
```

Create each future schema change as a revision:

```bash
python -m alembic revision -m "describe the schema change"
```

Then implement `upgrade()` and `downgrade()` in the generated file and test both directions. Downgrading the initial baseline to `base` removes application tables, so back up production data before running destructive downgrade commands.

## Embedding providers

The app defaults to deterministic local embeddings so upload, RAG, and citations work without an API key:

```text
APP_EMBEDDING_PROVIDER=local
APP_VECTOR_DIMENSIONS=384
```

For higher-quality semantic retrieval, enable OpenAI embeddings:

```text
APP_EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
APP_VECTOR_DIMENSIONS=384
```

The OpenAI provider uses the embeddings API with `text-embedding-3-small` by default and requests 384 output dimensions so it remains compatible with the local pgvector schema. After changing embedding providers or dimensions, re-index existing documents or re-upload them so stored chunk vectors match the active provider.

## OAuth connector setup

Connectors are wired for real OAuth but require provider credentials. Add the relevant values to `.env`, then restart the backend:

```text
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=
JIRA_CLIENT_ID=
JIRA_CLIENT_SECRET=
```

Use `http://127.0.0.1:8000/api/connectors/{provider}/callback` as the redirect URL pattern when creating provider apps.

### Google Drive connector

The Google connector can browse recent Drive files and import selected files into the RAG library after OAuth is connected.

1. Create a Google Cloud OAuth app and enable the Google Drive API.
2. Add this redirect URI to the OAuth client:

```text
http://127.0.0.1:8000/api/connectors/google/callback
```

3. Add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to `.env`.
4. Restart the backend or Docker stack.
5. Open the app, sign in, authorize Google Workspace, search Drive files, select importable files, and click **Import selected**.

The backend uses Google Drive `files.list` for browsing, `files.get?alt=media` for stored files, and `files.export` for Google Docs, Sheets, and Slides.

## CI and Docker

GitHub Actions run backend tests, frontend build, and frontend audit on pushes to `main` and pull requests.

Docker Compose can run the full stack when Docker is available:

```bash
docker compose up --build
```

This starts:

- FastAPI backend on `http://127.0.0.1:8000`
- React preview server on `http://127.0.0.1:5173`
- PostgreSQL with `pgvector`
- Redis and an RQ ingestion worker

For a repeatable verification run, install and start Docker Desktop, then run one of these commands from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_docker_stack.ps1
```

```bash
bash scripts/verify_docker_stack.sh
```

The verification scripts build the stack, verify the database is at the latest Alembic head, sign in with the demo admin user, import a sample connector note through Redis/RQ, query PostgreSQL/pgvector for a cited answer, and confirm the frontend is reachable.

## Suggested next steps

1. Add retrieval quality evaluation sets for local vs OpenAI embeddings.
2. Add state-machine execution behind the persisted workflow records.
3. Add organization-aware identity, permissions, and tenant isolation.
4. Replace simulated MCP side effects with provider-backed delivery adapters.
5. Add more real connectors after Google Drive, such as Gmail and Calendar imports.
