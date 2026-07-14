from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.observability import BudgetExceededError, observability_service


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_runtime_summary_tracks_rag_and_mcp_operations() -> None:
    headers = _auth_headers()
    query = client.post(
        "/api/documents/query",
        headers=headers,
        json={"question": "What requires approval?"},
    )
    assert query.status_code == 200

    execution = client.post(
        "/api/mcp/executions",
        headers=headers,
        json={
            "tool_name": "create_task",
            "arguments": {"title": f"Observed task {uuid4().hex[:8]}"},
        },
    )
    assert execution.status_code == 200
    assert execution.json()["status"] == "completed"

    response = client.get("/api/observability/summary?hours=24", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total_operations"] >= 4
    assert body["success_rate"] > 0
    observed_operations = {item["operation_type"] for item in body["breakdown"]}
    assert {"embedding", "rag_query", "mcp_tool"} <= observed_operations
    assert body["budgets"][0]["name"] == "Daily AI runtime budget"

    events = client.get(
        "/api/observability/events?hours=24&limit=100", headers=headers
    )
    assert events.status_code == 200
    traces = {event["trace_id"] for event in events.json()}
    assert all(trace.startswith("trace_") for trace in traces)


def test_observability_is_restricted_to_operations_roles() -> None:
    response = client.get(
        "/api/observability/summary",
        headers=_auth_headers("employee@demo.local"),
    )
    assert response.status_code == 403


def test_cost_budget_state_and_enforcement() -> None:
    headers = _auth_headers()
    name = f"Test budget {uuid4().hex}"
    created = client.post(
        "/api/observability/budgets",
        headers=headers,
        json={
            "name": name,
            "period": "daily",
            "limit_usd": 1,
            "warning_percent": 50,
        },
    )
    assert created.status_code == 201
    budget_id = created.json()["budget_id"]

    observability_service.record(
        operation_type="embedding",
        provider="test-provider",
        model="test-model",
        status="completed",
        latency_ms=2,
        estimated_cost_usd=0.75,
    )
    budgets = client.get("/api/observability/budgets", headers=headers).json()
    budget = next(item for item in budgets if item["budget_id"] == budget_id)
    assert budget["state"] == "warning"
    assert budget["spent_usd"] >= 0.75

    updated = client.patch(
        f"/api/observability/budgets/{budget_id}",
        headers=headers,
        json={"limit_usd": 0.5},
    )
    assert updated.status_code == 200
    assert updated.json()["state"] == "exceeded"

    with pytest.raises(BudgetExceededError):
        observability_service.assert_budget_available(0.01)

    deleted = client.delete(
        f"/api/observability/budgets/{budget_id}", headers=headers
    )
    assert deleted.status_code == 204


def test_managers_can_read_but_cannot_mutate_budgets() -> None:
    manager_headers = _auth_headers("manager@demo.local")
    assert client.get(
        "/api/observability/budgets", headers=manager_headers
    ).status_code == 200
    response = client.post(
        "/api/observability/budgets",
        headers=manager_headers,
        json={"name": f"Manager budget {uuid4().hex}", "limit_usd": 1},
    )
    assert response.status_code == 403
