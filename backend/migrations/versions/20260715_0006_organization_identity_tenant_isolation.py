"""Add organization identity, sessions, SSO, and tenant isolation.

Revision ID: 20260715_0006
Revises: 20260715_0005
Create Date: 2026-07-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260715_0006"
down_revision: str | None = "20260715_0005"
branch_labels: str | None = None
depends_on: str | None = None

DEFAULT_ORGANIZATION_ID = "org_default"


def _timestamp_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return sa.DateTime(timezone=True)
    return sa.Text()


def _boolean_default(value: bool) -> sa.TextClause:
    postgres = "TRUE" if value else "FALSE"
    sqlite = "1" if value else "0"
    return sa.text(postgres if op.get_bind().dialect.name == "postgresql" else sqlite)


def _columns(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    timestamp = _timestamp_type()
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.Text(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO organizations (organization_id, slug, name, created_by)
            VALUES (:organization_id, 'default', 'Default Workspace', 'system')
            """
        ).bindparams(organization_id=DEFAULT_ORGANIZATION_ID)
    )

    for column_name, column in (
        ("display_name", sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''"))),
        ("disabled", sa.Column("disabled", sa.Boolean(), nullable=False, server_default=_boolean_default(False))),
        ("token_version", sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0"))),
        ("last_login_at", sa.Column("last_login_at", timestamp, nullable=True)),
        ("password_changed_at", sa.Column("password_changed_at", timestamp, nullable=True)),
    ):
        if column_name not in _columns("users"):
            op.add_column("users", column)

    op.create_table(
        "organization_memberships",
        sa.Column("membership_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_membership_organization_user"),
    )
    op.create_index("idx_memberships_user_status", "organization_memberships", ["user_id", "status"])

    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT user_id, role, scopes_json FROM users")).mappings()
    for user in users:
        connection.execute(
            sa.text(
                """
                INSERT INTO organization_memberships (
                    membership_id, organization_id, user_id, role, scopes_json, status
                ) VALUES (
                    :membership_id, :organization_id, :user_id, :role, :scopes_json, 'active'
                )
                """
            ),
            {
                "membership_id": f"mem_default_{user['user_id']}",
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "user_id": user["user_id"],
                "role": user["role"],
                "scopes_json": user["scopes_json"],
            },
        )

    op.create_table(
        "organization_invitations",
        sa.Column("invitation_id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("invited_by", sa.Text(), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("accepted_by", sa.Text(), nullable=True),
        sa.Column("accepted_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_invitations_organization_status", "organization_invitations", ["organization_id", "status"])

    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("membership_id", sa.Text(), sa.ForeignKey("organization_memberships.membership_id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("revoked_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_used_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])

    op.create_table(
        "oidc_providers",
        sa.Column("provider_id", sa.Text(), primary_key=True),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("issuer_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_cipher", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=_boolean_default(True)),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("organization_id", "issuer_url", name="uq_oidc_provider_organization_issuer"),
    )
    op.create_table(
        "oidc_authorization_states",
        sa.Column("state_hash", sa.Text(), primary_key=True),
        sa.Column("provider_id", sa.Text(), sa.ForeignKey("oidc_providers.provider_id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Text(), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier_cipher", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    tenant_tables = (
        "documents",
        "document_chunks",
        "audit_events",
        "approval_requests",
        "oauth_states",
        "connector_accounts",
        "policies",
        "background_jobs",
        "agent_workflows",
        "workspace_tasks",
        "mcp_tool_executions",
        "workflow_actions",
        "runtime_observations",
        "cost_budgets",
        "rag_evaluation_datasets",
        "rag_evaluation_cases",
        "rag_evaluation_runs",
        "rag_evaluation_results",
    )
    for table_name in tenant_tables:
        if "organization_id" not in _columns(table_name):
            op.add_column(
                table_name,
                sa.Column(
                    "organization_id",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text(f"'{DEFAULT_ORGANIZATION_ID}'"),
                ),
            )
        op.create_index(
            f"idx_{table_name}_organization",
            table_name,
            ["organization_id"],
        )

    _replace_unique_constraint(
        "cost_budgets",
        old_columns=["name"],
        old_fallback_name="uq_cost_budgets_name",
        new_name="uq_cost_budgets_organization_name",
        new_columns=["organization_id", "name"],
    )
    _replace_unique_constraint(
        "rag_evaluation_datasets",
        old_columns=["name"],
        old_fallback_name="uq_rag_evaluation_datasets_name",
        new_name="uq_rag_evaluation_datasets_organization_name",
        new_columns=["organization_id", "name"],
    )

    op.drop_index("idx_mcp_executions_idempotency_key", table_name="mcp_tool_executions")
    op.create_index(
        "idx_mcp_executions_organization_idempotency",
        "mcp_tool_executions",
        ["organization_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_mcp_executions_organization_idempotency",
        table_name="mcp_tool_executions",
    )
    op.create_index(
        "idx_mcp_executions_idempotency_key",
        "mcp_tool_executions",
        ["idempotency_key"],
        unique=True,
    )
    _replace_unique_constraint(
        "rag_evaluation_datasets",
        old_columns=["organization_id", "name"],
        old_fallback_name="uq_rag_evaluation_datasets_organization_name",
        new_name="uq_rag_evaluation_datasets_name",
        new_columns=["name"],
    )
    _replace_unique_constraint(
        "cost_budgets",
        old_columns=["organization_id", "name"],
        old_fallback_name="uq_cost_budgets_organization_name",
        new_name="uq_cost_budgets_name",
        new_columns=["name"],
    )
    tenant_tables = (
        "documents",
        "document_chunks",
        "audit_events",
        "approval_requests",
        "oauth_states",
        "connector_accounts",
        "policies",
        "background_jobs",
        "agent_workflows",
        "workspace_tasks",
        "mcp_tool_executions",
        "workflow_actions",
        "runtime_observations",
        "cost_budgets",
        "rag_evaluation_datasets",
        "rag_evaluation_cases",
        "rag_evaluation_runs",
        "rag_evaluation_results",
    )
    for table_name in reversed(tenant_tables):
        op.drop_index(f"idx_{table_name}_organization", table_name=table_name)
        op.drop_column(table_name, "organization_id")

    op.drop_table("oidc_authorization_states")
    op.drop_table("oidc_providers")
    op.drop_table("auth_sessions")
    op.drop_table("organization_invitations")
    op.drop_table("organization_memberships")
    for column_name in (
        "password_changed_at",
        "last_login_at",
        "token_version",
        "disabled",
        "display_name",
    ):
        op.drop_column("users", column_name)
    op.drop_table("organizations")


def _replace_unique_constraint(
    table_name: str,
    *,
    old_columns: list[str],
    old_fallback_name: str,
    new_name: str,
    new_columns: list[str],
) -> None:
    inspector = sa.inspect(op.get_bind())
    matching = next(
        (
            constraint
            for constraint in inspector.get_unique_constraints(table_name)
            if constraint.get("column_names") == old_columns
        ),
        None,
    )
    if matching is None:
        return
    constraint_name = matching.get("name") or old_fallback_name
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            recreate="always",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="unique")
            batch_op.create_unique_constraint(new_name, new_columns)
        return
    op.drop_constraint(constraint_name, table_name, type_="unique")
    op.create_unique_constraint(new_name, table_name, new_columns)
