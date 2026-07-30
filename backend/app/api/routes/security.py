from fastapi import APIRouter, Depends, HTTPException, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import (
    KeyRotationRequest,
    KeyRotationResult,
    KeyRotationStatus,
    RetentionExecuteRequest,
    RetentionExecutionResult,
    RetentionPreview,
    SecurityFindingRecord,
    SecurityPolicyRecord,
    SecurityPolicyUpdateRequest,
    SecurityPosture,
)
from app.services.audit import audit_service
from app.services.security_admin import security_admin_service
from app.services.security_controls import security_control_service

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/posture", response_model=SecurityPosture)
def get_security_posture(user=Depends(get_current_user)) -> SecurityPosture:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return security_admin_service.posture(user.organization_id)


@router.get("/policy", response_model=SecurityPolicyRecord)
def get_security_policy(user=Depends(get_current_user)) -> SecurityPolicyRecord:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return security_control_service.get_policy(user.organization_id)


@router.put("/policy", response_model=SecurityPolicyRecord)
def update_security_policy(
    payload: SecurityPolicyUpdateRequest,
    user=Depends(get_current_user),
) -> SecurityPolicyRecord:
    require_roles(user.role, allowed_roles={"admin"})
    policy = security_control_service.update_policy(
        user.organization_id, user.user_id, payload
    )
    audit_service.record(
        actor_id=user.user_id,
        event_type="security.policy_updated",
        detail={
            "dlp_mode": policy.dlp_mode,
            "malware_mode": policy.malware_mode,
            "retention_enabled": policy.retention_enabled,
        },
        organization_id=user.organization_id,
    )
    return policy


@router.get("/findings", response_model=list[SecurityFindingRecord])
def list_security_findings(
    limit: int = 100, user=Depends(get_current_user)
) -> list[SecurityFindingRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return security_control_service.list_findings(user.organization_id, limit)


@router.get("/retention/preview", response_model=RetentionPreview)
def preview_retention(user=Depends(get_current_user)) -> RetentionPreview:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    return security_admin_service.retention_preview(user.organization_id)


@router.post("/retention/execute", response_model=RetentionExecutionResult)
def execute_retention(
    payload: RetentionExecuteRequest,
    user=Depends(get_current_user),
) -> RetentionExecutionResult:
    require_roles(user.role, allowed_roles={"admin"})
    try:
        result = security_admin_service.execute_retention(
            user.organization_id, confirmed=payload.confirm
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="security.retention_executed",
        detail={
            "deleted_by_category": result.deleted_by_category,
            "total_deleted": result.total_deleted,
        },
        organization_id=user.organization_id,
    )
    return result


@router.get("/key-rotation", response_model=KeyRotationStatus)
def get_key_rotation_status(
    user=Depends(get_current_user),
) -> KeyRotationStatus:
    require_roles(user.role, allowed_roles={"admin"})
    return security_admin_service.key_rotation_status(user.organization_id)


@router.post("/key-rotation", response_model=KeyRotationResult)
def rotate_encryption_keys(
    payload: KeyRotationRequest,
    user=Depends(get_current_user),
) -> KeyRotationResult:
    require_roles(user.role, allowed_roles={"admin"})
    try:
        result = security_admin_service.rotate_keys(
            user.organization_id, confirmed=payload.confirm
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="security.encryption_keys_rotated",
        detail={
            "active_key_id": result.active_key_id,
            "rotated_ciphertexts": result.rotated_ciphertexts,
        },
        organization_id=user.organization_id,
    )
    return result
