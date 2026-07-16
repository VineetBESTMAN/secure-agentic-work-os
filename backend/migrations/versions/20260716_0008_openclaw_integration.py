"""Add tenant-scoped OpenClaw service clients.

Revision ID: 20260716_0008
Revises: 20260715_0007
Create Date: 2026-07-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0008"
down_revision: str | None = "20260715_0007"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def upgrade() -> None:
    timestamp = _timestamp_type()
    op.create_table(
        "openclaw_clients",
        sa.Column("client_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("last_used_at", timestamp),
        sa.Column("revoked_at", timestamp),
        sa.Column("rotated_at", timestamp),
        sa.Column(
            "created_at",
            timestamp,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_openclaw_clients_org_name"
        ),
    )
    op.create_index(
        "ix_openclaw_clients_org_status",
        "openclaw_clients",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_openclaw_clients_org_status", table_name="openclaw_clients")
    op.drop_table("openclaw_clients")
