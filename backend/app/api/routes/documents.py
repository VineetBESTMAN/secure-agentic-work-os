from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.rbac import require_roles, require_scope
from app.core.security import get_current_user
from app.models.schemas import (
    AsyncJobResponse,
    DocumentDetail,
    DocumentRecord,
    DocumentUpdateRequest,
    RagAnswer,
    RagQuery,
    ReindexResponse,
)
from app.services.audit import audit_service
from app.services.background_tasks import BackgroundQueueError, background_task_service
from app.services.prompt_guard import prompt_guard_service
from app.services.rag import rag_service

router = APIRouter(prefix="/documents", tags=["documents"])


def _validate_classification(classification: str) -> None:
    if classification not in {"public", "internal", "restricted"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="classification must be public, internal, or restricted",
        )


@router.post("/upload", response_model=DocumentRecord)
async def upload_document(
    file: UploadFile = File(...),
    classification: str = Form(default="internal"),
    owner_team: str = Form(default="general"),
    user=Depends(get_current_user),
) -> DocumentRecord:
    require_scope(user.scopes, "documents:write")
    _validate_classification(classification)

    try:
        document = rag_service.ingest_file(
            filename=file.filename or "uploaded-document.txt",
            data=await file.read(),
            classification=classification,
            owner_team=owner_team,
            uploaded_by=user.user_id,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.upload",
        detail={
            "document_id": document.document_id,
            "filename": document.filename,
            "classification": document.classification,
            "unsafe": document.unsafe,
        },
        organization_id=user.organization_id,
    )
    return document


@router.post(
    "/upload/async",
    response_model=AsyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_document_upload(
    file: UploadFile = File(...),
    classification: str = Form(default="internal"),
    owner_team: str = Form(default="general"),
    user=Depends(get_current_user),
) -> AsyncJobResponse:
    require_scope(user.scopes, "documents:write")
    _validate_classification(classification)
    try:
        job = background_task_service.enqueue_document(
            filename=file.filename or "uploaded-document.txt",
            data=await file.read(),
            classification=classification,
            owner_team=owner_team,
            uploaded_by=user.user_id,
            organization_id=user.organization_id,
        )
    except BackgroundQueueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.upload_queued",
        detail={"job_id": job.job_id, "filename": file.filename or "uploaded-document.txt"},
        organization_id=user.organization_id,
    )
    return AsyncJobResponse(job=job, message="Document ingestion was queued.")


@router.post("/query", response_model=RagAnswer)
def query_documents(
    payload: RagQuery, user=Depends(get_current_user)
) -> RagAnswer:
    scan = prompt_guard_service.scan_text(payload.question)
    try:
        answer = rag_service.answer(
            question=payload.question,
            role=user.role,
            actor_id=user.user_id,
            organization_id=user.organization_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.query",
        detail={
            "question": payload.question,
            "role": user.role,
            "flagged": scan.flagged,
        },
        organization_id=user.organization_id,
    )
    return answer


@router.get("/library", response_model=list[DocumentRecord])
def list_documents(user=Depends(get_current_user)) -> list[DocumentRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.list",
        detail={"role": user.role},
        organization_id=user.organization_id,
    )
    return rag_service.list_documents(role=user.role, organization_id=user.organization_id)


@router.get("/unsafe", response_model=list[DocumentRecord])
def list_unsafe_documents(user=Depends(get_current_user)) -> list[DocumentRecord]:
    require_roles(user.role, allowed_roles={"admin", "manager"})
    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.unsafe_list",
        detail={"role": user.role},
        organization_id=user.organization_id,
    )
    return rag_service.list_unsafe_documents(user.organization_id)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document_detail(
    document_id: str, user=Depends(get_current_user)
) -> DocumentDetail:
    try:
        document = rag_service.get_document_detail(
            document_id=document_id, role=user.role, organization_id=user.organization_id
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.detail",
        detail={"document_id": document_id, "chunks": len(document.chunks)},
        organization_id=user.organization_id,
    )
    return document


@router.patch("/{document_id}", response_model=DocumentRecord)
def update_document(
    document_id: str,
    payload: DocumentUpdateRequest,
    user=Depends(get_current_user),
) -> DocumentRecord:
    require_scope(user.scopes, "documents:write")
    try:
        document = rag_service.update_document(
            document_id=document_id,
            payload=payload,
            role=user.role,
            organization_id=user.organization_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.update",
        detail={"document_id": document_id, "classification": document.classification},
        organization_id=user.organization_id,
    )
    return document


@router.post("/{document_id}/reindex", response_model=ReindexResponse)
def reindex_document(
    document_id: str, user=Depends(get_current_user)
) -> ReindexResponse:
    require_scope(user.scopes, "documents:write")
    try:
        document = rag_service.reindex_document(
            document_id=document_id, role=user.role, organization_id=user.organization_id
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.reindex",
        detail={"document_id": document_id, "chunks": document.chunk_count},
        organization_id=user.organization_id,
    )
    return ReindexResponse(document=document, message="Document reindexed successfully.")


@router.post(
    "/{document_id}/reindex-async",
    response_model=AsyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_document_reindex(
    document_id: str, user=Depends(get_current_user)
) -> AsyncJobResponse:
    require_scope(user.scopes, "documents:write")
    try:
        document = rag_service.get_document(
            document_id=document_id, organization_id=user.organization_id
        )
        if not rag_service.can_access_document(
            document=document, role=user.role, organization_id=user.organization_id
        ):
            raise PermissionError("You do not have access to this document.")
        job = background_task_service.enqueue_reindex(
            document_id=document_id,
            role=user.role,
            requested_by=user.user_id,
            organization_id=user.organization_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.reindex_queued",
        detail={"document_id": document_id, "job_id": job.job_id},
        organization_id=user.organization_id,
    )
    return AsyncJobResponse(job=job, message="Document reindex was queued.")


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str, user=Depends(get_current_user)) -> None:
    require_scope(user.scopes, "documents:write")
    try:
        rag_service.delete_document(
            document_id=document_id, role=user.role, organization_id=user.organization_id
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.delete",
        detail={"document_id": document_id},
        organization_id=user.organization_id,
    )
