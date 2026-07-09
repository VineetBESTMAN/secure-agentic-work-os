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

    def list_requests(self) -> list[ApprovalRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                ORDER BY created_at DESC
                LIMIT 100
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def create(self, action_id: str, requested_by: str) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=f"apr_{uuid4().hex}",
            action_id=action_id,
            requested_by=requested_by,
            status="pending",
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO approval_requests
                    (approval_id, action_id, requested_by, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.approval_id,
                    record.action_id,
                    record.requested_by,
                    record.status,
                    record.created_at.isoformat(),
                ),
            )
        return record

    def decide(
        self, approval_id: str, approved: bool, reviewer_id: str
    ) -> ApprovalRecord | None:
        status = "approved" if approved else "rejected"
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, reviewed_by = ?
                WHERE approval_id = ?
                """,
                (status, reviewer_id, approval_id),
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
        )
        return record


approval_service = ApprovalService()
