from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
import time
from typing import Callable, Iterator, TypedDict
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.core.database import get_connection, is_postgres_database
from app.models.schemas import AgentWorkflowRecord, UserContext, WorkflowGraphEvent
from app.services.observability import observability_service


class GovernedWorkflowState(TypedDict):
    workflow_id: str
    organization_id: str
    requested_by: str
    status: str
    current_action_index: int
    graph_step_count: int
    last_node: str
    error_code: str | None


WorkflowLoader = Callable[[str], AgentWorkflowRecord]
WorkflowAdvancer = Callable[[str, UserContext], AgentWorkflowRecord]


class LangGraphWorkflowService:
    """Checkpoint orchestration IDs while Work OS remains the execution authority."""

    _setup_lock = Lock()
    _initialized_postgres_backends: set[str] = set()

    def setup_checkpoint_storage(self) -> None:
        """Initialize official saver tables during deployment migrations."""
        with self._checkpointer() as saver:
            if isinstance(saver, SqliteSaver):
                saver.setup()

    def run(
        self,
        workflow: AgentWorkflowRecord,
        requester: UserContext,
        *,
        load_workflow: WorkflowLoader,
        advance_once: WorkflowAdvancer,
    ) -> AgentWorkflowRecord:
        started_at = time.perf_counter()
        final_status = "failed"
        thread_id = workflow.graph_thread_id or self.thread_id_for(workflow)
        try:
            self._ensure_thread_id(workflow.workflow_id, thread_id)
            workflow = load_workflow(workflow.workflow_id)
            with self._checkpointer() as checkpointer:
                graph = self._build_graph(
                    checkpointer,
                    requester=requester,
                    load_workflow=load_workflow,
                    advance_once=advance_once,
                    thread_id=thread_id,
                )
                config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "organization_id": workflow.organization_id,
                        "workflow_id": workflow.workflow_id,
                    },
                    "recursion_limit": get_settings().langgraph_recursion_limit,
                }
                graph.invoke(self._state_from(workflow, "dispatch"), config=config)
                snapshot = graph.get_state(config)
                checkpoint_id = str(
                    snapshot.config.get("configurable", {}).get("checkpoint_id", "")
                ) or None
                self._persist_checkpoint(
                    workflow.workflow_id,
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                )
            updated = load_workflow(workflow.workflow_id)
            final_status = self._observation_status(updated.status)
            return updated
        finally:
            observability_service.record_safely(
                operation_type="workflow_orchestration",
                provider="langgraph",
                model="stategraph-v1",
                status=final_status,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                actor_id=requester.user_id,
                organization_id=workflow.organization_id,
                metadata={
                    "workflow_id": workflow.workflow_id,
                    "thread_id": thread_id,
                    "engine": "langgraph",
                },
            )

    def checkpoint_terminal(
        self,
        workflow: AgentWorkflowRecord,
        requester: UserContext,
        *,
        load_workflow: WorkflowLoader,
        advance_once: WorkflowAdvancer,
    ) -> None:
        self.run(
            workflow,
            requester,
            load_workflow=load_workflow,
            advance_once=advance_once,
        )

    @staticmethod
    def thread_id_for(workflow: AgentWorkflowRecord) -> str:
        digest = sha256(
            f"{workflow.organization_id}:{workflow.workflow_id}".encode("utf-8")
        ).hexdigest()
        return f"lg_{digest[:32]}"

    def list_events(
        self,
        workflow_id: str,
        organization_id: str,
        *,
        limit: int = 100,
    ) -> list[WorkflowGraphEvent]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_graph_events
                WHERE workflow_id = ? AND organization_id = ?
                ORDER BY step DESC
                LIMIT ?
                """,
                (workflow_id, organization_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _build_graph(
        self,
        checkpointer: BaseCheckpointSaver,
        *,
        requester: UserContext,
        load_workflow: WorkflowLoader,
        advance_once: WorkflowAdvancer,
        thread_id: str,
    ):
        builder = StateGraph(GovernedWorkflowState)

        def hydrate(state: GovernedWorkflowState) -> GovernedWorkflowState:
            workflow = load_workflow(state["workflow_id"])
            return self._record_node(workflow, thread_id, "hydrate")

        def advance(state: GovernedWorkflowState) -> GovernedWorkflowState:
            workflow = advance_once(state["workflow_id"], requester)
            return self._record_node(workflow, thread_id, "advance")

        def after_hydrate(state: GovernedWorkflowState) -> str:
            if state["status"] in {"planned", "running", "waiting_for_approval"}:
                return "advance"
            return END

        def after_advance(state: GovernedWorkflowState) -> str:
            return "hydrate" if state["status"] == "running" else END

        builder.add_node("hydrate", hydrate)
        builder.add_node("advance", advance)
        builder.add_edge(START, "hydrate")
        builder.add_conditional_edges("hydrate", after_hydrate)
        builder.add_conditional_edges("advance", after_advance)
        return builder.compile(checkpointer=checkpointer)

    @contextmanager
    def _checkpointer(self) -> Iterator[BaseCheckpointSaver]:
        settings = get_settings()
        if is_postgres_database():
            database_url = settings.database_url or ""
            backend_key = sha256(database_url.encode("utf-8")).hexdigest()
            with PostgresSaver.from_conn_string(database_url) as saver:
                with self._setup_lock:
                    if backend_key not in self._initialized_postgres_backends:
                        saver.setup()
                        self._initialized_postgres_backends.add(backend_key)
                yield saver
            return

        checkpoint_path = Path(settings.langgraph_sqlite_path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            yield saver

    def _record_node(
        self,
        workflow: AgentWorkflowRecord,
        thread_id: str,
        node_name: str,
    ) -> GovernedWorkflowState:
        now = self._now()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE agent_workflows
                SET graph_step_count = graph_step_count + 1,
                    graph_last_node = ?, graph_thread_id = ?, updated_at = ?
                WHERE workflow_id = ? AND organization_id = ?
                """,
                (
                    node_name,
                    thread_id,
                    now,
                    workflow.workflow_id,
                    workflow.organization_id,
                ),
            )
            row = connection.execute(
                """
                SELECT graph_step_count FROM agent_workflows
                WHERE workflow_id = ? AND organization_id = ?
                """,
                (workflow.workflow_id, workflow.organization_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("Workflow disappeared while recording graph state.")
            step = int(row["graph_step_count"])
            state = self._state_from(workflow, node_name, graph_step_count=step)
            state_hash = sha256(
                json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO workflow_graph_events (
                    graph_event_id, organization_id, workflow_id, thread_id,
                    node_name, status, step, current_action_index, state_hash,
                    checkpoint_id, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    f"wge_{uuid4().hex}",
                    workflow.organization_id,
                    workflow.workflow_id,
                    thread_id,
                    node_name,
                    workflow.status,
                    step,
                    workflow.current_action_index,
                    state_hash,
                    self._safe_error(workflow.last_error),
                    now,
                ),
            )
        return state

    @staticmethod
    def _state_from(
        workflow: AgentWorkflowRecord,
        last_node: str,
        *,
        graph_step_count: int | None = None,
    ) -> GovernedWorkflowState:
        return {
            "workflow_id": workflow.workflow_id,
            "organization_id": workflow.organization_id,
            "requested_by": workflow.requested_by,
            "status": workflow.status,
            "current_action_index": workflow.current_action_index,
            "graph_step_count": (
                workflow.graph_step_count
                if graph_step_count is None
                else graph_step_count
            ),
            "last_node": last_node,
            "error_code": (
                "workflow_error" if workflow.last_error is not None else None
            ),
        }

    @staticmethod
    def _safe_error(error: str | None) -> str | None:
        return "Workflow transition failed; inspect the governed audit trail." if error else None

    @staticmethod
    def _observation_status(status: str) -> str:
        if status == "completed":
            return "completed"
        if status in {"blocked", "cancelled"}:
            return status
        if status == "waiting_for_approval":
            return "blocked"
        return "failed"

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_event(row) -> WorkflowGraphEvent:
        return WorkflowGraphEvent(
            graph_event_id=row["graph_event_id"],
            organization_id=row["organization_id"],
            workflow_id=row["workflow_id"],
            thread_id=row["thread_id"],
            node_name=row["node_name"],
            status=row["status"],
            step=int(row["step"]),
            current_action_index=int(row["current_action_index"]),
            state_hash=row["state_hash"],
            checkpoint_id=row["checkpoint_id"],
            error=row["error"],
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
        )

    @staticmethod
    def _ensure_thread_id(workflow_id: str, thread_id: str) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE agent_workflows
                SET graph_thread_id = ?
                WHERE workflow_id = ? AND graph_thread_id IS NULL
                """,
                (thread_id, workflow_id),
            )

    @staticmethod
    def _persist_checkpoint(
        workflow_id: str,
        *,
        thread_id: str,
        checkpoint_id: str | None,
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE agent_workflows
                SET graph_thread_id = ?, graph_checkpoint_id = ?
                WHERE workflow_id = ?
                """,
                (thread_id, checkpoint_id, workflow_id),
            )
            if checkpoint_id:
                connection.execute(
                    """
                    UPDATE workflow_graph_events
                    SET checkpoint_id = ?
                    WHERE workflow_id = ?
                      AND step = (
                          SELECT MAX(step) FROM workflow_graph_events
                          WHERE workflow_id = ?
                      )
                    """,
                    (checkpoint_id, workflow_id, workflow_id),
                )


langgraph_workflow_service = LangGraphWorkflowService()
