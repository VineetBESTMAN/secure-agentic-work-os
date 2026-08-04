from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_required_runtime_components() -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    components = {item["name"]: item for item in body["components"]}
    assert components["database"]["status"] == "healthy"
    assert components["object_storage"]["status"] == "healthy"
    assert components["redis"]["status"] == "not_configured"


def test_prometheus_metrics_are_exposed_without_secrets() -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "workos_readiness" in response.text
    assert "test-only-jwt-secret" not in response.text
