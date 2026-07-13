from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import get_connection, is_postgres_database
from app.models.schemas import ApprovalRecord


class ApprovalService:
    def seed_demo_request(self) -> None:
        if is_postgres_database():
            insert_sql = """
                INSERT INTO approval_requests
                    (approval_id, action_id, requested_by, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (approval_id) DO NOTHING
            """
        else:
            insert_sql = """
                INSERT OR IGNORE INTO approval_requests
                    (approval_id, action_id, requested_by, status)
                VALUES (?, ?, ?, ?)
            """

        with get_connection() as connection:
            connection.execute(
                insert_sql,
                ("apr_demo_1", "act_send_email", "u_employee", "pending"),
            )

    def list_requests(self, requested_by: str | None = None) -> list[ApprovalRecord]:
        where_clause = "WHERE requested_by = ?" if requested_by else ""
        params = (requested_by,) if requested_by else ()
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM approval_requests
                {where_clause}
                ORDER BY created_at DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def create(
        self,
        action_id: str,
        requested_by: str,
        execution_id: str | None = None,
        arguments_hash: str | None = None,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=f"apr_{uuid4().hex}",
            action_id=action_id,
            requested_by=requested_by,
            status="pending",
            execution_id=execution_id,
            arguments_hash=arguments_hash,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO approval_requests
                    (
                        approval_id, action_id, requested_by, status, created_at,
                        execution_id, arguments_hash
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.approval_id,
                    record.action_id,
                    record.requested_by,
                    record.status,
                    record.created_at.isoformat(),
                    record.execution_id,
                    record.arguments_hash,
                ),
            )
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def decide(
        self, approval_id: str, approved: bool, reviewer_id: str
    ) -> ApprovalRecord | None:
        status = "approved" if approved else "rejected"
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if existing is None:
                return None
            if existing["requested_by"] == reviewer_id:
                raise PermissionError("Requesters cannot approve or reject their own actions.")
            if existing["status"] != "pending":
                raise ValueError("This approval request has already been decided.")
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, reviewed_by = ?, reviewed_at = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                (status, reviewer_id, reviewed_at, approval_id),
            )
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=row["approval_id"],
            action_id=row["action_id"],
            requested_by=row["requested_by"],
            status=row["status"],
            created_at=row["created_at"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
            execution_id=row["execution_id"],
            arguments_hash=row["arguments_hash"],
        )
        return record


approval_service = ApprovalService()
