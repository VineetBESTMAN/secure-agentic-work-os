from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.core.rbac import require_roles
from app.models.schemas import AuditEvent
from app.services.audit import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=list[AuditEvent])
def get_audit_events(user=Depends(get_current_user)) -> list[AuditEvent]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return audit_service.list_events(user.organization_id)
