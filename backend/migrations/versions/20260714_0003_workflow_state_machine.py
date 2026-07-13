"""Add resumable workflow actions and MCP idempotency keys.

Revision ID: 20260714_0003
Revises: 20260714_0002
Create Date: 2026-07-14
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision: str = "20260714_0003"
down_revision: str | None = "20260714_0002"
branch_labels: str | None = None
depends_on: str | None = None


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def _backfill_workflow_actions() -> None:
    connection = op.get_bind()
    workflows = connection.execute(
        sa.text("SELECT workflow_id, plan_json FROM agent_workflows")
    ).mappings()
    now = datetime.now(timezone.utc).isoformat()
    tool_by_action = {
        "search_email": "search_documents",
        "search_documents": "search_documents",
        "create_task": "create_task",
        "send_email": "send_email",
    }
    for workflow in workflows:
        try:
            actions = json.loads(workflow["plan_json"]).get("actions", [])
        except (AttributeError, json.JSONDecodeError, TypeError):
            continue
        for sequence, action in enumerate(actions):
            tool_name = tool_by_action.get(action.get("action_type"))
            if tool_name is None:
                continue
            connection.execute(
                sa.text(
                    """
                    INSERT INTO workflow_actions (
                        action_instance_id, workflow_id, sequence, action_type,
                        tool_name, description, required_scope, requires_approval,
                        status, idempotency_key, created_at, updated_at
                    )
                    VALUES (
                        :action_instance_id, :workflow_id, :sequence, :action_type,
                        :tool_name, :description, :required_scope, :requires_approval,
                        'pending', :idempotency_key, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "action_instance_id": f"wfa_{uuid4().hex}",
                    "workflow_id": workflow["workflow_id"],
                    "sequence": sequence,
                    "action_type": action["action_type"],
                    "tool_name": tool_name,
                    "description": action.get("description", tool_name),
                    "required_scope": action.get("scope", ""),
                    "requires_approval": bool(action.get("requires_approval", False)),
                    "idempotency_key": (
                        f"workflow:{workflow['workflow_id']}:action:{sequence}"
                    ),
                    "created_at": now,
                    "updated_at": now,
                },
            )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.add_column(
        "mcp_tool_executions",
        sa.Column("idempotency_key", sa.Text()),
    )
    op.create_index(
        "idx_mcp_executions_idempotency_key",
        "mcp_tool_executions",
        ["idempotency_key"],
        unique=True,
    )

    op.add_column(
        "agent_workflows",
        sa.Column(
            "current_action_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("agent_workflows", sa.Column("last_error", sa.Text()))
    op.add_column("agent_workflows", sa.Column("started_at", _timestamp_type()))
    op.add_column("agent_workflows", sa.Column("completed_at", _timestamp_type()))
    op.add_column("agent_workflows", sa.Column("cancelled_at", _timestamp_type()))

    op.create_table(
        "workflow_actions",
        sa.Column("action_instance_id", sa.Text(), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.Text(),
            sa.ForeignKey("agent_workflows.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_scope", sa.Text(), nullable=False),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE" if dialect == "postgresql" else "0"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "input_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "result_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("execution_id", sa.Text(), unique=True),
        sa.Column("approval_id", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", _timestamp_type()),
        sa.Column("completed_at", _timestamp_type()),
        sa.Column(
            "updated_at",
            _timestamp_type(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("workflow_id", "sequence", name="uq_workflow_action_sequence"),
    )
    op.create_index(
        "idx_workflow_actions_workflow_status",
        "workflow_actions",
        ["workflow_id", "status", "sequence"],
    )
    _backfill_workflow_actions()


def downgrade() -> None:
    op.drop_table("workflow_actions")
    for column_name in (
        "cancelled_at",
        "completed_at",
        "started_at",
        "last_error",
        "current_action_index",
    ):
        op.drop_column("agent_workflows", column_name)
    op.drop_index(
        "idx_mcp_executions_idempotency_key",
        table_name="mcp_tool_executions",
    )
    op.drop_column("mcp_tool_executions", "idempotency_key")
