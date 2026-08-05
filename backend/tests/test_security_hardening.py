from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_secret, encrypt_secret, encrypted_key_id
from app.core.database import encode_json, get_connection
from app.main import app
from app.services.rate_limit import RateLimitService
from app.services.security_admin import security_admin_service
from app.services.security_controls import security_control_service


client = TestClient(app)


def _login(email: str = "admin@demo.local") -> dict[str, object]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _headers(login: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}"}


def _new_tenant() -> tuple[dict[str, object], dict[str, str]]:
    login = _login()
    suffix = uuid4().hex[:10]
    organization = client.post(
        "/api/organizations",
        headers=_headers(login),
        json={"name": f"Security {suffix}", "slug": f"security-{suffix}"},
    )
    assert organization.status_code == 201, organization.text
    switched = client.post(
        "/api/auth/switch-organization",
        headers=_headers(login),
        json={"organization_id": organization.json()["organization_id"]},
    )
    assert switched.status_code == 200, switched.text
    body = switched.json()
    return body, _headers(body)


def test_security_headers_and_posture_are_secret_safe() -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in health.headers["content-security-policy"]

    login = _login()
    posture = client.get("/api/security/posture", headers=_headers(login))
    assert posture.status_code == 200, posture.text
    body = posture.json()
    assert "secret_key" not in json.dumps(body)
    assert body["encryption_key_count"] >= 1
    assert body["policy"]["organization_id"] == "org_default"


def test_malware_signature_is_blocked_before_persistence() -> None:
    login = _login()
    filename = f"eicar-{uuid4().hex}.txt"
    response = client.post(
        "/api/documents/upload/async",
        headers=_headers(login),
        files={
            "file": (
                filename,
                (
                    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
                    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
                ),
                "text/plain",
            )
        },
    )
    assert response.status_code == 400
    assert "malware" in response.json()["detail"].lower()
    findings = client.get("/api/security/findings", headers=_headers(login))
    assert findings.status_code == 200
    assert any(
        item["resource_name"] == filename
        and item["control_type"] == "malware"
        and item["action"] == "blocked"
        for item in findings.json()
    )
    with get_connection() as connection:
        document = connection.execute(
            "SELECT 1 FROM documents WHERE filename = ?", (filename,)
        ).fetchone()
    assert document is None


def test_oversized_upload_is_stopped_by_bounded_reader() -> None:
    login = _login()
    settings = get_settings()
    original_maximum = settings.upload_max_bytes
    try:
        settings.upload_max_bytes = 1_024
        response = client.post(
            "/api/documents/upload/async",
            headers=_headers(login),
            files={
                "file": (
                    "oversized.txt",
                    b"x" * 1_025,
                    "text/plain",
                )
            },
        )
        assert response.status_code == 413
        assert "1024-byte security limit" in response.json()["detail"]
    finally:
        settings.upload_max_bytes = original_maximum


