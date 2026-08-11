from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_connection
from app.main import app
from app.services.langgraph_workflows import LangGraphWorkflowService
from app.services.users import user_service
from app.services.workflows import workflow_service


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_workflow(prompt: str) -> dict:
    response = client.post(
        "/api/agent/workflows",
        headers=_auth_headers(),
        json={"prompt": prompt},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_langgraph_runs_governed_actions_and_records_safe_checkpoints() -> None:
    secret_marker = "checkpoint-secret-must-not-persist"
    workflow = _create_workflow(f"Create a task for {secret_marker}")

    assert workflow["orchestration_engine"] == "langgraph"
    assert workflow["status"] == "completed"
    assert workflow["graph_thread_id"].startswith("lg_")
    assert workflow["graph_checkpoint_id"]
    assert workflow["graph_step_count"] >= 3
    assert workflow["graph_last_node"] == "advance"
    assert workflow["recent_graph_events"]
    assert all(len(event["state_hash"]) == 64 for event in workflow["recent_graph_events"])

    checkpoint_path = Path(get_settings().langgraph_sqlite_path)
    assert checkpoint_path.exists()
    assert secret_marker.encode("utf-8") not in checkpoint_path.read_bytes()
    with sqlite3.connect(checkpoint_path) as connection:
        checkpoint_count = connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            (workflow["graph_thread_id"],),
        ).fetchone()[0]
    assert checkpoint_count > 0


def test_graph_events_follow_workflow_and_tenant_access_rules() -> None:
    workflow = _create_workflow("Create a tenant-safe graph visibility task")

    events = client.get(
        f"/api/agent/workflows/{workflow['workflow_id']}/graph-events",
        headers=_auth_headers(),
    )
    assert events.status_code == 200
    assert events.json()
    assert {
        event["organization_id"] for event in events.json()
    } == {"org_default"}

    employee = client.get(
        f"/api/agent/workflows/{workflow['workflow_id']}/graph-events",
        headers=_auth_headers("employee@demo.local"),
    )
    assert employee.status_code == 403

    admin_login = client.post(
        "/api/auth/login",
        json={"email": "admin@demo.local", "password": "demo-password"},
    ).json()
    suffix = uuid4().hex[:10]
    organization = client.post(
        "/api/organizations",
        headers={"Authorization": f"Bearer {admin_login['access_token']}"},
        json={"name": f"Graph tenant {suffix}", "slug": f"graph-{suffix}"},
    )
    assert organization.status_code == 201
    switched = client.post(
        "/api/auth/switch-organization",
        headers={"Authorization": f"Bearer {admin_login['access_token']}"},
        json={"organization_id": organization.json()["organization_id"]},
    )
    assert switched.status_code == 200
    other_headers = {
        "Authorization": f"Bearer {switched.json()['access_token']}"
    }
    other_tenant = client.get(
        f"/api/agent/workflows/{workflow['workflow_id']}/graph-events",
        headers=other_headers,
    )
    assert other_tenant.status_code == 404


def test_fresh_orchestrator_reuses_checkpoint_without_duplicate_action() -> None:
    workflow = _create_workflow("Create a restart-safe graph task")
    action = next(
        item for item in workflow["actions"] if item["tool_name"] == "create_task"
    )
    requester = user_service.get_by_id("u_admin", "org_default")
    assert requester is not None

    fresh_service = LangGraphWorkflowService()
    updated = fresh_service.run(
        workflow_service._require_workflow(workflow["workflow_id"]),
        requester,
        load_workflow=workflow_service._require_workflow,
        advance_once=workflow_service._advance_workflow_once,
    )

    assert updated.status == "completed"
    assert updated.graph_checkpoint_id
    with get_connection() as connection:
        execution_count = connection.execute(
            "SELECT COUNT(*) FROM mcp_tool_executions WHERE idempotency_key = ?",
            (action["idempotency_key"],),
        ).fetchone()[0]
        task_count = connection.execute(
            "SELECT COUNT(*) FROM workspace_tasks WHERE source_execution_id = ?",
            (action["execution_id"],),
        ).fetchone()[0]
    assert execution_count == 1
    assert task_count == 1


def test_langgraph_failure_uses_deterministic_governed_fallback(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("checkpoint backend unavailable")

    monkeypatch.setattr(
        "app.services.workflows.langgraph_workflow_service.run",
        unavailable,
    )
    workflow = _create_workflow("Create a fallback-safe task")

    assert workflow["status"] == "completed"
    assert workflow["orchestration_engine"] == "langgraph"
    assert workflow["actions"][0]["status"] == "completed"
    with get_connection() as connection:
        fallback = connection.execute(
            """
            SELECT detail_json FROM audit_events
            WHERE event_type = 'agent.workflow_orchestrator_fallback'
              AND organization_id = 'org_default'
            ORDER BY timestamp DESC LIMIT 1
            """
        ).fetchone()
    assert fallback is not None
    assert "checkpoint backend unavailable" not in fallback["detail_json"]


def test_langgraph_failure_can_fail_closed_without_executing_actions(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("checkpoint backend unavailable")

    settings = get_settings()
    original = settings.langgraph_fallback_enabled
    settings.langgraph_fallback_enabled = False
    monkeypatch.setattr(
        "app.services.workflows.langgraph_workflow_service.run",
        unavailable,
    )
    try:
        workflow = _create_workflow("Create a fail-closed task")
    finally:
        settings.langgraph_fallback_enabled = original

    assert workflow["status"] == "failed"
    assert workflow["actions"][0]["status"] == "pending"
    assert workflow["last_error"] == "The workflow orchestrator is unavailable."


def test_deterministic_engine_remains_available_for_new_workflows() -> None:
    settings = get_settings()
    original = settings.workflow_engine
    settings.workflow_engine = "deterministic"
    try:
        workflow = _create_workflow("Create a deterministic fallback task")
    finally:
        settings.workflow_engine = original

    assert workflow["status"] == "completed"
    assert workflow["orchestration_engine"] == "deterministic"
    assert workflow["graph_thread_id"] is None
    assert workflow["recent_graph_events"] == []


def test_cancelled_langgraph_workflow_gets_terminal_checkpoint() -> None:
    workflow = _create_workflow("Create a task and send an approval-gated email")
    assert workflow["status"] == "waiting_for_approval"

    cancelled = client.post(
        f"/api/agent/workflows/{workflow['workflow_id']}/cancel",
        headers=_auth_headers(),
    )
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["graph_last_node"] == "hydrate"
    assert body["recent_graph_events"][0]["status"] == "cancelled"
