from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.rbac import require_scope
from app.core.security import get_current_user
from app.models.schemas import DocumentRecord, RagAnswer, RagQuery
from app.services.audit import audit_service
from app.services.prompt_guard import prompt_guard_service
from app.services.rag import rag_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentRecord)
async def upload_document(
    file: UploadFile = File(...),
    classification: str = Form(default="internal"),
    owner_team: str = Form(default="general"),
    user=Depends(get_current_user),
) -> DocumentRecord:
    require_scope(user.scopes, "documents:write")
    if classification not in {"public", "internal", "restricted"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="classification must be public, internal, or restricted",
        )

    try:
        document = rag_service.ingest_file(
            filename=file.filename or "uploaded-document.txt",
            data=await file.read(),
            classification=classification,
            owner_team=owner_team,
            uploaded_by=user.user_id,
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
    )
    return document


@router.post("/query", response_model=RagAnswer)
def query_documents(
    payload: RagQuery, user=Depends(get_current_user)
) -> RagAnswer:
    scan = prompt_guard_service.scan_text(payload.question)
    answer = rag_service.answer(question=payload.question, role=user.role)
    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.query",
        detail={
            "question": payload.question,
            "role": user.role,
            "flagged": scan.flagged,
        },
    )
    return answer


@router.get("/library", response_model=list[DocumentRecord])
def list_documents(user=Depends(get_current_user)) -> list[DocumentRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="documents.list",
        detail={"role": user.role},
    )
    return rag_service.list_documents(role=user.role)
