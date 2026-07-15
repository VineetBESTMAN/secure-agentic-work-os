from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import get_settings
from app.core.security import (
    bearer_scheme,
    create_user_access_token,
    get_access_token_payload,
    get_current_user,
)
from app.models.schemas import (
    InvitationAcceptRequest,
    LoginRequest,
    LogoutRequest,
    OrganizationSwitchRequest,
    OIDCAuthorizationResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserContext,
)
from app.services.users import user_service
from app.services.oidc import oidc_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(
    user: UserContext, session_id: str, refresh_token: str
) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=create_user_access_token(user, session_id),
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=user,
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = user_service.authenticate(
        payload.email.lower(), payload.password, payload.organization_slug
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or inactive organization membership",
        )
    settings = get_settings()
    session_id, refresh_token = user_service.create_session(
        user, settings.refresh_token_expire_days
    )
    return _token_response(user, session_id, refresh_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshTokenRequest) -> TokenResponse:
    settings = get_settings()
    rotated = user_service.rotate_session(
        payload.refresh_token, settings.refresh_token_expire_days
    )
    if rotated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is invalid, expired, or already used",
        )
    user, session_id, refresh_token = rotated
    return _token_response(user, session_id, refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    _: LogoutRequest,
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    if credentials is None:
        return
    payload = get_access_token_payload(credentials.credentials)
    user_service.revoke_session(str(payload.get("sid", "")), user.user_id)


@router.post("/switch-organization", response_model=TokenResponse)
def switch_organization(
    payload: OrganizationSwitchRequest,
    user=Depends(get_current_user),
) -> TokenResponse:
    target_user = user_service.get_by_id(user.user_id, payload.organization_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have an active membership in that organization",
        )
    settings = get_settings()
    session_id, refresh_token = user_service.create_session(
        target_user, settings.refresh_token_expire_days
    )
    return _token_response(target_user, session_id, refresh_token)


@router.post("/invitations/accept", response_model=TokenResponse)
def accept_invitation(payload: InvitationAcceptRequest) -> TokenResponse:
    try:
        user = user_service.accept_invitation(
            payload.token, payload.display_name, payload.password
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    settings = get_settings()
    session_id, refresh_token = user_service.create_session(
        user, settings.refresh_token_expire_days
    )
    return _token_response(user, session_id, refresh_token)


@router.get(
    "/oidc/{provider_id}/authorize", response_model=OIDCAuthorizationResponse
)
async def start_oidc_authorization(provider_id: str) -> OIDCAuthorizationResponse:
    try:
        return await oidc_service.start_authorization(provider_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/oidc/{provider_id}/callback", response_model=TokenResponse)
async def complete_oidc_authorization(
    provider_id: str, code: str = Query(...), state: str = Query(...)
) -> TokenResponse:
    try:
        email, organization_id = await oidc_service.complete_authorization(
            provider_id, code, state
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    user = user_service.get_by_email(email, organization_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your verified identity does not have an active membership in this organization",
        )
    settings = get_settings()
    session_id, refresh_token = user_service.create_session(
        user, settings.refresh_token_expire_days
    )
    return _token_response(user, session_id, refresh_token)
