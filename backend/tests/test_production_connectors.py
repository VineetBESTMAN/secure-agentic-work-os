from __future__ import annotations

import anyio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import decode_json, encode_json, get_connection
from app.main import app
from app.services.connectors import connector_service


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _insert_account(
    provider: str,
    *,
    metadata: dict[str, object] | None = None,
    access_token: str | None = None,
    expires_at: str | None = None,
) -> str:
    connector_id = f"con_test_{provider}_{uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO connector_accounts (
                connector_id, provider, account_label, status, scopes_json,
                token_cipher, refresh_token_cipher, expires_at, created_by,
                organization_id, external_account_id, metadata_json, updated_at
            ) VALUES (?, ?, ?, 'connected', ?, ?, ?, ?, 'u_admin',
                      'org_default', ?, ?, ?)
            """,
            (
                connector_id,
                provider,
                f"{provider}-production-test",
                encode_json([]),
                encrypt_secret(access_token or f"{provider}-access-token"),
                encrypt_secret(f"{provider}-refresh-token"),
                expires_at
                or (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                f"{provider}-external-account",
                encode_json(metadata or {}),
                now,
            ),
        )
    return connector_id


def test_incremental_sync_normalizes_all_provider_resources(monkeypatch) -> None:
    connector_ids = {
        "google": _insert_account("google"),
        "slack": _insert_account("slack"),
        "github": _insert_account("github"),
        "jira": _insert_account(
            "jira", metadata={"cloud_id": "cloud-123", "site_url": "https://acme.atlassian.net"}
        ),
        "notion": _insert_account("notion"),
    }
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/gmail/v1/users/me/messages"):
            return httpx.Response(200, json={"messages": [{"id": "gmail-1"}]})
        if path.endswith("/gmail/v1/users/me/profile"):
            return httpx.Response(200, json={"historyId": "101"})
        if path.endswith("/gmail/v1/users/me/messages/gmail-1"):
            encoded = "U2VjdXJlIHJlbmV3YWwgbm90ZQ"
            return httpx.Response(
                200,
                json={
                    "id": "gmail-1",
                    "threadId": "thread-1",
                    "internalDate": "1784111400000",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Subject", "value": "Renewal mail"},
                            {"name": "From", "value": "client@example.com"},
                        ],
                        "body": {"data": encoded},
                    },
                },
            )
        if path.endswith("/calendar/v3/calendars/primary/events"):
            return httpx.Response(
                200,
                json={
                    "nextSyncToken": "calendar-cursor-1",
                    "items": [
                        {
                            "id": "calendar-1",
                            "summary": "Renewal review",
                            "description": "Review the secure renewal note.",
                            "start": {"dateTime": "2026-07-20T10:00:00Z"},
                            "end": {"dateTime": "2026-07-20T10:30:00Z"},
                            "updated": "2026-07-15T10:00:00Z",
                            "htmlLink": "https://calendar.google.com/event?eid=calendar-1",
                            "status": "confirmed",
                        }
                    ],
                },
            )
        if path.endswith("/conversations.list"):
            return httpx.Response(
                200, json={"ok": True, "channels": [{"id": "C1", "name": "ops"}]}
            )
        if path.endswith("/conversations.history"):
            return httpx.Response(
                200,
                json={"ok": True, "messages": [{"ts": "1784111400.100", "text": "Renewal is ready", "user": "U1"}]},
            )
        if str(request.url).startswith("https://api.github.com/issues"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 501,
                        "number": 5,
                        "title": "Renewal issue",
                        "body": "Review the renewal automation.",
                        "state": "open",
                        "updated_at": "2026-07-15T11:00:00Z",
                        "html_url": "https://github.com/acme/ops/issues/5",
                        "repository_url": "https://api.github.com/repos/acme/ops",
                    }
                ],
            )
        if path.endswith("/rest/api/3/search/jql"):
            return httpx.Response(
                200,
                json={
                    "issues": [
                        {
                            "id": "9001",
                            "key": "OPS-9",
                            "fields": {
                                "summary": "Renewal Jira issue",
                                "description": "Review the secure automation.",
                                "updated": "2026-07-15T12:00:00Z",
                                "status": {"name": "Open"},
                            },
                        }
                    ]
                },
            )
        if path.endswith("/v1/search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "notion-page-1",
                            "url": "https://notion.so/notion-page-1",
                            "last_edited_time": "2026-07-15T13:00:00Z",
                            "properties": {
                                "Name": {
                                    "type": "title",
                                    "title": [{"plain_text": "Renewal Notion page"}],
                                }
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected sync URL: {request.method} {request.url}")

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    headers = _auth_headers()
    requests = {
        "google": ["gmail", "calendar"],
        "slack": ["messages"],
        "github": ["issues"],
        "jira": ["issues"],
        "notion": ["pages"],
    }
    for provider, resources in requests.items():
        response = client.post(
            f"/api/connectors/{provider}/sync",
            headers=headers,
            json={"resources": resources, "classification": "internal", "owner_team": "operations"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["job"]["status"] == "completed"
        assert response.json()["job"]["result"]["items_changed"] == len(resources)

    with get_connection() as connection:
        synced = connection.execute(
            """
            SELECT provider, resource, document_id, content_hash
            FROM connector_sync_items
            WHERE connector_id IN (?, ?, ?, ?, ?)
            """,
            tuple(connector_ids.values()),
        ).fetchall()
        cursors = connection.execute(
            "SELECT cursor_cipher FROM connector_sync_states WHERE connector_id = ?",
            (connector_ids["google"],),
        ).fetchall()
    assert len(synced) == 6
    assert all(row["document_id"] and len(row["content_hash"]) == 64 for row in synced)
    assert all(row["cursor_cipher"] and "calendar-cursor-1" not in row["cursor_cipher"] for row in cursors)


def test_oauth_pkce_refresh_and_disconnect_revocation(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret")
    get_settings.cache_clear()
    real_async_client = httpx.AsyncClient
    refresh_seen = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refresh_seen
        if request.url.path.endswith("/token"):
            if b"refresh_token" in request.content:
                refresh_seen = True
                return httpx.Response(
                    200,
                    json={
                        "access_token": "google-refreshed-token",
                        "refresh_token": "google-rotated-refresh",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "google-oauth-token",
                    "refresh_token": "google-oauth-refresh",
                    "expires_in": 3600,
                    "scope": "openid email https://www.googleapis.com/auth/gmail.send",
                    "token_type": "Bearer",
                },
            )
        if request.url.path.endswith("/v1/userinfo"):
            return httpx.Response(
                200,
                json={"sub": "google-user-123", "email": "oauth@example.com", "name": "OAuth User"},
            )
        if request.url.path.endswith("/revoke"):
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected OAuth URL: {request.method} {request.url}")

    monkeypatch.setattr(
        "app.services.connectors.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    try:
        headers = _auth_headers()
        start = client.post("/api/connectors/google/authorize", headers=headers)
        assert start.status_code == 200, start.text
        query = parse_qs(urlparse(start.json()["authorization_url"]).query)
        state = query["state"][0]
        assert query["code_challenge_method"] == ["S256"]
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        with get_connection() as connection:
            oauth_state = connection.execute(
                "SELECT state, code_verifier_cipher, expires_at FROM oauth_states WHERE state = ?",
                (state_hash,),
            ).fetchone()
        assert oauth_state is not None
        assert oauth_state["state"] != state
        assert decrypt_secret(oauth_state["code_verifier_cipher"])

        callback = client.get(
            "/api/connectors/google/callback",
            params={"code": "oauth-code", "state": state},
        )
        assert callback.status_code == 200, callback.text
        assert callback.json()["status"] == "connected"
        with get_connection() as connection:
            account = connection.execute(
                """
                SELECT * FROM connector_accounts
                WHERE provider = 'google' AND external_account_id = 'google-user-123'
                ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()
            connection.execute(
                "UPDATE connector_accounts SET expires_at = ? WHERE connector_id = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), account["connector_id"]),
            )

        token = anyio.run(connector_service._access_token, "google", "org_default")
        assert token == "google-refreshed-token"
        assert refresh_seen is True
        disconnected = client.delete("/api/connectors/google", headers=headers)
        assert disconnected.status_code == 200, disconnected.text
        assert disconnected.json()["remote_revoked"] is True
        with get_connection() as connection:
            wiped = connection.execute(
                "SELECT status, token_cipher, refresh_token_cipher, revoked_at FROM connector_accounts WHERE connector_id = ?",
                (account["connector_id"],),
            ).fetchone()
        assert wiped["status"] == "disconnected"
        assert wiped["token_cipher"] is None
        assert wiped["refresh_token_cipher"] is None
        assert wiped["revoked_at"]
    finally:
        get_settings.cache_clear()


