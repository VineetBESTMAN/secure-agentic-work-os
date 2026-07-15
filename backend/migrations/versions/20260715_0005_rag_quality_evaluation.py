"""Add persistent RAG quality evaluation datasets and runs.

Revision ID: 20260715_0005
Revises: 20260715_0004
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260715_0005"
down_revision: str | None = "20260715_0004"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    boolean_false = sa.text("FALSE" if dialect == "postgresql" else "0")

    op.create_table(
        "rag_evaluation_datasets",
        sa.Column("dataset_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("document_ids_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("minimum_score", sa.Float(), nullable=False, server_default=sa.text("0")),
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

    op.create_table(
        "rag_evaluation_cases",
        sa.Column("case_id", sa.Text(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Text(),
            sa.ForeignKey("rag_evaluation_datasets.dataset_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_document_ids_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("expected_chunk_ids_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("expected_facts_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("reference_answer", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("unanswerable", sa.Boolean(), nullable=False, server_default=boolean_false),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("dataset_id", "position", name="uq_rag_evaluation_case_position"),
    )
    op.create_index(
        "idx_rag_evaluation_cases_dataset",
        "rag_evaluation_cases",
        ["dataset_id", "position"],
    )

    op.create_table(
        "rag_evaluation_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("comparison_id", sa.Text(), nullable=False),
        sa.Column(
            "dataset_id",
            sa.Text(),
            sa.ForeignKey("rag_evaluation_datasets.dataset_id"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retrieval_accuracy", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("citation_correctness", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("groundedness", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("hallucination_rate", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("average_latency_ms", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("p95_latency_ms", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("index_latency_ms", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", _timestamp_type(), nullable=True),
    )
    op.create_index(
        "idx_rag_evaluation_runs_dataset_created",
        "rag_evaluation_runs",
        ["dataset_id", "created_at"],
    )
    op.create_index(
        "idx_rag_evaluation_runs_comparison",
        "rag_evaluation_runs",
        ["comparison_id", "provider"],
    )

    op.create_table(
        "rag_evaluation_results",
        sa.Column("result_id", sa.Text(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("rag_evaluation_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            sa.Text(),
            sa.ForeignKey("rag_evaluation_cases.case_id"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("retrieval_accuracy", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("citation_correctness", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("groundedness", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("hallucination_detected", sa.Boolean(), nullable=False, server_default=boolean_false),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "idx_rag_evaluation_results_run",
        "rag_evaluation_results",
        ["run_id", "case_id"],
    )


def downgrade() -> None:
    op.drop_table("rag_evaluation_results")
    op.drop_table("rag_evaluation_runs")
    op.drop_table("rag_evaluation_cases")
    op.drop_table("rag_evaluation_datasets")
