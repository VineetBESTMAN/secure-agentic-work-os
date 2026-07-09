from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import get_current_user
from app.models.schemas import ConnectorRecord, OAuthStartResponse
from app.services.audit import audit_service
from app.services.connectors import connector_service

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("", response_model=list[ConnectorRecord])
def list_connectors(user=Depends(get_current_user)) -> list[ConnectorRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.list",
        detail={"role": user.role},
    )
    return connector_service.list_connectors()


@router.post("/{provider}/authorize", response_model=OAuthStartResponse)
def authorize_connector(provider: str, user=Depends(get_current_user)) -> OAuthStartResponse:
    try:
        response = connector_service.start_authorization(provider=provider, user=user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.authorize",
        detail={"provider": provider, "configured": response.configured},
    )
    return response


@router.get("/{provider}/callback", response_model=ConnectorRecord)
async def connector_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
) -> ConnectorRecord:
    try:
        return await connector_service.complete_callback(
            provider=provider,
            code=code,
            state=state,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
