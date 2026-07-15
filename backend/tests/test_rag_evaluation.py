from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.embeddings import embedding_service


client = TestClient(app)


def _auth_headers(email: str = "admin@demo.local") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "demo-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _upload(filename: str, content: str, classification: str = "internal") -> dict:
    response = client.post(
        "/api/documents/upload",
        headers=_auth_headers(),
        files={"file": (filename, content, "text/plain")},
        data={"classification": classification, "owner_team": "evaluation"},
    )
    assert response.status_code == 200
    return response.json()


def _chunk_id(document_id: str) -> str:
    response = client.get(f"/api/documents/{document_id}", headers=_auth_headers())
    assert response.status_code == 200
    return response.json()["chunks"][0]["chunk_id"]


def test_persistent_dataset_measures_quality_and_skips_unconfigured_openai() -> None:
    suffix = uuid4().hex[:8]
    relevant = _upload(
        f"renewal-approval-{suffix}.txt",
        "Renewal summaries require manager approval before external distribution.",
    )
    decoy = _upload(
        f"office-hours-{suffix}.txt",
        "The office kitchen closes at six in the evening.",
    )
    relevant_chunk_id = _chunk_id(relevant["document_id"])

    create = client.post(
        "/api/rag-evaluations/datasets",
        headers=_auth_headers(),
        json={
            "name": f"Renewal quality {suffix}",
            "description": "Regression cases for retrieval and unsupported citations.",
            "document_ids": [relevant["document_id"], decoy["document_id"]],
            "top_k": 1,
            "minimum_score": 0.3,
            "cases": [
                {
                    "question": "What approval is required for renewal summaries?",
                    "expected_document_ids": [relevant["document_id"]],
                    "expected_chunk_ids": [relevant_chunk_id],
                    "expected_facts": ["manager approval before external distribution"],
                    "reference_answer": (
                        "Manager approval is required before external distribution."
                    ),
                },
                {
                    "question": "What is the lunar payroll deadline?",
                    "unanswerable": True,
                },
            ],
        },
    )
    assert create.status_code == 201
    dataset = create.json()
    assert dataset["case_count"] == 2
    assert len(dataset["cases"]) == 2

    run = client.post(
        f"/api/rag-evaluations/datasets/{dataset['dataset_id']}/runs",
        headers=_auth_headers(),
        json={"providers": ["local", "openai"]},
    )
    assert run.status_code == 201
    by_provider = {item["provider"]: item for item in run.json()["runs"]}
    assert by_provider["local"]["status"] == "completed"
    assert by_provider["local"]["retrieval_accuracy"] == 100.0
    assert by_provider["local"]["citation_correctness"] == 100.0
    assert by_provider["local"]["groundedness"] == 100.0
    assert by_provider["local"]["hallucination_rate"] == 0.0
    assert len(by_provider["local"]["results"]) == 2
    assert by_provider["openai"]["status"] == "skipped"
    assert "OPENAI_API_KEY" in by_provider["openai"]["error"]

    persisted = client.get(
        f"/api/rag-evaluations/runs/{by_provider['local']['run_id']}",
        headers=_auth_headers(),
    )
    assert persisted.status_code == 200
    assert len(persisted.json()["results"]) == 2


def test_provider_comparison_persists_different_quality(monkeypatch) -> None:
    suffix = uuid4().hex[:8]
    relevant = _upload(
        f"orchid-release-{suffix}.txt",
        "Orchid deployments require release manager approval.",
    )
    decoy = _upload(
        f"cafeteria-menu-{suffix}.txt",
        "The cafeteria serves soup on Tuesdays.",
    )

    create = client.post(
        "/api/rag-evaluations/datasets",
        headers=_auth_headers(),
        json={
            "name": f"Provider comparison {suffix}",
            "document_ids": [relevant["document_id"], decoy["document_id"]],
            "top_k": 1,
            "minimum_score": 0.1,
            "cases": [
                {
                    "question": "Who approves Orchid deployments?",
                    "expected_document_ids": [relevant["document_id"]],
                    "expected_facts": ["release manager approval"],
                    "reference_answer": "A release manager approves Orchid deployments.",
                }
            ],
        },
    )
    assert create.status_code == 201

    monkeypatch.setattr(
        embedding_service,
        "provider_unavailable_reason",
        lambda provider: None,
    )

    def fake_embed_many(texts: list[str], provider: str | None = None) -> list[list[float]]:
        vectors = []
        for text in texts:
            is_question = text.startswith("Who approves")
            is_relevant = text.startswith("Orchid deployments")
            if provider == "local":
                vectors.append([1.0, 0.0] if is_question or is_relevant else [0.0, 1.0])
            else:
                vectors.append([1.0, 0.0] if is_question or not is_relevant else [0.0, 1.0])
        return vectors

    monkeypatch.setattr(embedding_service, "embed_many", fake_embed_many)

    comparison = client.post(
        f"/api/rag-evaluations/datasets/{create.json()['dataset_id']}/runs",
        headers=_auth_headers(),
        json={"providers": ["local", "openai"]},
    )
    assert comparison.status_code == 201
    runs = {item["provider"]: item for item in comparison.json()["runs"]}
    assert runs["local"]["retrieval_accuracy"] == 100.0
    assert runs["openai"]["retrieval_accuracy"] == 0.0
    assert runs["openai"]["hallucination_rate"] == 100.0


def test_rag_evaluation_requires_manager_role_and_valid_expectations() -> None:
    employee = client.get(
        "/api/rag-evaluations/datasets",
        headers=_auth_headers("employee@demo.local"),
    )
    assert employee.status_code == 403

    invalid = client.post(
        "/api/rag-evaluations/datasets",
        headers=_auth_headers(),
        json={
            "name": f"Invalid {uuid4().hex[:8]}",
            "cases": [
                {
                    "question": "This case has contradictory expectations",
                    "expected_document_ids": ["doc_missing"],
                    "unanswerable": True,
                }
            ],
        },
    )
    assert invalid.status_code == 422


def test_manager_cannot_discover_restricted_evaluation_dataset() -> None:
    suffix = uuid4().hex[:8]
    restricted = _upload(
        f"restricted-evidence-{suffix}.txt",
        "Restricted acquisition evidence requires executive review.",
        classification="restricted",
    )
    create = client.post(
        "/api/rag-evaluations/datasets",
        headers=_auth_headers(),
        json={
            "name": f"Restricted evaluation {suffix}",
            "document_ids": [restricted["document_id"]],
            "cases": [
                {
                    "question": "What review does acquisition evidence require?",
                    "expected_document_ids": [restricted["document_id"]],
                    "expected_facts": ["executive review"],
                    "reference_answer": "It requires executive review.",
                }
            ],
        },
    )
    assert create.status_code == 201
    dataset_id = create.json()["dataset_id"]

    manager_headers = _auth_headers("manager@demo.local")
    listing = client.get("/api/rag-evaluations/datasets", headers=manager_headers)
    assert listing.status_code == 200
    assert dataset_id not in {item["dataset_id"] for item in listing.json()}
    detail = client.get(
        f"/api/rag-evaluations/datasets/{dataset_id}", headers=manager_headers
    )
    assert detail.status_code == 404
