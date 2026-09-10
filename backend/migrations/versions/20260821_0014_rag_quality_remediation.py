"""Add hybrid retrieval metadata and answer-level RAG evaluation.

Revision ID: 20260821_0014
Revises: 20260809_0013
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_0014"
down_revision: str | None = "20260809_0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    boolean_false = sa.text("FALSE" if dialect == "postgresql" else "0")

    op.add_column("document_chunks", sa.Column("heading", sa.Text()))
    op.add_column(
        "document_chunks",
        sa.Column(
            "locator", sa.Text(), nullable=False, server_default=sa.text("'legacy chunk'")
        ),
    )
    op.add_column(
        "document_chunks",
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "document_chunks",
        sa.Column("content_hash", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "embedding_model", sa.Text(), nullable=False, server_default=sa.text("'legacy'")
        ),
    )
    op.create_index(
        "idx_chunks_organization_embedding_model",
        "document_chunks",
        ["organization_id", "embedding_model"],
    )

    op.add_column(
        "rag_evaluation_runs",
        sa.Column("answer_correctness", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rag_evaluation_results",
        sa.Column("answer", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "rag_evaluation_results",
        sa.Column("answerable", sa.Boolean(), nullable=False, server_default=boolean_false),
    )
    op.add_column(
        "rag_evaluation_results",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rag_evaluation_results",
        sa.Column("answer_correctness", sa.Float(), nullable=False, server_default="0"),
    )

    if dialect == "postgresql":
        op.execute(
            "CREATE INDEX idx_chunks_search_text_gin ON document_chunks USING gin "
            "(to_tsvector('english', coalesce(heading, '') || ' ' || text))"
        )
        return

    op.execute(
        "CREATE VIRTUAL TABLE document_chunks_fts USING fts5("
        "heading, text, content='document_chunks', content_rowid='rowid')"
    )
    op.execute(
        "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')"
    )
    op.execute(
        "CREATE TRIGGER document_chunks_fts_insert AFTER INSERT ON document_chunks BEGIN "
        "INSERT INTO document_chunks_fts(rowid, heading, text) "
        "VALUES (new.rowid, new.heading, new.text); END"
    )
    op.execute(
        "CREATE TRIGGER document_chunks_fts_delete AFTER DELETE ON document_chunks BEGIN "
        "INSERT INTO document_chunks_fts(document_chunks_fts, rowid, heading, text) "
        "VALUES ('delete', old.rowid, old.heading, old.text); END"
    )
    op.execute(
        "CREATE TRIGGER document_chunks_fts_update AFTER UPDATE ON document_chunks BEGIN "
        "INSERT INTO document_chunks_fts(document_chunks_fts, rowid, heading, text) "
        "VALUES ('delete', old.rowid, old.heading, old.text); "
        "INSERT INTO document_chunks_fts(rowid, heading, text) "
        "VALUES (new.rowid, new.heading, new.text); END"
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.drop_index("idx_chunks_search_text_gin", table_name="document_chunks")
    else:
        op.execute("DROP TRIGGER IF EXISTS document_chunks_fts_update")
        op.execute("DROP TRIGGER IF EXISTS document_chunks_fts_delete")
        op.execute("DROP TRIGGER IF EXISTS document_chunks_fts_insert")
        op.execute("DROP TABLE IF EXISTS document_chunks_fts")

    op.drop_column("rag_evaluation_results", "answer_correctness")
    op.drop_column("rag_evaluation_results", "confidence")
    op.drop_column("rag_evaluation_results", "answerable")
    op.drop_column("rag_evaluation_results", "answer")
    op.drop_column("rag_evaluation_runs", "answer_correctness")
    op.drop_index("idx_chunks_organization_embedding_model", table_name="document_chunks")
    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("document_chunks", "content_hash")
    op.drop_column("document_chunks", "token_count")
    op.drop_column("document_chunks", "locator")
    op.drop_column("document_chunks", "heading")
