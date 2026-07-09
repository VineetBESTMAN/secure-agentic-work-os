from fastapi import HTTPException, status


def require_roles(role: str, allowed_roles: set[str]) -> None:
    if role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this resource",
        )


def require_scope(user_scopes: list[str], required_scope: str) -> None:
    if required_scope not in user_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required scope: {required_scope}",
        )
