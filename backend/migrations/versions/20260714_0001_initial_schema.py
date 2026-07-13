"""Adopt the initial application schema.

Revision ID: 20260714_0001
Revises:
Create Date: 2026-07-14
"""
from __future__ import annotations

from collections.abc import Callable

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

revision: str = "20260714_0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def _table_missing(table_name: str) -> bool:
    return table_name not in sa.inspect(op.get_bind()).get_table_names()


def _column_missing(table_name: str, column_name: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return column_name not in {column["name"] for column in columns}


def _index_missing(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return index_name not in {index["name"] for index in indexes}


def _create_if_missing(table_name: str, create: Callable[[], None]) -> None:
    if _table_missing(table_name):
        create()


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    dimensions = int(op.get_context().config.attributes.get("vector_dimensions", 384))
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    _create_if_missing(
        "users",
        lambda: op.create_table(
            "users",
            sa.Column("user_id", sa.Text(), primary_key=True),
            sa.Column("email", sa.Text(), nullable=False, unique=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False),
            sa.Column("scopes_json", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "documents",
        lambda: op.create_table(
            "documents",
            sa.Column("document_id", sa.Text(), primary_key=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("classification", sa.Text(), nullable=False),
            sa.Column("owner_team", sa.Text(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("uploaded_by", sa.Text(), nullable=False),
            sa.Column(
                "unsafe",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE" if dialect == "postgresql" else "0"),
            ),
            sa.Column(
                "unsafe_reasons_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )

    def create_document_chunks() -> None:
        columns: list[sa.Column] = [
            sa.Column("chunk_id", sa.Text(), primary_key=True),
            sa.Column(
                "document_id",
                sa.Text(),
                sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
        ]
        if dialect == "postgresql":
            columns.append(sa.Column("embedding", Vector(dimensions), nullable=True))
        else:
            columns.append(sa.Column("embedding_json", sa.Text(), nullable=True))
        columns.append(
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        op.create_table("document_chunks", *columns)

    _create_if_missing("document_chunks", create_document_chunks)
    if dialect == "postgresql" and _column_missing("document_chunks", "embedding"):
        op.add_column("document_chunks", sa.Column("embedding", Vector(dimensions)))
    if dialect == "sqlite" and _column_missing("document_chunks", "embedding_json"):
        op.add_column("document_chunks", sa.Column("embedding_json", sa.Text()))
    if _index_missing("document_chunks", "idx_chunks_document_id"):
        op.create_index("idx_chunks_document_id", "document_chunks", ["document_id"])
    if dialect == "postgresql" and _index_missing(
        "document_chunks", "idx_chunks_embedding_hnsw"
    ):
        op.execute(
            "CREATE INDEX idx_chunks_embedding_hnsw "
            "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
        )

    _create_if_missing(
        "audit_events",
        lambda: op.create_table(
            "audit_events",
            sa.Column("event_id", sa.Text(), primary_key=True),
            sa.Column("actor_id", sa.Text(), nullable=False),
            sa.Column("event_type", sa.Text(), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False),
            sa.Column(
                "timestamp",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "approval_requests",
        lambda: op.create_table(
            "approval_requests",
            sa.Column("approval_id", sa.Text(), primary_key=True),
            sa.Column("action_id", sa.Text(), nullable=False),
            sa.Column("requested_by", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("reviewed_by", sa.Text()),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "oauth_states",
        lambda: op.create_table(
            "oauth_states",
            sa.Column("state", sa.Text(), primary_key=True),
            sa.Column("provider", sa.Text(), nullable=False),
            sa.Column("requested_by", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "connector_accounts",
        lambda: op.create_table(
            "connector_accounts",
            sa.Column("connector_id", sa.Text(), primary_key=True),
            sa.Column("provider", sa.Text(), nullable=False),
            sa.Column("account_label", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("scopes_json", sa.Text(), nullable=False),
            sa.Column("token_cipher", sa.Text()),
            sa.Column("refresh_token_cipher", sa.Text()),
            sa.Column("expires_at", _timestamp_type()),
            sa.Column("created_by", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "policies",
        lambda: op.create_table(
            "policies",
            sa.Column("policy_id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("rule_type", sa.Text(), nullable=False),
            sa.Column("effect", sa.Text(), nullable=False),
            sa.Column("conditions_json", sa.Text(), nullable=False),
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE" if dialect == "postgresql" else "1"),
            ),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "background_jobs",
        lambda: op.create_table(
            "background_jobs",
            sa.Column("job_id", sa.Text(), primary_key=True),
            sa.Column("job_type", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False),
            sa.Column(
                "result_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("created_by", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )
    _create_if_missing(
        "agent_workflows",
        lambda: op.create_table(
            "agent_workflows",
            sa.Column("workflow_id", sa.Text(), primary_key=True),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("requested_by", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("plan_json", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                _timestamp_type(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ),
    )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "agent_workflows",
        "background_jobs",
        "policies",
        "connector_accounts",
        "oauth_states",
        "approval_requests",
        "audit_events",
        "document_chunks",
        "documents",
        "users",
    ):
        if table_name in existing:
            op.drop_table(table_name)
