from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agent, approvals, audit, auth, connectors, documents, health, jobs, mcp, policies
from app.core.config import get_settings
from app.core.migrations import upgrade_database
from app.services.approval import approval_service
from app.services.mcp_protocol import security_mcp, security_mcp_http_app
from app.services.policies import policy_service
from app.services.users import user_service
from app.services.workflows import workflow_service

settings = get_settings()
if settings.run_migrations_on_startup:
    upgrade_database()
user_service.seed_demo_users()
approval_service.seed_demo_request()
policy_service.seed_defaults()
workflow_service.backfill_legacy_actions()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with security_mcp.session_manager.run():
        yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Secure enterprise starter for agentic workflows with approval gates.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(approvals.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(connectors.router, prefix="/api")
app.include_router(policies.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.mount("/protocol", security_mcp_http_app)
