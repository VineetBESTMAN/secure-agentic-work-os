"""Add tenant security policies and secret-safe security findings.

Revision ID: 20260725_0010
Revises: 20260717_0009
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260725_0010"
down_revision: str | None = "20260717_0009"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def _boolean_default(value: bool) -> sa.TextClause:
    if op.get_bind().dialect.name == "postgresql":
        return sa.text("true" if value else "false")
    return sa.text("1" if value else "0")


def upgrade() -> None:
    timestamp = _timestamp_type()
    op.create_table(
        "organization_security_policies",
        sa.Column("policy_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("dlp_mode", sa.Text(), nullable=False, server_default=sa.text("'audit'")),
        sa.Column(
            "dlp_data_types_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text(
                """'["api_key","credit_card","private_key","ssn"]'"""
            ),
        ),
        sa.Column(
            "malware_mode", sa.Text(), nullable=False, server_default=sa.text("'basic'")
        ),
        sa.Column(
            "retention_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=_boolean_default(False),
        ),
        sa.Column(
            "audit_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("365"),
        ),
        sa.Column(
            "runtime_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("90"),
        ),
        sa.Column(
            "connector_validation_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("90"),
        ),
        sa.Column(
            "rag_evaluation_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("180"),
        ),
        sa.Column(
            "security_finding_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("365"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            timestamp,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            timestamp,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "security_findings",
        sa.Column("finding_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("control_type", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_name", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("findings_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            timestamp,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "idx_security_findings_organization_created",
        "security_findings",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "idx_security_findings_organization_control",
        "security_findings",
        ["organization_id", "control_type", "action"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_security_findings_organization_control",
        table_name="security_findings",
    )
    op.drop_index(
        "idx_security_findings_organization_created",
        table_name="security_findings",
    )
    op.drop_table("security_findings")
    op.drop_table("organization_security_policies")
