from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from app.core.database import encode_json, get_connection
from app.main import app
from app.services.connectors import connector_service
from app.services.embeddings import embedding_service
from app.services.jobs import job_service
from app.services.operations import operations_service
from app.services.rag import rag_service
from app.services.resilience import retry_idempotent_provider_read
from app.worker import handle_work_horse_killed


client = TestClient(app)


def _headers() -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@demo.local", "password": "demo-password"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_worker_loss_is_retried_then_dead_lettered() -> None:
    job = job_service.create(
        job_type="reliability.worker_loss",
        detail={},
        created_by="u_admin",
    )
    first = job_service.start(job.job_id, {"progress": 1})
    assert first.attempt_count == 1

    handle_work_horse_killed(
        SimpleNamespace(id=job.job_id, retries_left=1), None, None, None
    )
    queued = job_service.get(job.job_id)
    assert queued.status == "queued"
    assert queued.last_error == "Worker process stopped before the task completed."

    second = job_service.start(job.job_id, {"progress": 1})
    assert second.attempt_count == 2
    handle_work_horse_killed(
        SimpleNamespace(id=job.job_id, retries_left=0), None, None, None
    )
    dead_lettered = job_service.get(job.job_id)
    assert dead_lettered.status == "dead_lettered"
    assert dead_lettered.dead_lettered_at is not None


def test_idempotent_provider_reads_retry_transient_failures_only() -> None:
    settings = get_settings()
    original = (
        settings.connector_read_max_retries,
        settings.connector_retry_base_delay_seconds,
        settings.connector_retry_max_delay_seconds,
    )
    settings.connector_read_max_retries = 2
    settings.connector_retry_base_delay_seconds = 0
    settings.connector_retry_max_delay_seconds = 0
    attempts = 0

    async def eventually_succeeds() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout(
                "temporary timeout", request=httpx.Request("GET", "https://provider.test")
            )
        return "ok"

    non_retry_attempts = 0

    async def bad_request() -> str:
        nonlocal non_retry_attempts
        non_retry_attempts += 1
        request = httpx.Request("GET", "https://provider.test")
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    try:
        assert asyncio.run(retry_idempotent_provider_read(eventually_succeeds)) == "ok"
        assert attempts == 3
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(retry_idempotent_provider_read(bad_request))
        assert non_retry_attempts == 1
    finally:
        (
            settings.connector_read_max_retries,
            settings.connector_retry_base_delay_seconds,
            settings.connector_retry_max_delay_seconds,
        ) = original


def test_concurrent_webhook_replays_are_atomically_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex
    connector_id = f"con_reliability_{suffix}"
    subscription_id = f"whs_reliability_{suffix}"
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO connector_accounts (
                connector_id, provider, account_label, status, scopes_json,
                token_cipher, created_by, organization_id, metadata_json, updated_at
            ) VALUES (?, 'github', 'reliability-test', 'connected', '[]', ?,
                      'u_admin', 'org_default', '{}', ?)
            """,
            (connector_id, encrypt_secret("access-token"), now),
        )
        connection.execute(
            """
            INSERT INTO connector_webhook_subscriptions (
                subscription_id, organization_id, connector_id, provider,
                resource, secret_cipher, registration_mode, status, created_by,
                created_at, updated_at
            ) VALUES (?, 'org_default', ?, 'github', 'issues', ?, 'manual',
                      'active', 'u_admin', ?, ?)
            """,
            (subscription_id, connector_id, encrypt_secret("webhook-secret"), now, now),
        )

    async def valid_signature(**_kwargs) -> bool:
        return True

    monkeypatch.setattr(connector_service, "_verify_webhook_signature", valid_signature)
    body = b'{"action":"opened","issue":{"id":42}}'
    headers = {
        "x-github-delivery": f"delivery-{suffix}",
        "x-github-event": "issues",
    }

    def deliver():
        return asyncio.run(
            connector_service.receive_webhook(
                provider="github",
                subscription_id=subscription_id,
                raw_body=body,
                headers=headers,
                payload=json.loads(body),
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        deliveries = list(pool.map(lambda _index: deliver(), range(8)))

    assert sum(not item.duplicate for item in deliveries) == 1
    assert sum(item.duplicate for item in deliveries) == 7
    assert len({item.delivery_id for item in deliveries}) == 1
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS deliveries, MAX(duplicate_count) AS duplicates
            FROM connector_webhook_deliveries
            WHERE subscription_id = ?
            """,
            (subscription_id,),
        ).fetchone()
    assert row["deliveries"] == 1
    assert row["duplicates"] == 7


