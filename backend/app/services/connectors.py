from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import (
    ConnectorImportRequest,
    ConnectorImportResponse,
    ConnectorRecord,
    GoogleDriveFileListResponse,
    GoogleDriveFileRecord,
    GoogleDriveImportRequest,
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

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_MAX_IMPORT_BYTES = 10 * 1024 * 1024
SUPPORTED_RAG_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".eml", ".pdf", ".docx"}

GOOGLE_WORKSPACE_EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}

DRIVE_MIME_EXTENSIONS = {
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "message/rfc822": ".eml",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/plain": ".txt",
}


class ConnectorService:
    def list_connectors(
        self, organization_id: str = "org_default"
    ) -> list[ConnectorRecord]:
        connected = self._connected_accounts_by_provider(organization_id)
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
                INSERT INTO oauth_states (state, provider, requested_by, organization_id)
                VALUES (?, ?, ?, ?)
                """,
                (state, provider, user.user_id, user.organization_id),
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
        requested_by, organization_id = self._consume_state(provider=provider, state=state)

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
                    created_at, updated_at, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    requested_by,
                    now,
                    now,
                    organization_id,
                ),
            )

        return next(
            record
            for record in self.list_connectors(organization_id)
            if record.provider == provider
        )

    def import_items(
        self, payload: ConnectorImportRequest, user: UserContext
    ) -> ConnectorImportResponse:
        self._require_provider(payload.provider)
        job = job_service.create(
            job_type=f"{payload.provider}.import",
            detail={"provider": payload.provider, "items": len(payload.items)},
            created_by=user.user_id,
            organization_id=user.organization_id,
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
                        organization_id=user.organization_id,
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

    async def list_google_drive_files(
        self,
        search: str | None,
        page_size: int,
        page_token: str | None,
        organization_id: str = "org_default",
    ) -> GoogleDriveFileListResponse:
        token = await self._access_token(
            provider="google", organization_id=organization_id
        )
        params: dict[str, str | int | bool] = {
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink)",
            "includeItemsFromAllDrives": True,
            "orderBy": "modifiedTime desc,name",
            "pageSize": max(1, min(page_size, 100)),
            "q": self._drive_query(search),
            "spaces": "drive",
            "supportsAllDrives": True,
        }
        if page_token:
            params["pageToken"] = page_token

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files",
                headers=self._bearer_headers(token),
                params=params,
            )
        response.raise_for_status()
        body = response.json()
        return GoogleDriveFileListResponse(
            files=[
                self._drive_file_record(file_data)
                for file_data in body.get("files", [])
                if file_data.get("mimeType") != DRIVE_FOLDER_MIME_TYPE
            ],
            next_page_token=body.get("nextPageToken"),
        )

    async def import_google_drive_files(
        self,
        payload: GoogleDriveImportRequest,
        user: UserContext,
    ) -> ConnectorImportResponse:
        token = await self._access_token(
            provider="google", organization_id=user.organization_id
        )
        job = job_service.create(
            job_type="google.drive_import",
            detail={"provider": "google", "file_ids": payload.file_ids},
            created_by=user.user_id,
            organization_id=user.organization_id,
        )
        imported = []
        try:
            for file_id in payload.file_ids:
                metadata = await self._google_drive_file_metadata(token=token, file_id=file_id)
                filename, data = await self._google_drive_file_content(
                    token=token,
                    metadata=metadata,
                )
                imported.append(
                    rag_service.ingest_file(
                        filename=filename,
                        data=data,
                        classification=payload.classification,
                        owner_team=payload.owner_team,
                        uploaded_by=user.user_id,
                        organization_id=user.organization_id,
                    )
                )
            job = job_service.update(
                job.job_id,
                status="completed",
                result={
                    "imported_documents": len(imported),
                    "document_ids": [document.document_id for document in imported],
                    "source_file_ids": payload.file_ids,
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

    def _connected_accounts_by_provider(self, organization_id: str):
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM connector_accounts
                WHERE status = 'connected' AND organization_id = ?
                ORDER BY updated_at DESC
                """,
                (organization_id,),
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

    def _consume_state(self, provider: str, state: str) -> tuple[str, str]:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM oauth_states WHERE state = ? AND provider = ?",
                (state, provider),
            ).fetchone()
            if row is None:
                raise ValueError("OAuth state is invalid or expired.")
            connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        return row["requested_by"], row["organization_id"]

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

    async def _access_token(
        self, provider: str, organization_id: str = "org_default"
    ) -> str:
        account = self._connected_account(
            provider=provider, organization_id=organization_id
        )
        token = decrypt_secret(account["token_cipher"])
        if not token:
            raise ValueError(f"{PROVIDERS[provider]['display_name']} is not connected.")

        if provider == "google" and self._token_expires_soon(account["expires_at"]):
            refresh_token = decrypt_secret(account["refresh_token_cipher"])
            if refresh_token:
                token = await self._refresh_access_token(
                    provider=provider,
                    connector_id=account["connector_id"],
                    refresh_token=refresh_token,
                )
        return token

    def _connected_account(self, provider: str, organization_id: str):
        self._require_provider(provider)
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM connector_accounts
                WHERE provider = ? AND status = 'connected' AND organization_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (provider, organization_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"{PROVIDERS[provider]['display_name']} is not connected.")
        return row

    async def _refresh_access_token(
        self,
        provider: str,
        connector_id: str,
        refresh_token: str,
    ) -> str:
        if not self._client_id(provider) or not self._client_secret(provider):
            raise ValueError(
                f"{PROVIDERS[provider]['display_name']} needs OAuth credentials to refresh access."
            )

        data = {
            "client_id": self._client_id(provider),
            "client_secret": self._client_secret(provider),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(PROVIDERS[provider]["token_url"], data=data)
        response.raise_for_status()
        token_payload = response.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("OAuth provider did not return a refreshed access token.")

        scopes = token_payload.get("scope")
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_accounts
                SET token_cipher = ?,
                    refresh_token_cipher = ?,
                    scopes_json = COALESCE(?, scopes_json),
                    expires_at = ?,
                    updated_at = ?
                WHERE connector_id = ?
                """,
                (
                    encrypt_secret(str(access_token)),
                    encrypt_secret(token_payload.get("refresh_token") or refresh_token),
                    encode_json(scopes.split()) if isinstance(scopes, str) else None,
                    self._expires_at(token_payload),
                    now,
                    connector_id,
                ),
            )
        return str(access_token)

    def _token_expires_soon(self, expires_at: Any) -> bool:
        if not expires_at:
            return False
        if isinstance(expires_at, datetime):
            expiry = expires_at
        else:
            try:
                expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError:
                return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= datetime.now(timezone.utc) + timedelta(minutes=2)

    def _bearer_headers(self, token: str) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    def _drive_query(self, search: str | None) -> str:
        base = f"trashed = false and mimeType != '{DRIVE_FOLDER_MIME_TYPE}'"
        if not search or not search.strip():
            return base
        escaped = search.strip().replace("\\", "\\\\").replace("'", "\\'")
        return f"{base} and (name contains '{escaped}' or fullText contains '{escaped}')"

    def _drive_file_record(self, file_data: dict[str, Any]) -> GoogleDriveFileRecord:
        size = file_data.get("size")
        return GoogleDriveFileRecord(
            file_id=file_data["id"],
            name=file_data.get("name") or "Untitled Drive file",
            mime_type=file_data.get("mimeType") or "application/octet-stream",
            modified_time=file_data.get("modifiedTime"),
            size=int(size) if isinstance(size, str) and size.isdigit() else None,
            web_view_link=file_data.get("webViewLink"),
            importable=self._is_drive_file_importable(file_data),
        )

    async def _google_drive_file_metadata(self, token: str, file_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files/{file_id}",
                headers=self._bearer_headers(token),
                params={
                    "fields": "id,name,mimeType,modifiedTime,size,webViewLink",
                    "supportsAllDrives": True,
                },
            )
        response.raise_for_status()
        metadata = response.json()
        if not self._is_drive_file_importable(metadata):
            raise ValueError(f"{metadata.get('name', file_id)} is not a supported RAG file type.")
        size = metadata.get("size")
        if isinstance(size, str) and size.isdigit() and int(size) > DRIVE_MAX_IMPORT_BYTES:
            raise ValueError("Drive file is larger than the 10 MB import limit.")
        return metadata

    async def _google_drive_file_content(
        self,
        token: str,
        metadata: dict[str, Any],
    ) -> tuple[str, bytes]:
        mime_type = metadata.get("mimeType") or "application/octet-stream"
        filename = self._drive_import_filename(metadata)
        headers = self._bearer_headers(token)

        if mime_type in GOOGLE_WORKSPACE_EXPORTS:
            export_mime_type, _ = GOOGLE_WORKSPACE_EXPORTS[mime_type]
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{DRIVE_API_BASE}/files/{metadata['id']}/export",
                    headers=headers,
                    params={"mimeType": export_mime_type},
                )
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{DRIVE_API_BASE}/files/{metadata['id']}",
                    headers=headers,
                    params={"alt": "media", "supportsAllDrives": True},
                )

        response.raise_for_status()
        if len(response.content) > DRIVE_MAX_IMPORT_BYTES:
            raise ValueError("Drive file is larger than the 10 MB import limit.")
        return filename, response.content

    def _drive_import_filename(self, metadata: dict[str, Any]) -> str:
        name = metadata.get("name") or metadata.get("id") or "google-drive-file"
        mime_type = metadata.get("mimeType") or "application/octet-stream"
        suffix = Path(name).suffix.lower()
        if suffix in SUPPORTED_RAG_SUFFIXES:
            return name
        if mime_type in GOOGLE_WORKSPACE_EXPORTS:
            return f"{name}{GOOGLE_WORKSPACE_EXPORTS[mime_type][1]}"
        if mime_type in DRIVE_MIME_EXTENSIONS:
            return f"{name}{DRIVE_MIME_EXTENSIONS[mime_type]}"
        return name

    def _is_drive_file_importable(self, metadata: dict[str, Any]) -> bool:
        mime_type = metadata.get("mimeType") or ""
        if mime_type in GOOGLE_WORKSPACE_EXPORTS:
            return True
        return Path(self._drive_import_filename(metadata)).suffix.lower() in SUPPORTED_RAG_SUFFIXES


connector_service = ConnectorService()
