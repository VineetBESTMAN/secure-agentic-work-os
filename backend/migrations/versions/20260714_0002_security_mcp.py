"""Persist security MCP executions and bind approvals to exact payloads.

Revision ID: 20260714_0002
Revises: 20260714_0001
Create Date: 2026-07-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260714_0002"
down_revision: str | None = "20260714_0001"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def _column_missing(table_name: str, column_name: str) -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return column_name not in {column["name"] for column in columns}


def _index_missing(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return index_name not in {index["name"] for index in indexes}


def upgrade() -> None:
    op.create_table(
        "workspace_tasks",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("source_execution_id", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "mcp_tool_executions",
        sa.Column("execution_id", sa.Text(), primary_key=True),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("required_scope", sa.Text(), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("arguments_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("approval_id", sa.Text(), unique=True),
        sa.Column(
            "result_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("error", sa.Text()),
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
    )
    op.create_index(
        "idx_mcp_executions_requested_by",
        "mcp_tool_executions",
        ["requested_by", "created_at"],
    )
    op.create_index(
        "idx_mcp_executions_status",
        "mcp_tool_executions",
        ["status"],
    )

    if _column_missing("approval_requests", "execution_id"):
        op.add_column("approval_requests", sa.Column("execution_id", sa.Text()))
    if _column_missing("approval_requests", "arguments_hash"):
        op.add_column("approval_requests", sa.Column("arguments_hash", sa.Text()))
    if _column_missing("approval_requests", "reviewed_at"):
        op.add_column("approval_requests", sa.Column("reviewed_at", _timestamp_type()))
    if _index_missing("approval_requests", "idx_approval_execution_id"):
        op.create_index(
            "idx_approval_execution_id",
            "approval_requests",
            ["execution_id"],
            unique=True,
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "approval_requests" in existing_tables:
        if not _index_missing("approval_requests", "idx_approval_execution_id"):
            op.drop_index("idx_approval_execution_id", table_name="approval_requests")
        for column_name in ("reviewed_at", "arguments_hash", "execution_id"):
            if not _column_missing("approval_requests", column_name):
                op.drop_column("approval_requests", column_name)
    if "mcp_tool_executions" in existing_tables:
        op.drop_table("mcp_tool_executions")
    if "workspace_tasks" in existing_tables:
        op.drop_table("workspace_tasks")
