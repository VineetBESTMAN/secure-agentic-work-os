from __future__ import annotations

import anyio
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from jose import jwk, jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import encode_json, get_connection
from app.main import app
from app.services.connector_providers import NOTION_API_VERSION, probe_provider_access
from app.services.connectors import connector_service


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _run_probe(provider: str) -> dict[str, object]:
    metadata = (
        {"cloud_id": "cloud-1"}
        if provider == "jira"
        else {"bot_id": "notion-bot-1"}
        if provider == "notion"
        else {}
    )
    expected_id = {
        "google": "google-user-1",
        "slack": "team-1",
        "github": "501",
        "jira": "cloud-1",
        "notion": "workspace-1",
    }[provider]
    return await probe_provider_access(
        provider=provider,
        access_token=f"{provider}-secret-access-token",
        account_metadata=metadata,
        expected_external_account_id=expected_id,
    )


def test_read_only_live_probes_cover_every_provider_without_returning_content(
    monkeypatch,
) -> None:
    real_async_client = httpx.AsyncClient
    seen_notion_versions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/userinfo"):
            return httpx.Response(200, json={"sub": "google-user-1", "email": "private@example.com"})
        if path.endswith("/gmail/v1/users/me/profile"):
            return httpx.Response(200, json={"emailAddress": "private@example.com", "messagesTotal": 99})
        if path.endswith("/calendar/v3/calendars/primary"):
            return httpx.Response(200, json={"id": "private-calendar", "summary": "Private"})
        if path.endswith("/drive/v3/files"):
            return httpx.Response(200, json={"files": [{"id": "private-file"}]})
        if path.endswith("/auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "team-1", "user": "private-user"})
        if path.endswith("/conversations.list"):
            return httpx.Response(200, json={"ok": True, "channels": [{"name": "private-channel"}]})
        if path == "/user":
            return httpx.Response(200, json={"id": 501, "login": "private-login"})
        if path == "/issues":
            return httpx.Response(200, json=[{"title": "private issue"}])
        if path.endswith("/oauth/token/accessible-resources"):
            return httpx.Response(200, json=[{"id": "cloud-1", "name": "Private Jira"}])
        if path.endswith("/rest/api/3/myself"):
            return httpx.Response(200, json={"accountId": "private-jira-user"})
        if path.endswith("/rest/api/3/search/jql"):
            return httpx.Response(200, json={"issues": [{"fields": {"summary": "private"}}]})
        if path.endswith("/v1/users/me"):
            seen_notion_versions.append(request.headers.get("Notion-Version", ""))
            return httpx.Response(200, json={"id": "notion-bot-1", "name": "Private"})
        if path.endswith("/v1/search"):
            seen_notion_versions.append(request.headers.get("Notion-Version", ""))
            return httpx.Response(200, json={"results": [{"id": "private-page"}]})
        raise AssertionError(f"Unexpected validation URL: {request.method} {request.url}")

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    for provider in ("google", "slack", "github", "jira", "notion"):
        result = anyio.run(_run_probe, provider)
        assert result["identity_match"] is True
        serialized = json.dumps(result)
        assert "secret-access-token" not in serialized
        assert "private@" not in serialized
        assert "private issue" not in serialized
        assert "private-page" not in serialized
        assert all(check["status"] == "passed" for check in result["checks"])
    assert seen_notion_versions == [NOTION_API_VERSION, NOTION_API_VERSION]


def _insert_complete_google_lifecycle() -> tuple[str, str]:
    now = datetime.now(timezone.utc).isoformat()
    connector_id = f"con_validation_{uuid4().hex}"
    execution_ids = [f"mcp_validation_{uuid4().hex}" for _ in range(2)]
    required_scopes = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ]
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
            ) VALUES (?, 'google', 'validation@example.com', 'connected', ?, ?, ?, ?,
                      'u_admin', 'org_default', 'google-user-validation', '{}', ?)
            """,
            (
                connector_id,
                encode_json(required_scopes),
                encrypt_secret("old-secret-access-token"),
                encrypt_secret("old-secret-refresh-token"),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                now,
            ),
        )
        for resource in ("gmail", "calendar"):
            connection.execute(
                """
                INSERT INTO connector_sync_states (
                    sync_state_id, organization_id, connector_id, provider, resource,
                    status, items_seen, items_changed, last_completed_at, created_at, updated_at
                ) VALUES (?, 'org_default', ?, 'google', ?, 'completed', 1, 1, ?, ?, ?)
                """,
                (f"css_validation_{uuid4().hex}", connector_id, resource, now, now, now),
            )
        for resource in ("gmail", "calendar"):
            subscription_id = f"cws_validation_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO connector_webhook_subscriptions (
                    subscription_id, organization_id, connector_id, provider, resource,
                    secret_cipher, registration_mode, status, created_by, created_at, updated_at
                ) VALUES (?, 'org_default', ?, 'google', ?, ?, 'remote', 'active',
                          'u_admin', ?, ?)
                """,
                (
                    subscription_id,
                    connector_id,
                    resource,
                    encrypt_secret(f"{resource}-webhook-secret"),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO connector_webhook_deliveries (
                    delivery_id, organization_id, subscription_id, provider,
                    external_delivery_id, event_type, payload_hash, signature_valid, received_at
                ) VALUES (?, 'org_default', ?, 'google', ?, ?, ?, ?, ?)
                """,
                (
                    f"cwd_validation_{uuid4().hex}",
                    subscription_id,
                    f"external-{uuid4().hex}",
                    f"{resource}.changed",
                    "a" * 64,
                    True,
                    now,
                ),
            )
        for action, execution_id in zip(
            ("send_email", "create_calendar_event"), execution_ids, strict=True
        ):
            connection.execute(
                """
                INSERT INTO mcp_tool_executions (
                    execution_id, tool_name, requested_by, required_scope,
                    arguments_json, arguments_hash, idempotency_key, status,
                    approval_id, result_json, organization_id
                ) VALUES (?, ?, 'u_admin', 'connectors:act', '{}', ?, ?, 'completed', ?, '{}', 'org_default')
                """,
                (
                    execution_id,
                    action,
                    "b" * 64,
                    f"idem-{uuid4().hex}",
                    f"apr_validation_{uuid4().hex}",
                ),
            )
            connection.execute(
                """
                INSERT INTO connector_action_receipts (
                    receipt_id, organization_id, connector_id, execution_id,
                    provider, action, request_hash, status, external_id,
                    result_json, created_at, updated_at
                ) VALUES (?, 'org_default', ?, ?, 'google', ?, ?, 'completed', ?, '{}', ?, ?)
                """,
                (
                    f"car_validation_{uuid4().hex}",
                    connector_id,
                    execution_id,
                    action,
                    "c" * 64,
                    f"external-{uuid4().hex}",
                    now,
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO audit_events (
                event_id, actor_id, event_type, detail_json, timestamp, organization_id
            ) VALUES (?, 'u_admin', 'connectors.disconnect', ?, ?, 'org_default')
            """,
            (
                f"audit_validation_{uuid4().hex}",
                encode_json({"provider": "google", "remote_revoked": True}),
                now,
            ),
        )
    return connector_id, "old-secret-refresh-token"


