import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
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
    ConnectorDisconnectResponse,
    ConnectorSyncRequest,
    ConnectorSyncResponse,
    ConnectorSyncStateRecord,
    GoogleDriveFileListResponse,
    GoogleDriveFileRecord,
    GoogleDriveImportRequest,
    OAuthStartResponse,
    UserContext,
    WebhookDeliveryResponse,
    WebhookSubscriptionCreateRequest,
    WebhookSubscriptionRecord,
)
from app.services.connector_providers import (
    execute_provider_action,
    provider_actions,
    provider_resources,
    register_provider_webhook,
    sync_provider_resource,
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
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        "pkce": True,
    },
    "github": {
        "display_name": "GitHub",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scopes": ["read:user", "user:email", "repo"],
        "pkce": True,
    },
    "slack": {
        "display_name": "Slack",
        "auth_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "scopes": [
            "channels:history",
            "channels:read",
            "chat:write",
            "groups:history",
            "groups:read",
            "users:read",
        ],
        "pkce": False,
    },
    "notion": {
        "display_name": "Notion",
        "auth_url": "https://api.notion.com/v1/oauth/authorize",
        "token_url": "https://api.notion.com/v1/oauth/token",
        "scopes": [],
        "pkce": False,
    },
    "jira": {
        "display_name": "Jira",
        "auth_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        "scopes": [
            "read:jira-work",
            "read:jira-user",
            "write:jira-work",
            "manage:jira-webhook",
            "offline_access",
        ],
        "pkce": True,
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
        accounts = self._latest_accounts_by_provider(organization_id)
        records = []
        for provider, definition in PROVIDERS.items():
            account = accounts.get(provider)
            configured = self._is_configured(provider)
            account_status = account["status"] if account else None
            if account_status == "connected":
                status = "error" if account["last_error"] else "connected"
            elif account_status in {"disconnected", "revoked"}:
                status = "disconnected"
            else:
                status = "ready" if configured else "not_configured"
            records.append(
                ConnectorRecord(
                    provider=provider,
                    display_name=definition["display_name"],
                    configured=configured,
                    status=status,
                    scopes=definition["scopes"],
                    connector_id=account["connector_id"] if account else None,
                    account_label=account["account_label"] if account else None,
                    connected_at=account["updated_at"] if account else None,
                    expires_at=self._string_or_none(account["expires_at"]) if account else None,
                    last_sync_at=self._string_or_none(account["last_sync_at"]) if account else None,
                    last_error=account["last_error"] if account else None,
                    resources=provider_resources(provider),
                    actions=provider_actions(provider),
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

        state = secrets.token_urlsafe(48)
        stored_state = self._hash_value(state)
        code_verifier = secrets.token_urlsafe(64) if definition.get("pkce") else None
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=get_settings().connector_oauth_state_ttl_seconds)
        ).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO oauth_states (
                    state, provider, requested_by, organization_id,
                    code_verifier_cipher, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_state,
                    provider,
                    user.user_id,
                    user.organization_id,
                    encrypt_secret(code_verifier),
                    expires_at,
                ),
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
        if code_verifier:
            query["code_challenge"] = self._pkce_challenge(code_verifier)
            query["code_challenge_method"] = "S256"
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
        requested_by, organization_id, code_verifier = self._consume_state(
            provider=provider, state=state
        )

        token_payload = await self._exchange_code(
            provider=provider, code=code, code_verifier=code_verifier
        )
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("OAuth provider did not return an access token.")

        account_label, external_account_id, metadata = await self._account_identity(
            provider=provider,
            token=str(access_token),
            token_payload=token_payload,
        )
        scopes = token_payload.get("scope") or " ".join(PROVIDERS[provider]["scopes"])
        scopes_list = (
            re.split(r"[\s,]+", scopes.strip())
            if isinstance(scopes, str)
            else PROVIDERS[provider]["scopes"]
        )
        scopes_list = [scope for scope in scopes_list if scope]
        now = datetime.now(timezone.utc).isoformat()

        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM connector_accounts
                WHERE provider = ? AND organization_id = ?
                  AND (external_account_id = ? OR account_label = ?)
                ORDER BY updated_at DESC LIMIT 1
                """,
                (provider, organization_id, external_account_id, account_label),
            ).fetchone()
            refresh_cipher = encrypt_secret(token_payload.get("refresh_token"))
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO connector_accounts (
                        connector_id, provider, account_label, status, scopes_json,
                        token_cipher, refresh_token_cipher, expires_at, created_by,
                        created_at, updated_at, organization_id, external_account_id,
                        metadata_json, token_type, refresh_expires_at, revoked_at,
                        last_error
                    )
                    VALUES (?, ?, ?, 'connected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        f"con_{uuid4().hex}",
                        provider,
                        account_label,
                        encode_json(scopes_list),
                        encrypt_secret(str(access_token)),
                        refresh_cipher,
                        self._expires_at(token_payload),
                        requested_by,
                        now,
                        now,
                        organization_id,
                        external_account_id,
                        encode_json(metadata),
                        token_payload.get("token_type"),
                        self._refresh_expires_at(token_payload),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE connector_accounts
                    SET account_label = ?, status = 'connected', scopes_json = ?,
                        token_cipher = ?,
                        refresh_token_cipher = COALESCE(?, refresh_token_cipher),
                        expires_at = ?, external_account_id = ?, metadata_json = ?,
                        token_type = ?, refresh_expires_at = ?, revoked_at = NULL,
                        last_error = NULL, updated_at = ?
                    WHERE connector_id = ? AND organization_id = ?
                    """,
                    (
                        account_label,
                        encode_json(scopes_list),
                        encrypt_secret(str(access_token)),
                        refresh_cipher,
                        self._expires_at(token_payload),
                        external_account_id,
                        encode_json(metadata),
                        token_payload.get("token_type"),
                        self._refresh_expires_at(token_payload),
                        now,
                        existing["connector_id"],
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

    def list_sync_states(
        self, organization_id: str = "org_default"
    ) -> list[ConnectorSyncStateRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM connector_sync_states
                WHERE organization_id = ?
                ORDER BY provider, resource
                """,
                (organization_id,),
            ).fetchall()
        return [self._row_to_sync_state(row) for row in rows]

    async def sync_connector(
        self,
        *,
        provider: str,
        payload: ConnectorSyncRequest,
        user: UserContext,
    ) -> ConnectorSyncResponse:
        self._require_provider(provider)
        resources = payload.resources or provider_resources(provider)
        unsupported = sorted(set(resources) - set(provider_resources(provider)))
        if unsupported:
            raise ValueError(
                f"Unsupported {provider} sync resources: {', '.join(unsupported)}"
            )
        account = self._connected_account(provider, user.organization_id)
        job = job_service.create(
            job_type=f"{provider}.incremental_sync",
            detail={"provider": provider, "resources": resources},
            created_by=user.user_id,
            organization_id=user.organization_id,
        )
        job_service.update(job.job_id, status="running", result={"progress": 5})
        total_seen = 0
        total_changed = 0
        try:
            token = await self._access_token(provider, user.organization_id)
            metadata = decode_json(account["metadata_json"], {})
            for resource in resources:
                state = self._ensure_sync_state(
                    account=account,
                    provider=provider,
                    resource=resource,
                    organization_id=user.organization_id,
                )
                cursor = decrypt_secret(state["cursor_cipher"])
                self._set_sync_state_running(state["sync_state_id"])
                try:
                    batch = await sync_provider_resource(
                        provider=provider,
                        resource=resource,
                        access_token=token,
                        cursor=cursor,
                        account_metadata=metadata,
                    )
                    changed = 0
                    for item in batch.items:
                        if self._persist_sync_item(
                            account=account,
                            provider=provider,
                            resource=resource,
                            item=item,
                            payload=payload,
                            user=user,
                        ):
                            changed += 1
                    self._complete_sync_state(
                        sync_state_id=state["sync_state_id"],
                        cursor=batch.cursor,
                        items_seen=len(batch.items),
                        items_changed=changed,
                    )
                    total_seen += len(batch.items)
                    total_changed += changed
                except Exception as exc:
                    self._fail_sync_state(state["sync_state_id"], str(exc))
                    raise
            now = datetime.now(timezone.utc).isoformat()
            with get_connection() as connection:
                connection.execute(
                    """
                    UPDATE connector_accounts
                    SET last_sync_at = ?, last_error = NULL, updated_at = ?
                    WHERE connector_id = ? AND organization_id = ?
                    """,
                    (now, now, account["connector_id"], user.organization_id),
                )
            job = job_service.update(
                job.job_id,
                status="completed",
                result={
                    "progress": 100,
                    "resources": resources,
                    "items_seen": total_seen,
                    "items_changed": total_changed,
                },
            )
        except Exception as exc:
            message = self._safe_provider_error(exc)
            self._mark_account_error(
                account["connector_id"], user.organization_id, message
            )
            job_service.fail(job.job_id, message)
            raise ValueError(message) from exc
        return ConnectorSyncResponse(
            job=job,
            states=[
                state
                for state in self.list_sync_states(user.organization_id)
                if state.connector_id == account["connector_id"]
            ],
        )

    async def disconnect(
        self, provider: str, organization_id: str
    ) -> ConnectorDisconnectResponse:
        account = self._connected_account(provider, organization_id)
        access_token = decrypt_secret(account["token_cipher"])
        remote_revoked = False
        warning = ""
        try:
            if access_token:
                remote_revoked = await self._revoke_remote_token(provider, access_token)
        except Exception as exc:
            warning = self._safe_provider_error(exc)
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_accounts
                SET status = 'disconnected', token_cipher = NULL,
                    refresh_token_cipher = NULL, expires_at = NULL,
                    refresh_expires_at = NULL, revoked_at = ?, last_error = ?,
                    updated_at = ?
                WHERE connector_id = ? AND organization_id = ?
                """,
                (
                    now,
                    warning or None,
                    now,
                    account["connector_id"],
                    organization_id,
                ),
            )
        message = "Local credentials were securely removed."
        if remote_revoked:
            message = "Provider access was revoked and local credentials were securely removed."
        elif warning:
            message += f" Remote revocation could not be confirmed: {warning}"
        return ConnectorDisconnectResponse(
            provider=provider,
            remote_revoked=remote_revoked,
            message=message,
        )

    def execute_action(
        self,
        *,
        provider: str,
        action: str,
        arguments: dict[str, object],
        user: UserContext,
        execution_id: str,
    ) -> dict[str, object]:
        self._require_provider(provider)
        request_hash = self._hash_value(
            json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM connector_action_receipts
                WHERE execution_id = ? AND organization_id = ?
                """,
                (execution_id, user.organization_id),
            ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash or existing["action"] != action:
                raise ValueError("The provider action receipt is bound to different arguments.")
            if existing["status"] == "completed":
                return decode_json(existing["result_json"], {})
            raise ValueError(
                "The provider action already has a non-terminal or failed receipt; "
                "manual reconciliation is required before retrying."
            )

        token, account = self._access_token_sync(provider, user.organization_id)
        receipt_id = f"car_{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO connector_action_receipts (
                    receipt_id, organization_id, connector_id, execution_id,
                    provider, action, request_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    receipt_id,
                    user.organization_id,
                    account["connector_id"],
                    execution_id,
                    provider,
                    action,
                    request_hash,
                    now,
                    now,
                ),
            )
        try:
            result = execute_provider_action(
                provider=provider,
                action=action,
                access_token=token,
                account_metadata=decode_json(account["metadata_json"], {}),
                arguments=arguments,
                idempotency_key=execution_id,
            )
            with get_connection() as connection:
                connection.execute(
                    """
                    UPDATE connector_action_receipts
                    SET status = 'completed', external_id = ?, result_json = ?,
                        updated_at = ?
                    WHERE receipt_id = ? AND organization_id = ?
                    """,
                    (
                        str(result.get("external_id") or "") or None,
                        encode_json(result),
                        datetime.now(timezone.utc).isoformat(),
                        receipt_id,
                        user.organization_id,
                    ),
                )
            return result
        except Exception as exc:
            message = self._safe_provider_error(exc)
            with get_connection() as connection:
                connection.execute(
                    """
                    UPDATE connector_action_receipts
                    SET status = 'failed', error = ?, updated_at = ?
                    WHERE receipt_id = ? AND organization_id = ?
                    """,
                    (
                        message,
                        datetime.now(timezone.utc).isoformat(),
                        receipt_id,
                        user.organization_id,
                    ),
                )
            self._mark_account_error(
                account["connector_id"], user.organization_id, message
            )
            raise ValueError(message) from exc

    async def create_webhook_subscription(
        self,
        *,
        provider: str,
        payload: WebhookSubscriptionCreateRequest,
        user: UserContext,
    ) -> WebhookSubscriptionRecord:
        self._require_provider(provider)
        if payload.resource not in provider_resources(provider):
            raise ValueError(
                f"{payload.resource} is not a supported {provider} webhook resource."
            )
        account = self._connected_account(provider, user.organization_id)
        subscription_id = f"whs_{uuid4().hex}"
        secret = secrets.token_urlsafe(48)
        callback_url = self._webhook_callback_url(provider, subscription_id)
        remote_id = None
        registration_mode = "manual"
        expires_at = None
        if payload.register_remote:
            token = await self._access_token(provider, user.organization_id)
            remote_id, registration_mode, remote_expiry = await register_provider_webhook(
                provider=provider,
                resource=payload.resource,
                target=payload.target,
                callback_url=callback_url,
                secret=secret,
                access_token=token,
                account_metadata=decode_json(account["metadata_json"], {}),
            )
            expires_at = self._normalize_remote_expiry(remote_expiry)
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO connector_webhook_subscriptions (
                    subscription_id, organization_id, connector_id, provider,
                    resource, target, remote_id, secret_cipher,
                    registration_mode, status, expires_at, created_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    subscription_id,
                    user.organization_id,
                    account["connector_id"],
                    provider,
                    payload.resource,
                    payload.target,
                    remote_id,
                    encrypt_secret(secret),
                    registration_mode,
                    expires_at,
                    user.user_id,
                    now,
                    now,
                ),
            )
        record = self.get_webhook_subscription(
            subscription_id, user.organization_id
        )
        return record.model_copy(update={"secret": secret})

    def list_webhook_subscriptions(
        self, organization_id: str
    ) -> list[WebhookSubscriptionRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM connector_webhook_subscriptions
                WHERE organization_id = ?
                ORDER BY created_at DESC
                """,
                (organization_id,),
            ).fetchall()
        return [self._row_to_webhook(row) for row in rows]

    def get_webhook_subscription(
        self, subscription_id: str, organization_id: str
    ) -> WebhookSubscriptionRecord:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM connector_webhook_subscriptions
                WHERE subscription_id = ? AND organization_id = ?
                """,
                (subscription_id, organization_id),
            ).fetchone()
        if row is None:
            raise ValueError("Webhook subscription not found.")
        return self._row_to_webhook(row)

    def revoke_webhook_subscription(
        self, subscription_id: str, organization_id: str
    ) -> WebhookSubscriptionRecord:
        self.get_webhook_subscription(subscription_id, organization_id)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_webhook_subscriptions
                SET status = 'revoked', updated_at = ?
                WHERE subscription_id = ? AND organization_id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    subscription_id,
                    organization_id,
                ),
            )
        return self.get_webhook_subscription(subscription_id, organization_id)

    def receive_webhook(
        self,
        *,
        provider: str,
        subscription_id: str,
        raw_body: bytes,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> WebhookDeliveryResponse:
        self._require_provider(provider)
        with get_connection() as connection:
            subscription = connection.execute(
                """
                SELECT * FROM connector_webhook_subscriptions
                WHERE subscription_id = ? AND provider = ? AND status = 'active'
                """,
                (subscription_id, provider),
            ).fetchone()
        if subscription is None:
            raise ValueError("Webhook subscription is not active.")
        secret = decrypt_secret(subscription["secret_cipher"])
        if not secret or not self._verify_webhook_signature(
            provider=provider, secret=secret, raw_body=raw_body, headers=headers
        ):
            raise PermissionError("Webhook signature verification failed.")
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        external_delivery_id = self._external_delivery_id(
            provider=provider,
            headers=headers,
            payload=payload,
            payload_hash=payload_hash,
        )
        event_type = self._webhook_event_type(provider, headers, payload)
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT delivery_id FROM connector_webhook_deliveries
                WHERE subscription_id = ? AND external_delivery_id = ?
                """,
                (subscription_id, external_delivery_id),
            ).fetchone()
            if existing is not None:
                return WebhookDeliveryResponse(
                    duplicate=True,
                    delivery_id=existing["delivery_id"],
                    sync_requested=False,
                    challenge=self._webhook_challenge(payload),
                )
            delivery_id = f"whd_{uuid4().hex}"
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO connector_webhook_deliveries (
                    delivery_id, organization_id, subscription_id, provider,
                    external_delivery_id, event_type, payload_hash,
                    signature_valid, processed_at, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    subscription["organization_id"],
                    subscription_id,
                    provider,
                    external_delivery_id,
                    event_type,
                    payload_hash,
                    True,
                    now,
                    now,
                ),
            )
            sync_state = connection.execute(
                """
                SELECT sync_state_id FROM connector_sync_states
                WHERE connector_id = ? AND resource = ?
                """,
                (subscription["connector_id"], subscription["resource"]),
            ).fetchone()
            if sync_state is None:
                connection.execute(
                    """
                    INSERT INTO connector_sync_states (
                        sync_state_id, organization_id, connector_id, provider,
                        resource, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        f"css_{uuid4().hex}",
                        subscription["organization_id"],
                        subscription["connector_id"],
                        provider,
                        subscription["resource"],
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE connector_sync_states SET status = 'pending', updated_at = ?
                    WHERE sync_state_id = ?
                    """,
                    (now, sync_state["sync_state_id"]),
                )
        return WebhookDeliveryResponse(
            delivery_id=delivery_id,
            challenge=self._webhook_challenge(payload),
        )

    def _latest_accounts_by_provider(self, organization_id: str):
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM connector_accounts
                WHERE organization_id = ?
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

    def _consume_state(self, provider: str, state: str) -> tuple[str, str, str | None]:
        stored_state = self._hash_value(state)
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM oauth_states
                WHERE state IN (?, ?) AND provider = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (stored_state, state, provider),
            ).fetchone()
            if row is None:
                raise ValueError("OAuth state is invalid or expired.")
            connection.execute("DELETE FROM oauth_states WHERE state = ?", (row["state"],))
        expires_at = self._parse_datetime(row["expires_at"])
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            raise ValueError("OAuth state is invalid or expired.")
        return (
            row["requested_by"],
            row["organization_id"],
            decrypt_secret(row["code_verifier_cipher"]),
        )

    async def _exchange_code(
        self, provider: str, code: str, code_verifier: str | None
    ) -> dict[str, Any]:
        definition = PROVIDERS[provider]
        data = {
            "client_id": self._client_id(provider),
            "client_secret": self._client_secret(provider),
            "code": code,
            "redirect_uri": self._redirect_uri(provider),
            "grant_type": "authorization_code",
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        headers = {"Accept": "application/json"}
        request_kwargs: dict[str, object] = {"data": data, "headers": headers}
        if provider in {"jira"}:
            request_kwargs = {"json": data, "headers": headers}
        elif provider == "notion":
            credentials = base64.b64encode(
                f"{self._client_id(provider)}:{self._client_secret(provider)}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"
            notion_data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri(provider),
            }
            request_kwargs = {"json": notion_data, "headers": headers}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(definition["token_url"], **request_kwargs)
        response.raise_for_status()
        payload = response.json()
        if provider == "slack" and not payload.get("ok"):
            raise ValueError(
                f"Slack OAuth failed: {payload.get('error') or 'unknown_error'}"
            )
        if payload.get("error") and not payload.get("access_token"):
            raise ValueError(f"OAuth provider rejected the code: {payload.get('error')}")
        return payload

    async def _account_identity(
        self, provider: str, token: str, token_payload: dict[str, Any]
    ) -> tuple[str, str, dict[str, object]]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if provider == "github":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get("https://api.github.com/user", headers=headers)
            if response.status_code == 200:
                body = response.json()
                label = body.get("login") or body.get("email") or "GitHub account"
                return str(label), str(body.get("id") or label), {
                    "login": body.get("login") or "",
                    "html_url": body.get("html_url") or "",
                }
        if provider == "google":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
            if response.status_code == 200:
                body = response.json()
                label = body.get("email") or body.get("name") or "Google account"
                return str(label), str(body.get("sub") or label), {
                    "email": body.get("email") or "",
                    "name": body.get("name") or "",
                }
        if provider == "slack":
            team = token_payload.get("team") or {}
            user = token_payload.get("authed_user") or {}
            label = team.get("name") or token_payload.get("app_id") or "Slack workspace"
            external_id = team.get("id") or user.get("id") or label
            return str(label), str(external_id), {
                "team_id": team.get("id") or "",
                "team_name": team.get("name") or "",
                "user_id": user.get("id") or "",
            }
        if provider == "notion":
            label = token_payload.get("workspace_name") or "Notion workspace"
            external_id = token_payload.get("workspace_id") or token_payload.get("bot_id") or label
            return str(label), str(external_id), {
                "workspace_id": token_payload.get("workspace_id") or "",
                "workspace_name": token_payload.get("workspace_name") or "",
                "workspace_icon": token_payload.get("workspace_icon") or "",
            }
        if provider == "jira":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://api.atlassian.com/oauth/token/accessible-resources",
                    headers=headers,
                )
            response.raise_for_status()
            resources = response.json()
            if not resources:
                raise ValueError("Jira OAuth returned no accessible cloud sites.")
            site = resources[0]
            label = site.get("name") or site.get("url") or "Jira site"
            return str(label), str(site.get("id") or label), {
                "cloud_id": site.get("id") or "",
                "site_url": site.get("url") or "",
                "site_name": site.get("name") or "",
            }
        label = PROVIDERS[provider]["display_name"]
        return label, label, {}

    def _expires_at(self, token_payload: dict[str, Any]) -> str | None:
        expires_in = token_payload.get("expires_in")
        try:
            expires_seconds = int(expires_in)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + expires_seconds,
            tz=timezone.utc,
        ).isoformat()

    def _refresh_expires_at(self, token_payload: dict[str, Any]) -> str | None:
        expires_in = token_payload.get("refresh_token_expires_in")
        try:
            expires_seconds = int(expires_in)
        except (TypeError, ValueError):
            return None
        return (
            datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
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

        if self._token_expires_soon(account["expires_at"]):
            refresh_token = decrypt_secret(account["refresh_token_cipher"])
            if refresh_token:
                token = await self._refresh_access_token(
                    provider=provider,
                    connector_id=account["connector_id"],
                    organization_id=organization_id,
                    refresh_token=refresh_token,
                )
            else:
                self._mark_account_error(
                    account["connector_id"],
                    organization_id,
                    "The access token expired and no refresh token is available. Reconnect the provider.",
                )
                raise ValueError(
                    f"{PROVIDERS[provider]['display_name']} needs to be reconnected."
                )
        return token

    def _access_token_sync(self, provider: str, organization_id: str) -> tuple[str, Any]:
        account = self._connected_account(provider, organization_id)
        token = decrypt_secret(account["token_cipher"])
        if not token:
            raise ValueError(f"{PROVIDERS[provider]['display_name']} is not connected.")
        if self._token_expires_soon(account["expires_at"]):
            refresh_token = decrypt_secret(account["refresh_token_cipher"])
            if not refresh_token:
                self._mark_account_error(
                    account["connector_id"],
                    organization_id,
                    "The access token expired and no refresh token is available.",
                )
                raise ValueError(
                    f"{PROVIDERS[provider]['display_name']} needs to be reconnected."
                )
            token = self._refresh_access_token_sync(
                provider=provider,
                connector_id=account["connector_id"],
                organization_id=organization_id,
                refresh_token=refresh_token,
            )
            account = self._connected_account(provider, organization_id)
        return token, account

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
        organization_id: str,
        refresh_token: str,
    ) -> str:
        if not self._client_id(provider) or not self._client_secret(provider):
            raise ValueError(
                f"{PROVIDERS[provider]['display_name']} needs OAuth credentials to refresh access."
            )

        request_kwargs = self._refresh_request(provider, refresh_token)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                PROVIDERS[provider]["token_url"], **request_kwargs
            )
        response.raise_for_status()
        token_payload = response.json()
        return self._store_refreshed_token(
            provider=provider,
            connector_id=connector_id,
            organization_id=organization_id,
            refresh_token=refresh_token,
            token_payload=token_payload,
        )

    def _refresh_access_token_sync(
        self,
        *,
        provider: str,
        connector_id: str,
        organization_id: str,
        refresh_token: str,
    ) -> str:
        request_kwargs = self._refresh_request(provider, refresh_token)
        with httpx.Client(timeout=get_settings().connector_request_timeout_seconds) as client:
            response = client.post(PROVIDERS[provider]["token_url"], **request_kwargs)
        response.raise_for_status()
        return self._store_refreshed_token(
            provider=provider,
            connector_id=connector_id,
            organization_id=organization_id,
            refresh_token=refresh_token,
            token_payload=response.json(),
        )

    def _refresh_request(
        self, provider: str, refresh_token: str
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "client_id": self._client_id(provider),
            "client_secret": self._client_secret(provider),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        headers: dict[str, str] = {"Accept": "application/json"}
        if provider == "jira":
            return {"json": data, "headers": headers}
        if provider == "notion":
            credentials = base64.b64encode(
                f"{self._client_id(provider)}:{self._client_secret(provider)}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {credentials}"
            return {
                "json": {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                "headers": headers,
            }
        return {"data": data, "headers": headers}

    def _store_refreshed_token(
        self,
        *,
        provider: str,
        connector_id: str,
        organization_id: str,
        refresh_token: str,
        token_payload: dict[str, Any],
    ) -> str:
        if provider == "slack" and not token_payload.get("ok"):
            raise ValueError(
                f"Slack token refresh failed: {token_payload.get('error') or 'unknown_error'}"
            )
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("OAuth provider did not return a refreshed access token.")
        scopes = token_payload.get("scope")
        encoded_scopes = None
        if isinstance(scopes, str):
            encoded_scopes = encode_json(
                [scope for scope in re.split(r"[\s,]+", scopes.strip()) if scope]
            )
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_accounts
                SET token_cipher = ?, refresh_token_cipher = ?,
                    scopes_json = COALESCE(?, scopes_json), expires_at = ?,
                    refresh_expires_at = COALESCE(?, refresh_expires_at),
                    token_type = COALESCE(?, token_type), last_error = NULL,
                    updated_at = ?
                WHERE connector_id = ? AND organization_id = ?
                """,
                (
                    encrypt_secret(str(access_token)),
                    encrypt_secret(token_payload.get("refresh_token") or refresh_token),
                    encoded_scopes,
                    self._expires_at(token_payload),
                    self._refresh_expires_at(token_payload),
                    token_payload.get("token_type"),
                    now,
                    connector_id,
                    organization_id,
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

    def _ensure_sync_state(
        self,
        *,
        account,
        provider: str,
        resource: str,
        organization_id: str,
    ):
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM connector_sync_states
                WHERE connector_id = ? AND resource = ? AND organization_id = ?
                """,
                (account["connector_id"], resource, organization_id),
            ).fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                sync_state_id = f"css_{uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO connector_sync_states (
                        sync_state_id, organization_id, connector_id, provider,
                        resource, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'idle', ?, ?)
                    """,
                    (
                        sync_state_id,
                        organization_id,
                        account["connector_id"],
                        provider,
                        resource,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM connector_sync_states WHERE sync_state_id = ?",
                    (sync_state_id,),
                ).fetchone()
        return row

    def _set_sync_state_running(self, sync_state_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_sync_states
                SET status = 'running', last_started_at = ?, last_error = NULL,
                    updated_at = ?
                WHERE sync_state_id = ?
                """,
                (now, now, sync_state_id),
            )

    def _complete_sync_state(
        self,
        *,
        sync_state_id: str,
        cursor: str | None,
        items_seen: int,
        items_changed: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_sync_states
                SET cursor_cipher = ?, status = 'completed', items_seen = ?,
                    items_changed = ?, last_completed_at = ?, last_error = NULL,
                    updated_at = ?
                WHERE sync_state_id = ?
                """,
                (
                    encrypt_secret(cursor),
                    items_seen,
                    items_changed,
                    now,
                    now,
                    sync_state_id,
                ),
            )

    def _fail_sync_state(self, sync_state_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_sync_states
                SET status = 'failed', last_error = ?, updated_at = ?
                WHERE sync_state_id = ?
                """,
                (self._safe_provider_error(error), now, sync_state_id),
            )

    def _persist_sync_item(
        self,
        *,
        account,
        provider: str,
        resource: str,
        item,
        payload: ConnectorSyncRequest,
        user: UserContext,
    ) -> bool:
        content_hash = self._hash_value(item.content)
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM connector_sync_items
                WHERE connector_id = ? AND resource = ? AND external_id = ?
                  AND organization_id = ?
                """,
                (
                    account["connector_id"],
                    resource,
                    item.external_id,
                    user.organization_id,
                ),
            ).fetchone()

        if item.deleted:
            if existing is None:
                with get_connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO connector_sync_items (
                            sync_item_id, organization_id, connector_id, provider,
                            resource, external_id, title, content_hash,
                            metadata_json, source_url, external_updated_at,
                            deleted_at, synced_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"csi_{uuid4().hex}",
                            user.organization_id,
                            account["connector_id"],
                            provider,
                            resource,
                            item.external_id,
                            item.title,
                            content_hash,
                            encode_json(item.metadata),
                            item.source_url,
                            item.updated_at,
                            now,
                            now,
                        ),
                    )
                return True
            with get_connection() as connection:
                connection.execute(
                    """
                    UPDATE connector_sync_items
                    SET deleted_at = COALESCE(deleted_at, ?), metadata_json = ?,
                        external_updated_at = ?, synced_at = ?
                    WHERE sync_item_id = ? AND organization_id = ?
                    """,
                    (
                        now,
                        encode_json(item.metadata),
                        item.updated_at,
                        now,
                        existing["sync_item_id"],
                        user.organization_id,
                    ),
                )
            return existing["deleted_at"] is None

        changed = existing is None or existing["content_hash"] != content_hash
        document_id = existing["document_id"] if existing is not None else None
        if changed:
            filename = self._sync_filename(provider, resource, item.external_id, item.title)
            source_header = f"Source: {provider}/{resource}"
            if item.source_url:
                source_header += f"\nURL: {item.source_url}"
            document_bytes = f"{item.title}\n{source_header}\n\n{item.content}".encode("utf-8")
            if document_id:
                rag_service.replace_file_content(
                    document_id=document_id,
                    filename=filename,
                    data=document_bytes,
                    classification=payload.classification,
                    owner_team=payload.owner_team,
                    uploaded_by=user.user_id,
                    organization_id=user.organization_id,
                )
            else:
                document = rag_service.ingest_file(
                    filename=filename,
                    data=document_bytes,
                    classification=payload.classification,
                    owner_team=payload.owner_team,
                    uploaded_by=user.user_id,
                    organization_id=user.organization_id,
                )
                document_id = document.document_id

        if existing is None:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO connector_sync_items (
                        sync_item_id, organization_id, connector_id, provider,
                        resource, external_id, title, content_hash, metadata_json,
                        source_url, external_updated_at, document_id, deleted_at,
                        synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        f"csi_{uuid4().hex}",
                        user.organization_id,
                        account["connector_id"],
                        provider,
                        resource,
                        item.external_id,
                        item.title,
                        content_hash,
                        encode_json(item.metadata),
                        item.source_url,
                        item.updated_at,
                        document_id,
                        now,
                    ),
                )
        else:
            with get_connection() as connection:
                connection.execute(
                    """
                    UPDATE connector_sync_items
                    SET title = ?, content_hash = ?, metadata_json = ?,
                        source_url = ?, external_updated_at = ?, document_id = ?,
                        deleted_at = NULL, synced_at = ?
                    WHERE sync_item_id = ? AND organization_id = ?
                    """,
                    (
                        item.title,
                        content_hash,
                        encode_json(item.metadata),
                        item.source_url,
                        item.updated_at,
                        document_id,
                        now,
                        existing["sync_item_id"],
                        user.organization_id,
                    ),
                )
        return changed

    async def _revoke_remote_token(self, provider: str, access_token: str) -> bool:
        timeout = get_settings().connector_request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider == "google":
                response = await client.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": access_token},
                )
            elif provider == "slack":
                response = await client.post(
                    "https://slack.com/api/auth.revoke",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            elif provider == "github":
                response = await client.request(
                    "DELETE",
                    f"https://api.github.com/applications/{self._client_id(provider)}/token",
                    auth=(self._client_id(provider) or "", self._client_secret(provider) or ""),
                    json={"access_token": access_token},
                    headers={"Accept": "application/vnd.github+json"},
                )
            elif provider == "notion":
                response = await client.request(
                    "DELETE",
                    "https://api.notion.com/v1/oauth/revoke",
                    auth=(self._client_id(provider) or "", self._client_secret(provider) or ""),
                    json={"token": access_token},
                    headers={"Notion-Version": "2022-06-28"},
                )
            else:
                return False
        response.raise_for_status()
        if provider == "slack":
            body = response.json()
            if not body.get("ok"):
                raise ValueError(
                    f"Slack token revocation failed: {body.get('error') or 'unknown_error'}"
                )
        return True

    def _mark_account_error(
        self, connector_id: str, organization_id: str, error: str
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE connector_accounts SET last_error = ?, updated_at = ?
                WHERE connector_id = ? AND organization_id = ?
                """,
                (
                    self._safe_provider_error(error),
                    datetime.now(timezone.utc).isoformat(),
                    connector_id,
                    organization_id,
                ),
            )

    @staticmethod
    def _row_to_sync_state(row) -> ConnectorSyncStateRecord:
        return ConnectorSyncStateRecord(
            sync_state_id=row["sync_state_id"],
            connector_id=row["connector_id"],
            provider=row["provider"],
            resource=row["resource"],
            status=row["status"],
            items_seen=int(row["items_seen"]),
            items_changed=int(row["items_changed"]),
            has_cursor=bool(row["cursor_cipher"]),
            last_started_at=ConnectorService._string_or_none(row["last_started_at"]),
            last_completed_at=ConnectorService._string_or_none(row["last_completed_at"]),
            last_error=row["last_error"],
        )

    def _row_to_webhook(self, row) -> WebhookSubscriptionRecord:
        return WebhookSubscriptionRecord(
            subscription_id=row["subscription_id"],
            connector_id=row["connector_id"],
            provider=row["provider"],
            resource=row["resource"],
            target=row["target"],
            remote_id=row["remote_id"],
            registration_mode=row["registration_mode"],
            status=row["status"],
            callback_url=self._webhook_callback_url(
                row["provider"], row["subscription_id"]
            ),
            expires_at=self._string_or_none(row["expires_at"]),
            created_at=self._string_or_none(row["created_at"]),
        )

    @staticmethod
    def _verify_webhook_signature(
        *, provider: str, secret: str, raw_body: bytes, headers: dict[str, str]
    ) -> bool:
        lowered = {key.lower(): value for key, value in headers.items()}
        if provider == "google":
            return hmac.compare_digest(lowered.get("x-goog-channel-token", ""), secret)
        if provider == "slack":
            timestamp = lowered.get("x-slack-request-timestamp", "")
            try:
                if abs(datetime.now(timezone.utc).timestamp() - int(timestamp)) > 300:
                    return False
            except ValueError:
                return False
            signed = f"v0:{timestamp}:".encode() + raw_body
            expected = "v0=" + hmac.new(
                secret.encode(), signed, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(lowered.get("x-slack-signature", ""), expected)
        signature = (
            lowered.get("x-hub-signature-256")
            or lowered.get("x-notion-signature")
            or lowered.get("x-workos-signature")
        )
        if signature:
            expected = "sha256=" + hmac.new(
                secret.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(signature, expected)
        return hmac.compare_digest(
            lowered.get("x-workos-webhook-secret", ""), secret
        )

    @staticmethod
    def _external_delivery_id(
        *,
        provider: str,
        headers: dict[str, str],
        payload: dict[str, object],
        payload_hash: str,
    ) -> str:
        lowered = {key.lower(): value for key, value in headers.items()}
        candidates = {
            "github": lowered.get("x-github-delivery"),
            "slack": payload.get("event_id"),
            "google": lowered.get("x-goog-message-number"),
            "jira": lowered.get("x-atlassian-webhook-identifier"),
            "notion": lowered.get("x-notion-request-id"),
        }
        return str(candidates.get(provider) or payload_hash)

    @staticmethod
    def _webhook_event_type(
        provider: str, headers: dict[str, str], payload: dict[str, object]
    ) -> str:
        lowered = {key.lower(): value for key, value in headers.items()}
        if provider == "github":
            return lowered.get("x-github-event", "unknown")
        if provider == "slack":
            event = payload.get("event")
            return str(event.get("type") if isinstance(event, dict) else payload.get("type") or "unknown")
        return str(payload.get("webhookEvent") or payload.get("type") or "change")

    @staticmethod
    def _webhook_challenge(payload: dict[str, object]) -> str | None:
        challenge = payload.get("challenge")
        return str(challenge) if challenge is not None else None

    @staticmethod
    def _hash_value(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _pkce_challenge(code_verifier: str) -> str:
        return base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _safe_provider_error(error: Exception | str) -> str:
        if isinstance(error, httpx.HTTPStatusError):
            return (
                f"Provider request failed with HTTP {error.response.status_code}. "
                "Reconnect the account if the provider revoked access."
            )
        message = str(error).strip() or "Provider request failed."
        return message[:1000]

    @staticmethod
    def _sync_filename(
        provider: str, resource: str, external_id: str, title: str
    ) -> str:
        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-.")[:80]
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", external_id).strip("-.")[:40]
        return f"{provider}-{resource}-{safe_id}-{safe_title or 'item'}.txt"

    @staticmethod
    def _normalize_remote_expiry(value: str | None) -> str | None:
        if not value:
            return None
        if str(value).isdigit():
            numeric = int(str(value))
            if numeric > 10_000_000_000:
                numeric //= 1000
            return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
        return str(value)

    @staticmethod
    def _webhook_callback_url(provider: str, subscription_id: str) -> str:
        base = get_settings().connector_webhook_base_url.rstrip("/")
        return f"{base}/{provider}/{subscription_id}"

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
