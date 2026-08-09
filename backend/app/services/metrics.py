from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


HTTP_REQUESTS = Counter(
    "workos_http_requests_total",
    "HTTP requests handled by Secure Agentic Work OS.",
    ("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    "workos_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
READINESS = Gauge(
    "workos_readiness",
    "Whether all required runtime dependencies are ready.",
)
COMPONENT_HEALTH = Gauge(
    "workos_component_health",
    "Dependency health by component (1 healthy, 0 unhealthy).",
    ("component",),
)
BACKUP_AGE = Gauge(
    "workos_backup_age_seconds",
    "Age of the latest verified database backup object in seconds.",
)
BACKUP_CONFIGURED = Gauge(
    "workos_backup_configured",
    "Whether remote backup status monitoring is configured.",
)
BACKUP_RPO = Gauge(
    "workos_backup_rpo_seconds",
    "Configured database backup recovery-point objective in seconds.",
)
BACKGROUND_JOBS = Gauge(
    "workos_background_jobs",
    "Persisted background jobs by terminal or active state.",
    ("status",),
)
STALE_RUNNING_JOBS = Gauge(
    "workos_stale_running_jobs",
    "Running jobs whose heartbeat exceeded the configured threshold.",
)
PROVIDER_ACCOUNTS_IN_ERROR = Gauge(
    "workos_provider_accounts_in_error",
    "Connected provider accounts with a recorded synchronization error.",
)
WEBHOOK_DELIVERIES_24H = Gauge(
    "workos_webhook_deliveries_24h",
    "Accepted provider webhook deliveries in the previous 24 hours.",
)
WEBHOOK_DUPLICATES_24H = Gauge(
    "workos_webhook_duplicates_24h",
    "Deduplicated provider webhook replays in the previous 24 hours.",
)


class MetricsService:
    content_type = CONTENT_TYPE_LATEST

    def observe_request(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        safe_route = route if route.startswith("/") else "<unmatched>"
        HTTP_REQUESTS.labels(method=method, route=safe_route, status=str(status_code)).inc()
        HTTP_LATENCY.labels(method=method, route=safe_route).observe(
            max(0.0, duration_seconds)
        )

    def update_readiness(self, components: dict[str, bool]) -> None:
        ready = bool(components) and all(components.values())
        READINESS.set(1 if ready else 0)
        for component, healthy in components.items():
            COMPONENT_HEALTH.labels(component=component).set(1 if healthy else 0)

    def update_backup(
        self, *, configured: bool, age_seconds: int | None, rpo_seconds: int
    ) -> None:
        BACKUP_CONFIGURED.set(1 if configured else 0)
        BACKUP_AGE.set(age_seconds if age_seconds is not None else -1)
        BACKUP_RPO.set(rpo_seconds)

    def update_reliability(
        self,
        *,
        queued_jobs: int,
        running_jobs: int,
        failed_jobs: int,
        dead_lettered_jobs: int,
        stale_running_jobs: int,
        provider_accounts_in_error: int,
        webhook_deliveries_24h: int,
        webhook_duplicates_24h: int,
    ) -> None:
        for status, count in {
            "queued": queued_jobs,
            "running": running_jobs,
            "failed": failed_jobs,
            "dead_lettered": dead_lettered_jobs,
        }.items():
            BACKGROUND_JOBS.labels(status=status).set(count)
        STALE_RUNNING_JOBS.set(stale_running_jobs)
        PROVIDER_ACCOUNTS_IN_ERROR.set(provider_accounts_in_error)
        WEBHOOK_DELIVERIES_24H.set(webhook_deliveries_24h)
        WEBHOOK_DUPLICATES_24H.set(webhook_duplicates_24h)

    @staticmethod
    def render() -> bytes:
        return generate_latest()


metrics_service = MetricsService()