def test_dlp_audit_and_block_modes_are_tenant_scoped() -> None:
    tenant_login, tenant_headers = _new_tenant()
    audited = client.post(
        "/api/documents/upload",
        headers=tenant_headers,
        files={
            "file": (
                "audited-secret.txt",
                b"Temporary API key: sk-example012345678901234567890",
                "text/plain",
            )
        },
    )
    assert audited.status_code == 200, audited.text
    assert audited.json()["unsafe"] is True
    assert "DLP detected: api_key" in audited.json()["unsafe_reasons"]

    policy = client.get("/api/security/policy", headers=tenant_headers).json()
    policy["dlp_mode"] = "block"
    for key in ("policy_id", "organization_id", "updated_by", "created_at", "updated_at"):
        policy.pop(key, None)
    updated = client.put(
        "/api/security/policy", headers=tenant_headers, json=policy
    )
    assert updated.status_code == 200, updated.text

    blocked_name = f"blocked-{uuid4().hex}.txt"
    blocked = client.post(
        "/api/documents/upload",
        headers=tenant_headers,
        files={
            "file": (
                blocked_name,
                b"AWS credential AKIAABCDEFGHIJKLMNOP must not be indexed.",
                "text/plain",
            )
        },
    )
    assert blocked.status_code == 400
    assert "data-loss prevention" in blocked.json()["detail"]
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM documents
            WHERE filename = ? AND organization_id = ?
            """,
            (blocked_name, tenant_login["user"]["organization_id"]),
        ).fetchone()
    assert row is None

    employee = _login("employee@demo.local")
    denied = client.put(
        "/api/security/policy",
        headers=_headers(employee),
        json=policy,
    )
    assert denied.status_code == 403


def test_rate_limiter_enforces_window_without_storing_raw_identity() -> None:
    limiter = RateLimitService()
    decisions = [
        limiter.consume("sensitive-user-token", limit=2, window_seconds=60)
        for _ in range(3)
    ]
    assert [decision.allowed for decision in decisions] == [True, True, False]
    assert decisions[-1].remaining == 0
    assert "sensitive-user-token" not in limiter._memory


def test_rate_limit_middleware_returns_retry_and_budget_headers() -> None:
    login = _login()
    settings = get_settings()
    original_enabled = settings.rate_limit_enabled
    original_backend = settings.rate_limit_backend
    original_limit = settings.rate_limit_requests
    try:
        settings.rate_limit_enabled = True
        settings.rate_limit_backend = "memory"
        settings.rate_limit_requests = 2
        responses = [
            client.get("/api/models/status", headers=_headers(login))
            for _ in range(3)
        ]
        assert [response.status_code for response in responses] == [200, 200, 429]
        assert responses[0].headers["x-ratelimit-limit"] == "2"
        assert responses[1].headers["x-ratelimit-remaining"] == "0"
        assert int(responses[2].headers["retry-after"]) >= 1
    finally:
        settings.rate_limit_enabled = original_enabled
        settings.rate_limit_backend = original_backend
        settings.rate_limit_requests = original_limit


def test_required_malware_scanner_fails_closed_when_unavailable(monkeypatch) -> None:
    tenant_login, tenant_headers = _new_tenant()
    organization_id = str(tenant_login["user"]["organization_id"])
    policy = security_control_service.get_policy(organization_id).model_dump()
    policy["malware_mode"] = "clamav"
    for key in ("policy_id", "organization_id", "updated_by", "created_at", "updated_at"):
        policy.pop(key, None)
    assert (
        client.put("/api/security/policy", headers=tenant_headers, json=policy).status_code
        == 200
    )
    monkeypatch.setattr(
        "app.services.security_controls.socket.create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("scanner unavailable")),
    )
    settings = get_settings()
    original_fail_closed = settings.malware_fail_closed
    try:
        settings.malware_fail_closed = True
        response = client.post(
            "/api/documents/upload",
            headers=tenant_headers,
            files={"file": ("scan.txt", b"ordinary content", "text/plain")},
        )
        assert response.status_code == 400
        assert "scanner is unavailable" in response.json()["detail"]
    finally:
        settings.malware_fail_closed = original_fail_closed


def test_production_secret_files_and_fail_closed_controls_are_required(
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "jwt-secret"
    keyring_file = tmp_path / "keyring"
    secret_file.write_text("j" * 48, encoding="utf-8")
    keyring_file.write_text(
        json.dumps({"primary": "e" * 48}), encoding="utf-8"
    )
    settings = Settings(
        APP_ENV="production",
        APP_PUBLIC_BASE_URL="https://workos.example.com",
        APP_CORS_ORIGINS="https://workos.example.com",
        APP_OIDC_REDIRECT_BASE_URL="https://workos.example.com/api/auth/oidc",
        APP_OAUTH_REDIRECT_BASE_URL="https://workos.example.com/api/connectors",
        APP_CONNECTOR_WEBHOOK_BASE_URL=(
            "https://workos.example.com/api/connectors/webhooks"
        ),
        APP_MCP_ISSUER_URL="https://workos.example.com",
        APP_MCP_SERVER_URL="https://workos.example.com/protocol/mcp",
        APP_SECRET_KEY_FILE=str(secret_file),
        APP_ENCRYPTION_KEYRING_FILE=str(keyring_file),
        DATABASE_URL="postgresql://workos:secret@db.example.com/workos?sslmode=require",
        APP_DATABASE_TLS_REQUIRED=True,
        REDIS_URL="rediss://cache.example.com:6379/0",
        APP_REDIS_TLS_REQUIRED=True,
        APP_RATE_LIMIT_BACKEND="redis",
        APP_MALWARE_SCANNER_MODE="clamav",
        APP_MALWARE_FAIL_CLOSED=True,
        APP_OBJECT_STORAGE_BACKEND="s3",
        APP_OBJECT_STORAGE_BUCKET="workos-production",
        APP_OBJECT_STORAGE_SSE="AES256",
        APP_METRICS_TOKEN="m" * 32,
        _env_file=None,
    )
    assert settings.secret_key == "j" * 48
    assert settings.encryption_keyring == json.dumps({"primary": "e" * 48})

    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            APP_SECRET_KEY="change-me",
            APP_RATE_LIMIT_BACKEND="memory",
            APP_MALWARE_SCANNER_MODE="disabled",
            _env_file=None,
        )


def test_keyring_rotation_reencrypts_only_the_selected_tenant() -> None:
    tenant_login, _ = _new_tenant()
    organization_id = str(tenant_login["user"]["organization_id"])
    settings = get_settings()
    original_keyring = settings.encryption_keyring
    original_active = settings.active_encryption_key_id
    try:
        legacy_cipher = encrypt_secret("connector-secret")
        connector_id = f"con_rotation_{uuid4().hex}"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO connector_accounts (
                    connector_id, provider, account_label, status, scopes_json,
                    token_cipher, created_by, organization_id
                ) VALUES (?, 'github', 'Rotation test', 'connected', ?, ?, ?, ?)
                """,
                (
                    connector_id,
                    encode_json([]),
                    legacy_cipher,
                    str(tenant_login["user"]["user_id"]),
                    organization_id,
                ),
            )
        settings.encryption_keyring = json.dumps(
            {"legacy": settings.secret_key, "next": "rotation-key-" + "x" * 40}
        )
        settings.active_encryption_key_id = "next"
        status = security_admin_service.key_rotation_status(organization_id)
        assert status.rotatable_ciphertexts >= 1
        result = security_admin_service.rotate_keys(organization_id, confirmed=True)
        assert result.rotated_ciphertexts >= 1
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT token_cipher FROM connector_accounts
                WHERE connector_id = ? AND organization_id = ?
                """,
                (connector_id, organization_id),
            ).fetchone()
        assert encrypted_key_id(row["token_cipher"]) == "next"
        assert decrypt_secret(row["token_cipher"]) == "connector-secret"
    finally:
        settings.encryption_keyring = original_keyring
        settings.active_encryption_key_id = original_active


def test_retention_requires_enablement_confirmation_and_tenant_scope() -> None:
    tenant_login, tenant_headers = _new_tenant()
    organization_id = str(tenant_login["user"]["organization_id"])
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    default_finding_id = f"secfind_default_{uuid4().hex}"
    tenant_finding_id = f"secfind_tenant_{uuid4().hex}"
    with get_connection() as connection:
        for finding_id, tenant in (
            (default_finding_id, "org_default"),
            (tenant_finding_id, organization_id),
        ):
            connection.execute(
                """
                INSERT INTO security_findings (
                    finding_id, organization_id, actor_id, control_type, action,
                    resource_type, resource_name, content_hash, findings_json, created_at
                ) VALUES (?, ?, 'u_admin', 'dlp', 'audited', 'test', 'old.txt',
                          'hash', '[]', ?)
                """,
                (finding_id, tenant, old_timestamp),
            )

    unconfirmed = client.post(
        "/api/security/retention/execute",
        headers=tenant_headers,
        json={"confirm": False},
    )
    assert unconfirmed.status_code == 400

    policy = security_control_service.get_policy(organization_id).model_dump()
    policy["retention_enabled"] = True
    policy["security_finding_retention_days"] = 7
    for key in ("policy_id", "organization_id", "updated_by", "created_at", "updated_at"):
        policy.pop(key, None)
    assert (
        client.put("/api/security/policy", headers=tenant_headers, json=policy).status_code
        == 200
    )
    preview = client.get(
        "/api/security/retention/preview", headers=tenant_headers
    )
    assert preview.status_code == 200
    assert preview.json()["eligible_by_category"]["security_findings"] >= 1

    executed = client.post(
        "/api/security/retention/execute",
        headers=tenant_headers,
        json={"confirm": True},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["deleted_by_category"]["security_findings"] >= 1
    with get_connection() as connection:
        tenant_row = connection.execute(
            "SELECT 1 FROM security_findings WHERE finding_id = ?",
            (tenant_finding_id,),
        ).fetchone()
        default_row = connection.execute(
            "SELECT 1 FROM security_findings WHERE finding_id = ?",
            (default_finding_id,),
        ).fetchone()
    assert tenant_row is None
    assert default_row is not None
