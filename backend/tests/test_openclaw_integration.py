from __future__ import annotations

import anyio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from app.core.crypto import encrypt_secret
from app.core.database import decode_json, encode_json, get_connection
from app.main import app
from app.models.schemas import MCPExecutionRequest
from app.services.mcp_gateway import mcp_gateway_service
from app.services.mcp_protocol import WorkOSJWTVerifier, security_mcp
from app.services.openclaw import openclaw_service


client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _login(email: str = "admin@demo.local") -> dict[str, object]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _headers(login: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


def _create_client(
    headers: dict[str, str], scopes: list[str]
) -> dict[str, object]:
    response = client.post(
        "/api/openclaw/clients",
        headers=headers,
        json={
            "name": f"OpenClaw test {uuid4().hex[:10]}",
            "scopes": scopes,
            "expires_in_days": 7,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _principal(token: str):
    principal = openclaw_service.resolve_token(token)
    assert principal is not None
    return principal


def test_openclaw_credentials_are_tenant_scoped_hashed_and_returned_once() -> None:
    admin = _login()
    headers = _headers(admin)
    credential = _create_client(headers, ["documents:read", "tasks:write"])
    record = credential["client"]

    assert credential["token"].startswith("wos_oc_")
    assert record["actor_id"] == f"openclaw:{record['client_id']}"
    assert record["organization_id"] == "org_default"
    assert credential["docker_mcp_server_url"] == (
        "http://backend:8000/protocol/mcp"
    )
    server = credential["docker_openclaw_config"]["mcp"]["servers"][
        "secure-work-os"
    ]
    assert server["transport"] == "streamable-http"
    assert server["toolFilter"]["include"] == [
        "create_task",
        "export_data",
        "search_documents",
    ]
    assert "exclude" not in server["toolFilter"]

    with get_connection() as connection:
        stored = connection.execute(
            "SELECT token_hash, scopes_json FROM openclaw_clients WHERE client_id = ?",
            (record["client_id"],),
        ).fetchone()
    assert credential["token"] not in stored["token_hash"]
    assert len(stored["token_hash"]) == 64
    assert decode_json(stored["scopes_json"], []) == [
        "documents:read",
        "tasks:write",
    ]

    listed = client.get("/api/openclaw/clients", headers=headers)
    assert listed.status_code == 200
    listed_record = next(
        item for item in listed.json() if item["client_id"] == record["client_id"]
    )
    assert "token" not in listed_record
    rest_attempt = client.get(
        "/api/documents/library",
        headers={"Authorization": f"Bearer {credential['token']}"},
    )
    assert rest_attempt.status_code == 401

    rotated = client.post(
        f"/api/openclaw/clients/{record['client_id']}/rotate", headers=headers
    )
    assert rotated.status_code == 200
    assert rotated.json()["token"] != credential["token"]
    assert anyio.run(
        WorkOSJWTVerifier().verify_token, credential["token"]
    ) is None
    assert anyio.run(
        WorkOSJWTVerifier().verify_token, rotated.json()["token"]
    ) is not None

    manager_create = client.post(
        "/api/openclaw/clients",
        headers=_headers(_login("manager@demo.local")),
        json={"name": "Forbidden client", "scopes": ["documents:read"]},
    )
    assert manager_create.status_code == 403


def test_openclaw_token_authenticates_mcp_and_scope_limits_execution() -> None:
    credential = _create_client(_headers(_login()), ["documents:read"])
    token = credential["token"]
    principal = _principal(token)
    with pytest.raises(HTTPException) as denied:
        mcp_gateway_service.request_execution(
            MCPExecutionRequest(
                tool_name="create_task",
                arguments={"title": "Must not be created"},
            ),
            principal,
        )
    assert denied.value.status_code == 403
    assert denied.value.detail == "Missing required scope: tasks:write"

    verifier_token = anyio.run(WorkOSJWTVerifier().verify_token, token)
    assert verifier_token is not None
    assert verifier_token.claims["principal_type"] == "openclaw"
    assert verifier_token.claims["organization_id"] == "org_default"


def test_openclaw_mcp_execution_is_audited_observed_and_revocable() -> None:
    admin_headers = _headers(_login())
    credential = _create_client(admin_headers, ["tasks:write"])
    token = credential["token"]
    client_id = credential["client"]["client_id"]
    actor_id = credential["client"]["actor_id"]

    result = mcp_gateway_service.request_execution(
        MCPExecutionRequest(
            tool_name="create_task",
            arguments={"title": f"OpenClaw task {uuid4().hex[:8]}"},
        ),
        _principal(token),
    )
    assert result.status == "completed"

    with get_connection() as connection:
        execution = connection.execute(
            """
            SELECT * FROM mcp_tool_executions
            WHERE requested_by = ? AND tool_name = 'create_task'
            ORDER BY created_at DESC LIMIT 1
            """,
            (actor_id,),
        ).fetchone()
        audit = connection.execute(
            """
            SELECT * FROM audit_events
            WHERE actor_id = ? AND event_type = 'mcp.execution_completed'
            ORDER BY timestamp DESC LIMIT 1
            """,
            (actor_id,),
        ).fetchone()
        observation = connection.execute(
            """
            SELECT * FROM runtime_observations
            WHERE actor_id = ? AND operation_type = 'mcp_tool'
            ORDER BY created_at DESC LIMIT 1
            """,
            (actor_id,),
        ).fetchone()
    assert execution["organization_id"] == "org_default"
    assert execution["status"] == "completed"
    assert audit is not None
    assert observation is not None

    revoked = client.delete(f"/api/openclaw/clients/{client_id}", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert anyio.run(WorkOSJWTVerifier().verify_token, token) is None


def test_openclaw_tenant_and_document_role_isolation_through_mcp() -> None:
    default_login = _login()
    default_headers = _headers(default_login)
    suffix = uuid4().hex[:10]
    organization = client.post(
        "/api/organizations",
        headers=default_headers,
        json={"name": f"OpenClaw Tenant {suffix}", "slug": f"oc-{suffix}"},
    ).json()
    switched = client.post(
        "/api/auth/switch-organization",
        headers=default_headers,
        json={"organization_id": organization["organization_id"]},
    ).json()
    tenant_headers = _headers(switched)

    default_document = client.post(
        "/api/documents/upload",
        headers=default_headers,
        files={
            "file": (
                f"default-{suffix}.txt",
                f"The default-only marker is default-{suffix}.",
                "text/plain",
            )
        },
    ).json()
    tenant_document = client.post(
        "/api/documents/upload",
        headers=tenant_headers,
        files={
            "file": (
                f"tenant-{suffix}.txt",
                f"The tenant-only marker is tenant-{suffix}.",
                "text/plain",
            )
        },
    ).json()
    restricted_document = client.post(
        "/api/documents/upload",
        headers=tenant_headers,
        files={
            "file": (
                f"restricted-{suffix}.txt",
                f"The restricted marker is restricted-{suffix}.",
                "text/plain",
            )
        },
        data={"classification": "restricted", "owner_team": "security"},
    ).json()

    credential = _create_client(tenant_headers, ["documents:read"])
    result = mcp_gateway_service.request_execution(
        MCPExecutionRequest(
            tool_name="search_documents",
            arguments={"question": f"What is the marker tenant-{suffix}?"},
        ),
        _principal(credential["token"]),
    )
    assert result.status == "completed"
    actor_id = credential["client"]["actor_id"]
    with get_connection() as connection:
        execution = connection.execute(
            """
            SELECT result_json FROM mcp_tool_executions
            WHERE requested_by = ? AND tool_name = 'search_documents'
            ORDER BY created_at DESC LIMIT 1
            """,
            (actor_id,),
        ).fetchone()
    answer = decode_json(execution["result_json"], {})
    cited_ids = {
        citation["document_id"] for citation in answer.get("citations", [])
    }
    assert tenant_document["document_id"] in cited_ids
    assert default_document["document_id"] not in cited_ids
    assert restricted_document["document_id"] not in cited_ids

    default_clients = client.get("/api/openclaw/clients", headers=default_headers).json()
    assert credential["client"]["client_id"] not in {
        item["client_id"] for item in default_clients
    }


def test_openclaw_approval_resolves_only_an_active_service_principal(monkeypatch) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE connector_accounts SET status = 'disconnected' WHERE provider = 'google' AND organization_id = 'org_default'"
        )
        connection.execute(
            """
            INSERT INTO connector_accounts (
                connector_id, provider, account_label, status, scopes_json,
                token_cipher, refresh_token_cipher, expires_at, created_by,
                organization_id, external_account_id, metadata_json, updated_at
            ) VALUES (?, 'google', ?, 'connected', ?, ?, ?, ?, 'u_admin',
                      'org_default', ?, '{}', ?)
            """,
            (
                f"con_openclaw_{uuid4().hex}",
                "openclaw@example.com",
                encode_json(["https://www.googleapis.com/auth/gmail.send"]),
                encrypt_secret("openclaw-access-token"),
                encrypt_secret("openclaw-refresh-token"),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "openclaw-google-account",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/users/me/messages/send")
        return httpx.Response(
            200,
            json={"id": "gmail_openclaw_message", "threadId": "gmail_openclaw_thread"},
        )

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    credential = _create_client(_headers(_login()), ["email:send"])
    result = mcp_gateway_service.request_execution(
        MCPExecutionRequest(
            tool_name="send_email",
            arguments={
                "to": "client@example.com",
                "subject": "OpenClaw approval test",
                "body": "This must wait for a Work OS manager.",
            },
        ),
        _principal(credential["token"]),
    )
    assert result.status == "pending_approval"

    with get_connection() as connection:
        pending = connection.execute(
            """
            SELECT * FROM mcp_tool_executions
            WHERE requested_by = ? AND tool_name = 'send_email'
            ORDER BY created_at DESC LIMIT 1
            """,
            (credential["client"]["actor_id"],),
        ).fetchone()
    assert pending["status"] == "pending_approval"
    decision = client.post(
        f"/api/approvals/{pending['approval_id']}/decision",
        headers=_headers(_login("manager@demo.local")),
        json={"approved": True},
    )
    assert decision.status_code == 200, decision.text
    with get_connection() as connection:
        completed = connection.execute(
            "SELECT * FROM mcp_tool_executions WHERE execution_id = ?",
            (pending["execution_id"],),
        ).fetchone()
        result_payload = decode_json(completed["result_json"], {})
    assert completed["status"] == "completed"
    assert result_payload["external_id"] == "gmail_openclaw_message"


def test_revoked_openclaw_principal_cannot_resume_pending_approval() -> None:
    admin_headers = _headers(_login())
    credential = _create_client(admin_headers, ["email:send"])
    principal = _principal(credential["token"])
    execution = mcp_gateway_service.request_execution(
        MCPExecutionRequest(
            tool_name="send_email",
            arguments={
                "to": "client@example.com",
                "subject": "Revoked OpenClaw request",
                "body": "This must never be sent.",
            },
        ),
        principal,
    )
    assert execution.status == "pending_approval"

    revoked = client.delete(
        f"/api/openclaw/clients/{credential['client']['client_id']}",
        headers=admin_headers,
    )
    assert revoked.status_code == 200
    decision = client.post(
        f"/api/approvals/{execution.approval_id}/decision",
        headers=_headers(_login("manager@demo.local")),
        json={"approved": True},
    )
    assert decision.status_code == 200
    with get_connection() as connection:
        failed = connection.execute(
            "SELECT * FROM mcp_tool_executions WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()
    assert failed["status"] == "failed"
    assert failed["error"] == "The requesting principal is no longer active."


def test_openclaw_docker_overlay_has_no_host_data_or_docker_socket_access() -> None:
    overlay = (REPO_ROOT / "docker-compose.openclaw.yml").read_text(encoding="utf-8")
    bootstrap = (REPO_ROOT / "openclaw" / "bootstrap.mjs").read_text(
        encoding="utf-8"
    )

    assert "ghcr.io/openclaw/openclaw:2026.6.6" in overlay
    assert "/var/run/docker.sock" not in overlay
    assert "DATABASE_URL" not in overlay
    assert "REDIS_URL" not in overlay
    assert "read_only: true" in overlay
    assert 'cap_drop: ["ALL"]' in overlay
    assert "no-new-privileges:true" in overlay
    assert "internal: true" in overlay
    assert "network_mode: none" in overlay
    assert "service_completed_successfully" in overlay
    assert "./openclaw/bootstrap.mjs" in overlay
    assert "./.secrets/openclaw-workos-token" in overlay
    assert "OPENCLAW_CONFIG_PATH: /run/openclaw/openclaw.json" in overlay
    assert "Authorization: `Bearer ${workOsToken}`" in bootstrap
    assert '"exec"' in bootstrap and '"apply_patch"' in bootstrap


def test_mcp_dns_rebinding_protection_allows_configured_internal_host() -> None:
    transport = security_mcp.settings.transport_security

    assert transport is not None
    assert transport.enable_dns_rebinding_protection is True
    assert "127.0.0.1:8000" in transport.allowed_hosts
    assert "backend:8000" in transport.allowed_hosts
