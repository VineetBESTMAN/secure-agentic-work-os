from pathlib import Path
from typing import Any

from redis import Redis
from rq import Queue, Retry

from app.core.config import get_settings
from app.models.schemas import ConnectorImportItem, JobRecord
from app.services.jobs import job_service
from app.services.tasks import (
    ingest_connector_items_task,
    ingest_document_task,
    reindex_document_task,
)


class BackgroundQueueError(ValueError):
    pass


class BackgroundTaskService:
    def enqueue_document(
        self,
        filename: str,
        data: bytes,
        classification: str,
        owner_team: str,
        uploaded_by: str,
    ) -> JobRecord:
        safe_filename = Path(filename).name or "uploaded-document.txt"
        job = job_service.create(
            job_type="document.ingest",
            detail={
                "filename": safe_filename,
                "classification": classification,
                "owner_team": owner_team,
                "size_bytes": len(data),
            },
            created_by=uploaded_by,
        )
        staged_path = self._staging_path(job.job_id, safe_filename)
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_bytes(data)
        try:
            return self._dispatch(
                job=job,
                task=ingest_document_task,
                args=(
                    job.job_id,
                    str(staged_path),
                    safe_filename,
                    classification,
                    owner_team,
                    uploaded_by,
                ),
            )
        except BackgroundQueueError:
            staged_path.unlink(missing_ok=True)
            raise

    def enqueue_reindex(self, document_id: str, role: str, requested_by: str) -> JobRecord:
        job = job_service.create(
            job_type="document.reindex",
            detail={"document_id": document_id},
            created_by=requested_by,
        )
        return self._dispatch(
            job=job,
            task=reindex_document_task,
            args=(job.job_id, document_id, role),
        )

    def enqueue_connector_items(
        self,
        provider: str,
        items: list[ConnectorImportItem],
        requested_by: str,
    ) -> JobRecord:
        serialized_items = [item.model_dump() for item in items]
        job = job_service.create(
            job_type=f"{provider}.import",
            detail={"provider": provider, "items": len(items)},
            created_by=requested_by,
        )
        return self._dispatch(
            job=job,
            task=ingest_connector_items_task,
            args=(job.job_id, serialized_items, requested_by),
        )

    def _dispatch(self, job: JobRecord, task: Any, args: tuple[Any, ...]) -> JobRecord:
        settings = get_settings()
        if not settings.async_jobs_enabled:
            task(*args)
            return job_service.get(job.job_id)

        try:
            queue = Queue(
                name=settings.job_queue_name,
                connection=Redis.from_url(settings.redis_url),
                default_timeout=settings.job_timeout_seconds,
            )
            queue.enqueue(
                task,
                *args,
                job_id=job.job_id,
                retry=Retry(max=3),
                result_ttl=86400,
                failure_ttl=604800,
            )
        except Exception as exc:
            if not settings.async_jobs_fallback_sync:
                job_service.fail(job.job_id, exc)
                raise BackgroundQueueError("The background queue is unavailable.") from exc
            task(*args)
        return job_service.get(job.job_id)

    def _staging_path(self, job_id: str, filename: str) -> Path:
        return Path(get_settings().upload_dir) / "staging" / f"{job_id}_{filename}"


background_task_service = BackgroundTaskService()
