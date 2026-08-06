"""Add durable job and webhook reliability state.

Revision ID: 20260805_0012
Revises: 20260804_0011
Create Date: 2026-08-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260805_0012"
down_revision: str | None = "20260804_0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "background_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column("background_jobs", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("background_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column(
        "background_jobs", sa.Column("dead_lettered_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "idx_background_jobs_organization_status_updated",
        "background_jobs",
        ["organization_id", "status", "updated_at"],
    )
    op.create_index(
        "idx_documents_organization_created",
        "documents",
        ["organization_id", "created_at"],
    )
    op.add_column(
        "connector_webhook_deliveries",
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "connector_webhook_deliveries",
        sa.Column("last_duplicate_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("connector_webhook_deliveries", "last_duplicate_at")
    op.drop_column("connector_webhook_deliveries", "duplicate_count")
    op.drop_index("idx_documents_organization_created", table_name="documents")
    op.drop_index(
        "idx_background_jobs_organization_status_updated",
        table_name="background_jobs",
    )
    op.drop_column("background_jobs", "dead_lettered_at")
    op.drop_column("background_jobs", "heartbeat_at")
    op.drop_column("background_jobs", "last_error")
    op.drop_column("background_jobs", "max_attempts")
    op.drop_column("background_jobs", "attempt_count")
