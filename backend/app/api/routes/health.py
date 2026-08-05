import secrets

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.core.config import get_settings
from app.services.metrics import metrics_service
from app.services.operations import operations_service

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness_check(response: Response) -> dict[str, object]:
    components, readiness = operations_service.readiness()
    ready = all(readiness.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not_ready",
        "components": [component.model_dump(mode="json") for component in components],
    }


@router.get("/metrics")
def prometheus_metrics(authorization: str | None = Header(default=None)) -> Response:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    if settings.metrics_token:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            token, settings.metrics_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Metrics authentication failed.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    operations_service.status()
    return Response(content=metrics_service.render(), media_type=metrics_service.content_type)
