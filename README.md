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
- Local extractive RAG over uploaded document chunks with citations
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
5. Review the cited passages, document library, approvals, connector status, and audit trail.

Uploaded files and searchable chunks persist in `backend/data/workos.db` and `backend/data/uploads/`.

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

## Suggested next steps

1. Move SQLite to PostgreSQL plus `pgvector` for production deployment.
2. Add connector-specific ingestion jobs for Gmail, Drive, Calendar, Slack, Jira, and GitHub.
3. Add LangGraph-based multi-step planning with approval checkpoints.
4. Introduce integration and security tests for prompt injection, RBAC, and approval bypass attempts.
5. Add background processing with Redis and Celery for large document ingestion.
