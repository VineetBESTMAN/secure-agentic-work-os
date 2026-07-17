from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import ConnectorValidationCheck, ConnectorValidationRunRecord
from app.services.connector_providers import provider_actions, provider_resources
from app.services.connectors import connector_service


CheckStatus = Literal["passed", "failed", "pending", "not_applicable"]


class ConnectorValidationService:
    async def run(
        self,
        *,
        provider: str,
        requested_by: str,
        organization_id: str,
        force_token_refresh: bool = False,
    ) -> ConnectorValidationRunRecord:
        started_at = self._now()
        checks: list[ConnectorValidationCheck] = []
        configured = connector_service.provider_is_configured(provider)
        self._add(
            checks,
            "developer_app",
            "Developer application configuration",
            "passed" if configured else "pending",
            "OAuth client credentials are configured." if configured else
            "Configure this provider's client ID and secret before live validation.",
            {"configured": configured},
        )

        connector = next(
            record
            for record in connector_service.list_connectors(organization_id)
            if record.provider == provider
        )
        if connector.status not in {"connected", "error"} or not connector.connector_id:
            self._add(
                checks,
                "oauth_connection",
                "OAuth account connection",
                "pending",
                "Authorize a real provider account before running live probes.",
                {"connected": False},
            )
            self._add_lifecycle_evidence(checks, provider, organization_id)
            return self._persist(
                provider=provider,
                connector_id=None,
                requested_by=requested_by,
                organization_id=organization_id,
                checks=checks,
                started_at=started_at,
            )

        self._add(
            checks,
            "oauth_connection",
            "OAuth account connection",
            "passed",
            "A tenant-scoped provider account is connected.",
            {"connected": True},
        )
        if provider == "google":
            pubsub_identity_configured = bool(
                str(get_settings().google_pubsub_service_account or "").strip()
            )
            self._add(
                checks,
                "gmail_push_identity",
                "Gmail Pub/Sub push identity",
                "passed" if pubsub_identity_configured else "pending",
                "The allowed Pub/Sub push service account is configured."
                if pubsub_identity_configured
                else "Set GOOGLE_PUBSUB_SERVICE_ACCOUNT before validating Gmail push delivery.",
                {"service_account_configured": pubsub_identity_configured},
            )
        try:
            evidence = await connector_service.live_probe(
                provider=provider,
                organization_id=organization_id,
                force_token_refresh=force_token_refresh,
            )
        except Exception:
            self._add(
                checks,
                "live_access",
                "Live provider access",
                "failed",
                "Live access could not be validated. Reconnect the provider and retry.",
            )
            self._add_operational_evidence(
                checks, provider, connector.connector_id, organization_id
            )
            self._add_lifecycle_evidence(checks, provider, organization_id)
            return self._persist(
                provider=provider,
                connector_id=connector.connector_id,
                requested_by=requested_by,
                organization_id=organization_id,
                checks=checks,
                started_at=started_at,
            )

        granted_scopes = set(str(scope) for scope in evidence["granted_scopes"])
        required_scopes = set(str(scope) for scope in evidence["required_scopes"])
        missing_scopes = sorted(required_scopes - granted_scopes)
        self._add(
            checks,
            "oauth_scopes",
            "Granted OAuth scopes",
            "passed" if not missing_scopes else "failed",
            "All configured OAuth scopes are granted." if not missing_scopes else
            "Reconnect the account and grant the missing OAuth scopes.",
            {
                "granted_scopes": sorted(granted_scopes),
                "required_scopes": sorted(required_scopes),
                "missing_scopes": missing_scopes,
            },
        )
        has_refresh = bool(evidence["has_refresh_token"])
        refreshed = bool(evidence["refresh_performed"] or evidence["last_refresh_at"])
        expires = bool(evidence["expires_at"])
        if refreshed:
            refresh_status: CheckStatus = "passed"
            refresh_message = "A token refresh completed and rotated credentials remain encrypted."
        elif has_refresh:
            refresh_status = "pending"
            refresh_message = "A refresh token is stored, but refresh has not yet been exercised."
        elif expires:
            refresh_status = "failed"
            refresh_message = "This expiring access token has no refresh token; reconnect with offline access."
        else:
            refresh_status = "not_applicable"
            refresh_message = "This provider issued a non-expiring token without a refresh token."
        self._add(
            checks,
            "token_refresh",
            "OAuth token refresh",
            refresh_status,
            refresh_message,
            {
                "has_refresh_token": has_refresh,
                "refresh_performed": bool(evidence["refresh_performed"]),
                "last_refresh_at": evidence["last_refresh_at"],
            },
        )

        probe = evidence["probe"]
        identity_match = probe.get("identity_match")
        self._add(
            checks,
            "identity_match",
            "Connected account identity",
            "passed" if identity_match is True else "failed" if identity_match is False else "pending",
            "The live provider identity matches the tenant-scoped account." if identity_match is True
            else "The live provider identity does not match the stored account." if identity_match is False
            else "The provider identity could not be verified.",
            {"matches_connected_account": identity_match},
        )
        for item in probe.get("checks", []):
            self._add(
                checks,
                f"probe_{item['key']}",
                str(item["label"]),
                str(item["status"]),
                str(item["message"]),
                dict(item.get("evidence") or {}),
            )

        self._add_operational_evidence(
            checks, provider, str(evidence["connector_id"]), organization_id
        )
        self._add_lifecycle_evidence(checks, provider, organization_id)
        return self._persist(
            provider=provider,
            connector_id=str(evidence["connector_id"]),
            requested_by=requested_by,
            organization_id=organization_id,
            checks=checks,
            started_at=started_at,
        )

    def list_runs(
        self,
        *,
        organization_id: str,
        provider: str | None = None,
        limit: int = 50,
    ) -> list[ConnectorValidationRunRecord]:
        where = "organization_id = ?"
        params: list[object] = [organization_id]
        if provider:
            connector_service.provider_is_configured(provider)
            where += " AND provider = ?"
            params.append(provider)
        params.append(limit)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM connector_validation_runs
                WHERE {where}
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _add_operational_evidence(
        self,
        checks: list[ConnectorValidationCheck],
        provider: str,
        connector_id: str,
        organization_id: str,
    ) -> None:
        with get_connection() as connection:
            sync_rows = connection.execute(
                """
                SELECT resource, status, items_seen, last_completed_at
                FROM connector_sync_states
                WHERE connector_id = ? AND organization_id = ?
                """,
                (connector_id, organization_id),
            ).fetchall()
            active_webhooks = connection.execute(
                """
                SELECT resource, COUNT(*) AS count
                FROM connector_webhook_subscriptions
                WHERE provider = ? AND organization_id = ? AND status = 'active'
                GROUP BY resource
                """,
                (provider, organization_id),
            ).fetchall()
            valid_deliveries = connection.execute(
                """
                SELECT subscription.resource, COUNT(*) AS count
                FROM connector_webhook_deliveries AS delivery
                JOIN connector_webhook_subscriptions AS subscription
                  ON subscription.subscription_id = delivery.subscription_id
                 AND subscription.organization_id = delivery.organization_id
                WHERE delivery.provider = ? AND delivery.organization_id = ?
                  AND delivery.signature_valid = ?
                GROUP BY subscription.resource
                """,
                (provider, organization_id, True),
            ).fetchall()
            action_rows = connection.execute(
                """
                SELECT receipt.action, receipt.status, execution.approval_id
                FROM connector_action_receipts AS receipt
                LEFT JOIN mcp_tool_executions AS execution
                  ON execution.execution_id = receipt.execution_id
                 AND execution.organization_id = receipt.organization_id
                WHERE receipt.provider = ? AND receipt.organization_id = ?
                """,
                (provider, organization_id),
            ).fetchall()

        by_resource = {row["resource"]: row for row in sync_rows}
        for resource in provider_resources(provider):
            row = by_resource.get(resource)
            if row is not None and row["status"] == "completed":
                status: CheckStatus = "passed"
                message = "Incremental synchronization completed for this resource."
            elif row is not None and row["status"] == "failed":
                status = "failed"
                message = "The most recent synchronization failed."
            else:
                status = "pending"
                message = "Run synchronization for this resource to capture lifecycle evidence."
            self._add(
                checks,
                f"sync_{resource}",
                f"{resource.title()} incremental sync",
                status,
                message,
                {
                    "items_seen": int(row["items_seen"]) if row is not None else 0,
                    "last_completed_at": row["last_completed_at"] if row is not None else None,
                },
            )

        webhooks_by_resource = {row["resource"]: int(row["count"]) for row in active_webhooks}
        deliveries_by_resource = {
            row["resource"]: int(row["count"]) for row in valid_deliveries
        }
        for resource in provider_resources(provider):
            active_count = webhooks_by_resource.get(resource, 0)
            delivery_count = deliveries_by_resource.get(resource, 0)
            self._add(
                checks,
                f"webhook_subscription_{resource}",
                f"{resource.title()} webhook subscription",
                "passed" if active_count else "pending",
                "An active webhook subscription exists." if active_count else
                "Create and register a provider-authenticated webhook subscription.",
                {"active_subscriptions": active_count},
            )
            self._add(
                checks,
                f"webhook_delivery_{resource}",
                f"Verified {resource} webhook delivery",
                "passed" if delivery_count else "pending",
                "At least one provider-authenticated delivery was accepted." if delivery_count else
                "Deliver a real provider event to validate authentication and replay protection.",
                {"verified_deliveries": delivery_count},
            )

        for action in provider_actions(provider):
            approved = any(
                row["action"] == action
                and row["status"] == "completed"
                and bool(row["approval_id"])
                for row in action_rows
            )
            self._add(
                checks,
                f"approved_action_{action}",
                f"Approved {action.replace('_', ' ')}",
                "passed" if approved else "pending",
                "An approval-gated provider receipt proves this action." if approved else
                "Run this action through MCP and obtain manager approval; validation never triggers it.",
                {"approved_provider_receipt": approved},
            )

    def _add_lifecycle_evidence(
        self,
        checks: list[ConnectorValidationCheck],
        provider: str,
        organization_id: str,
    ) -> None:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT detail_json, timestamp FROM audit_events
                WHERE organization_id = ? AND event_type = 'connectors.disconnect'
                ORDER BY timestamp DESC
                """,
                (organization_id,),
            ).fetchall()
        matching = []
        for row in rows:
            detail = decode_json(row["detail_json"], {})
            if detail.get("provider") == provider:
                matching.append((detail, row["timestamp"]))
        disconnected = bool(matching)
        self._add(
            checks,
            "disconnect_lifecycle",
            "Secure disconnect lifecycle",
            "passed" if disconnected else "pending",
            "A prior disconnect wiped local credentials and was audited." if disconnected else
            "Disconnect after validation to prove local credential wiping and audit evidence.",
            {"disconnect_audited": disconnected, "last_disconnect_at": matching[0][1] if matching else None},
        )
        if provider == "jira":
            remote_status: CheckStatus = "not_applicable"
            remote_message = "Jira 3LO tokens are disconnected locally; this integration has no remote revoke endpoint."
            remote_revoked = False
        else:
            remote_revoked = any(bool(detail.get("remote_revoked")) for detail, _ in matching)
            remote_status = "passed" if remote_revoked else "pending"
            remote_message = (
                "Remote token revocation was confirmed."
                if remote_revoked
                else "Disconnect a connected account to capture confirmed remote revocation evidence."
            )
        self._add(
            checks,
            "remote_revocation",
            "Remote OAuth revocation",
            remote_status,
            remote_message,
            {"remote_revoked": remote_revoked},
        )

    def _persist(
        self,
        *,
        provider: str,
        connector_id: str | None,
        requested_by: str,
        organization_id: str,
        checks: list[ConnectorValidationCheck],
        started_at: str,
    ) -> ConnectorValidationRunRecord:
        completed_at = self._now()
        counts = Counter(check.status for check in checks)
        status = "failed" if counts["failed"] else "incomplete" if counts["pending"] else "passed"
        record = ConnectorValidationRunRecord(
            validation_run_id=f"cvr_{uuid4().hex}",
            connector_id=connector_id,
            provider=provider,
            status=status,
            requested_by=requested_by,
            checks=checks,
            passed_count=counts["passed"],
            failed_count=counts["failed"],
            pending_count=counts["pending"],
            not_applicable_count=counts["not_applicable"],
            started_at=started_at,
            completed_at=completed_at,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO connector_validation_runs (
                    validation_run_id, organization_id, connector_id, provider,
                    status, requested_by, checks_json, passed_count, failed_count,
                    pending_count, not_applicable_count, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.validation_run_id,
                    organization_id,
                    connector_id,
                    provider,
                    record.status,
                    requested_by,
                    encode_json([check.model_dump(mode="json") for check in checks]),
                    record.passed_count,
                    record.failed_count,
                    record.pending_count,
                    record.not_applicable_count,
                    started_at,
                    completed_at,
                ),
            )
        return record

    @staticmethod
    def _add(
        checks: list[ConnectorValidationCheck],
        key: str,
        label: str,
        status: CheckStatus,
        message: str,
        evidence: dict[str, object] | None = None,
    ) -> None:
        checks.append(
            ConnectorValidationCheck(
                key=key,
                label=label,
                status=status,
                message=message,
                evidence=evidence or {},
                checked_at=ConnectorValidationService._now(),
            )
        )

    @staticmethod
    def _row_to_record(row) -> ConnectorValidationRunRecord:
        return ConnectorValidationRunRecord(
            validation_run_id=row["validation_run_id"],
            connector_id=row["connector_id"],
            provider=row["provider"],
            status=row["status"],
            requested_by=row["requested_by"],
            checks=decode_json(row["checks_json"], []),
            passed_count=int(row["passed_count"]),
            failed_count=int(row["failed_count"]),
            pending_count=int(row["pending_count"]),
            not_applicable_count=int(row["not_applicable_count"]),
            started_at=str(row["started_at"]),
            completed_at=str(row["completed_at"]),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


connector_validation_service = ConnectorValidationService()
