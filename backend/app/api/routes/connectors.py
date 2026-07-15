from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.rbac import require_scope
from app.core.security import get_current_user
from app.models.schemas import (
    AsyncJobResponse,
    ConnectorImportRequest,
    ConnectorImportResponse,
    ConnectorRecord,
    GoogleDriveFileListResponse,
    GoogleDriveImportRequest,
    OAuthStartResponse,
)
from app.services.audit import audit_service
from app.services.background_tasks import BackgroundQueueError, background_task_service
from app.services.connectors import connector_service

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("", response_model=list[ConnectorRecord])
def list_connectors(user=Depends(get_current_user)) -> list[ConnectorRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.list",
        detail={"role": user.role},
        organization_id=user.organization_id,
    )
    return connector_service.list_connectors(user.organization_id)


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
        organization_id=user.organization_id,
    )
    return response


@router.post("/import", response_model=ConnectorImportResponse)
def import_connector_items(
    payload: ConnectorImportRequest,
    user=Depends(get_current_user),
) -> ConnectorImportResponse:
    require_scope(user.scopes, "documents:write")
    response = connector_service.import_items(payload=payload, user=user)
    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.import",
        detail={
            "provider": payload.provider,
            "items": len(payload.items),
            "job_id": response.job.job_id,
        },
        organization_id=user.organization_id,
    )
    return response


@router.post(
    "/import/async",
    response_model=AsyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_connector_items(
    payload: ConnectorImportRequest,
    user=Depends(get_current_user),
) -> AsyncJobResponse:
    require_scope(user.scopes, "documents:write")
    try:
        job = background_task_service.enqueue_connector_items(
            provider=payload.provider,
            items=payload.items,
            requested_by=user.user_id,
            organization_id=user.organization_id,
        )
    except BackgroundQueueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.import_queued",
        detail={
            "provider": payload.provider,
            "items": len(payload.items),
            "job_id": job.job_id,
        },
        organization_id=user.organization_id,
    )
    return AsyncJobResponse(job=job, message="Connector import was queued.")


@router.get("/google/drive/files", response_model=GoogleDriveFileListResponse)
async def list_google_drive_files(
    search: str | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=100),
    page_token: str | None = Query(default=None),
    user=Depends(get_current_user),
) -> GoogleDriveFileListResponse:
    require_scope(user.scopes, "documents:read")
    try:
        response = await connector_service.list_google_drive_files(
            search=search,
            page_size=page_size,
            page_token=page_token,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.google_drive_list",
        detail={"files": len(response.files), "search": search or ""},
        organization_id=user.organization_id,
    )
    return response


@router.post("/google/drive/import", response_model=ConnectorImportResponse)
async def import_google_drive_files(
    payload: GoogleDriveImportRequest,
    user=Depends(get_current_user),
) -> ConnectorImportResponse:
    require_scope(user.scopes, "documents:write")
    try:
        response = await connector_service.import_google_drive_files(payload=payload, user=user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="connectors.google_drive_import",
        detail={
            "files": len(payload.file_ids),
            "job_id": response.job.job_id,
            "documents": len(response.imported_documents),
        },
        organization_id=user.organization_id,
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
