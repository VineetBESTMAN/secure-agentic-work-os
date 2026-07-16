"""Add production connector lifecycle, sync, webhook, and action storage.

Revision ID: 20260715_0007
Revises: 20260715_0006
Create Date: 2026-07-15
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from alembic import op
import sqlalchemy as sa

revision: str = "20260715_0007"
down_revision: str | None = "20260715_0006"
branch_labels: str | None = None
depends_on: str | None = None

CONNECTOR_SCOPES = {
    "connectors:read",
    "connectors:manage",
    "connectors:sync",
    "connectors:act",
}


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


def _update_membership_scopes(*, remove: bool = False) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT membership_id, role, scopes_json FROM organization_memberships")
    ).mappings()
    additions = {
        "admin": CONNECTOR_SCOPES,
        "manager": {"connectors:read", "connectors:sync"},
        "employee": {"connectors:read"},
    }
    for row in rows:
        try:
            current = set(json.loads(row["scopes_json"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            current = set()
        if remove:
            current -= CONNECTOR_SCOPES
        else:
            current |= additions.get(row["role"], {"connectors:read"})
        connection.execute(
            sa.text(
                "UPDATE organization_memberships SET scopes_json = :scopes "
                "WHERE membership_id = :membership_id"
            ),
            {
                "membership_id": row["membership_id"],
                "scopes": json.dumps(sorted(current)),
            },
        )


def upgrade() -> None:
    timestamp = _timestamp_type()

    for column_name, column in (
        ("code_verifier_cipher", sa.Column("code_verifier_cipher", sa.Text())),
        ("expires_at", sa.Column("expires_at", timestamp)),
    ):
        if column_name not in _columns("oauth_states"):
            op.add_column("oauth_states", column)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE oauth_states "
                "SET expires_at = CURRENT_TIMESTAMP + INTERVAL '10 minutes' "
                "WHERE expires_at IS NULL"
            )
        )
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        op.execute(
            sa.text(
                "UPDATE oauth_states SET expires_at = :expires_at WHERE expires_at IS NULL"
            ).bindparams(expires_at=expires_at)
        )

    connector_columns = (
        ("external_account_id", sa.Column("external_account_id", sa.Text())),
        (
            "metadata_json",
            sa.Column(
                "metadata_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        ),
        ("token_type", sa.Column("token_type", sa.Text())),
        ("refresh_expires_at", sa.Column("refresh_expires_at", timestamp)),
        ("revoked_at", sa.Column("revoked_at", timestamp)),
        ("last_sync_at", sa.Column("last_sync_at", timestamp)),
        ("last_error", sa.Column("last_error", sa.Text())),
    )
    for column_name, column in connector_columns:
        if column_name not in _columns("connector_accounts"):
            op.add_column("connector_accounts", column)
    op.create_index(
        "idx_connector_accounts_organization_provider_status",
        "connector_accounts",
        ["organization_id", "provider", "status", "updated_at"],
    )

    op.create_table(
        "connector_sync_states",
        sa.Column("sync_state_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_id",
            sa.Text(),
            sa.ForeignKey("connector_accounts.connector_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("cursor_cipher", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("items_changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_started_at", timestamp),
        sa.Column("last_completed_at", timestamp),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("connector_id", "resource", name="uq_connector_sync_resource"),
    )
    op.create_index(
        "idx_connector_sync_states_organization_status",
        "connector_sync_states",
        ["organization_id", "status", "updated_at"],
    )

    op.create_table(
        "connector_sync_items",
        sa.Column("sync_item_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_id",
            sa.Text(),
            sa.ForeignKey("connector_accounts.connector_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_url", sa.Text()),
        sa.Column("external_updated_at", timestamp),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.document_id", ondelete="SET NULL"),
        ),
        sa.Column("deleted_at", timestamp),
        sa.Column("synced_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "connector_id",
            "resource",
            "external_id",
            name="uq_connector_sync_item_external",
        ),
    )
    op.create_index(
        "idx_connector_sync_items_organization_resource",
        "connector_sync_items",
        ["organization_id", "provider", "resource", "synced_at"],
    )

    op.create_table(
        "connector_webhook_subscriptions",
        sa.Column("subscription_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_id",
            sa.Text(),
            sa.ForeignKey("connector_accounts.connector_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("target", sa.Text()),
        sa.Column("remote_id", sa.Text()),
        sa.Column("secret_cipher", sa.Text(), nullable=False),
        sa.Column("registration_mode", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("expires_at", timestamp),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index(
        "idx_connector_webhooks_organization_status",
        "connector_webhook_subscriptions",
        ["organization_id", "provider", "status"],
    )

    op.create_table(
        "connector_webhook_deliveries",
        sa.Column("delivery_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.Text(),
            sa.ForeignKey("connector_webhook_subscriptions.subscription_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_delivery_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=_boolean_default(False)),
        sa.Column("processed_at", timestamp),
        sa.Column("received_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "subscription_id",
            "external_delivery_id",
            name="uq_connector_webhook_delivery_external",
        ),
    )
    op.create_index(
        "idx_connector_webhook_deliveries_organization_received",
        "connector_webhook_deliveries",
        ["organization_id", "received_at"],
    )

    op.create_table(
        "connector_action_receipts",
        sa.Column("receipt_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Text(),
            sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_id",
            sa.Text(),
            sa.ForeignKey("connector_accounts.connector_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("result_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "organization_id",
            "execution_id",
            name="uq_connector_action_execution",
        ),
    )
    op.create_index(
        "idx_connector_action_receipts_organization_provider",
        "connector_action_receipts",
        ["organization_id", "provider", "created_at"],
    )

    _update_membership_scopes()


def downgrade() -> None:
    _update_membership_scopes(remove=True)

    for table_name in (
        "connector_action_receipts",
        "connector_webhook_deliveries",
        "connector_webhook_subscriptions",
        "connector_sync_items",
        "connector_sync_states",
    ):
        op.drop_table(table_name)

    op.drop_index(
        "idx_connector_accounts_organization_provider_status",
        table_name="connector_accounts",
    )
    for column_name in (
        "last_error",
        "last_sync_at",
        "revoked_at",
        "refresh_expires_at",
        "token_type",
        "metadata_json",
        "external_account_id",
    ):
        if column_name in _columns("connector_accounts"):
            op.drop_column("connector_accounts", column_name)

    for column_name in ("expires_at", "code_verifier_cipher"):
        if column_name in _columns("oauth_states"):
            op.drop_column("oauth_states", column_name)
