"""Add durable object-storage metadata to documents.

Revision ID: 20260804_0011
Revises: 20260725_0010
Create Date: 2026-08-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260804_0011"
down_revision: str | None = "20260725_0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "storage_backend",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
    )
    op.add_column("documents", sa.Column("storage_key", sa.Text(), nullable=True))
    op.execute(
        "UPDATE documents SET storage_key = document_id || '_' || filename "
        "WHERE storage_key IS NULL"
    )
    op.create_index(
        "idx_documents_storage_location",
        "documents",
        ["storage_backend", "storage_key"],
    )


def downgrade() -> None:
    op.drop_index("idx_documents_storage_location", table_name="documents")
    op.drop_column("documents", "storage_key")
    op.drop_column("documents", "storage_backend")