def test_all_external_mcp_actions_use_provider_backends_and_receipts(monkeypatch) -> None:
    _insert_account("google")
    _insert_account("slack")
    _insert_account("github")
    _insert_account(
        "jira", metadata={"cloud_id": "cloud-actions", "site_url": "https://acme.atlassian.net"}
    )
    _insert_account("notion")
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/send"):
            return httpx.Response(200, json={"id": "gmail-action-1", "threadId": "thread-1"})
        if path.endswith("/calendars/primary/events"):
            return httpx.Response(
                200,
                json={"id": "calendar-action-1", "status": "confirmed", "htmlLink": "https://calendar/event/1"},
            )
        if path.endswith("/chat.postMessage"):
            return httpx.Response(200, json={"ok": True, "ts": "1784111400.2", "channel": "C1"})
        if path.endswith("/repos/acme/ops/issues"):
            return httpx.Response(
                201,
                json={"id": 701, "number": 7, "state": "open", "html_url": "https://github.com/acme/ops/issues/7"},
            )
        if path.endswith("/rest/api/3/issue"):
            return httpx.Response(201, json={"id": "8001", "key": "OPS-8"})
        if path.endswith("/v1/pages"):
            return httpx.Response(200, json={"id": "notion-action-1", "url": "https://notion.so/action-1"})
        raise AssertionError(f"Unexpected action URL: {request.method} {request.url}")

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    actions = [
        ("send_email", {"to": "client@example.com", "subject": "Approved", "body": "Ready"}),
        (
            "create_calendar_event",
            {
                "summary": "Approved review",
                "description": "Review",
                "start": "2026-07-20T10:00:00+00:00",
                "end": "2026-07-20T10:30:00+00:00",
                "timezone": "UTC",
                "attendees": [],
            },
        ),
        ("send_slack_message", {"channel": "C1", "text": "Approved update"}),
        (
            "create_github_issue",
            {"repository": "acme/ops", "title": "Approved issue", "body": "Ready", "labels": []},
        ),
        (
            "create_jira_issue",
            {"project_key": "OPS", "summary": "Approved issue", "description": "Ready", "issue_type": "Task"},
        ),
        (
            "create_notion_page",
            {"parent_id": "12345678-abcd", "parent_type": "page_id", "title": "Approved page", "content": "Ready"},
        ),
    ]
    admin_headers = _auth_headers()
    manager_headers = _auth_headers("manager@demo.local")
    execution_ids = []
    for tool_name, arguments in actions:
        requested = client.post(
            "/api/mcp/executions",
            headers=admin_headers,
            json={"tool_name": tool_name, "arguments": arguments},
        )
        assert requested.status_code == 200, requested.text
        execution = requested.json()
        assert execution["status"] == "pending_approval"
        approved = client.post(
            f"/api/approvals/{execution['approval_id']}/decision",
            headers=manager_headers,
            json={"approved": True},
        )
        assert approved.status_code == 200, approved.text
        completed = client.get(
            f"/api/mcp/executions/{execution['execution_id']}", headers=admin_headers
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["result"]["delivery_mode"] == "provider"
        execution_ids.append(execution["execution_id"])

    with get_connection() as connection:
        receipts = connection.execute(
            "SELECT execution_id, status, external_id FROM connector_action_receipts"
        ).fetchall()
    by_execution = {row["execution_id"]: row for row in receipts}
    assert set(execution_ids) <= set(by_execution)
    assert all(by_execution[execution_id]["status"] == "completed" for execution_id in execution_ids)
    assert all(by_execution[execution_id]["external_id"] for execution_id in execution_ids)


def test_verified_webhook_registration_delivery_and_replay_protection(monkeypatch) -> None:
    connector_id = _insert_account("github")
    real_async_client = httpx.AsyncClient

    def registration_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/repos/acme/ops/hooks")
        return httpx.Response(201, json={"id": 321, "url": "https://api.github.com/repos/acme/ops/hooks/321"})

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(registration_handler), **kwargs
        ),
    )
    headers = _auth_headers()
    created = client.post(
        "/api/connectors/github/webhook-subscriptions",
        headers=headers,
        json={"resource": "issues", "target": "acme/ops", "register_remote": True},
    )
    assert created.status_code == 201, created.text
    subscription = created.json()
    assert subscription["registration_mode"] == "remote"
    assert subscription["remote_id"] == "321"
    assert subscription["secret"]
    with get_connection() as connection:
        stored = connection.execute(
            "SELECT organization_id, secret_cipher FROM connector_webhook_subscriptions WHERE subscription_id = ?",
            (subscription["subscription_id"],),
        ).fetchone()
    assert stored["organization_id"] == "org_default"
    assert stored["secret_cipher"] != subscription["secret"]

    raw_body = json.dumps({"action": "opened", "issue": {"id": 1}}, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(
        subscription["secret"].encode(), raw_body, hashlib.sha256
    ).hexdigest()
    webhook_headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": "delivery-123",
        "X-GitHub-Event": "issues",
    }
    delivered = client.post(
        urlparse(subscription["callback_url"]).path,
        content=raw_body,
        headers=webhook_headers,
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["duplicate"] is False
    replay = client.post(
        urlparse(subscription["callback_url"]).path,
        content=raw_body,
        headers=webhook_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True

    rejected = client.post(
        urlparse(subscription["callback_url"]).path,
        content=b'{"action":"edited"}',
        headers={**webhook_headers, "X-GitHub-Delivery": "delivery-124", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert rejected.status_code == 401
    with get_connection() as connection:
        state = connection.execute(
            "SELECT status FROM connector_sync_states WHERE connector_id = ? AND resource = 'issues'",
            (connector_id,),
        ).fetchone()
        deliveries = connection.execute(
            "SELECT COUNT(*) FROM connector_webhook_deliveries WHERE subscription_id = ?",
            (subscription["subscription_id"],),
        ).fetchone()[0]
    assert state["status"] == "pending"
    assert deliveries == 1
