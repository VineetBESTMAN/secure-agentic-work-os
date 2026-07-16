from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import (
    OpenClawClientCreateRequest,
    OpenClawClientCredential,
    OpenClawClientRecord,
    OpenClawIntegrationStatus,
    UserContext,
)


OPENCLAW_TOKEN_PREFIX = "wos_oc_"
OPENCLAW_SCOPES = {
    "documents:read",
    "tasks:write",
    "email:send",
    "connectors:act",
}
TOOLS_BY_SCOPE = {
    "documents:read": ["search_documents", "export_data"],
    "tasks:write": ["create_task"],
    "email:send": ["send_email"],
    "connectors:act": [
        "create_calendar_event",
        "send_slack_message",
        "create_github_issue",
        "create_jira_issue",
        "create_notion_page",
    ],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc
    )


class OpenClawService:
    def list_clients(self, organization_id: str) -> list[OpenClawClientRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM openclaw_clients
                WHERE organization_id = ?
                ORDER BY created_at DESC
                """,
                (organization_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def status(self, organization_id: str) -> OpenClawIntegrationStatus:
        clients = self.list_clients(organization_id)
        settings = get_settings()
        return OpenClawIntegrationStatus(
            configured_clients=len(clients),
            active_clients=sum(client.status == "active" for client in clients),
            mcp_server_url=settings.mcp_server_url,
            docker_mcp_server_url=settings.openclaw_mcp_internal_url,
        )

    def create_client(
        self,
        payload: OpenClawClientCreateRequest,
        actor: UserContext,
    ) -> OpenClawClientCredential:
        settings = get_settings()
        scopes = sorted(set(payload.scopes))
        self._validate_scopes(scopes, actor)
        expiry_days = payload.expires_in_days or settings.openclaw_client_default_expiry_days
        if expiry_days > settings.openclaw_client_max_expiry_days:
            raise ValueError(
                "OpenClaw client expiry exceeds the configured maximum."
            )
        client_id = f"oc_{uuid4().hex}"
        token = self._new_token()
        expires_at = _now() + timedelta(days=expiry_days)
        try:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO openclaw_clients (
                        client_id, organization_id, name, token_hash, scopes_json,
                        status, created_by, expires_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        client_id,
                        actor.organization_id,
                        payload.name.strip(),
                        self._hash_token(token),
                        encode_json(scopes),
                        actor.user_id,
                        expires_at.isoformat(),
                    ),
                )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError(
                    "An OpenClaw client with this name already exists in the organization."
                ) from exc
            raise
        record = self.get_client(client_id, actor.organization_id)
        if record is None:  # pragma: no cover
            raise RuntimeError("OpenClaw client could not be persisted.")
        return self._credential(record, token)

    def rotate_client(
        self, client_id: str, actor: UserContext
    ) -> OpenClawClientCredential | None:
        existing = self.get_client(client_id, actor.organization_id)
        if existing is None:
            return None
        self._validate_scopes(existing.scopes, actor)
        settings = get_settings()
        token = self._new_token()
        now = _now()
        expires_at = now + timedelta(
            days=settings.openclaw_client_default_expiry_days
        )
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE openclaw_clients
                SET token_hash = ?, status = 'active', expires_at = ?,
                    revoked_at = NULL, rotated_at = ?
                WHERE client_id = ? AND organization_id = ?
                """,
                (
                    self._hash_token(token),
                    expires_at.isoformat(),
                    now.isoformat(),
                    client_id,
                    actor.organization_id,
                ),
            )
        record = self.get_client(client_id, actor.organization_id)
        if record is None:  # pragma: no cover
            raise RuntimeError("OpenClaw client disappeared during rotation.")
        return self._credential(record, token)

    def revoke_client(
        self, client_id: str, organization_id: str
    ) -> OpenClawClientRecord | None:
        existing = self.get_client(client_id, organization_id)
        if existing is None:
            return None
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE openclaw_clients
                SET status = 'revoked', revoked_at = ?
                WHERE client_id = ? AND organization_id = ?
                """,
                (_now().isoformat(), client_id, organization_id),
            )
        return self.get_client(client_id, organization_id)

    def get_client(
        self, client_id: str, organization_id: str
    ) -> OpenClawClientRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM openclaw_clients
                WHERE client_id = ? AND organization_id = ?
                """,
                (client_id, organization_id),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def resolve_token(self, token: str) -> UserContext | None:
        if not token.startswith(OPENCLAW_TOKEN_PREFIX):
            return None
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT client.*, organization.slug AS organization_slug,
                       organization.name AS organization_name
                FROM openclaw_clients AS client
                JOIN organizations AS organization
                  ON organization.organization_id = client.organization_id
                WHERE client.token_hash = ? AND client.status = 'active'
                """,
                (self._hash_token(token),),
            ).fetchone()
            if row is None or _as_datetime(row["expires_at"]) <= _now():
                return None
            connection.execute(
                "UPDATE openclaw_clients SET last_used_at = ? WHERE client_id = ?",
                (_now().isoformat(), row["client_id"]),
            )
        return self._row_to_user(row)

    def resolve_actor(
        self, actor_id: str, organization_id: str
    ) -> UserContext | None:
        prefix = "openclaw:"
        if not actor_id.startswith(prefix):
            return None
        client_id = actor_id[len(prefix) :]
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT client.*, organization.slug AS organization_slug,
                       organization.name AS organization_name
                FROM openclaw_clients AS client
                JOIN organizations AS organization
                  ON organization.organization_id = client.organization_id
                WHERE client.client_id = ? AND client.organization_id = ?
                  AND client.status = 'active'
                """,
                (client_id, organization_id),
            ).fetchone()
        if row is None or _as_datetime(row["expires_at"]) <= _now():
            return None
        return self._row_to_user(row)

    def _credential(
        self, record: OpenClawClientRecord, token: str
    ) -> OpenClawClientCredential:
        settings = get_settings()
        return OpenClawClientCredential(
            client=record,
            token=token,
            mcp_server_url=settings.mcp_server_url,
            docker_mcp_server_url=settings.openclaw_mcp_internal_url,
            openclaw_config=self._configuration(
                settings.mcp_server_url, token, record.scopes
            ),
            docker_openclaw_config=self._configuration(
                settings.openclaw_mcp_internal_url, token, record.scopes
            ),
        )

    @staticmethod
    def _configuration(
        url: str, token: str, scopes: list[str]
    ) -> dict[str, object]:
        tools = sorted(
            {
                tool
                for scope in scopes
                for tool in TOOLS_BY_SCOPE.get(scope, [])
            }
        )
        return {
            "mcp": {
                "servers": {
                    "secure-work-os": {
                        "url": url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": f"Bearer {token}"},
                        "connectTimeout": 10,
                        "timeout": 30,
                        "supportsParallelToolCalls": False,
                        "toolFilter": {"include": tools},
                    }
                }
            }
        }

    @staticmethod
    def _validate_scopes(scopes: list[str], actor: UserContext) -> None:
        if not scopes or any(scope not in OPENCLAW_SCOPES for scope in scopes):
            raise ValueError("OpenClaw client scopes are invalid.")
        missing = [scope for scope in scopes if scope not in actor.scopes]
        if missing:
            raise PermissionError(
                "The administrator cannot delegate scopes they do not hold: "
                + ", ".join(missing)
            )

    @staticmethod
    def _new_token() -> str:
        return OPENCLAW_TOKEN_PREFIX + secrets.token_urlsafe(32)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _row_to_record(self, row) -> OpenClawClientRecord:
        status = str(row["status"])
        if status == "active" and _as_datetime(row["expires_at"]) <= _now():
            status = "expired"
        return OpenClawClientRecord(
            client_id=row["client_id"],
            organization_id=row["organization_id"],
            name=row["name"],
            actor_id=f"openclaw:{row['client_id']}",
            scopes=decode_json(row["scopes_json"], []),
            status=status,
            created_by=row["created_by"],
            expires_at=str(row["expires_at"]),
            last_used_at=(
                str(row["last_used_at"]) if row["last_used_at"] is not None else None
            ),
            revoked_at=(
                str(row["revoked_at"]) if row["revoked_at"] is not None else None
            ),
            rotated_at=(
                str(row["rotated_at"]) if row["rotated_at"] is not None else None
            ),
            created_at=(
                str(row["created_at"]) if row["created_at"] is not None else None
            ),
        )

    @staticmethod
    def _row_to_user(row) -> UserContext:
        client_id = str(row["client_id"])
        return UserContext(
            user_id=f"openclaw:{client_id}",
            email=f"{client_id}@service.invalid",
            display_name=f"OpenClaw: {row['name']}",
            organization_id=row["organization_id"],
            organization_slug=row["organization_slug"],
            organization_name=row["organization_name"],
            membership_id=f"openclaw:{client_id}",
            role="employee",
            scopes=decode_json(row["scopes_json"], []),
            principal_type="openclaw",
            integration_id=client_id,
        )


openclaw_service = OpenClawService()
