from fastapi import APIRouter, Depends, HTTPException, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import JobRecord
from app.services.audit import audit_service
from app.services.jobs import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRecord)
def get_job(job_id: str, user=Depends(get_current_user)) -> JobRecord:
    try:
        job = job_service.get(job_id, user.organization_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if user.role not in {"admin", "manager"} and job.created_by != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this job.",
        )
    return job


@router.get("", response_model=list[JobRecord])
def list_jobs(user=Depends(get_current_user)) -> list[JobRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    audit_service.record(
        actor_id=user.user_id,
        event_type="jobs.list",
        detail={"role": user.role},
        organization_id=user.organization_id,
    )
    return job_service.list_jobs(user.organization_id)
