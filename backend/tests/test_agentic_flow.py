from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_authenticated_document_query_returns_citation() -> None:
    upload = client.post(
        "/api/documents/upload",
        headers=_auth_headers(),
        files={
            "file": (
                "customer-escalation-sop.txt",
                "Urgent customer escalations must be triaged within one business hour.",
                "text/plain",
            )
        },
        data={"classification": "internal", "owner_team": "operations"},
    )
    assert upload.status_code == 200

    response = client.post(
        "/api/documents/query",
        headers=_auth_headers(),
        json={"question": "How do we triage escalations?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["citations"][0]["title"] == "customer escalation sop"
    assert "Urgent customer escalations" in body["citations"][0]["excerpt"]


def test_mcp_gateway_requires_approval_for_send_email() -> None:
    response = client.post(
        "/api/mcp/tool-call",
        headers=_auth_headers(),
        json={
            "tool_name": "send_email",
            "scope": "email:send",
            "arguments": {"to": "client@example.com"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approval_required"
    assert body["approval_id"].startswith("apr_")
