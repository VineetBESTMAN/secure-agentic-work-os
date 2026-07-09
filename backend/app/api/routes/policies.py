from fastapi import APIRouter, Depends

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import PolicyCreateRequest, PolicyRecord
from app.services.audit import audit_service
from app.services.policies import policy_service

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=list[PolicyRecord])
def list_policies(user=Depends(get_current_user)) -> list[PolicyRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    audit_service.record(
        actor_id=user.user_id,
        event_type="policies.list",
        detail={"role": user.role},
    )
    return policy_service.list_policies()


@router.post("", response_model=PolicyRecord)
def create_policy(
    payload: PolicyCreateRequest,
    user=Depends(get_current_user),
) -> PolicyRecord:
    require_roles(user.role, allowed_roles={"admin"})
    policy = policy_service.create_policy(payload)
    audit_service.record(
        actor_id=user.user_id,
        event_type="policies.create",
        detail={"policy_id": policy.policy_id, "rule_type": policy.rule_type},
    )
    return policy
