from pathlib import Path
from typing import Any

from redis import Redis
from rq import Queue, Retry

from app.core.config import get_settings
from app.models.schemas import ConnectorImportItem, JobRecord
from app.services.jobs import job_service
from app.services.security_controls import security_control_service
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
        organization_id: str = "org_default",
    ) -> JobRecord:
        safe_filename = Path(filename).name or "uploaded-document.txt"
        security_control_service.preflight_upload(
            filename=safe_filename,
            data=data,
            actor_id=uploaded_by,
            organization_id=organization_id,
        )
        job = job_service.create(
            job_type="document.ingest",
            detail={
                "filename": safe_filename,
                "classification": classification,
                "owner_team": owner_team,
                "size_bytes": len(data),
            },
            created_by=uploaded_by,
            organization_id=organization_id,
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
                    organization_id,
                ),
            )
        except BackgroundQueueError:
            staged_path.unlink(missing_ok=True)
            raise

    def enqueue_reindex(
        self,
        document_id: str,
        role: str,
        requested_by: str,
        organization_id: str = "org_default",
    ) -> JobRecord:
        job = job_service.create(
            job_type="document.reindex",
            detail={"document_id": document_id},
            created_by=requested_by,
            organization_id=organization_id,
        )
        return self._dispatch(
            job=job,
            task=reindex_document_task,
            args=(job.job_id, document_id, role, organization_id),
        )

    def enqueue_connector_items(
        self,
        provider: str,
        items: list[ConnectorImportItem],
        requested_by: str,
        organization_id: str = "org_default",
    ) -> JobRecord:
        serialized_items = [item.model_dump() for item in items]
        job = job_service.create(
            job_type=f"{provider}.import",
            detail={"provider": provider, "items": len(items)},
            created_by=requested_by,
            organization_id=organization_id,
        )
        return self._dispatch(
            job=job,
            task=ingest_connector_items_task,
            args=(job.job_id, serialized_items, requested_by, organization_id),
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
                retry=(
                    Retry(
                        max=settings.job_max_attempts - 1,
                        interval=self._retry_intervals(
                            settings.job_retry_intervals,
                            settings.job_max_attempts - 1,
                        ),
                    )
                    if settings.job_max_attempts > 1
                    else None
                ),
                result_ttl=86400,
                failure_ttl=604800,
            )
        except Exception as exc:
            if not settings.async_jobs_fallback_sync:
                job_service.fail(job.job_id, exc)
                raise BackgroundQueueError("The background queue is unavailable.") from exc
            task(*args)
        return job_service.get(job.job_id)

    @staticmethod
    def _retry_intervals(value: str, retry_count: int) -> list[int]:
        try:
            parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
        except ValueError as exc:
            raise BackgroundQueueError("Job retry intervals must be integers.") from exc
        if any(item < 0 for item in parsed):
            raise BackgroundQueueError("Job retry intervals cannot be negative.")
        if retry_count == 0:
            return []
        if not parsed:
            return [0] * retry_count
        return [*parsed, *([parsed[-1]] * retry_count)][:retry_count]

    def _staging_path(self, job_id: str, filename: str) -> Path:
        return Path(get_settings().upload_dir) / "staging" / f"{job_id}_{filename}"


background_task_service = BackgroundTaskService()
