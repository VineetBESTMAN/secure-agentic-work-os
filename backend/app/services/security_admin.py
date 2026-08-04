from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.crypto import (
    active_encryption_key_id,
    encrypted_key_id,
    encryption_key_ids,
    reencrypt_secret,
)
from app.core.database import get_connection
from app.models.schemas import (
    KeyRotationResult,
    KeyRotationStatus,
    RetentionExecutionResult,
    RetentionPreview,
    SecurityPosture,
)
from app.services.security_controls import security_control_service


_SECRET_COLUMNS = (
    ("connector_accounts", "connector_id", ("token_cipher", "refresh_token_cipher")),
    ("oauth_states", "state", ("code_verifier_cipher",)),
    ("connector_sync_states", "sync_state_id", ("cursor_cipher",)),
    (
        "connector_webhook_subscriptions",
        "subscription_id",
        ("secret_cipher",),
    ),
    ("oidc_providers", "provider_id", ("client_secret_cipher",)),
    (
        "oidc_authorization_states",
        "state_hash",
        ("code_verifier_cipher",),
    ),
)


class SecurityAdminService:
    def posture(self, organization_id: str) -> SecurityPosture:
        settings = get_settings()
        if settings.secret_key_file:
            secret_source = "file"
        elif settings.secret_key == "change-me":
            secret_source = "development-default"
        else:
            secret_source = "environment"
        return SecurityPosture(
            environment=settings.app_env,
            production_requirements_enforced=settings.app_env.lower() == "production",
            secret_source=secret_source,
            encryption_active_key_id=active_encryption_key_id(),
            encryption_key_count=len(encryption_key_ids()),
            rate_limit_enabled=settings.rate_limit_enabled,
            rate_limit_backend=settings.rate_limit_backend,
            malware_scanner_mode=settings.malware_scanner_mode,
            malware_fail_closed=settings.malware_fail_closed,
            security_headers_enabled=settings.security_headers_enabled,
            policy=security_control_service.get_policy(organization_id),
            recent_findings=security_control_service.list_findings(
                organization_id, limit=25
            ),
        )

    def retention_preview(self, organization_id: str) -> RetentionPreview:
        policy = security_control_service.get_policy(organization_id)
        categories = self._retention_categories(policy)
        cutoff_by_category: dict[str, str] = {}
        eligible_by_category: dict[str, int] = {}
        with get_connection() as connection:
            for category, table, _, timestamp_column, days in categories:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                cutoff_by_category[category] = cutoff.isoformat()
                row = connection.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM {table}
                    WHERE organization_id = ? AND {timestamp_column} < ?
                    """,
                    (organization_id, cutoff.isoformat()),
                ).fetchone()
                eligible_by_category[category] = int(row["count"] if row else 0)
        return RetentionPreview(
            enabled=policy.retention_enabled,
            cutoff_by_category=cutoff_by_category,
            eligible_by_category=eligible_by_category,
            total_eligible=sum(eligible_by_category.values()),
        )

    def execute_retention(
        self, organization_id: str, *, confirmed: bool
    ) -> RetentionExecutionResult:
        if not confirmed:
            raise ValueError("Retention execution requires explicit confirmation.")
        policy = security_control_service.get_policy(organization_id)
        if not policy.retention_enabled:
            raise ValueError("Retention enforcement is disabled for this organization.")
        deleted: dict[str, int] = {}
        batch_size = get_settings().retention_batch_size
        with get_connection() as connection:
            for category, table, primary_key, timestamp_column, days in self._retention_categories(
                policy
            ):
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                rows = connection.execute(
                    f"""
                    SELECT {primary_key}
                    FROM {table}
                    WHERE organization_id = ? AND {timestamp_column} < ?
                    ORDER BY {timestamp_column} ASC
                    LIMIT ?
                    """,
                    (organization_id, cutoff.isoformat(), batch_size),
                ).fetchall()
                identifiers = [str(row[primary_key]) for row in rows]
                if identifiers:
                    placeholders = ",".join("?" for _ in identifiers)
                    connection.execute(
                        f"""
                        DELETE FROM {table}
                        WHERE organization_id = ? AND {primary_key} IN ({placeholders})
                        """,
                        (organization_id, *identifiers),
                    )
                deleted[category] = len(identifiers)
        return RetentionExecutionResult(
            deleted_by_category=deleted,
            total_deleted=sum(deleted.values()),
        )

    def key_rotation_status(self, organization_id: str) -> KeyRotationStatus:
        counts: dict[str, int] = {}
        with get_connection() as connection:
            for table, _, columns in _SECRET_COLUMNS:
                rows = connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table} WHERE organization_id = ?",
                    (organization_id,),
                ).fetchall()
                for row in rows:
                    for column in columns:
                        key_id = encrypted_key_id(row[column])
                        if key_id != "empty":
                            counts[key_id] = counts.get(key_id, 0) + 1
        active = active_encryption_key_id()
        return KeyRotationStatus(
            active_key_id=active,
            available_key_ids=encryption_key_ids(),
            ciphertexts_by_key_id=counts,
            rotatable_ciphertexts=sum(
                count for key_id, count in counts.items() if key_id != active
            ),
        )

    def rotate_keys(
        self, organization_id: str, *, confirmed: bool
    ) -> KeyRotationResult:
        if not confirmed:
            raise ValueError("Encryption-key rotation requires explicit confirmation.")
        active = active_encryption_key_id()
        rotated = 0
        with get_connection() as connection:
            for table, primary_key, columns in _SECRET_COLUMNS:
                rows = connection.execute(
                    f"""
                    SELECT {primary_key}, {', '.join(columns)}
                    FROM {table}
                    WHERE organization_id = ?
                    """,
                    (organization_id,),
                ).fetchall()
                for row in rows:
                    updates: list[str] = []
                    values: list[str] = []
                    for column in columns:
                        value = row[column]
                        if value and encrypted_key_id(value) != active:
                            updates.append(f"{column} = ?")
                            rotated_value = reencrypt_secret(str(value))
                            if rotated_value is None:
                                continue
                            values.append(rotated_value)
                            rotated += 1
                    if updates:
                        values.extend((str(row[primary_key]), organization_id))
                        connection.execute(
                            f"""
                            UPDATE {table}
                            SET {', '.join(updates)}
                            WHERE {primary_key} = ? AND organization_id = ?
                            """,
                            tuple(values),
                        )
        return KeyRotationResult(
            active_key_id=active,
            rotated_ciphertexts=rotated,
        )

    @staticmethod
    def _retention_categories(policy):
        return (
            (
                "audit_events",
                "audit_events",
                "event_id",
                "timestamp",
                policy.audit_retention_days,
            ),
            (
                "runtime_observations",
                "runtime_observations",
                "observation_id",
                "created_at",
                policy.runtime_retention_days,
            ),
            (
                "connector_validation_runs",
                "connector_validation_runs",
                "validation_run_id",
                "completed_at",
                policy.connector_validation_retention_days,
            ),
            (
                "rag_evaluation_runs",
                "rag_evaluation_runs",
                "run_id",
                "created_at",
                policy.rag_evaluation_retention_days,
            ),
            (
                "security_findings",
                "security_findings",
                "finding_id",
                "created_at",
                policy.security_finding_retention_days,
            ),
        )


security_admin_service = SecurityAdminService()
