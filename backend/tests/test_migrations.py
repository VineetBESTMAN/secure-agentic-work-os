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
    assert revision == ("20260714_0001",)

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
    assert revision == ("20260714_0001",)


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

    assert user == ("existing-user", "existing@example.com")
    assert revision == ("20260714_0001",)
    assert documents_exists == (1,)
