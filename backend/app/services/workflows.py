from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import AgentPlanResponse, AgentWorkflowRecord, UserContext
from app.services.agent import agent_service


class WorkflowService:
    def create_workflow(self, prompt: str, user: UserContext) -> AgentWorkflowRecord:
        plan = agent_service.build_plan(prompt=prompt, user=user)
        status = (
            "waiting_for_approval"
            if any(action.requires_approval for action in plan.actions)
            else "planned"
        )
        now = datetime.now(timezone.utc).isoformat()
        workflow = AgentWorkflowRecord(
            workflow_id=f"wf_{uuid4().hex}",
            prompt=prompt,
            requested_by=user.user_id,
            status=status,
            plan=plan,
            created_at=now,
            updated_at=now,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_workflows (
                    workflow_id, prompt, requested_by, status, plan_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow.workflow_id,
                    workflow.prompt,
                    workflow.requested_by,
                    workflow.status,
                    encode_json(workflow.plan.model_dump()),
                    workflow.created_at,
                    workflow.updated_at,
                ),
            )
        return workflow

    def list_workflows(self) -> list[AgentWorkflowRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM agent_workflows
                ORDER BY created_at DESC
                LIMIT 100
                """
            ).fetchall()
        return [self._row_to_workflow(row) for row in rows]

    def _row_to_workflow(self, row) -> AgentWorkflowRecord:
        return AgentWorkflowRecord(
            workflow_id=row["workflow_id"],
            prompt=row["prompt"],
            requested_by=row["requested_by"],
            status=row["status"],
            plan=AgentPlanResponse(**decode_json(row["plan_json"], {})),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )


workflow_service = WorkflowService()
