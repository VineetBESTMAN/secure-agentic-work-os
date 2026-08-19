"""Add tenant-bound LangGraph orchestration metadata.

Revision ID: 20260809_0013
Revises: 20260805_0012
Create Date: 2026-08-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_0013"
down_revision: str | None = "20260805_0012"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def upgrade() -> None:
    op.add_column(
        "agent_workflows",
        sa.Column(
            "orchestration_engine",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'deterministic'"),
        ),
    )
    op.add_column("agent_workflows", sa.Column("graph_thread_id", sa.Text()))
    op.add_column("agent_workflows", sa.Column("graph_checkpoint_id", sa.Text()))
    op.add_column(
        "agent_workflows",
        sa.Column("graph_step_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_workflows", sa.Column("graph_last_node", sa.Text()))
    op.create_index(
        "idx_agent_workflows_organization_engine_updated",
        "agent_workflows",
        ["organization_id", "orchestration_engine", "updated_at"],
    )

    op.create_table(
        "workflow_graph_events",
        sa.Column("graph_event_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.Text(),
            sa.ForeignKey("agent_workflows.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("node_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("current_action_index", sa.Integer(), nullable=False),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("workflow_id", "step", name="uq_workflow_graph_event_step"),
    )
    op.create_index(
        "idx_workflow_graph_events_organization_workflow_created",
        "workflow_graph_events",
        ["organization_id", "workflow_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_workflow_graph_events_organization_workflow_created",
        table_name="workflow_graph_events",
    )
    op.drop_table("workflow_graph_events")
    op.drop_index(
        "idx_agent_workflows_organization_engine_updated",
        table_name="agent_workflows",
    )
    op.drop_column("agent_workflows", "graph_last_node")
    op.drop_column("agent_workflows", "graph_step_count")
    op.drop_column("agent_workflows", "graph_checkpoint_id")
    op.drop_column("agent_workflows", "graph_thread_id")
    op.drop_column("agent_workflows", "orchestration_engine")
