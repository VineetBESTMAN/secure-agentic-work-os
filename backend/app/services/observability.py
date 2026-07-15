from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timedelta, timezone
import math
from typing import Iterator
from uuid import uuid4

from app.core.database import decode_json, encode_json, get_connection, is_postgres_database
from app.models.schemas import (
    CostBudgetCreateRequest,
    CostBudgetRecord,
    CostBudgetUpdateRequest,
    ObservationBreakdown,
    RuntimeObservation,
    RuntimeSummary,
)

_actor_context: ContextVar[str] = ContextVar("observation_actor", default="system")
_trace_context: ContextVar[str | None] = ContextVar("observation_trace", default=None)
_organization_context: ContextVar[str] = ContextVar(
    "observation_organization", default="org_default"
)


class BudgetExceededError(ValueError):
    pass


class ObservabilityService:
    @contextmanager
    def context(
        self,
        actor_id: str,
        trace_id: str | None = None,
        organization_id: str = "org_default",
    ) -> Iterator[str]:
        trace = trace_id or f"trace_{uuid4().hex}"
        actor_token: Token[str] = _actor_context.set(actor_id)
        trace_token: Token[str | None] = _trace_context.set(trace)
        organization_token: Token[str] = _organization_context.set(organization_id)
        try:
            yield trace
        finally:
            _trace_context.reset(trace_token)
            _actor_context.reset(actor_token)
            _organization_context.reset(organization_token)

    @property
    def actor_id(self) -> str:
        return _actor_context.get()

    @property
    def trace_id(self) -> str:
        return _trace_context.get() or f"trace_{uuid4().hex}"

    @property
    def current_trace_id(self) -> str | None:
        return _trace_context.get()

    def record(
        self,
        *,
        operation_type: str,
        provider: str,
        model: str,
        status: str,
        latency_ms: float,
        input_units: int = 0,
        output_units: int = 0,
        estimated_cost_usd: float = 0.0,
        metadata: dict[str, object] | None = None,
        actor_id: str | None = None,
        trace_id: str | None = None,
        organization_id: str | None = None,
    ) -> RuntimeObservation:
        observation = RuntimeObservation(
            observation_id=f"obs_{uuid4().hex}",
            trace_id=trace_id or self.trace_id,
            operation_type=operation_type,
            actor_id=actor_id or self.actor_id,
            provider=provider,
            model=model,
            status=status,
            latency_ms=round(max(0.0, latency_ms), 3),
            input_units=max(0, input_units),
            output_units=max(0, output_units),
            estimated_cost_usd=round(max(0.0, estimated_cost_usd), 8),
            metadata=metadata or {},
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO runtime_observations (
                    observation_id, trace_id, operation_type, actor_id,
                    provider, model, status, latency_ms, input_units,
                    output_units, estimated_cost_usd, metadata_json, created_at,
                    organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.trace_id,
                    observation.operation_type,
                    observation.actor_id,
                    observation.provider,
                    observation.model,
                    observation.status,
                    observation.latency_ms,
                    observation.input_units,
                    observation.output_units,
                    observation.estimated_cost_usd,
                    encode_json(observation.metadata),
                    observation.created_at.isoformat(),
                    organization_id or _organization_context.get(),
                ),
            )
        return observation

    def record_safely(self, **kwargs) -> RuntimeObservation | None:
        """Keep telemetry failures from taking down the governed operation."""
        try:
            return self.record(**kwargs)
        except Exception:
            return None

    def list_observations(
        self, *, hours: int = 24, limit: int = 200, organization_id: str = "org_default"
    ) -> list[RuntimeObservation]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_observations
                WHERE created_at >= ? AND organization_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (self._database_timestamp(cutoff), organization_id, limit),
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def summary(
        self, *, hours: int = 24, organization_id: str = "org_default"
    ) -> RuntimeSummary:
        observations = self.list_observations(
            hours=hours, limit=10_000, organization_id=organization_id
        )
        latencies = sorted(item.latency_ms for item in observations)
        completed = sum(item.status == "completed" for item in observations)
        failed = sum(item.status == "failed" for item in observations)
        blocked = sum(item.status in {"blocked", "rejected", "cancelled"} for item in observations)
        grouped: dict[tuple[str, str, str], list[RuntimeObservation]] = {}
        for item in observations:
            grouped.setdefault((item.operation_type, item.provider, item.model), []).append(item)

        breakdown = []
        for (operation_type, provider, model), items in sorted(grouped.items()):
            breakdown.append(
                ObservationBreakdown(
                    operation_type=operation_type,
                    provider=provider,
                    model=model,
                    operations=len(items),
                    completed=sum(item.status == "completed" for item in items),
                    failed_or_blocked=sum(item.status != "completed" for item in items),
                    average_latency_ms=round(
                        sum(item.latency_ms for item in items) / len(items), 3
                    ),
                    estimated_cost_usd=round(
                        sum(item.estimated_cost_usd for item in items), 8
                    ),
                )
            )

        total = len(observations)
        return RuntimeSummary(
            window_hours=hours,
            total_operations=total,
            completed_operations=completed,
            failed_operations=failed,
            blocked_operations=blocked,
            success_rate=round((completed / total * 100) if total else 0.0, 2),
            average_latency_ms=round(
                (sum(latencies) / len(latencies)) if latencies else 0.0, 3
            ),
            p95_latency_ms=round(self._percentile(latencies, 0.95), 3),
            input_units=sum(item.input_units for item in observations),
            output_units=sum(item.output_units for item in observations),
            estimated_cost_usd=round(
                sum(item.estimated_cost_usd for item in observations), 8
            ),
            breakdown=breakdown,
            budgets=self.list_budgets(organization_id),
        )

    def seed_defaults(
        self, limit_usd: float, organization_id: str = "org_default"
    ) -> None:
        if self.list_budgets(organization_id):
            return
        self.create_budget(
            CostBudgetCreateRequest(
                name="Daily AI runtime budget",
                period="daily",
                limit_usd=limit_usd,
                warning_percent=80,
                enabled=True,
            ),
            created_by="system",
            organization_id=organization_id,
        )

    def create_budget(
        self,
        payload: CostBudgetCreateRequest,
        *,
        created_by: str,
        organization_id: str = "org_default",
    ) -> CostBudgetRecord:
        budget_id = f"budget_{uuid4().hex}"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO cost_budgets (
                    budget_id, name, period, limit_usd, warning_percent,
                    enabled, created_by, organization_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    budget_id,
                    payload.name.strip(),
                    payload.period,
                    payload.limit_usd,
                    payload.warning_percent,
                    payload.enabled,
                    created_by,
                    organization_id,
                ),
            )
        budget = self.get_budget(budget_id, organization_id)
        if budget is None:  # pragma: no cover
            raise RuntimeError("Cost budget could not be persisted.")
        return budget

    def update_budget(
        self,
        budget_id: str,
        payload: CostBudgetUpdateRequest,
        organization_id: str = "org_default",
    ) -> CostBudgetRecord:
        existing = self.get_budget(budget_id, organization_id)
        if existing is None:
            raise ValueError("Cost budget not found.")
        updates = payload.model_dump(exclude_none=True)
        if not updates:
            return existing
        fields = [f"{field} = ?" for field in updates]
        params = list(updates.values())
        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.extend((budget_id, organization_id))
        with get_connection() as connection:
            connection.execute(
                f"UPDATE cost_budgets SET {', '.join(fields)} WHERE budget_id = ? AND organization_id = ?",
                params,
            )
        updated = self.get_budget(budget_id, organization_id)
        if updated is None:  # pragma: no cover
            raise RuntimeError("Cost budget disappeared during update.")
        return updated

    def delete_budget(
        self, budget_id: str, organization_id: str = "org_default"
    ) -> bool:
        with get_connection() as connection:
            cursor = connection.execute(
                "DELETE FROM cost_budgets WHERE budget_id = ? AND organization_id = ?",
                (budget_id, organization_id),
            )
            return cursor.rowcount > 0

    def get_budget(
        self, budget_id: str, organization_id: str = "org_default"
    ) -> CostBudgetRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM cost_budgets WHERE budget_id = ? AND organization_id = ?",
                (budget_id, organization_id),
            ).fetchone()
        return self._row_to_budget(row) if row is not None else None

    def list_budgets(
        self, organization_id: str = "org_default"
    ) -> list[CostBudgetRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM cost_budgets WHERE organization_id = ? ORDER BY created_at ASC",
                (organization_id,),
            ).fetchall()
        return [self._row_to_budget(row) for row in rows]

    def assert_budget_available(
        self, additional_cost_usd: float, organization_id: str | None = None
    ) -> None:
        if additional_cost_usd <= 0:
            return
        for budget in self.list_budgets(organization_id or _organization_context.get()):
            if budget.enabled and budget.spent_usd + additional_cost_usd > budget.limit_usd:
                raise BudgetExceededError(
                    f"{budget.name} would be exceeded by this operation "
                    f"(${budget.spent_usd:.4f} spent of ${budget.limit_usd:.4f})."
                )

    def _row_to_budget(self, row) -> CostBudgetRecord:
        period_start, period_end = self._period_bounds(row["period"])
        with get_connection() as connection:
            spent_row = connection.execute(
                """
                SELECT COALESCE(SUM(estimated_cost_usd), 0) AS spent
                FROM runtime_observations
                WHERE created_at >= ? AND created_at < ? AND organization_id = ?
                """,
                (
                    self._database_timestamp(period_start),
                    self._database_timestamp(period_end),
                    row["organization_id"],
                ),
            ).fetchone()
        spent = float(spent_row["spent"] or 0.0)
        limit_usd = float(row["limit_usd"])
        utilization = (spent / limit_usd * 100) if limit_usd > 0 else 100.0
        if utilization >= 100:
            state = "exceeded"
        elif utilization >= int(row["warning_percent"]):
            state = "warning"
        else:
            state = "ok"
        return CostBudgetRecord(
            budget_id=row["budget_id"],
            name=row["name"],
            period=row["period"],
            limit_usd=limit_usd,
            warning_percent=int(row["warning_percent"]),
            enabled=bool(row["enabled"]),
            created_by=row["created_by"],
            spent_usd=round(spent, 8),
            remaining_usd=round(max(0.0, limit_usd - spent), 8),
            utilization_percent=round(utilization, 2),
            state=state,
            period_start=period_start,
            period_end=period_end,
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )

    @staticmethod
    def _row_to_observation(row) -> RuntimeObservation:
        return RuntimeObservation(
            observation_id=row["observation_id"],
            trace_id=row["trace_id"],
            operation_type=row["operation_type"],
            actor_id=row["actor_id"],
            provider=row["provider"],
            model=row["model"],
            status=row["status"],
            latency_ms=float(row["latency_ms"]),
            input_units=int(row["input_units"]),
            output_units=int(row["output_units"]),
            estimated_cost_usd=float(row["estimated_cost_usd"]),
            metadata=decode_json(row["metadata_json"], {}),
            created_at=row["created_at"],
        )

    @staticmethod
    def _period_bounds(period: str) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        if period == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
            return start, end
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        index = max(0, math.ceil(len(values) * percentile) - 1)
        return values[index]

    @staticmethod
    def _database_timestamp(value: datetime) -> datetime | str:
        return value if is_postgres_database() else value.isoformat()


observability_service = ObservabilityService()