def test_full_live_validation_refreshes_safely_and_is_tenant_scoped(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "validation-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "validation-client-secret")
    monkeypatch.setenv(
        "GOOGLE_PUBSUB_SERVICE_ACCOUNT",
        "gmail-push@validation-project.iam.gserviceaccount.com",
    )
    get_settings.cache_clear()
    connector_id, old_refresh_token = _insert_complete_google_lifecycle()
    real_async_client = httpx.AsyncClient

    def refresh_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/token")
        assert old_refresh_token.encode() in request.content
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-secret-access-token",
                "refresh_token": "rotated-secret-refresh-token",
                "expires_in": 3600,
                "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/calendar.events",
                "token_type": "Bearer",
            },
        )

    def probe_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer rotated-secret-access-token"
        path = request.url.path
        if path.endswith("/v1/userinfo"):
            return httpx.Response(200, json={"sub": "google-user-validation", "email": "private@example.com"})
        if path.endswith("/gmail/v1/users/me/profile"):
            return httpx.Response(200, json={"emailAddress": "private@example.com"})
        if path.endswith("/calendar/v3/calendars/primary"):
            return httpx.Response(200, json={"id": "private-calendar"})
        if path.endswith("/drive/v3/files"):
            return httpx.Response(200, json={"files": [{"id": "private-file"}]})
        raise AssertionError(f"Unexpected probe URL: {request.method} {request.url}")

    def combined_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return refresh_handler(request)
        return probe_handler(request)

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(combined_handler), **kwargs
        ),
    )
    try:
        headers = _auth_headers()
        response = client.post(
            "/api/connectors/google/validation-runs",
            headers=headers,
            json={"force_token_refresh": True},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        failed_checks = [check for check in body["checks"] if check["status"] == "failed"]
        assert not failed_checks, [(check["key"], check["message"]) for check in failed_checks]
        assert body["status"] == "passed", body
        assert body["failed_count"] == 0
        assert body["pending_count"] == 0
        serialized = response.text
        for secret in (
            "old-secret-access-token",
            "old-secret-refresh-token",
            "rotated-secret-access-token",
            "rotated-secret-refresh-token",
            "private@example.com",
            "private-file",
        ):
            assert secret not in serialized

        with get_connection() as connection:
            account = connection.execute(
                """
                SELECT token_cipher, refresh_token_cipher, last_refresh_at
                FROM connector_accounts WHERE connector_id = ?
                """,
                (connector_id,),
            ).fetchone()
            stored = connection.execute(
                "SELECT organization_id, checks_json FROM connector_validation_runs WHERE validation_run_id = ?",
                (body["validation_run_id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT OR IGNORE INTO organizations (
                    organization_id, name, slug, created_by, created_at, updated_at
                ) VALUES ('org_validation_other', 'Other validation tenant', 'validation-other',
                          'u_admin', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
            connection.execute(
                """
                INSERT INTO connector_validation_runs (
                    validation_run_id, organization_id, provider, status, requested_by,
                    checks_json, started_at, completed_at
                ) VALUES (?, 'org_validation_other', 'google', 'passed', 'other-user', '[]', ?, ?)
                """,
                (f"cvr_other_{uuid4().hex}", datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
        assert account["last_refresh_at"]
        assert decrypt_secret(account["token_cipher"]) == "rotated-secret-access-token"
        assert decrypt_secret(account["refresh_token_cipher"]) == "rotated-secret-refresh-token"
        assert stored["organization_id"] == "org_default"
        assert "secret" not in stored["checks_json"].lower()

        listed = client.get("/api/connectors/validation-runs", headers=headers)
        assert listed.status_code == 200
        assert all(run["requested_by"] != "other-user" for run in listed.json())

        manager = _auth_headers("manager@demo.local")
        denied = client.post(
            "/api/connectors/google/validation-runs",
            headers=manager,
            json={"force_token_refresh": True},
        )
        assert denied.status_code == 403
        employee = _auth_headers("employee@demo.local")
        denied_read_only_role = client.post(
            "/api/connectors/google/validation-runs",
            headers=employee,
            json={"force_token_refresh": False},
        )
        assert denied_read_only_role.status_code == 403
    finally:
        get_settings.cache_clear()


def test_unconnected_provider_validation_is_persisted_as_incomplete() -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE connector_accounts SET status = 'disconnected' WHERE provider = 'slack' AND organization_id = 'org_default'"
        )
    response = client.post(
        "/api/connectors/slack/validation-runs",
        headers=_auth_headers(),
        json={"force_token_refresh": False},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "incomplete"
    assert {check["key"] for check in response.json()["checks"]} >= {
        "developer_app",
        "oauth_connection",
        "disconnect_lifecycle",
        "remote_revocation",
    }


def test_notion_remote_revocation_uses_current_post_contract(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_CLIENT_ID", "notion-client-id")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "notion-client-secret")
    get_settings.cache_clear()
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/oauth/revoke"
        assert request.headers["Notion-Version"] == NOTION_API_VERSION
        assert json.loads(request.content) == {"token": "notion-access-token"}
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"request_id": str(uuid4())})

    monkeypatch.setattr(
        "app.services.connectors.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    try:
        assert anyio.run(
            connector_service._revoke_remote_token, "notion", "notion-access-token"
        ) is True
    finally:
        get_settings.cache_clear()


def test_jira_and_notion_authorization_urls_follow_provider_contracts(monkeypatch) -> None:
    for provider in ("JIRA", "NOTION"):
        monkeypatch.setenv(f"{provider}_CLIENT_ID", f"{provider.lower()}-client-id")
        monkeypatch.setenv(f"{provider}_CLIENT_SECRET", f"{provider.lower()}-client-secret")
    get_settings.cache_clear()
    try:
        headers = _auth_headers()
        jira = client.post("/api/connectors/jira/authorize", headers=headers)
        notion = client.post("/api/connectors/notion/authorize", headers=headers)
        assert jira.status_code == 200, jira.text
        assert notion.status_code == 200, notion.text

        jira_query = parse_qs(urlparse(jira.json()["authorization_url"]).query)
        assert jira_query["audience"] == ["api.atlassian.com"]
        assert jira_query["prompt"] == ["consent"]
        assert "offline_access" in jira_query["scope"][0].split()
        assert "code_challenge" not in jira_query
        assert "code_challenge_method" not in jira_query

        notion_query = parse_qs(urlparse(notion.json()["authorization_url"]).query)
        assert notion_query["owner"] == ["user"]
        assert notion_query["response_type"] == ["code"]
        assert notion_query["state"]
    finally:
        get_settings.cache_clear()


def test_jira_webhook_requires_signed_bearer_and_remote_subscription_binding(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JIRA_CLIENT_ID", "jira-webhook-client")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "jira-webhook-secret")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    connector_id = f"con_jira_webhook_{uuid4().hex}"
    subscription_id = f"cws_jira_webhook_{uuid4().hex}"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO connector_accounts (
                connector_id, provider, account_label, status, scopes_json,
                token_cipher, created_by, organization_id, external_account_id,
                metadata_json, updated_at
            ) VALUES (?, 'jira', 'Jira webhook validation', 'connected', '[]', ?,
                      'u_admin', 'org_default', ?, ?, ?)
            """,
            (
                connector_id,
                encrypt_secret("jira-access-token"),
                f"jira-external-{uuid4().hex}",
                encode_json({"cloud_id": "jira-cloud-webhook"}),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO connector_webhook_subscriptions (
                subscription_id, organization_id, connector_id, provider, resource,
                remote_id, secret_cipher, registration_mode, status, created_by,
                created_at, updated_at
            ) VALUES (?, 'org_default', ?, 'jira', 'issues', '1000', ?, 'remote',
                      'active', 'u_admin', ?, ?)
            """,
            (
                subscription_id,
                connector_id,
                encrypt_secret("unused-local-secret"),
                now.isoformat(),
                now.isoformat(),
            ),
        )
    token = jwt.encode(
        {
            "aud": "jira-webhook-client",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        "jira-webhook-secret",
        algorithm="HS256",
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Atlassian-Webhook-Identifier": f"jira-delivery-{uuid4().hex}",
    }
    try:
        accepted = client.post(
            f"/api/connectors/webhooks/jira/{subscription_id}",
            headers=headers,
            json={"webhookEvent": "jira:issue_updated", "matchedWebhookIds": [1000]},
        )
        assert accepted.status_code == 200, accepted.text

        wrong_binding = client.post(
            f"/api/connectors/webhooks/jira/{subscription_id}",
            headers={**headers, "X-Atlassian-Webhook-Identifier": f"jira-delivery-{uuid4().hex}"},
            json={"webhookEvent": "jira:issue_updated", "matchedWebhookIds": [2000]},
        )
        assert wrong_binding.status_code == 401
        invalid_signature = client.post(
            f"/api/connectors/webhooks/jira/{subscription_id}",
            headers={
                "Authorization": f"Bearer {jwt.encode({'exp': int((now + timedelta(minutes=5)).timestamp())}, 'wrong-secret', algorithm='HS256')}",
                "X-Atlassian-Webhook-Identifier": f"jira-delivery-{uuid4().hex}",
            },
            json={"webhookEvent": "jira:issue_updated", "matchedWebhookIds": [1000]},
        )
        assert invalid_signature.status_code == 401
    finally:
        get_settings.cache_clear()


def test_gmail_pubsub_webhook_requires_google_oidc_identity(monkeypatch) -> None:
    service_account = "gmail-push@validation-project.iam.gserviceaccount.com"
    audience = "https://work-os.example.com/google-gmail-push"
    monkeypatch.setenv("GOOGLE_PUBSUB_SERVICE_ACCOUNT", service_account)
    monkeypatch.setenv("GOOGLE_PUBSUB_AUDIENCE", audience)
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    connector_id = f"con_google_webhook_{uuid4().hex}"
    subscription_id = f"cws_google_webhook_{uuid4().hex}"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO connector_accounts (
                connector_id, provider, account_label, status, scopes_json,
                token_cipher, created_by, organization_id, external_account_id,
                metadata_json, updated_at
            ) VALUES (?, 'google', 'Gmail push validation', 'connected', '[]', ?,
                      'u_admin', 'org_default', ?, '{}', ?)
            """,
            (
                connector_id,
                encrypt_secret("google-access-token"),
                f"google-external-{uuid4().hex}",
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO connector_webhook_subscriptions (
                subscription_id, organization_id, connector_id, provider, resource,
                secret_cipher, registration_mode, status, created_by, created_at, updated_at
            ) VALUES (?, 'org_default', ?, 'google', 'gmail', ?, 'manual', 'active',
                      'u_admin', ?, ?)
            """,
            (
                subscription_id,
                connector_id,
                encrypt_secret("local-subscription-secret"),
                now.isoformat(),
                now.isoformat(),
            ),
        )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwk.construct(private_key.public_key(), algorithm="RS256").to_dict()
    public_jwk.update({"kid": "google-validation-key", "use": "sig"})
    real_async_client = httpx.AsyncClient

    def cert_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/v3/certs"
        return httpx.Response(200, json={"keys": [public_jwk]})

    monkeypatch.setattr(
        "app.services.connectors.httpx.AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(cert_handler), **kwargs
        ),
    )

    def signed_token(*, email: str, token_audience: str) -> str:
        return jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": token_audience,
                "email": email,
                "email_verified": True,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "google-validation-key"},
        )

    try:
        accepted = client.post(
            f"/api/connectors/webhooks/google/{subscription_id}",
            headers={"Authorization": f"Bearer {signed_token(email=service_account, token_audience=audience)}"},
            json={"message": {"messageId": "gmail-push-1", "data": "ignored"}},
        )
        assert accepted.status_code == 200, accepted.text
        wrong_identity = client.post(
            f"/api/connectors/webhooks/google/{subscription_id}",
            headers={
                "Authorization": f"Bearer {signed_token(email='attacker@example.com', token_audience=audience)}"
            },
            json={"message": {"messageId": "gmail-push-2", "data": "ignored"}},
        )
        assert wrong_identity.status_code == 401
        wrong_audience = client.post(
            f"/api/connectors/webhooks/google/{subscription_id}",
            headers={
                "Authorization": f"Bearer {signed_token(email=service_account, token_audience='https://attacker.example.com')}"
            },
            json={"message": {"messageId": "gmail-push-3", "data": "ignored"}},
        )
        assert wrong_audience.status_code == 401
    finally:
        get_settings.cache_clear()
