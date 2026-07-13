from pathlib import Path
from typing import Any

from rq import get_current_job

from app.models.schemas import ConnectorImportItem
from app.services.jobs import job_service
from app.services.rag import rag_service


def _record_task_failure(job_id: str, error: Exception) -> bool:
    current_job = get_current_job()
    if current_job is not None and current_job.retries_left:
        job_service.update(
            job_id,
            status="queued",
            result={
                "progress": 0,
                "message": "Task failed and will be retried.",
                "error": str(error),
            },
        )
        return True
    job_service.fail(job_id, error)
    return False


def ingest_document_task(
    job_id: str,
    staged_path: str,
    filename: str,
    classification: str,
    owner_team: str,
    uploaded_by: str,
) -> dict[str, object]:
    path = Path(staged_path)
    completed = False
    job_service.update(
        job_id,
        status="running",
        result={"progress": 10, "message": "Reading uploaded file."},
    )
    try:
        data = path.read_bytes()
        job_service.update(
            job_id,
            status="running",
            result={"progress": 35, "message": "Extracting and embedding document."},
        )
        document = rag_service.ingest_file(
            filename=filename,
            data=data,
            classification=classification,
            owner_team=owner_team,
            uploaded_by=uploaded_by,
            document_id=f"doc_{job_id.removeprefix('job_')}",
        )
        result = {
            "progress": 100,
            "message": "Document ingestion completed.",
            "document_id": document.document_id,
            "filename": document.filename,
            "chunk_count": document.chunk_count,
        }
        job_service.update(job_id, status="completed", result=result)
        completed = True
        return result
    except Exception as exc:
        retrying = _record_task_failure(job_id, exc)
        if not retrying:
            path.unlink(missing_ok=True)
        raise
    finally:
        if completed:
            path.unlink(missing_ok=True)


def reindex_document_task(job_id: str, document_id: str, role: str) -> dict[str, object]:
    job_service.update(
        job_id,
        status="running",
        result={"progress": 20, "message": "Rebuilding searchable chunks."},
    )
    try:
        document = rag_service.reindex_document(document_id=document_id, role=role)
        result = {
            "progress": 100,
            "message": "Document reindex completed.",
            "document_id": document.document_id,
            "chunk_count": document.chunk_count,
        }
        job_service.update(job_id, status="completed", result=result)
        return result
    except Exception as exc:
        _record_task_failure(job_id, exc)
        raise


def ingest_connector_items_task(
    job_id: str,
    items: list[dict[str, Any]],
    uploaded_by: str,
) -> dict[str, object]:
    job_service.update(
        job_id,
        status="running",
        result={"progress": 5, "message": "Starting connector import."},
    )
    document_ids: list[str] = []
    try:
        total = max(1, len(items))
        for index, raw_item in enumerate(items):
            item = ConnectorImportItem(**raw_item)
            document = rag_service.ingest_file(
                filename=item.filename,
                data=item.content.encode("utf-8"),
                classification=item.classification,
                owner_team=item.owner_team,
                uploaded_by=uploaded_by,
                document_id=f"doc_{job_id.removeprefix('job_')}_{index}",
            )
            document_ids.append(document.document_id)
            job_service.update(
                job_id,
                status="running",
                result={
                    "progress": min(95, int(((index + 1) / total) * 90)),
                    "message": f"Imported {index + 1} of {len(items)} items.",
                    "document_ids": document_ids,
                },
            )
        result = {
            "progress": 100,
            "message": "Connector import completed.",
            "imported_documents": len(document_ids),
            "document_ids": document_ids,
        }
        job_service.update(job_id, status="completed", result=result)
        return result
    except Exception as exc:
        _record_task_failure(job_id, exc)
        raise
