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

    @staticmethod
    def render() -> bytes:
        return generate_latest()


metrics_service = MetricsService()
