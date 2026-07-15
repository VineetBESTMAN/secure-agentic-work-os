from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import get_settings
from app.models.schemas import UserContext
from app.services.users import user_service

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(
    subject: str,
    expires_delta: timedelta,
    claims: dict[str, object] | None = None,
) -> str:
    settings = get_settings()
    payload: dict[str, object] = {
        "sub": subject,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
        "type": "access",
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_user_access_token(user: UserContext, session_id: str) -> str:
    settings = get_settings()
    return create_access_token(
        subject=user.user_id,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        claims={
            "sid": session_id,
            "org": user.organization_id,
            "mem": user.membership_id,
            "ver": _token_version(user.user_id),
        },
    )


def decode_access_token(token: str) -> UserContext:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require_sub": True, "require_exp": True},
        )
    except JWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc

    if payload.get("type") != "access":
        raise _unauthorized("Invalid token type")
    subject = payload.get("sub")
    session_id = payload.get("sid")
    organization_id = payload.get("org")
    membership_id = payload.get("mem")
    token_version = payload.get("ver")
    if not all((subject, session_id, organization_id, membership_id)) or token_version is None:
        raise _unauthorized("Token is missing required security claims")
    try:
        version = int(token_version)
    except (TypeError, ValueError) as exc:
        raise _unauthorized("Token has an invalid security version") from exc
    user = user_service.get_session_user(
        str(session_id), str(subject), str(organization_id), version
    )
    if user is None or user.membership_id != str(membership_id):
        raise _unauthorized("Session or organization membership is no longer active")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserContext:
    if credentials is None:
        raise _unauthorized("Authorization header is required")
    return decode_access_token(credentials.credentials)


def get_access_token_payload(token: str) -> dict[str, object]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require_sub": True, "require_exp": True},
        )
    except JWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc


def _token_version(user_id: str) -> int:
    from app.core.database import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT token_version FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        raise _unauthorized("User account is unavailable")
    return int(row["token_version"])


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
