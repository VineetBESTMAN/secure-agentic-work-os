from fastapi import APIRouter, Depends, HTTPException, status

from app.core.rbac import require_roles
from app.core.security import get_current_user
from app.models.schemas import (
    OpenClawClientCreateRequest,
    OpenClawClientCredential,
    OpenClawClientRecord,
    OpenClawIntegrationStatus,
)
from app.services.audit import audit_service
from app.services.openclaw import openclaw_service


router = APIRouter(prefix="/openclaw", tags=["openclaw"])


@router.get("/status", response_model=OpenClawIntegrationStatus)
def integration_status(user=Depends(get_current_user)) -> OpenClawIntegrationStatus:
    require_roles(user.role, {"admin", "manager"})
    return openclaw_service.status(user.organization_id)


@router.get("/clients", response_model=list[OpenClawClientRecord])
def list_clients(user=Depends(get_current_user)) -> list[OpenClawClientRecord]:
    require_roles(user.role, {"admin", "manager"})
    return openclaw_service.list_clients(user.organization_id)


@router.post(
    "/clients",
    response_model=OpenClawClientCredential,
    status_code=status.HTTP_201_CREATED,
)
def create_client(
    payload: OpenClawClientCreateRequest,
    user=Depends(get_current_user),
) -> OpenClawClientCredential:
    require_roles(user.role, {"admin"})
    try:
        credential = openclaw_service.create_client(payload, user)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="openclaw.client_created",
        detail={
            "client_id": credential.client.client_id,
            "scopes": credential.client.scopes,
            "expires_at": credential.client.expires_at,
        },
        organization_id=user.organization_id,
    )
    return credential


@router.post(
    "/clients/{client_id}/rotate", response_model=OpenClawClientCredential
)
def rotate_client(
    client_id: str, user=Depends(get_current_user)
) -> OpenClawClientCredential:
    require_roles(user.role, {"admin"})
    try:
        credential = openclaw_service.rotate_client(client_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if credential is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OpenClaw client not found")
    audit_service.record(
        actor_id=user.user_id,
        event_type="openclaw.client_rotated",
        detail={"client_id": client_id},
        organization_id=user.organization_id,
    )
    return credential


@router.delete("/clients/{client_id}", response_model=OpenClawClientRecord)
def revoke_client(
    client_id: str, user=Depends(get_current_user)
) -> OpenClawClientRecord:
    require_roles(user.role, {"admin"})
    client = openclaw_service.revoke_client(client_id, user.organization_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OpenClaw client not found")
    audit_service.record(
        actor_id=user.user_id,
        event_type="openclaw.client_revoked",
        detail={"client_id": client_id},
        organization_id=user.organization_id,
    )
    return client
