# Secure Agentic AI Work OS

Secure Agentic AI Work OS is a portfolio-grade starter for an enterprise AI copilot that can search company knowledge, summarize emails, propose actions, and enforce approvals before sensitive tool execution.

## Why this project matters

This repository is designed to demonstrate the engineering patterns companies want from modern AI systems:

- Retrieval-augmented answers over internal documents with citations
- Role-based access and policy-aware retrieval
- Approval-gated agent workflows instead of unchecked tool execution
- MCP-style tool routing behind a security gateway
- Prompt injection detection, audit logging, and action traceability

## Architecture

### Frontend

- React + TypeScript + Vite
- Tailwind-ready project structure
- Dashboard-focused UI shell for search, approvals, and audit visibility

### Backend

- FastAPI application with modular route groups
- JWT utilities and role-aware request handling
- SQLite persistence for users, documents, chunks, approvals, audit logs, and connectors
- Real upload ingestion for `.txt`, `.md`, `.csv`, `.json`, `.eml`, `.pdf`, and `.docx`
- Local embedding RAG over uploaded document chunks with citations
- Optional PostgreSQL + `pgvector` storage with HNSW vector indexing
- Document management for source inspection, metadata edits, delete, and re-index
- Unsafe document review for prompt-injection flagged uploads
- Policy engine with default document-access, tool-approval, and prompt-safety rules
- Background job tracking for connector ingestion and future async workers
- Persisted agent workflow plans for approval-backed automation
- CI and Docker scaffolding for production-oriented validation
- Approval workflow service for high-risk actions
- MCP gateway service with scoped permission checks
- Prompt guard for suspicious content detection
- OAuth-ready connector endpoints for Google Workspace, GitHub, Slack, Notion, and Jira
- Audit service for immutable-style activity trails

### Data and infra

- PostgreSQL for app state
- `pgvector` planned for embeddings
- Redis planned for async tasks and caching
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

Uploaded files and searchable chunks persist in `backend/data/workos.db` and `backend/data/uploads/`.

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
- Redis for future background workers

For a repeatable verification run, install and start Docker Desktop, then run one of these commands from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_docker_stack.ps1
```

```bash
bash scripts/verify_docker_stack.sh
```

The verification scripts build the stack, wait for backend readiness, sign in with the demo admin user, import a sample connector note into PostgreSQL/pgvector-backed RAG storage, query it for a cited answer, and confirm the frontend is reachable.

## Suggested next steps

1. Replace synchronous job completion with Redis and Celery workers.
2. Swap deterministic local embeddings for OpenAI or sentence-transformer embeddings.
3. Complete Google Drive OAuth file listing and selective import.
4. Add LangGraph state machines behind the persisted workflow records.
5. Add production migrations with Alembic.
