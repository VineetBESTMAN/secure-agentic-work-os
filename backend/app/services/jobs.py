from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import JobRecord


class JobService:
    def create(self, job_type: str, detail: dict[str, object], created_by: str) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        job = JobRecord(
            job_id=f"job_{uuid4().hex}",
            job_type=job_type,
            status="queued",
            detail=detail,
            result={},
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO background_jobs (
                    job_id, job_type, status, detail_json, result_json,
                    created_by, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
        return job

    def update(self, job_id: str, status: str, result: dict[str, object]) -> JobRecord:
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE background_jobs
                SET status = ?, result_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, encode_json(result), now, job_id),
            )
        return self.get(job_id)

    def fail(self, job_id: str, error: Exception | str) -> JobRecord:
        return self.update(
            job_id=job_id,
            status="failed",
            result={"progress": 100, "error": str(error)},
        )

    def get(self, job_id: str) -> JobRecord:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM background_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Job not found.")
        return self._row_to_job(row)

    def list_jobs(self) -> list[JobRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM background_jobs
                ORDER BY created_at DESC
                LIMIT 100
                """
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
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )


job_service = JobService()
