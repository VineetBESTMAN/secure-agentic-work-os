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
    assert revision == ("20260715_0007",)

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
    assert revision == ("20260715_0007",)


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
            "SELECT organization_id, slug FROM organizations WHERE organization_id = 'org_default'"
        ).fetchone()
        membership = connection.execute(
            "SELECT organization_id, user_id, role FROM organization_memberships WHERE user_id = 'existing-user'"
        ).fetchone()
        workflow_tenant = connection.execute(
            "SELECT organization_id FROM agent_workflows WHERE workflow_id = 'wf_existing'"
        ).fetchone()
        membership_scopes = json.loads(
            connection.execute(
                "SELECT scopes_json FROM organization_memberships WHERE user_id = 'existing-user'"
            ).fetchone()[0]
        )

    assert user == ("existing-user", "existing@example.com")
    assert revision == ("20260715_0007",)
    assert documents_exists == (1,)
    assert workflow_actions == [
        (0, "search_documents", "pending"),
        (1, "create_task", "pending"),
    ]
    assert organization == ("org_default", "default")
    assert membership == ("org_default", "existing-user", "admin")
    assert workflow_tenant == ("org_default",)
    assert {
        "connectors:read",
        "connectors:manage",
        "connectors:sync",
        "connectors:act",
    } <= set(membership_scopes)
