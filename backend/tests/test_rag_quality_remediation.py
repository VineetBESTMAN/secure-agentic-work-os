import hashlib
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import get_connection
from app.main import app
from app.models.schemas import Citation
from app.services.grounded_answers import grounded_answer_service
from app.services.retrieval import hybrid_retrieval_service


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_structured_chunks_fast_ingestion_grounded_answer_and_private_audit() -> None:
    suffix = uuid4().hex[:8]
    question = f"What approval does the Zephyr {suffix} launch protocol require?"
    document = f"""# Operations Manual

## Zephyr {suffix} Launch Protocol

The Zephyr {suffix} launch protocol requires cobalt manager approval before outbound deployment.

## Unrelated Cafeteria Notes

Lunch service closes at three.
"""
    started = time.perf_counter()
    upload = client.post(
        "/api/documents/upload",
        headers=_auth_headers(),
        files={"file": (f"zephyr-{suffix}.md", document, "text/markdown")},
        data={"classification": "internal", "owner_team": "operations"},
    )
    elapsed = time.perf_counter() - started
    assert upload.status_code == 200
    assert elapsed < 5.0

    detail = client.get(
        f"/api/documents/{upload.json()['document_id']}",
        headers=_auth_headers(),
    )
    assert detail.status_code == 200
    chunks = detail.json()["chunks"]
    protocol_chunk = next(
        chunk for chunk in chunks if "cobalt manager approval" in chunk["text"]
    )
    assert "Zephyr" in protocol_chunk["heading"]
    assert protocol_chunk["locator"].startswith("section")
    assert protocol_chunk["token_count"] > 0

    query = client.post(
        "/api/documents/query",
        headers=_auth_headers(),
        json={"question": question},
    )
    assert query.status_code == 200
    answer = query.json()
    assert answer["answerable"] is True
    assert answer["grounded"] is True
    assert answer["confidence"] > 0
    assert "cobalt manager approval" in answer["answer"].lower()
    assert answer["citations"][0]["locator"].startswith("section")

    with get_connection() as connection:
        audit = connection.execute(
            """
            SELECT detail_json FROM audit_events
            WHERE event_type = 'documents.query'
            ORDER BY timestamp DESC LIMIT 1
            """
        ).fetchone()
    detail_json = json.loads(audit["detail_json"])
    assert "question" not in detail_json
    assert detail_json["question_hash"] == hashlib.sha256(
        question.encode("utf-8")
    ).hexdigest()
    assert question not in audit["detail_json"]


def test_unanswerable_query_and_zero_overlap_fallback_refuse_evidence() -> None:
    response = client.post(
        "/api/documents/query",
        headers=_auth_headers(),
        json={
            "question": "What is the qxzv glaciomorph payroll deadline for lunar penguins?"
        },
    )
    assert response.status_code == 200
    answer = response.json()
    assert answer["answerable"] is False
    assert answer["grounded"] is False
    assert answer["citations"] == []

    fallback = grounded_answer_service.generate(
        question="What is the parental leave policy in Brazil?",
        citations=[
            Citation(
                document_id="doc_irrelevant",
                title="Container policy",
                excerpt="OpenClaw runs in an isolated container without database access.",
                chunk_id="chk_irrelevant",
                score=0.99,
                dense_score=0.99,
            )
        ],
        actor_id="u_admin",
        organization_id="org_default",
        force_deterministic=True,
    )
    assert fallback.answerable is False
    assert fallback.grounded is False
    assert fallback.citations == []


def test_hybrid_lexical_concepts_and_low_confidence_workflow_gate() -> None:
    rows = [
        {
            "chunk_id": "chk_policy",
            "document_id": "doc_policy",
            "title": "Distribution control",
            "heading": "External summaries",
            "text": "External summaries must receive manager approval before distribution.",
            "created_at": "2026-08-21T00:00:00+00:00",
        },
        {
            "chunk_id": "chk_food",
            "document_id": "doc_food",
            "title": "Cafeteria",
            "heading": None,
            "text": "Soup is served on Tuesday.",
            "created_at": "2026-08-21T00:00:00+00:00",
        },
    ]
    ranked = hybrid_retrieval_service.rank(
        question="What approval is required before external distribution?",
        rows=rows,
        top_k=1,
    )
    assert ranked
    assert ranked[0].row["chunk_id"] == "chk_policy"

    workflow = client.post(
        "/api/agent/workflows",
        headers=_auth_headers(),
        json={
            "prompt": (
                f"Find the qxzv-{uuid4().hex} lunar payroll policy and create a task"
            )
        },
    )
    assert workflow.status_code == 200
    body = workflow.json()
    assert body["status"] == "blocked"
    assert body["actions"][0]["status"] == "blocked"
    assert all(action["status"] != "completed" for action in body["actions"][1:])
    assert "downstream actions were not executed" in body["last_error"]
