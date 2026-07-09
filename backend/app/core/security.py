from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import get_settings
from app.models.schemas import UserContext

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(
    subject: str,
    expires_delta: timedelta,
    claims: dict[str, object] | None = None,
) -> str:
    settings = get_settings()
    payload: dict[str, object] = {
        "sub": subject,
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> UserContext:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    subject = payload.get("sub")
    email = payload.get("email")
    role = payload.get("role")
    scopes = payload.get("scopes", [])
    if not subject or not email or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing required claims",
        )

    return UserContext(
        user_id=str(subject),
        email=str(email),
        role=str(role),
        scopes=[str(scope) for scope in scopes],
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserContext:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required",
        )
    return decode_access_token(credentials.credentials)
