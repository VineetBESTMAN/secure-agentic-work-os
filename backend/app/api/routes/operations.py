from fastapi import APIRouter, Depends

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import OperationsStatus
from app.services.operations import operations_service


router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/status", response_model=OperationsStatus)
def get_operations_status(user=Depends(get_current_user)) -> OperationsStatus:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return operations_service.status()
