from fastapi import APIRouter, Depends, HTTPException, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import ApprovalDecisionRequest, ApprovalRecord
from app.services.approval import approval_service
from app.services.audit import audit_service
from app.services.mcp_gateway import mcp_gateway_service
from app.services.workflows import workflow_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalRecord])
def list_pending_approvals(user=Depends(get_current_user)) -> list[ApprovalRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="approvals.list",
        detail={"role": user.role},
        organization_id=user.organization_id,
    )
    requested_by = None if user.role in {"admin", "manager"} else user.user_id
    return approval_service.list_requests(
        organization_id=user.organization_id, requested_by=requested_by
    )


@router.post("/{approval_id}/decision", response_model=ApprovalRecord)
def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    user=Depends(get_current_user),
) -> ApprovalRecord:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    try:
        decision = approval_service.decide(
            approval_id=approval_id,
            approved=payload.approved,
            reviewer_id=user.user_id,
            organization_id=user.organization_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found",
        )
    execution = mcp_gateway_service.apply_approval(approval_id, user.organization_id)
    workflow = (
        workflow_service.handle_execution_update(execution) if execution else None
    )
    audit_service.record(
        actor_id=user.user_id,
        event_type="approvals.decide",
        detail={
            "approval_id": approval_id,
            "approved": payload.approved,
            "execution_id": decision.execution_id or "",
            "execution_status": execution.status if execution else "not_linked",
            "workflow_id": workflow.workflow_id if workflow else "",
            "workflow_status": workflow.status if workflow else "not_linked",
        },
        organization_id=user.organization_id,
    )
    return decision
