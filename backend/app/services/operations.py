from __future__ import annotations

from datetime import datetime, timedelta, timezone

from redis import Redis

from app.core.config import get_settings
from app.core.database import get_connection, is_postgres_database
from app.models.schemas import (
    BackupStatus,
    OperationalComponent,
    OperationsStatus,
    ReliabilityStatus,
)
from app.services.metrics import metrics_service
from app.services.object_storage import ObjectStorageError, object_storage_service


class OperationsService:
    def status(self, organization_id: str | None = None) -> OperationsStatus:
        settings = get_settings()
        components, readiness = self.readiness()
        backup = self.backup_status()
        reliability = self.reliability_status(organization_id=organization_id)
        metrics_service.update_backup(
            configured=backup.configured,
            age_seconds=backup.age_seconds,
            rpo_seconds=backup.rpo_seconds,
        )
        return OperationsStatus(
            environment=settings.app_env,
            public_base_url=settings.public_base_url,
            production_mode=settings.app_env.lower() == "production",
            database_backend="postgresql" if is_postgres_database() else "sqlite",
            database_tls=settings.database_tls_enabled(),
            redis_tls=settings.redis_url.startswith("rediss://"),
            object_storage_backend=settings.object_storage_backend,
            object_storage_bucket_configured=bool(settings.object_storage_bucket),
            metrics_enabled=settings.metrics_enabled,
            ready=all(readiness.values()),
            components=components,
            backup=backup,
            reliability=reliability,
        )

    def reliability_status(
        self, organization_id: str | None = None
    ) -> ReliabilityStatus:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(seconds=settings.job_stale_seconds)).isoformat()
        webhook_since = (now - timedelta(hours=24)).isoformat()
        job_tenant = ""
        account_tenant = ""
        webhook_tenant = ""
        job_params: list[object] = [stale_before]
        account_params: list[object] = []
        webhook_params: list[object] = [webhook_since]
        if organization_id is not None:
            job_tenant = "WHERE organization_id = ?"
            account_tenant = "AND organization_id = ?"
            webhook_tenant = "AND organization_id = ?"
            job_params.append(organization_id)
            account_params.append(organization_id)
            webhook_params.append(organization_id)
        try:
            with get_connection() as connection:
                jobs = connection.execute(
                    f"""
                    SELECT
                        SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                        SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                        SUM(CASE WHEN status = 'dead_lettered' THEN 1 ELSE 0 END) AS dead_lettered,
                        SUM(CASE WHEN status = 'running'
                            AND COALESCE(heartbeat_at, updated_at) < ?
                            THEN 1 ELSE 0 END) AS stale
                    FROM background_jobs
                    {job_tenant}
                    """,
                    tuple(job_params),
                ).fetchone()
                accounts = connection.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM connector_accounts
                    WHERE status = 'connected' AND last_error IS NOT NULL
                    {account_tenant}
                    """,
                    tuple(account_params),
                ).fetchone()
                webhooks = connection.execute(
                    f"""
                    SELECT COUNT(*) AS deliveries,
                           COALESCE(SUM(duplicate_count), 0) AS duplicates
                    FROM connector_webhook_deliveries
                    WHERE received_at >= ?
                    {webhook_tenant}
                    """,
                    tuple(webhook_params),
                ).fetchone()
            status = ReliabilityStatus(
                queued_jobs=int(jobs["queued"] or 0),
                running_jobs=int(jobs["running"] or 0),
                failed_jobs=int(jobs["failed"] or 0),
                dead_lettered_jobs=int(jobs["dead_lettered"] or 0),
                stale_running_jobs=int(jobs["stale"] or 0),
                provider_accounts_in_error=int(accounts["count"] or 0),
                webhook_deliveries_24h=int(webhooks["deliveries"] or 0),
                webhook_duplicates_24h=int(webhooks["duplicates"] or 0),
                detail="Reliability counters were read from durable runtime state.",
            )
            metrics_service.update_reliability(**status.model_dump(exclude={"available", "detail"}))
            return status
        except Exception:
            return ReliabilityStatus(
                available=False,
                detail="Reliability counters are unavailable while the database is unreachable.",
            )

    def readiness(self) -> tuple[list[OperationalComponent], dict[str, bool]]:
        settings = get_settings()
        components: list[OperationalComponent] = []
        readiness: dict[str, bool] = {}

        try:
            with get_connection() as connection:
                connection.execute("SELECT 1").fetchone()
            database_ok = True
            database_detail = (
                "PostgreSQL is reachable."
                if is_postgres_database()
                else "SQLite is reachable."
            )
        except Exception:
            database_ok = False
            database_detail = "Database connectivity check failed."
        readiness["database"] = database_ok
        components.append(
            OperationalComponent(
                name="database",
                status="healthy" if database_ok else "unavailable",
                detail=database_detail,
            )
        )

        redis_required = settings.async_jobs_enabled or settings.rate_limit_backend == "redis"
        if redis_required:
            try:
                redis_ok = bool(
                    Redis.from_url(
                        settings.redis_url,
                        socket_connect_timeout=2,
                        socket_timeout=2,
                    ).ping()
                )
            except Exception:
                redis_ok = False
            readiness["redis"] = redis_ok
            components.append(
                OperationalComponent(
                    name="redis",
                    status="healthy" if redis_ok else "unavailable",
                    detail=(
                        "Redis is reachable."
                        if redis_ok
                        else "Redis connectivity check failed."
                    ),
                )
            )
        else:
            components.append(
                OperationalComponent(
                    name="redis",
                    status="not_configured",
                    detail="Redis is not required by the current runtime mode.",
                )
            )

        storage_ok, storage_detail = object_storage_service.health()
        readiness["object_storage"] = storage_ok
        components.append(
            OperationalComponent(
                name="object_storage",
                status="healthy" if storage_ok else "unavailable",
                detail=storage_detail,
            )
        )
        metrics_service.update_readiness(readiness)
        return components, readiness

    def backup_status(self) -> BackupStatus:
        settings = get_settings()
        if settings.object_storage_backend != "s3" or not settings.object_storage_bucket:
            return BackupStatus(
                configured=False,
                rpo_seconds=settings.backup_rpo_seconds,
                detail="Remote PostgreSQL backup monitoring is not configured.",
            )
        try:
            latest = object_storage_service.latest_backup_at()
        except ObjectStorageError:
            return BackupStatus(
                configured=True,
                rpo_seconds=settings.backup_rpo_seconds,
                stale=True,
                detail="The backup object inventory could not be checked.",
            )
        if latest is None:
            return BackupStatus(
                configured=True,
                rpo_seconds=settings.backup_rpo_seconds,
                stale=True,
                detail="No PostgreSQL backup objects were found.",
            )
        age_seconds = max(
            0,
            int(
                (
                    datetime.now(timezone.utc) - latest.astimezone(timezone.utc)
                ).total_seconds()
            ),
        )
        return BackupStatus(
            configured=True,
            latest_backup_at=latest,
            age_seconds=age_seconds,
            rpo_seconds=settings.backup_rpo_seconds,
            stale=age_seconds > settings.backup_rpo_seconds,
            detail=(
                "Latest backup is within the configured recovery-point objective."
                if age_seconds <= settings.backup_rpo_seconds
                else "Latest backup exceeds the configured recovery-point objective."
            ),
        )


operations_service = OperationsService()
