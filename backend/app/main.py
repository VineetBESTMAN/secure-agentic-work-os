from contextlib import asynccontextmanager

import hashlib
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    agent,
    approvals,
    audit,
    auth,
    connectors,
    documents,
    health,
    jobs,
    mcp,
    models,
    observability,
    operations,
    openclaw,
    organizations,
    policies,
    rag_evaluations,
    security,
)
from app.core.config import get_settings
from app.core.migrations import upgrade_database
from app.services.approval import approval_service
from app.services.mcp_protocol import security_mcp, security_mcp_http_app
from app.services.metrics import metrics_service
from app.services.observability import observability_service
from app.services.policies import policy_service
from app.services.rate_limit import rate_limit_service
from app.services.users import user_service
from app.services.workflows import workflow_service

settings = get_settings()
if settings.run_migrations_on_startup:
    upgrade_database()
user_service.seed_demo_users()
approval_service.seed_demo_request()
policy_service.seed_defaults()
observability_service.seed_defaults(settings.default_daily_cost_limit_usd)
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
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    started_at = time.perf_counter()
    request_settings = get_settings()
    decision = None
    if request_settings.rate_limit_enabled and request.url.path not in {
        "/health",
        "/ready",
        "/metrics",
    }:
        if request.url.path.startswith(("/api/auth/login", "/api/auth/refresh")):
            limit = request_settings.rate_limit_auth_requests
            category = "auth"
        elif request.url.path.startswith("/api/documents/upload"):
            limit = request_settings.rate_limit_upload_requests
            category = "upload"
        else:
            limit = request_settings.rate_limit_requests
            category = "default"
        authorization = request.headers.get("authorization", "")
        if authorization:
            principal = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
        else:
            principal = request.client.host if request.client else "unknown"
        decision = rate_limit_service.consume(
            f"{category}:{principal}",
            limit=limit,
            window_seconds=request_settings.rate_limit_window_seconds,
        )
        if not decision.allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Request rate limit exceeded. Retry later."},
                headers={"Retry-After": str(decision.reset_after_seconds)},
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    if decision:
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(decision.reset_after_seconds)
    if request_settings.security_headers_enabled:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if request_settings.app_env.lower() == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
    route = getattr(request.scope.get("route"), "path", None)
    if not route:
        route = (
            request.url.path
            if request.url.path in {"/health", "/ready", "/metrics"}
            else "<unmatched>"
        )
    metrics_service.observe_request(
        method=request.method,
        route=route,
        status_code=response.status_code,
        duration_seconds=time.perf_counter() - started_at,
    )
    return response

app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(approvals.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(openclaw.router, prefix="/api")
app.include_router(connectors.router, prefix="/api")
app.include_router(policies.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(observability.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(rag_evaluations.router, prefix="/api")
app.include_router(organizations.router, prefix="/api")
app.include_router(security.router, prefix="/api")
app.mount("/protocol", security_mcp_http_app)
