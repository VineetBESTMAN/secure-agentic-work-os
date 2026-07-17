"""Add secret-safe live provider validation evidence.

Revision ID: 20260717_0009
Revises: 20260716_0008
Create Date: 2026-07-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260717_0009"
down_revision: str | None = "20260716_0008"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def upgrade() -> None:
    timestamp = _timestamp_type()
    op.add_column("connector_accounts", sa.Column("last_refresh_at", timestamp))
    op.create_table(
        "connector_validation_runs",
        sa.Column("validation_run_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_id",
            sa.Text(),
            sa.ForeignKey("connector_accounts.connector_id", ondelete="SET NULL"),
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "not_applicable_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("started_at", timestamp, nullable=False),
        sa.Column("completed_at", timestamp, nullable=False),
    )
    op.create_index(
        "idx_connector_validation_runs_organization_provider",
        "connector_validation_runs",
        ["organization_id", "provider", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_connector_validation_runs_organization_provider",
        table_name="connector_validation_runs",
    )
    op.drop_table("connector_validation_runs")
    op.drop_column("connector_accounts", "last_refresh_at")
