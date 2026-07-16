from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from app.core.crypto import encrypt_secret
from app.core.database import encode_json, get_connection
from app.main import app
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
    assert response.status_code == 200
    return response.json()


def test_workflow_executes_safe_actions_then_resumes_after_approval(monkeypatch) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE connector_accounts SET status = 'disconnected' WHERE provider = 'google' AND organization_id = 'org_default'"
        )
        connection.execute(
            """
            INSERT INTO connector_accounts (
                connector_id, provider, account_label, status, scopes_json,
                token_cipher, refresh_token_cipher, expires_at, created_by,
                organization_id, external_account_id, metadata_json, updated_at
            ) VALUES (?, 'google', ?, 'connected', ?, ?, ?, ?, 'u_admin',
                      'org_default', ?, '{}', ?)
            """,
            (
                "con_workflow_google",
                "workflow@example.com",
                encode_json(["https://www.googleapis.com/auth/gmail.send"]),
                encrypt_secret("workflow-google-token"),
                encrypt_secret("workflow-google-refresh"),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "workflow-google-account",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/users/me/messages/send")
        return httpx.Response(
            200,
            json={"id": "gmail_workflow_message", "threadId": "gmail_workflow_thread"},
        )

    monkeypatch.setattr(
        "app.services.connector_providers.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    admin_headers = _auth_headers()
    workflow = _create_workflow(
        "Find the workflow approval policy, create a launch task, and send a reply"
    )

    assert workflow["status"] == "waiting_for_approval"
    assert [action["status"] for action in workflow["actions"]] == [
        "completed",
        "completed",
        "waiting_for_approval",
    ]
    assert all(action["execution_id"] for action in workflow["actions"])
    pending_action = workflow["actions"][-1]
    assert pending_action["approval_id"]

    first_resume = client.post(
        f"/api/agent/workflows/{workflow['workflow_id']}/resume",
        headers=admin_headers,
    )
    second_resume = client.post(
        f"/api/agent/workflows/{workflow['workflow_id']}/resume",
        headers=admin_headers,
    )
    assert first_resume.status_code == 200
    assert second_resume.status_code == 200

    with get_connection() as connection:
        execution_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM mcp_tool_executions
            WHERE idempotency_key LIKE ?
            """,
            (f"workflow:{workflow['workflow_id']}:%",),
        ).fetchone()[0]
        task_count = connection.execute(
            "SELECT COUNT(*) FROM workspace_tasks WHERE source_execution_id = ?",
            (workflow["actions"][1]["execution_id"],),
        ).fetchone()[0]
    assert execution_count == 3
    assert task_count == 1

    decision = client.post(
        f"/api/approvals/{pending_action['approval_id']}/decision",
        headers=_auth_headers("manager@demo.local"),
        json={"approved": True},
    )
    assert decision.status_code == 200

    completed = client.get(
        f"/api/agent/workflows/{workflow['workflow_id']}",
        headers=admin_headers,
    )
    assert completed.status_code == 200
    body = completed.json()
    assert body["status"] == "completed"
    assert all(action["status"] == "completed" for action in body["actions"])
    assert body["actions"][-1]["result"]["delivery_mode"] == "provider"
    assert body["actions"][-1]["result"]["external_id"] == "gmail_workflow_message"


def test_rejection_blocks_workflow_and_cancellation_closes_pending_approval() -> None:
    manager_headers = _auth_headers("manager@demo.local")
    rejected = _create_workflow("Create a rejection test task and send a reply")
    rejected_action = rejected["actions"][-1]

    decision = client.post(
        f"/api/approvals/{rejected_action['approval_id']}/decision",
        headers=manager_headers,
        json={"approved": False},
    )
    assert decision.status_code == 200

    blocked = client.get(
        f"/api/agent/workflows/{rejected['workflow_id']}",
        headers=_auth_headers(),
    ).json()
    assert blocked["status"] == "blocked"
    assert blocked["actions"][-1]["status"] == "blocked"

    pending = _create_workflow("Create a cancellation test task and send a reply")
    pending_action = pending["actions"][-1]
    cancelled = client.post(
        f"/api/agent/workflows/{pending['workflow_id']}/cancel",
        headers=_auth_headers(),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["actions"][-1]["status"] == "cancelled"

    late_decision = client.post(
        f"/api/approvals/{pending_action['approval_id']}/decision",
        headers=manager_headers,
        json={"approved": True},
    )
    assert late_decision.status_code == 409


def test_workflow_visibility_is_limited_and_managers_can_monitor() -> None:
    workflow = _create_workflow("Create a visibility test task")

    employee_get = client.get(
        f"/api/agent/workflows/{workflow['workflow_id']}",
        headers=_auth_headers("employee@demo.local"),
    )
    assert employee_get.status_code == 403

    employee_list = client.get(
        "/api/agent/workflows",
        headers=_auth_headers("employee@demo.local"),
    )
    assert employee_list.status_code == 200
    assert workflow["workflow_id"] not in {
        item["workflow_id"] for item in employee_list.json()
    }

    manager_get = client.get(
        f"/api/agent/workflows/{workflow['workflow_id']}",
        headers=_auth_headers("manager@demo.local"),
    )
    assert manager_get.status_code == 200


def test_failed_task_retry_reuses_execution_without_duplicate_side_effect() -> None:
    workflow = _create_workflow("Create a retry-safe operations task")
    assert workflow["status"] == "completed"
    task_action = next(
        action for action in workflow["actions"] if action["tool_name"] == "create_task"
    )

    with get_connection() as connection:
        initial_task_count = connection.execute(
            "SELECT COUNT(*) FROM workspace_tasks WHERE source_execution_id = ?",
            (task_action["execution_id"],),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE mcp_tool_executions
            SET status = 'failed', result_json = ?, error = 'Temporary failure'
            WHERE execution_id = ?
            """,
            (encode_json({}), task_action["execution_id"]),
        )
        connection.execute(
            """
            UPDATE workflow_actions
            SET status = 'failed', result_json = ?, error = 'Temporary failure'
            WHERE action_instance_id = ?
            """,
            (encode_json({}), task_action["action_instance_id"]),
        )
        connection.execute(
            """
            UPDATE agent_workflows
            SET status = 'failed', last_error = 'Temporary failure'
            WHERE workflow_id = ?
            """,
            (workflow["workflow_id"],),
        )

    retried = client.post(
        f"/api/agent/workflows/{workflow['workflow_id']}/retry",
        headers=_auth_headers(),
    )
    assert retried.status_code == 200
    body = retried.json()
    assert body["status"] == "completed"
    retried_action = next(
        action for action in body["actions"] if action["tool_name"] == "create_task"
    )
    assert retried_action["attempt_count"] == 2
    assert retried_action["execution_id"] == task_action["execution_id"]

    with get_connection() as connection:
        final_task_count = connection.execute(
            "SELECT COUNT(*) FROM workspace_tasks WHERE source_execution_id = ?",
            (task_action["execution_id"],),
        ).fetchone()[0]
    assert initial_task_count == final_task_count == 1


def test_legacy_workflow_plans_are_backfilled_after_migration() -> None:
    workflow_id = "wf_legacy_state_machine_test"
    plan = {
        "summary": "Legacy persisted plan",
        "actions": [
            {
                "action_id": "act_search",
                "action_type": "search_email",
                "description": "Search workspace data",
                "requires_approval": False,
                "scope": "documents:read",
            },
            {
                "action_id": "act_task",
                "action_type": "create_task",
                "description": "Create the follow-up task",
                "requires_approval": False,
                "scope": "tasks:write",
            },
        ],
    }
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO agent_workflows (
                workflow_id, prompt, requested_by, status, plan_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                "Legacy workflow",
                "u_admin",
                "planned",
                encode_json(plan),
            ),
        )

    assert workflow_service.backfill_legacy_actions() >= 1
    repaired = client.get(
        f"/api/agent/workflows/{workflow_id}",
        headers=_auth_headers(),
    )
    assert repaired.status_code == 200
    assert [action["tool_name"] for action in repaired.json()["actions"]] == [
        "search_documents",
        "create_task",
    ]
    assert workflow_service.backfill_legacy_actions() == 0
