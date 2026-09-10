"""Optional real pgvector integration test; CI supplies a disposable service."""
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.database import get_connection
from app.core.migrations import downgrade_database, upgrade_database
from app.services.embeddings import embedding_service
from app.services.rag import rag_service
from app.services.users import user_service


@pytest.mark.skipif(not os.getenv("WORKOS_TEST_POSTGRES_URL"), reason="Disposable PostgreSQL URL not configured")
def test_pgvector_lexical_fallback_tenant_filters_and_migration_preservation(monkeypatch):
    url = os.environ["WORKOS_TEST_POSTGRES_URL"]
    assert urlparse(url).path.startswith("/workos_test_"), "Never run against an application database"
    monkeypatch.setattr(get_settings(), "database_url", url)
    upgrade_database(url)
    user_service.seed_demo_users()
    suffix = uuid4().hex
    tenant = "org_test_" + suffix
    with get_connection() as connection:
        connection.execute("INSERT INTO organizations (organization_id, name, slug) VALUES (?, ?, ?)",
                           (tenant, "Assessment tenant", "assessment-" + suffix))

    documents = []
    for organization, classification in [("org_default", "internal"), ("org_default", "restricted"), (tenant, "internal")]:
        documents.append(rag_service.ingest_file(
            filename="approvals-" + uuid4().hex + ".txt",
            data=b"Escalations require approvals before external transmission.",
            classification=classification, owner_team="assessment", uploaded_by="u_admin",
            organization_id=organization))
    with get_connection() as connection:
        for document in documents:
            connection.execute("UPDATE document_chunks SET embedding_model = 'retired-model' WHERE document_id = ?",
                               (document.document_id,))
    question = "How should escalations get approvals?"
    vector = embedding_service.embed(question, input_type="query")
    matches = rag_service._postgres_hybrid_matches(
        question, vector, role="employee", organization_id="org_default")
    ids = {match.row["document_id"] for match in matches}
    assert documents[0].document_id in ids, "OR lexical search must survive a missing query word and model mismatch"
    assert documents[1].document_id not in ids, "Restricted content leaked"
    assert documents[2].document_id not in ids, "Cross-tenant content leaked"

    # Returning to the previous tokenizer revision must never delete source data.
    downgrade_database(url, revision="20260821_0014")
    upgrade_database(url)
    with get_connection() as connection:
        assert connection.execute("SELECT document_id FROM documents WHERE document_id = ?",
                                  (documents[0].document_id,)).fetchone()
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"] == "20260908_0015"

    # A corrupted/mismatched chunk tenant is excluded independently of its parent.
    with get_connection() as connection:
        connection.execute("UPDATE document_chunks SET organization_id = ? WHERE document_id = ?",
                           (tenant, documents[0].document_id))
    assert documents[0].document_id not in {
        match.row["document_id"] for match in rag_service._postgres_hybrid_matches(
            question, vector, role="employee", organization_id="org_default")}