def test_large_local_document_collection_has_bounded_retrieval_latency() -> None:
    suffix = uuid4().hex
    document_rows = []
    chunk_rows = []
    texts = [
        (
            f"Scale record {index}. The project code is RELIABILITY-{index}. "
            "Worker recovery uses durable state and tenant-scoped evidence."
        )
        for index in range(250)
    ]
    embeddings = embedding_service.embed_many(texts)
    for index in range(250):
        document_id = f"doc_scale_{suffix}_{index}"
        text = texts[index]
        document_rows.append(
            (
                document_id,
                f"Scale record {index}",
                f"scale-{index}.txt",
                "internal",
                "reliability",
                text,
                "u_admin",
                0,
                "[]",
                "org_default",
                "local",
                f"scale/{suffix}/{index}.txt",
            )
        )
        chunk_rows.append(
            (
                f"chk_scale_{suffix}_{index}",
                document_id,
                0,
                text,
                encode_json(embeddings[index]),
                "org_default",
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO documents (
                document_id, title, filename, classification, owner_team, summary,
                uploaded_by, unsafe, unsafe_reasons_json, organization_id,
                storage_backend, storage_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            document_rows,
        )
        connection.executemany(
            """
            INSERT INTO document_chunks (
                chunk_id, document_id, chunk_index, text, embedding_json, organization_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            chunk_rows,
        )

    latencies = []
    try:
        for _ in range(5):
            started = time.perf_counter()
            answer = rag_service.answer(
                "What is project code RELIABILITY-249?",
                role="admin",
                actor_id="u_admin",
            )
            latencies.append(time.perf_counter() - started)
            assert answer.citations
        assert sorted(latencies)[-1] < 5.0
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM documents WHERE document_id LIKE ?",
                (f"doc_scale_{suffix}_%",),
            )


def test_concurrent_authenticated_reads_complete_without_server_errors() -> None:
    headers = _headers()
    started = time.perf_counter()

    def read_documents(_index: int) -> int:
        return client.get("/api/documents/library", headers=headers).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(read_documents, range(24)))

    assert statuses == [200] * 24
    assert time.perf_counter() - started < 15.0


def test_readiness_recovers_on_the_next_database_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.operations as operations_module

    real_get_connection = operations_module.get_connection
    calls = 0

    def flaky_connection():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("simulated database outage")
        return real_get_connection()

    monkeypatch.setattr(operations_module, "get_connection", flaky_connection)
    first_components, first = operations_service.readiness()
    second_components, second = operations_service.readiness()

    assert first["database"] is False
    assert second["database"] is True
    assert next(item for item in first_components if item.name == "database").detail == (
        "Database connectivity check failed."
    )
    assert next(item for item in second_components if item.name == "database").status == (
        "healthy"
    )


def test_operations_exposes_durable_reliability_counters() -> None:
    response = client.get("/api/operations/status", headers=_headers())
    assert response.status_code == 200, response.text
    reliability = response.json()["reliability"]
    assert reliability["available"] is True
    for field in (
        "queued_jobs",
        "running_jobs",
        "failed_jobs",
        "dead_lettered_jobs",
        "stale_running_jobs",
        "provider_accounts_in_error",
        "webhook_deliveries_24h",
        "webhook_duplicates_24h",
    ):
        assert reliability[field] >= 0

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert 'workos_background_jobs{status="dead_lettered"}' in metrics.text
    assert "workos_stale_running_jobs" in metrics.text
    assert "workos_webhook_duplicates_24h" in metrics.text
