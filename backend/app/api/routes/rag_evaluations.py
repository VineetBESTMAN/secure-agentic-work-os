from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import (
    RagEvaluationComparison,
    RagEvaluationDatasetCreate,
    RagEvaluationDatasetRecord,
    RagEvaluationRunRecord,
    RagEvaluationRunRequest,
)
from app.services.audit import audit_service
from app.services.rag_evaluation import rag_evaluation_service

router = APIRouter(prefix="/rag-evaluations", tags=["rag-evaluations"])


@router.get("/datasets", response_model=list[RagEvaluationDatasetRecord])
def list_evaluation_datasets(
    user=Depends(get_current_user),
) -> list[RagEvaluationDatasetRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return rag_evaluation_service.list_datasets(
        role=user.role,
        actor_id=user.user_id,
        organization_id=user.organization_id,
    )


@router.post(
    "/datasets",
    response_model=RagEvaluationDatasetRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_evaluation_dataset(
    payload: RagEvaluationDatasetCreate,
    user=Depends(get_current_user),
) -> RagEvaluationDatasetRecord:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    try:
        dataset = rag_evaluation_service.create_dataset(
            payload=payload,
            created_by=user.user_id,
            role=user.role,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A RAG evaluation dataset with this name already exists.",
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="rag_evaluation.dataset_created",
        detail={
            "dataset_id": dataset.dataset_id,
            "case_count": dataset.case_count,
            "document_count": len(dataset.document_ids),
        },
        organization_id=user.organization_id,
    )
    return dataset


@router.get("/datasets/{dataset_id}", response_model=RagEvaluationDatasetRecord)
def get_evaluation_dataset(
    dataset_id: str,
    user=Depends(get_current_user),
) -> RagEvaluationDatasetRecord:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    try:
        return rag_evaluation_service.get_dataset(
            dataset_id,
            role=user.role,
            actor_id=user.user_id,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/datasets/{dataset_id}/runs",
    response_model=RagEvaluationComparison,
    status_code=status.HTTP_201_CREATED,
)
def run_evaluation_dataset(
    dataset_id: str,
    payload: RagEvaluationRunRequest,
    user=Depends(get_current_user),
) -> RagEvaluationComparison:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    try:
        comparison = rag_evaluation_service.run_dataset(
            dataset_id=dataset_id,
            providers=payload.providers,
            created_by=user.user_id,
            role=user.role,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="rag_evaluation.run_completed",
        detail={
            "dataset_id": dataset_id,
            "comparison_id": comparison.comparison_id,
            "providers": payload.providers,
            "statuses": {run.provider: run.status for run in comparison.runs},
        },
        organization_id=user.organization_id,
    )
    return comparison


@router.get("/runs", response_model=list[RagEvaluationRunRecord])
def list_evaluation_runs(
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(get_current_user),
) -> list[RagEvaluationRunRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return rag_evaluation_service.list_runs(
        role=user.role,
        actor_id=user.user_id,
        limit=limit,
        organization_id=user.organization_id,
    )


@router.get("/runs/{run_id}", response_model=RagEvaluationRunRecord)
def get_evaluation_run(
    run_id: str,
    user=Depends(get_current_user),
) -> RagEvaluationRunRecord:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    try:
        return rag_evaluation_service.get_run(
            run_id,
            role=user.role,
            actor_id=user.user_id,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
