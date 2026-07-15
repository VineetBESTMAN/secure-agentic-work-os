from __future__ import annotations

import hashlib
import base64
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.core.database import get_connection
from app.main import app

client = TestClient(app)


def _login(email: str = "admin@demo.local", organization_slug: str | None = None):
    payload: dict[str, str] = {"email": email, "password": "demo-password"}
    if organization_slug:
        payload["organization_slug"] = organization_slug
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _create_organization(access_token: str) -> dict[str, object]:
    suffix = uuid4().hex[:10]
    response = client.post(
        "/api/organizations",
        headers=_headers(access_token),
        json={"name": f"Tenant {suffix}", "slug": f"tenant-{suffix}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _switch(access_token: str, organization_id: str) -> dict[str, object]:
    response = client.post(
        "/api/auth/switch-organization",
        headers=_headers(access_token),
        json={"organization_id": organization_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_tenant_data_isolation_covers_documents_workflows_audit_and_governance() -> None:
    default_login = _login()
    default_headers = _headers(default_login["access_token"])
    organization = _create_organization(default_login["access_token"])
    tenant_login = _switch(
        default_login["access_token"], organization["organization_id"]
    )
    tenant_headers = _headers(tenant_login["access_token"])

    tenant_upload = client.post(
        "/api/documents/upload",
        headers=tenant_headers,
        files={
            "file": (
                "tenant-secret.txt",
                b"The tenant-only recovery phrase is silver-orchid.",
                "text/plain",
            )
        },
    )
    assert tenant_upload.status_code == 200, tenant_upload.text
    tenant_document_id = tenant_upload.json()["document_id"]

    default_library = client.get("/api/documents/library", headers=default_headers)
    assert default_library.status_code == 200
    assert tenant_document_id not in {
        document["document_id"] for document in default_library.json()
    }
    assert (
        client.get(f"/api/documents/{tenant_document_id}", headers=default_headers).status_code
        == 404
    )

    workflow = client.post(
        "/api/agent/workflows",
        headers=tenant_headers,
        json={"prompt": "Create a tenant-only follow-up task"},
    )
    assert workflow.status_code == 200, workflow.text
    workflow_id = workflow.json()["workflow_id"]
    assert (
        client.get(f"/api/agent/workflows/{workflow_id}", headers=default_headers).status_code
        == 404
    )

    tenant_audit = client.get("/api/audit/events", headers=tenant_headers)
    default_audit = client.get("/api/audit/events", headers=default_headers)
    assert tenant_audit.status_code == default_audit.status_code == 200
    assert any(
        event["detail"].get("document_id") == tenant_document_id
        for event in tenant_audit.json()
    )
    assert all(
        event["detail"].get("document_id") != tenant_document_id
        for event in default_audit.json()
    )

    policies = client.get("/api/policies", headers=tenant_headers)
    budgets = client.get("/api/observability/budgets", headers=tenant_headers)
    assert policies.status_code == 200 and len(policies.json()) >= 3
    assert budgets.status_code == 200 and len(budgets.json()) >= 1


def test_tenant_data_isolation_covers_jobs_mcp_approvals_and_rag_evaluations() -> None:
    default_login = _login()
    default_headers = _headers(default_login["access_token"])
    organization = _create_organization(default_login["access_token"])
    tenant_login = _switch(
        default_login["access_token"], organization["organization_id"]
    )
    tenant_headers = _headers(tenant_login["access_token"])

    connector_import = client.post(
        "/api/connectors/import",
        headers=tenant_headers,
        json={
            "provider": "google",
            "items": [
                {
                    "filename": "tenant-evaluation.txt",
                    "content": "Tenant launch reviews require an operations manager.",
                    "classification": "internal",
                    "owner_team": "operations",
                }
            ],
        },
    )
    assert connector_import.status_code == 200, connector_import.text
    job_id = connector_import.json()["job"]["job_id"]
    document_id = connector_import.json()["imported_documents"][0]["document_id"]
    assert client.get(f"/api/jobs/{job_id}", headers=default_headers).status_code == 404
    assert job_id not in {
        job["job_id"] for job in client.get("/api/jobs", headers=default_headers).json()
    }

    task_execution = client.post(
        "/api/mcp/executions",
        headers=tenant_headers,
        json={
            "tool_name": "create_task",
            "arguments": {"title": "Tenant launch review"},
        },
    )
    assert task_execution.status_code == 200, task_execution.text
    task_execution_id = task_execution.json()["execution_id"]
    assert (
        client.get(
            f"/api/mcp/executions/{task_execution_id}", headers=default_headers
        ).status_code
        == 404
    )

    email_execution = client.post(
        "/api/mcp/executions",
        headers=tenant_headers,
        json={
            "tool_name": "send_email",
            "arguments": {
                "to": "reviewer@example.com",
                "subject": "Tenant launch",
                "body": "Review the tenant launch.",
            },
        },
    )
    assert email_execution.status_code == 200, email_execution.text
    assert email_execution.json()["status"] == "pending_approval"
    approval_id = email_execution.json()["approval_id"]
    assert approval_id not in {
        approval["approval_id"]
        for approval in client.get("/api/approvals", headers=default_headers).json()
    }
    assert (
        client.post(
            f"/api/approvals/{approval_id}/decision",
            headers=default_headers,
            json={"approved": False},
        ).status_code
        == 404
    )

    with get_connection() as connection:
        chunk_id = connection.execute(
            "SELECT chunk_id FROM document_chunks WHERE document_id = ? AND organization_id = ?",
            (document_id, organization["organization_id"]),
        ).fetchone()["chunk_id"]
    dataset_response = client.post(
        "/api/rag-evaluations/datasets",
        headers=tenant_headers,
        json={
            "name": f"Tenant evaluation {uuid4().hex[:8]}",
            "document_ids": [document_id],
            "cases": [
                {
                    "question": "Who reviews tenant launches?",
                    "expected_document_ids": [document_id],
                    "expected_chunk_ids": [chunk_id],
                    "expected_facts": ["operations manager"],
                    "reference_answer": "An operations manager reviews the launch.",
                }
            ],
        },
    )
    assert dataset_response.status_code == 201, dataset_response.text
    dataset_id = dataset_response.json()["dataset_id"]
    assert (
        client.get(
            f"/api/rag-evaluations/datasets/{dataset_id}", headers=default_headers
        ).status_code
        == 404
    )


def test_invitation_refresh_rotation_and_membership_revocation() -> None:
    default_login = _login()
    organization = _create_organization(default_login["access_token"])
    admin_login = _switch(
        default_login["access_token"], organization["organization_id"]
    )
    admin_headers = _headers(admin_login["access_token"])
    email = f"invite-{uuid4().hex[:10]}@example.com"

    invitation_response = client.post(
        "/api/organizations/current/invitations",
        headers=admin_headers,
        json={
            "email": email,
            "role": "employee",
            "scopes": ["documents:read"],
            "expires_in_hours": 24,
        },
    )
    assert invitation_response.status_code == 201, invitation_response.text
    invitation = invitation_response.json()
    token = invitation["invitation_token"]
    assert token
    with get_connection() as connection:
        stored = connection.execute(
            "SELECT token_hash FROM organization_invitations WHERE invitation_id = ?",
            (invitation["invitation_id"],),
        ).fetchone()
    assert stored["token_hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert stored["token_hash"] != token

    accepted_response = client.post(
        "/api/auth/invitations/accept",
        json={
            "token": token,
            "display_name": "Invited Person",
            "password": "Strong-password-123",
        },
    )
    assert accepted_response.status_code == 200, accepted_response.text
    accepted = accepted_response.json()
    assert accepted["user"]["organization_id"] == organization["organization_id"]
    assert accepted["user"]["scopes"] == ["documents:read"]

    replay = client.post(
        "/api/auth/invitations/accept",
        json={
            "token": token,
            "display_name": "Replay",
            "password": "Strong-password-123",
        },
    )
    assert replay.status_code == 400

    old_access_token = accepted["access_token"]
    old_refresh_token = accepted["refresh_token"]
    refreshed_response = client.post(
        "/api/auth/refresh", json={"refresh_token": old_refresh_token}
    )
    assert refreshed_response.status_code == 200, refreshed_response.text
    refreshed = refreshed_response.json()
    assert refreshed["refresh_token"] != old_refresh_token
    assert (
        client.post(
            "/api/auth/refresh", json={"refresh_token": old_refresh_token}
        ).status_code
        == 401
    )
    assert client.get("/api/documents/library", headers=_headers(old_access_token)).status_code == 401

    members_response = client.get(
        "/api/organizations/current/members", headers=admin_headers
    )
    member = next(item for item in members_response.json() if item["email"] == email)
    suspended = client.patch(
        f"/api/organizations/current/members/{member['membership_id']}",
        headers=admin_headers,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200, suspended.text
    assert (
        client.get(
            "/api/documents/library", headers=_headers(refreshed["access_token"])
        ).status_code
        == 401
    )


def test_oidc_configuration_is_tenant_scoped_and_secret_is_encrypted() -> None:
    default_login = _login()
    organization = _create_organization(default_login["access_token"])
    tenant_login = _switch(
        default_login["access_token"], organization["organization_id"]
    )
    tenant_headers = _headers(tenant_login["access_token"])
    client_secret = f"secret-{uuid4().hex}"

    created_response = client.post(
        "/api/organizations/current/oidc-providers",
        headers=tenant_headers,
        json={
            "name": "Corporate Identity",
            "issuer_url": "https://id.example.test",
            "client_id": "work-os",
            "client_secret": client_secret,
            "scopes": ["openid", "email", "profile"],
        },
    )
    assert created_response.status_code == 201, created_response.text
    provider = created_response.json()
    assert "client_secret" not in provider
    with get_connection() as connection:
        stored = connection.execute(
            "SELECT client_secret_cipher FROM oidc_providers WHERE provider_id = ?",
            (provider["provider_id"],),
        ).fetchone()
    assert stored["client_secret_cipher"] != client_secret
    assert client_secret not in stored["client_secret_cipher"]

    default_providers = client.get(
        "/api/organizations/current/oidc-providers",
        headers=_headers(default_login["access_token"]),
    )
    assert default_providers.status_code == 200
    assert provider["provider_id"] not in {
        item["provider_id"] for item in default_providers.json()
    }


def test_oidc_authorization_validates_pkce_state_nonce_signature_and_membership(
    monkeypatch,
) -> None:
    default_login = _login()
    organization = _create_organization(default_login["access_token"])
    tenant_login = _switch(
        default_login["access_token"], organization["organization_id"]
    )
    provider_response = client.post(
        "/api/organizations/current/oidc-providers",
        headers=_headers(tenant_login["access_token"]),
        json={
            "name": "Mock Identity",
            "issuer_url": "https://identity.example.test",
            "client_id": "work-os-client",
            "client_secret": "mock-client-secret",
            "scopes": ["openid", "email", "profile"],
        },
    )
    assert provider_response.status_code == 201, provider_response.text
    provider_id = provider_response.json()["provider_id"]

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()

    def b64_integer(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    state_context: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://identity.example.test",
                    "authorization_endpoint": "https://identity.example.test/authorize",
                    "token_endpoint": "https://identity.example.test/token",
                    "jwks_uri": "https://identity.example.test/jwks",
                },
            )
        if request.url.path == "/token":
            now = int(time.time())
            id_token = jwt.encode(
                {
                    "iss": "https://identity.example.test",
                    "aud": "work-os-client",
                    "sub": "admin-identity",
                    "email": "admin@demo.local",
                    "email_verified": True,
                    "nonce": state_context["nonce"],
                    "iat": now,
                    "exp": now + 300,
                },
                private_pem,
                algorithm="RS256",
                headers={"kid": "test-key"},
            )
            return httpx.Response(200, json={"id_token": id_token, "access_token": "mock"})
        if request.url.path == "/jwks":
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {
                            "kty": "RSA",
                            "kid": "test-key",
                            "use": "sig",
                            "alg": "RS256",
                            "n": b64_integer(public_numbers.n),
                            "e": b64_integer(public_numbers.e),
                        }
                    ]
                },
            )
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.oidc.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    start = client.get(f"/api/auth/oidc/{provider_id}/authorize")
    assert start.status_code == 200, start.text
    authorization_url = start.json()["authorization_url"]
    query = parse_qs(urlparse(authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    state = query["state"][0]
    with get_connection() as connection:
        saved = connection.execute(
            "SELECT nonce FROM oidc_authorization_states WHERE state_hash = ?",
            (hashlib.sha256(state.encode("utf-8")).hexdigest(),),
        ).fetchone()
    state_context["nonce"] = saved["nonce"]

    callback = client.get(
        f"/api/auth/oidc/{provider_id}/callback",
        params={"code": "authorization-code", "state": state},
    )
    assert callback.status_code == 200, callback.text
    assert callback.json()["user"]["organization_id"] == organization["organization_id"]
    assert client.get(
        "/api/documents/library", headers=_headers(callback.json()["access_token"])
    ).status_code == 200

    replay = client.get(
        f"/api/auth/oidc/{provider_id}/callback",
        params={"code": "authorization-code", "state": state},
    )
    assert replay.status_code == 400
