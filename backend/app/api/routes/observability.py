from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import (
    CostBudgetCreateRequest,
    CostBudgetRecord,
    CostBudgetUpdateRequest,
    RuntimeObservation,
    RuntimeSummary,
)
from app.services.audit import audit_service
from app.services.observability import observability_service

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/summary", response_model=RuntimeSummary)
def get_runtime_summary(
    hours: int = Query(default=24, ge=1, le=24 * 90),
    user=Depends(get_current_user),
) -> RuntimeSummary:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return observability_service.summary(hours=hours)


@router.get("/events", response_model=list[RuntimeObservation])
def get_runtime_events(
    hours: int = Query(default=24, ge=1, le=24 * 90),
    limit: int = Query(default=200, ge=1, le=1_000),
    user=Depends(get_current_user),
) -> list[RuntimeObservation]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return observability_service.list_observations(hours=hours, limit=limit)


@router.get("/budgets", response_model=list[CostBudgetRecord])
def get_cost_budgets(user=Depends(get_current_user)) -> list[CostBudgetRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return observability_service.list_budgets()


@router.post(
    "/budgets",
    response_model=CostBudgetRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_cost_budget(
    payload: CostBudgetCreateRequest,
    user=Depends(get_current_user),
) -> CostBudgetRecord:
    require_roles(user.role, allowed_roles={"admin"})
    try:
        budget = observability_service.create_budget(payload, created_by=user.user_id)
    except Exception as exc:
        if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A cost budget with this name already exists.",
        ) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="observability.budget_created",
        detail={"budget_id": budget.budget_id, "limit_usd": budget.limit_usd},
    )
    return budget


@router.patch("/budgets/{budget_id}", response_model=CostBudgetRecord)
def update_cost_budget(
    budget_id: str,
    payload: CostBudgetUpdateRequest,
    user=Depends(get_current_user),
) -> CostBudgetRecord:
    require_roles(user.role, allowed_roles={"admin"})
    try:
        budget = observability_service.update_budget(budget_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="observability.budget_updated",
        detail={"budget_id": budget.budget_id, "enabled": budget.enabled},
    )
    return budget


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cost_budget(budget_id: str, user=Depends(get_current_user)) -> None:
    require_roles(user.role, allowed_roles={"admin"})
    if not observability_service.delete_budget(budget_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cost budget not found.",
        )
    audit_service.record(
        actor_id=user.user_id,
        event_type="observability.budget_deleted",
        detail={"budget_id": budget_id},
    )
