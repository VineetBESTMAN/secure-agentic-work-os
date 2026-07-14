from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import (
    AgentPlanResponse,
    AgentWorkflowRecord,
    MCPExecutionRecord,
    MCPExecutionRequest,
    UserContext,
    WorkflowActionRecord,
)
from app.services.agent import agent_service
from app.services.approval import approval_service
from app.services.audit import audit_service
from app.services.mcp_gateway import mcp_gateway_service
from app.services.users import user_service


TOOL_BY_ACTION = {
    "search_email": "search_documents",
    "search_documents": "search_documents",
    "create_task": "create_task",
    "send_email": "send_email",
}

TERMINAL_ACTION_STATUSES = {"completed", "blocked", "failed", "cancelled", "skipped"}


class WorkflowService:
    def backfill_legacy_actions(self) -> int:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT workflow_id, plan_json
                FROM agent_workflows AS workflow
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM workflow_actions AS action
                    WHERE action.workflow_id = workflow.workflow_id
                )
                """
            ).fetchall()

        repaired = 0
        for row in rows:
            try:
                plan = AgentPlanResponse(**decode_json(row["plan_json"], {}))
            except (TypeError, ValueError):
                continue
            with get_connection() as connection:
                self._insert_actions(connection, row["workflow_id"], plan, self._now())
            repaired += 1
        return repaired

    def create_workflow(self, prompt: str, user: UserContext) -> AgentWorkflowRecord:
        plan = agent_service.build_plan(prompt=prompt, user=user)
        now = self._now()
        workflow_id = f"wf_{uuid4().hex}"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_workflows (
                    workflow_id, prompt, requested_by, status, plan_json,
                    current_action_index, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    prompt,
                    user.user_id,
                    "planned",
                    encode_json(plan.model_dump(mode="json")),
                    0,
                    now,
                    now,
                ),
            )
            self._insert_actions(connection, workflow_id, plan, now)

        audit_service.record(
            actor_id=user.user_id,
            event_type="agent.workflow_materialized",
            detail={"workflow_id": workflow_id, "actions": len(plan.actions)},
        )
        return self.run_workflow(workflow_id, user)

    def list_workflows(self, user: UserContext) -> list[AgentWorkflowRecord]:
        where_clause = "" if user.role in {"admin", "manager"} else "WHERE requested_by = ?"
        params = () if not where_clause else (user.user_id,)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM agent_workflows
                {where_clause}
                ORDER BY created_at DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
        return [self._row_to_workflow(row) for row in rows]

    def get_workflow(
        self, workflow_id: str, user: UserContext | None = None
    ) -> AgentWorkflowRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_workflows WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        if row is None:
            return None
        workflow = self._row_to_workflow(row)
        if user is not None and not self.can_access(workflow, user):
            raise PermissionError("You do not have permission to access this workflow.")
        return workflow

    @staticmethod
    def can_access(workflow: AgentWorkflowRecord, user: UserContext) -> bool:
        return user.role in {"admin", "manager"} or workflow.requested_by == user.user_id

    def run_workflow(
        self, workflow_id: str, user: UserContext
    ) -> AgentWorkflowRecord:
        workflow = self.get_workflow(workflow_id, user)
        if workflow is None:
            raise ValueError("Workflow not found.")
        if workflow.status in {"completed", "cancelled"}:
            return workflow
        if workflow.status in {"failed", "blocked"}:
            return workflow

        requester = user_service.get_by_id(workflow.requested_by)
        if requester is None:
            self._set_workflow_status(
                workflow_id,
                status="failed",
                current_action_index=workflow.current_action_index,
                last_error="The workflow requester no longer exists.",
            )
            return self._require_workflow(workflow_id)

        self._set_workflow_status(
            workflow_id,
            status="running",
            current_action_index=workflow.current_action_index,
            last_error=None,
            mark_started=True,
        )

        for _ in range(100):
            workflow = self._require_workflow(workflow_id)
            actions = workflow.actions
            next_action = next(
                (action for action in actions if action.status not in {"completed", "skipped"}),
                None,
            )
            if next_action is None:
                self._set_workflow_status(
                    workflow_id,
                    status="completed",
                    current_action_index=len(actions),
                    last_error=None,
                    mark_completed=True,
                )
                return self._require_workflow(workflow_id)

            if next_action.status == "waiting_for_approval":
                execution = self._execution_for_action(next_action)
                if execution is None:
                    self._fail_action(
                        next_action,
                        "The approval-bound MCP execution could not be found.",
                    )
                else:
                    next_action = self._sync_action_with_execution(next_action, execution)

            if next_action.status == "pending":
                next_action = self._execute_pending_action(
                    workflow,
                    next_action,
                    requester,
                )

            if next_action.status == "completed":
                self._set_workflow_status(
                    workflow_id,
                    status="running",
                    current_action_index=next_action.sequence + 1,
                    last_error=None,
                )
                continue
            if next_action.status == "waiting_for_approval":
                self._set_workflow_status(
                    workflow_id,
                    status="waiting_for_approval",
                    current_action_index=next_action.sequence,
                    last_error=None,
                )
                return self._require_workflow(workflow_id)
            if next_action.status == "failed":
                self._set_workflow_status(
                    workflow_id,
                    status="failed",
                    current_action_index=next_action.sequence,
                    last_error=next_action.error,
                )
                return self._require_workflow(workflow_id)
            if next_action.status == "blocked":
                self._set_workflow_status(
                    workflow_id,
                    status="blocked",
                    current_action_index=next_action.sequence,
                    last_error=next_action.error,
                )
                return self._require_workflow(workflow_id)
            if next_action.status == "cancelled":
                return self._require_workflow(workflow_id)
            return self._require_workflow(workflow_id)

        self._set_workflow_status(
            workflow_id,
            status="failed",
            current_action_index=workflow.current_action_index,
            last_error="Workflow exceeded the state transition safety limit.",
        )
        return self._require_workflow(workflow_id)

    def retry_workflow(
        self, workflow_id: str, user: UserContext
    ) -> AgentWorkflowRecord:
        workflow = self.get_workflow(workflow_id, user)
        if workflow is None:
            raise ValueError("Workflow not found.")
        if workflow.status != "failed":
            raise ValueError("Only failed workflows can be retried.")

        action = next((item for item in workflow.actions if item.status == "failed"), None)
        if action is None:
            raise ValueError("The failed workflow has no retryable action.")
        if action.attempt_count >= action.max_attempts:
            raise ValueError("This workflow action has reached its retry limit.")

        requester = user_service.get_by_id(workflow.requested_by)
        if requester is None:
            raise ValueError("The workflow requester no longer exists.")

        if not self._claim_failed_action_for_retry(action):
            raise ValueError("The failed action is already being retried.")

        if action.execution_id:
            try:
                execution = mcp_gateway_service.retry_execution(
                    action.execution_id,
                    requester,
                )
                action = self._sync_action_with_execution(action, execution)
            except Exception as exc:
                action = self._fail_action(action, str(exc))
        else:
            self._reset_action_for_retry(action.action_instance_id)

        if action.status == "failed":
            self._set_workflow_status(
                workflow_id,
                status="failed",
                current_action_index=action.sequence,
                last_error=action.error,
            )
            return self._require_workflow(workflow_id)

        self._set_workflow_status(
            workflow_id,
            status="running",
            current_action_index=action.sequence,
            last_error=None,
        )
        return self.run_workflow(workflow_id, user)

    def cancel_workflow(
        self, workflow_id: str, user: UserContext
    ) -> AgentWorkflowRecord:
        workflow = self.get_workflow(workflow_id, user)
        if workflow is None:
            raise ValueError("Workflow not found.")
        if workflow.status == "completed":
            raise ValueError("Completed workflows cannot be cancelled.")
        if workflow.status == "cancelled":
            return workflow

        requester = user_service.get_by_id(workflow.requested_by) or user
        for action in workflow.actions:
            if action.execution_id and action.status == "waiting_for_approval":
                approval_service.cancel_for_execution(
                    action.execution_id,
                    cancelled_by=user.user_id,
                )
                mcp_gateway_service.cancel_execution(action.execution_id, requester)

        now = self._now()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE workflow_actions
                SET status = 'cancelled', error = ?, completed_at = ?, updated_at = ?
                WHERE workflow_id = ?
                  AND status IN (
                      'pending', 'running', 'waiting_for_approval', 'blocked', 'failed'
                  )
                """,
                ("Workflow was cancelled.", now, now, workflow_id),
            )
        self._set_workflow_status(
            workflow_id,
            status="cancelled",
            current_action_index=workflow.current_action_index,
            last_error="Workflow was cancelled.",
            mark_cancelled=True,
        )
        audit_service.record(
            actor_id=user.user_id,
            event_type="agent.workflow_cancelled",
            detail={"workflow_id": workflow_id},
        )
        return self._require_workflow(workflow_id)

    def handle_execution_update(
        self, execution: MCPExecutionRecord
    ) -> AgentWorkflowRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_actions WHERE execution_id = ?",
                (execution.execution_id,),
            ).fetchone()
        if row is None:
            return None
        action = self._row_to_action(row)
        workflow = self._require_workflow(action.workflow_id)
        if workflow.status == "cancelled":
            return workflow

        action = self._sync_action_with_execution(action, execution)
        if action.status == "completed":
            requester = user_service.get_by_id(workflow.requested_by)
            if requester is None:
                return workflow
            self._set_workflow_status(
                workflow.workflow_id,
                status="running",
                current_action_index=action.sequence + 1,
                last_error=None,
            )
            return self.run_workflow(workflow.workflow_id, requester)
        if action.status == "blocked":
            self._set_workflow_status(
                workflow.workflow_id,
                status="blocked",
                current_action_index=action.sequence,
                last_error=action.error,
            )
        elif action.status == "failed":
            self._set_workflow_status(
                workflow.workflow_id,
                status="failed",
                current_action_index=action.sequence,
                last_error=action.error,
            )
        return self._require_workflow(workflow.workflow_id)

    def _execute_pending_action(
        self,
        workflow: AgentWorkflowRecord,
        action: WorkflowActionRecord,
        requester: UserContext,
    ) -> WorkflowActionRecord:
        arguments = self._build_arguments(workflow, action)
        now = self._now()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_actions
                SET status = 'running', input_json = ?,
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, ?), updated_at = ?, error = NULL
                WHERE action_instance_id = ? AND status = 'pending'
                """,
                (
                    encode_json(arguments),
                    now,
                    now,
                    action.action_instance_id,
                ),
            )
            claimed = cursor.rowcount == 1
        if not claimed:
            return self._require_action(action.action_instance_id)

        try:
            execution = mcp_gateway_service.request_execution(
                MCPExecutionRequest(
                    tool_name=action.tool_name,
                    arguments=arguments,
                    idempotency_key=action.idempotency_key,
                ),
                requester,
            )
            return self._sync_action_with_execution(action, execution)
        except Exception as exc:
            return self._fail_action(action, str(exc))

    def _sync_action_with_execution(
        self,
        action: WorkflowActionRecord,
        execution: MCPExecutionRecord,
    ) -> WorkflowActionRecord:
        status_map = {
            "running": "running",
            "pending_approval": "waiting_for_approval",
            "completed": "completed",
            "blocked": "blocked",
            "rejected": "blocked",
            "failed": "failed",
        }
        action_status = status_map[execution.status]
        error = execution.error
        if execution.status == "rejected" and not error:
            error = "The required approval was rejected."
        completed_at = self._now() if action_status in TERMINAL_ACTION_STATUSES else None
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE workflow_actions
                SET status = ?, result_json = ?, execution_id = ?, approval_id = ?,
                    error = ?, completed_at = ?, updated_at = ?
                WHERE action_instance_id = ?
                """,
                (
                    action_status,
                    encode_json(execution.result),
                    execution.execution_id,
                    execution.approval_id,
                    error,
                    completed_at,
                    self._now(),
                    action.action_instance_id,
                ),
            )
        updated = self._require_action(action.action_instance_id)
        audit_service.record(
            actor_id=execution.requested_by,
            event_type="agent.workflow_action_transition",
            detail={
                "workflow_id": action.workflow_id,
                "action_instance_id": action.action_instance_id,
                "execution_id": execution.execution_id,
                "status": updated.status,
            },
        )
        return updated

    def _fail_action(
        self, action: WorkflowActionRecord, error: str
    ) -> WorkflowActionRecord:
        now = self._now()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE workflow_actions
                SET status = 'failed', error = ?, completed_at = ?, updated_at = ?
                WHERE action_instance_id = ?
                """,
                (error, now, now, action.action_instance_id),
            )
        return self._require_action(action.action_instance_id)

    def _set_workflow_status(
        self,
        workflow_id: str,
        *,
        status: str,
        current_action_index: int,
        last_error: str | None,
        mark_started: bool = False,
        mark_completed: bool = False,
        mark_cancelled: bool = False,
    ) -> None:
        fields = [
            "status = ?",
            "current_action_index = ?",
            "last_error = ?",
            "updated_at = ?",
        ]
        now = self._now()
        params: list[object] = [status, current_action_index, last_error, now]
        if mark_started:
            fields.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        if mark_completed:
            fields.append("completed_at = ?")
            params.append(now)
        if mark_cancelled:
            fields.append("cancelled_at = ?")
            params.append(now)
        params.append(workflow_id)
        with get_connection() as connection:
            connection.execute(
                f"UPDATE agent_workflows SET {', '.join(fields)} WHERE workflow_id = ?",
                params,
            )

    def _build_arguments(
        self, workflow: AgentWorkflowRecord, action: WorkflowActionRecord
    ) -> dict[str, object]:
        prior_answer = ""
        for prior in workflow.actions:
            if prior.sequence >= action.sequence:
                break
            answer = prior.result.get("answer")
            if isinstance(answer, str) and answer:
                prior_answer = answer

        if action.tool_name == "search_documents":
            return {"question": workflow.prompt}
        if action.tool_name == "create_task":
            title = " ".join(workflow.prompt.split())[:180] or "Workflow follow-up"
            return {
                "title": title,
                "description": prior_answer or workflow.prompt,
            }
        if action.tool_name == "send_email":
            return {
                "to": "client@example.com",
                "subject": "Workflow follow-up",
                "body": prior_answer or workflow.prompt,
            }
        raise ValueError(f"Workflow action has no tool adapter: {action.action_type}")

    def _insert_actions(self, connection, workflow_id: str, plan, now: str) -> None:
        for sequence, proposal in enumerate(plan.actions):
            tool_name = TOOL_BY_ACTION.get(proposal.action_type)
            if tool_name is None:
                continue
            connection.execute(
                """
                INSERT INTO workflow_actions (
                    action_instance_id, workflow_id, sequence, action_type,
                    tool_name, description, required_scope, requires_approval,
                    status, idempotency_key, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"wfa_{uuid4().hex}",
                    workflow_id,
                    sequence,
                    proposal.action_type,
                    tool_name,
                    proposal.description,
                    proposal.scope,
                    proposal.requires_approval,
                    "pending",
                    f"workflow:{workflow_id}:action:{sequence}",
                    now,
                    now,
                ),
            )

    def _actions_for_workflow(self, workflow_id: str) -> list[WorkflowActionRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_actions
                WHERE workflow_id = ?
                ORDER BY sequence ASC
                """,
                (workflow_id,),
            ).fetchall()
        return [self._row_to_action(row) for row in rows]

    def _execution_for_action(
        self, action: WorkflowActionRecord
    ) -> MCPExecutionRecord | None:
        if not action.execution_id:
            return None
        return mcp_gateway_service.get_execution(action.execution_id)

    def _require_workflow(self, workflow_id: str) -> AgentWorkflowRecord:
        workflow = self.get_workflow(workflow_id)
        if workflow is None:  # pragma: no cover
            raise RuntimeError("Workflow disappeared during execution.")
        return workflow

    def _require_action(self, action_instance_id: str) -> WorkflowActionRecord:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_actions WHERE action_instance_id = ?",
                (action_instance_id,),
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RuntimeError("Workflow action disappeared during execution.")
        return self._row_to_action(row)

    def _claim_failed_action_for_retry(self, action: WorkflowActionRecord) -> bool:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_actions
                SET attempt_count = attempt_count + 1, status = 'running',
                    error = NULL, completed_at = NULL, updated_at = ?
                WHERE action_instance_id = ? AND status = 'failed'
                  AND attempt_count < max_attempts
                """,
                (self._now(), action.action_instance_id),
            )
            return cursor.rowcount == 1

    def _reset_action_for_retry(self, action_instance_id: str) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE workflow_actions
                SET status = 'pending', error = NULL, completed_at = NULL, updated_at = ?
                WHERE action_instance_id = ?
                """,
                (self._now(), action_instance_id),
            )

    def _row_to_workflow(self, row) -> AgentWorkflowRecord:
        return AgentWorkflowRecord(
            workflow_id=row["workflow_id"],
            prompt=row["prompt"],
            requested_by=row["requested_by"],
            status=row["status"],
            plan=AgentPlanResponse(**decode_json(row["plan_json"], {})),
            actions=self._actions_for_workflow(row["workflow_id"]),
            current_action_index=int(row["current_action_index"]),
            last_error=row["last_error"],
            created_at=self._string_or_none(row["created_at"]),
            updated_at=self._string_or_none(row["updated_at"]),
            started_at=self._string_or_none(row["started_at"]),
            completed_at=self._string_or_none(row["completed_at"]),
            cancelled_at=self._string_or_none(row["cancelled_at"]),
        )

    @staticmethod
    def _row_to_action(row) -> WorkflowActionRecord:
        return WorkflowActionRecord(
            action_instance_id=row["action_instance_id"],
            workflow_id=row["workflow_id"],
            sequence=int(row["sequence"]),
            action_type=row["action_type"],
            tool_name=row["tool_name"],
            description=row["description"],
            required_scope=row["required_scope"],
            requires_approval=bool(row["requires_approval"]),
            status=row["status"],
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            idempotency_key=row["idempotency_key"],
            input=decode_json(row["input_json"], {}),
            result=decode_json(row["result_json"], {}),
            execution_id=row["execution_id"],
            approval_id=row["approval_id"],
            error=row["error"],
            created_at=WorkflowService._string_or_none(row["created_at"]),
            started_at=WorkflowService._string_or_none(row["started_at"]),
            completed_at=WorkflowService._string_or_none(row["completed_at"]),
            updated_at=WorkflowService._string_or_none(row["updated_at"]),
        )

    @staticmethod
    def _string_or_none(value) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


workflow_service = WorkflowService()
