"""Align SQLite indexed retrieval with natural-language inflections.

Revision ID: 20260908_0015
Revises: 20260821_0014
"""
from alembic import op

revision = "20260908_0015"
down_revision = "20260821_0014"
branch_labels = None
depends_on = None


def _rebuild(tokenizer: str) -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    # Only the derived index is rebuilt; source documents/chunks are untouched.
    op.execute("DROP TABLE document_chunks_fts")
    op.execute(
        "CREATE VIRTUAL TABLE document_chunks_fts USING fts5("
        "heading, text, content='document_chunks', content_rowid='rowid', "
        f"tokenize='{tokenizer}')"
    )
    op.execute("INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')")


def upgrade() -> None:
    _rebuild("porter unicode61")


def downgrade() -> None:
    _rebuild("unicode61")
