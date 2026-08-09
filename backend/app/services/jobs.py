from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import JobRecord


class JobService:
    def create(
        self,
        job_type: str,
        detail: dict[str, object],
        created_by: str,
        organization_id: str = "org_default",
    ) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        max_attempts = get_settings().job_max_attempts
        job = JobRecord(
            job_id=f"job_{uuid4().hex}",
            job_type=job_type,
            status="queued",
            detail=detail,
            result={},
            created_by=created_by,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO background_jobs (
                    job_id, job_type, status, detail_json, result_json,
                    created_by, created_at, updated_at, organization_id,
                    attempt_count, max_attempts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.job_type,
                    job.status,
                    encode_json(job.detail),
                    encode_json(job.result),
                    job.created_by,
                    job.created_at,
                    job.updated_at,
                    organization_id,
                    0,
                    max_attempts,
                ),
            )
        return job

    def update(self, job_id: str, status: str, result: dict[str, object]) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = ?, result_json = ?, updated_at = ?,
                    heartbeat_at = CASE WHEN ? = 'running' THEN ? ELSE heartbeat_at END,
                    last_error = CASE WHEN ? = 'completed' THEN NULL ELSE last_error END
                WHERE job_id = ?
                """,
                (status, encode_json(result), now, status, now, status, job_id),
            )
        return self.get(job_id)

    def start(self, job_id: str, result: dict[str, object]) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE background_jobs
                SET status = 'running', result_json = ?,
                    attempt_count = attempt_count + 1,
                    heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND attempt_count < max_attempts
                """,
                (encode_json(result), now, now, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Job has exhausted its configured attempt limit.")
        return self.get(job_id)

    def retry(self, job_id: str, error: Exception | str) -> JobRecord:
        message = str(error)
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'queued', result_json = ?, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    encode_json(
                        {
                            "progress": 0,
                            "message": "Task failed and will be retried.",
                            "error": message,
                        }
                    ),
                    message,
                    now,
                    job_id,
                ),
            )
        return self.get(job_id)

    def fail(self, job_id: str, error: Exception | str) -> JobRecord:
        message = str(error)
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'failed', result_json = ?, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (encode_json({"progress": 100, "error": message}), message, now, job_id),
            )
        return self.get(job_id)

    def dead_letter(self, job_id: str, error: Exception | str) -> JobRecord:
        message = str(error)
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'dead_lettered', result_json = ?, last_error = ?,
                    dead_lettered_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    encode_json({"progress": 100, "error": message}),
                    message,
                    now,
                    now,
                    job_id,
                ),
            )
        return self.get(job_id)

    def handle_worker_loss(self, job_id: str, retries_left: int) -> JobRecord:
        message = "Worker process stopped before the task completed."
        if retries_left > 0:
            return self.retry(job_id, message)
        return self.dead_letter(job_id, message)

    def get(self, job_id: str, organization_id: str | None = None) -> JobRecord:
        where = "job_id = ?"
        params: tuple[object, ...] = (job_id,)
        if organization_id is not None:
            where += " AND organization_id = ?"
            params += (organization_id,)
        with get_connection() as connection:
            row = connection.execute(
                f"SELECT * FROM background_jobs WHERE {where}",
                params,
            ).fetchone()
        if row is None:
            raise ValueError("Job not found.")
        return self._row_to_job(row)

    def list_jobs(self, organization_id: str = "org_default") -> list[JobRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM background_jobs
                WHERE organization_id = ?
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (organization_id,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def _row_to_job(self, row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            detail=decode_json(row["detail_json"], {}),
            result=decode_json(row["result_json"], {}),
            created_by=row["created_by"],
            attempt_count=int(row["attempt_count"] or 0),
            max_attempts=int(row["max_attempts"] or 3),
            last_error=row["last_error"],
            heartbeat_at=(
                str(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None
            ),
            dead_lettered_at=(
                str(row["dead_lettered_at"])
                if row["dead_lettered_at"] is not None
                else None
            ),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )


job_service = JobService()
