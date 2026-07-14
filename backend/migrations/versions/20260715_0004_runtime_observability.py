"""Add runtime observations and cost budgets.

Revision ID: 20260715_0004
Revises: 20260714_0003
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260715_0004"
down_revision: str | None = "20260714_0003"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.create_table(
        "runtime_observations",
        sa.Column("observation_id", sa.Text(), primary_key=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column(
            "input_units",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "output_units",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "metadata_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "idx_runtime_observations_created_at",
        "runtime_observations",
        ["created_at"],
    )
    op.create_index(
        "idx_runtime_observations_operation_status",
        "runtime_observations",
        ["operation_type", "status", "created_at"],
    )
    op.create_index(
        "idx_runtime_observations_trace_id",
        "runtime_observations",
        ["trace_id"],
    )

    op.create_table(
        "cost_budgets",
        sa.Column("budget_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("limit_usd", sa.Float(), nullable=False),
        sa.Column("warning_percent", sa.Integer(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE" if dialect == "postgresql" else "1"),
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
    )


def downgrade() -> None:
    op.drop_table("cost_budgets")
    op.drop_table("runtime_observations")
