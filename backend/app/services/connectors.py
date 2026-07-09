from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import (
    ConnectorImportRequest,
    ConnectorImportResponse,
    ConnectorRecord,
    OAuthStartResponse,
    UserContext,
)
from app.services.jobs import job_service
from app.services.rag import rag_service

PROVIDERS: dict[str, dict[str, Any]] = {
    "google": {
        "display_name": "Google Workspace",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ],
    },
    "github": {
        "display_name": "GitHub",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scopes": ["read:user", "user:email", "repo"],
    },
    "slack": {
        "display_name": "Slack",
        "auth_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "scopes": ["channels:history", "channels:read", "users:read"],
    },
    "notion": {
        "display_name": "Notion",
        "auth_url": "https://api.notion.com/v1/oauth/authorize",
        "token_url": "https://api.notion.com/v1/oauth/token",
        "scopes": [],
    },
    "jira": {
        "display_name": "Jira",
        "auth_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        "scopes": ["read:jira-work", "read:jira-user", "offline_access"],
    },
}


class ConnectorService:
    def list_connectors(self) -> list[ConnectorRecord]:
        connected = self._connected_accounts_by_provider()
        records = []
        for provider, definition in PROVIDERS.items():
            account = connected.get(provider)
            configured = self._is_configured(provider)
            records.append(
                ConnectorRecord(
                    provider=provider,
                    display_name=definition["display_name"],
                    configured=configured,
                    status="connected" if account else ("ready" if configured else "not_configured"),
                    scopes=definition["scopes"],
                    account_label=account["account_label"] if account else None,
                    connected_at=account["updated_at"] if account else None,
                )
            )
        return records

    def start_authorization(self, provider: str, user: UserContext) -> OAuthStartResponse:
        self._require_provider(provider)
        definition = PROVIDERS[provider]
        client_id = self._client_id(provider)

        if not client_id or not self._client_secret(provider):
            return OAuthStartResponse(
                provider=provider,
                configured=False,
                authorization_url=None,
                message=(
                    f"{definition['display_name']} needs {provider.upper()}_CLIENT_ID "
                    f"and {provider.upper()}_CLIENT_SECRET in .env."
                ),
            )

        state = f"oauth_{uuid4().hex}"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO oauth_states (state, provider, requested_by)
                VALUES (?, ?, ?)
                """,
                (state, provider, user.user_id),
            )

        query: dict[str, str] = {
            "client_id": client_id,
            "redirect_uri": self._redirect_uri(provider),
            "response_type": "code",
            "state": state,
        }
        scopes = definition["scopes"]
        if scopes:
            query["scope"] = " ".join(scopes)
        if provider == "google":
            query["access_type"] = "offline"
            query["prompt"] = "consent"
        if provider == "jira":
            query["audience"] = "api.atlassian.com"
            query["prompt"] = "consent"

        return OAuthStartResponse(
            provider=provider,
            configured=True,
            authorization_url=f"{definition['auth_url']}?{urlencode(query)}",
            message="Open this URL to approve the connector.",
        )

    async def complete_callback(self, provider: str, code: str, state: str) -> ConnectorRecord:
        self._require_provider(provider)
        self._consume_state(provider=provider, state=state)

        token_payload = await self._exchange_code(provider=provider, code=code)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("OAuth provider did not return an access token.")

        account_label = await self._account_label(provider=provider, token=str(access_token))
        scopes = token_payload.get("scope") or " ".join(PROVIDERS[provider]["scopes"])
        scopes_list = scopes.split() if isinstance(scopes, str) else PROVIDERS[provider]["scopes"]
        now = datetime.now(timezone.utc).isoformat()

        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO connector_accounts (
                    connector_id, provider, account_label, status, scopes_json,
                    token_cipher, refresh_token_cipher, expires_at, created_by,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"con_{uuid4().hex}",
                    provider,
                    account_label,
                    "connected",
                    encode_json(scopes_list),
                    encrypt_secret(str(access_token)),
                    encrypt_secret(token_payload.get("refresh_token")),
                    self._expires_at(token_payload),
                    "oauth_callback",
                    now,
                    now,
                ),
            )

        return next(record for record in self.list_connectors() if record.provider == provider)

    def import_items(
        self, payload: ConnectorImportRequest, user: UserContext
    ) -> ConnectorImportResponse:
        self._require_provider(payload.provider)
        job = job_service.create(
            job_type=f"{payload.provider}.import",
            detail={"provider": payload.provider, "items": len(payload.items)},
            created_by=user.user_id,
        )
        imported = []
        try:
            for item in payload.items:
                imported.append(
                    rag_service.ingest_file(
                        filename=item.filename,
                        data=item.content.encode("utf-8"),
                        classification=item.classification,
                        owner_team=item.owner_team,
                        uploaded_by=user.user_id,
                    )
                )
            job = job_service.update(
                job.job_id,
                status="completed",
                result={
                    "imported_documents": len(imported),
                    "document_ids": [document.document_id for document in imported],
                },
            )
        except Exception as exc:
            job_service.update(
                job.job_id,
                status="failed",
                result={"error": str(exc)},
            )
            raise

        return ConnectorImportResponse(job=job, imported_documents=imported)

    def _connected_accounts_by_provider(self):
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM connector_accounts
                WHERE status = 'connected'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        accounts = {}
        for row in rows:
            accounts.setdefault(row["provider"], row)
        return accounts

    def _is_configured(self, provider: str) -> bool:
        return bool(self._client_id(provider) and self._client_secret(provider))

    def _client_id(self, provider: str) -> str | None:
        return getattr(get_settings(), f"{provider}_client_id")

    def _client_secret(self, provider: str) -> str | None:
        return getattr(get_settings(), f"{provider}_client_secret")

    def _redirect_uri(self, provider: str) -> str:
        return f"{get_settings().oauth_redirect_base_url}/{provider}/callback"

    def _require_provider(self, provider: str) -> None:
        if provider not in PROVIDERS:
            raise ValueError("Unsupported connector provider.")

    def _consume_state(self, provider: str, state: str) -> None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM oauth_states WHERE state = ? AND provider = ?",
                (state, provider),
            ).fetchone()
            if row is None:
                raise ValueError("OAuth state is invalid or expired.")
            connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

    async def _exchange_code(self, provider: str, code: str) -> dict[str, Any]:
        definition = PROVIDERS[provider]
        data = {
            "client_id": self._client_id(provider),
            "client_secret": self._client_secret(provider),
            "code": code,
            "redirect_uri": self._redirect_uri(provider),
            "grant_type": "authorization_code",
        }
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(definition["token_url"], data=data, headers=headers)
        response.raise_for_status()
        return response.json()

    async def _account_label(self, provider: str, token: str) -> str:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if provider == "github":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get("https://api.github.com/user", headers=headers)
            if response.status_code == 200:
                body = response.json()
                return body.get("login") or body.get("email") or "GitHub account"
        if provider == "google":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
            if response.status_code == 200:
                body = response.json()
                return body.get("email") or body.get("name") or "Google account"
        return PROVIDERS[provider]["display_name"]

    def _expires_at(self, token_payload: dict[str, Any]) -> str | None:
        expires_in = token_payload.get("expires_in")
        if not isinstance(expires_in, int):
            return None
        return datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + expires_in,
            tz=timezone.utc,
        ).isoformat()


connector_service = ConnectorService()
