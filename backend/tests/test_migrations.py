import json
import sqlite3
from pathlib import Path

from app.core.migrations import downgrade_database, upgrade_database


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def test_migration_round_trip_creates_versioned_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-round-trip.db"
    database_url = _sqlite_url(database_path)

    upgrade_database(database_url)
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert "documents" in tables
    assert "background_jobs" in tables
    assert "mcp_tool_executions" in tables
    assert "workspace_tasks" in tables
    assert "workflow_actions" in tables
    assert "runtime_observations" in tables
    assert "cost_budgets" in tables
    assert "rag_evaluation_datasets" in tables
    assert "rag_evaluation_cases" in tables
    assert "rag_evaluation_runs" in tables
    assert "rag_evaluation_results" in tables
    assert "organizations" in tables
    assert "organization_memberships" in tables
    assert "organization_invitations" in tables
    assert "auth_sessions" in tables
    assert "oidc_providers" in tables
    assert "connector_sync_states" in tables
    assert "connector_sync_items" in tables
    assert "connector_webhook_subscriptions" in tables
    assert "connector_webhook_deliveries" in tables
    assert "connector_action_receipts" in tables
    assert "openclaw_clients" in tables
    assert "connector_validation_runs" in tables
    assert "organization_security_policies" in tables
    assert "security_findings" in tables
    assert "workflow_graph_events" in tables
    with sqlite3.connect(database_path) as connection:
        connector_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(connector_accounts)").fetchall()
        }
        chunk_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(document_chunks)").fetchall()
        }
        evaluation_result_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(rag_evaluation_results)"
            ).fetchall()
        }
    assert "last_refresh_at" in connector_columns
    assert {"heading", "locator", "content_hash", "embedding_model"} <= chunk_columns
    assert {"answer", "answerable", "confidence", "answer_correctness"} <= (
        evaluation_result_columns
    )
    assert "document_chunks_fts" in tables
    assert revision == ("20260821_0014",)

    downgrade_database(database_url)
    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "documents" not in remaining

    upgrade_database(database_url)
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert revision == ("20260821_0014",)


def test_initial_migration_adopts_existing_tables_without_data_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "existing-schema.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE agent_workflows (
                workflow_id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO agent_workflows (
                workflow_id, prompt, requested_by, status, plan_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "wf_existing",
                "Find existing data and create a task",
                "existing-user",
                "planned",
                json.dumps(
                    {
                        "summary": "Existing workflow",
                        "actions": [
                            {
                                "action_id": "act_search",
                                "action_type": "search_email",
                                "description": "Search existing data",
                                "requires_approval": False,
                                "scope": "documents:read",
                            },
                            {
                                "action_id": "act_task",
                                "action_type": "create_task",
                                "description": "Create a task",
                                "requires_approval": False,
                                "scope": "tasks:write",
                            },
                        ],
                    }
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO users (user_id, email, password_hash, role, scopes_json)
            VALUES ('existing-user', 'existing@example.com', 'hash', 'admin', '[]')
            """
        )

    upgrade_database(_sqlite_url(database_path))

    with sqlite3.connect(database_path) as connection:
        user = connection.execute(
            "SELECT user_id, email FROM users WHERE user_id = 'existing-user'"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        documents_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone()
        workflow_actions = connection.execute(
            """
            SELECT sequence, tool_name, status
            FROM workflow_actions
            WHERE workflow_id = 'wf_existing'
            ORDER BY sequence
            """
        ).fetchall()
        organization = connection.execute(
            "SELECT organization_id, slug FROM organizations "
            "WHERE organization_id = 'org_default'"
        ).fetchone()
        membership = connection.execute(
            "SELECT organization_id, user_id, role FROM organization_memberships "
            "WHERE user_id = 'existing-user'"
        ).fetchone()
        workflow_tenant = connection.execute(
            "SELECT organization_id FROM agent_workflows WHERE workflow_id = 'wf_existing'"
        ).fetchone()
        workflow_engine = connection.execute(
            "SELECT orchestration_engine FROM agent_workflows "
            "WHERE workflow_id = 'wf_existing'"
        ).fetchone()
        document_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        membership_scopes = json.loads(
            connection.execute(
                "SELECT scopes_json FROM organization_memberships WHERE user_id = 'existing-user'"
            ).fetchone()[0]
        )

    assert user == ("existing-user", "existing@example.com")
    assert revision == ("20260821_0014",)
    assert documents_exists == (1,)
    assert workflow_actions == [
        (0, "search_documents", "pending"),
        (1, "create_task", "pending"),
    ]
    assert organization == ("org_default", "default")
    assert membership == ("org_default", "existing-user", "admin")
    assert workflow_tenant == ("org_default",)
    assert workflow_engine == ("deterministic",)
    assert {"storage_backend", "storage_key"} <= document_columns
    assert {
        "connectors:read",
        "connectors:manage",
        "connectors:sync",
        "connectors:act",
    } <= set(membership_scopes)


def test_object_storage_migration_backfills_existing_documents(tmp_path: Path) -> None:
    database_path = tmp_path / "storage-backfill.db"
    database_url = _sqlite_url(database_path)
    upgrade_database(database_url, revision="20260725_0010")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                document_id, title, filename, classification, owner_team, summary,
                uploaded_by, unsafe, unsafe_reasons_json, organization_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "doc_existing",
                "Existing document",
                "existing.txt",
                "internal",
                "operations",
                "Existing content",
                "existing-user",
                0,
                "[]",
                "org_default",
            ),
        )

    upgrade_database(database_url)

    with sqlite3.connect(database_path) as connection:
        storage = connection.execute(
            """
            SELECT storage_backend, storage_key
            FROM documents
            WHERE document_id = 'doc_existing'
            """
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert storage == ("local", "doc_existing_existing.txt")
    assert revision == ("20260821_0014",)


def test_reliability_migration_preserves_existing_job_state(tmp_path: Path) -> None:
    database_path = tmp_path / "reliability-backfill.db"
    database_url = _sqlite_url(database_path)
    upgrade_database(database_url, revision="20260804_0011")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO background_jobs (
                job_id, job_type, status, detail_json, result_json,
                created_by, organization_id
            ) VALUES ('job_existing', 'document.ingest', 'running', '{}',
                      '{"progress": 25}', 'existing-user', 'org_default')
            """
        )

    upgrade_database(database_url)

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        job = connection.execute(
            "SELECT * FROM background_jobs WHERE job_id = 'job_existing'"
        ).fetchone()
        webhook_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(connector_webhook_deliveries)"
            ).fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(background_jobs)"
            ).fetchall()
        }

    assert job["status"] == "running"
    assert json.loads(job["result_json"]) == {"progress": 25}
    assert job["attempt_count"] == 0
    assert job["max_attempts"] == 3
    assert job["last_error"] is None
    assert {"duplicate_count", "last_duplicate_at"} <= webhook_columns
    assert "idx_background_jobs_organization_status_updated" in indexes
