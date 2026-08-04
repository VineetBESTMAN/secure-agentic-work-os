from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import yaml

from app.core.config import get_settings
from app.main import app
from app.services.object_storage import ObjectStorageError, ObjectStorageService


client = TestClient(app)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_local_object_storage_is_tenant_scoped_and_traversal_safe(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    original_upload_dir = settings.upload_dir
    original_backend = settings.object_storage_backend
    settings.upload_dir = str(tmp_path / "objects")
    settings.object_storage_backend = "local"
    storage = ObjectStorageService()
    try:
        key = storage.document_key(
            organization_id="org_example",
            document_id="doc_example",
            filename="../evidence.txt",
        )
        assert key == "documents/org_example/doc_example/evidence.txt"
        storage.put(key, b"grounded evidence", filename="evidence.txt")
        assert storage.exists(key)
        assert storage.get(key) == b"grounded evidence"
        storage.delete(key)
        assert not storage.exists(key)
        with pytest.raises(ObjectStorageError):
            storage.put("../../outside.txt", b"blocked")
    finally:
        settings.upload_dir = original_upload_dir
        settings.object_storage_backend = original_backend


def test_s3_object_storage_uses_prefix_encryption_and_backup_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeS3:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}
            self.put_kwargs: dict[str, object] = {}

        def put_object(self, **kwargs):
            self.put_kwargs = kwargs
            self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])

        def get_object(self, **kwargs):
            return {"Body": BytesIO(self.objects[str(kwargs["Key"])])}

        def head_object(self, **kwargs):
            if str(kwargs["Key"]) not in self.objects:
                raise KeyError(kwargs["Key"])

        def delete_object(self, **kwargs):
            self.objects.pop(str(kwargs["Key"]), None)

        def head_bucket(self, **_kwargs):
            return None

        def list_objects_v2(self, **_kwargs):
            return {
                "Contents": [
                    {
                        "Key": "tenant/backups/postgres/workos.dump",
                        "LastModified": datetime(2026, 8, 4, tzinfo=timezone.utc),
                    }
                ]
            }

    settings = get_settings()
    original_values = {
        "object_storage_backend": settings.object_storage_backend,
        "object_storage_bucket": settings.object_storage_bucket,
        "object_storage_prefix": settings.object_storage_prefix,
        "object_storage_sse": settings.object_storage_sse,
        "object_storage_kms_key_id": settings.object_storage_kms_key_id,
        "backup_status_prefix": settings.backup_status_prefix,
    }
    settings.object_storage_backend = "s3"
    settings.object_storage_bucket = "workos-test"
    settings.object_storage_prefix = "tenant"
    settings.object_storage_sse = "aws:kms"
    settings.object_storage_kms_key_id = "test-kms-key"
    settings.backup_status_prefix = "backups/postgres"
    fake = FakeS3()
    storage = ObjectStorageService()
    monkeypatch.setattr(storage, "_client", lambda: fake)
    try:
        key = "documents/org_example/doc_example/evidence.txt"
        storage.put(key, b"evidence", filename="evidence.txt")
        assert fake.put_kwargs["Bucket"] == "workos-test"
        assert fake.put_kwargs["Key"] == f"tenant/{key}"
        assert fake.put_kwargs["ServerSideEncryption"] == "aws:kms"
        assert fake.put_kwargs["SSEKMSKeyId"] == "test-kms-key"
        assert storage.get(key) == b"evidence"
        assert storage.exists(key)
        assert storage.health() == (True, "object storage bucket is reachable")
        assert storage.latest_backup_at() == datetime(
            2026, 8, 4, tzinfo=timezone.utc
        )
        storage.delete(key)
        assert not storage.exists(key)
    finally:
        for field, value in original_values.items():
            setattr(settings, field, value)


def test_operations_status_is_authorized_and_secret_safe() -> None:
    response = client.get("/api/operations/status", headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["database_backend"] == "sqlite"
    assert body["database_tls"] is False
    assert body["object_storage_backend"] == "local"
    serialized = json.dumps(body)
    assert get_settings().secret_key not in serialized
    assert "DATABASE_URL" not in serialized

    forbidden = client.get(
        "/api/operations/status", headers=_headers("employee@demo.local")
    )
    assert forbidden.status_code == 403


def test_metrics_bearer_token_is_enforced() -> None:
    settings = get_settings()
    original_token = settings.metrics_token
    settings.metrics_token = "metrics-test-token-with-32-characters"
    try:
        assert client.get("/metrics").status_code == 401
        response = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {settings.metrics_token}"},
        )
        assert response.status_code == 200
        assert "workos_http_requests_total" in response.text
    finally:
        settings.metrics_token = original_token


def test_production_compose_and_restore_verification_are_fail_safe() -> None:
    compose_path = REPOSITORY_ROOT / "docker-compose.production.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    assert "postgres" not in services
    assert "redis" not in services
    assert services["backend"].get("ports") is None
    assert services["prometheus"].get("ports") is None
    assert services["alertmanager"].get("ports") is None
    assert services["caddy"]["ports"] == ["80:80", "443:443", "443:443/udp"]
    for service_name in ("backend", "worker", "migrate"):
        service = services[service_name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]

    caddyfile = (REPOSITORY_ROOT / "ops/caddy/Caddyfile").read_text(
        encoding="utf-8"
    )
    assert "/metrics" not in caddyfile
    assert "Strict-Transport-Security" in caddyfile
    assert "Content-Security-Policy" in caddyfile

    restore_script = (
        REPOSITORY_ROOT / "ops/backup/restore-verify.sh"
    ).read_text(encoding="utf-8")
    guard_position = restore_script.index(
        "The restore URL must target RESTORE_VERIFY_DATABASE_NAME exactly."
    )
    destructive_position = restore_script.index("DROP DATABASE IF EXISTS")
    assert guard_position < destructive_position
    assert "*_restore_verify database" in restore_script
    assert "SELECT current_database();" in restore_script


def test_deployment_workflow_requires_verified_ssh_host_identity() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github/workflows/deploy-production.yml"
    ).read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=yes" in workflow
    assert "PRODUCTION_SSH_KNOWN_HOSTS" in workflow
    assert "ssh-keyscan" not in workflow
    assert "${{ github.sha }}" in workflow
    assert "--wait --no-build" in workflow
