from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import AuditEvent


class AuditService:
    def record(
        self,
        actor_id: str,
        event_type: str,
        detail: dict[str, object],
        organization_id: str = "org_default",
    ) -> None:
        event = AuditEvent(actor_id=actor_id, event_type=event_type, detail=detail)
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, actor_id, event_type, detail_json, timestamp, organization_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.actor_id,
                    event.event_type,
                    encode_json(event.detail),
                    event.timestamp.isoformat(),
                    organization_id,
                ),
            )

    def list_events(self, organization_id: str) -> list[AuditEvent]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_events
                WHERE organization_id = ?
                ORDER BY timestamp DESC
                LIMIT 100
                """,
                (organization_id,),
            ).fetchall()
        return [
            AuditEvent(
                event_id=row["event_id"],
                actor_id=row["actor_id"],
                event_type=row["event_type"],
                detail=decode_json(row["detail_json"], {}),
                timestamp=row["timestamp"],
            )
            for row in rows
        ]


audit_service = AuditService()
