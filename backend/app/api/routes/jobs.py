from fastapi import APIRouter, Depends

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import JobRecord
from app.services.audit import audit_service
from app.services.jobs import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRecord])
def list_jobs(user=Depends(get_current_user)) -> list[JobRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    audit_service.record(
        actor_id=user.user_id,
        event_type="jobs.list",
        detail={"role": user.role},
    )
    return job_service.list_jobs()
