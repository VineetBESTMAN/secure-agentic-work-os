from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.schemas import ApprovalDecisionRequest, ApprovalRecord
from app.services.approval import approval_service
from app.services.audit import audit_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalRecord])
def list_pending_approvals(user=Depends(get_current_user)) -> list[ApprovalRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="approvals.list",
        detail={"role": user.role},
    )
    return approval_service.list_requests()


@router.post("/{approval_id}/decision", response_model=ApprovalRecord)
def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    user=Depends(get_current_user),
) -> ApprovalRecord:
    decision = approval_service.decide(
        approval_id=approval_id,
        approved=payload.approved,
        reviewer_id=user.user_id,
    )
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found",
        )
    audit_service.record(
        actor_id=user.user_id,
        event_type="approvals.decide",
        detail={"approval_id": approval_id, "approved": payload.approved},
    )
    return decision
