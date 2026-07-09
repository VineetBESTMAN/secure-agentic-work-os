from datetime import timedelta

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.schemas import LoginRequest, TokenResponse
from app.services.users import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = user_service.authenticate(payload.email.lower(), payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    settings = get_settings()
    token = create_access_token(
        subject=user.user_id,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        claims={"email": user.email, "role": user.role, "scopes": user.scopes},
    )
    return TokenResponse(access_token=token, user=user)
