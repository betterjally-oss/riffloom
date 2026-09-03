"""Store one encrypted Feishu OAuth grant per Riffloom user."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_feishu_user_oauth"
down_revision = "0012_pilot_member_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "feishu_oauth_credentials" not in inspector.get_table_names():
        op.create_table(
            "feishu_oauth_credentials",
            sa.Column(
                "connection_id",
                sa.String(length=64),
                sa.ForeignKey("feishu_connections.id"),
                primary_key=True,
            ),
            sa.Column("access_token_encrypted", sa.Text(), nullable=False),
            sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
            sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    def unique_names(table: str) -> set[str]:
        return {
            str(item["name"])
            for item in sa.inspect(op.get_bind()).get_unique_constraints(table)
            if item.get("name")
        }

    names = unique_names("feishu_connections")
    if "uq_feishu_connection_workspace_user" not in names:
        with op.batch_alter_table("feishu_connections") as batch:
            if "uq_feishu_connection_workspace" in names:
                batch.drop_constraint("uq_feishu_connection_workspace", type_="unique")
            batch.create_unique_constraint(
                "uq_feishu_connection_workspace_user", ["workspace_id", "created_by"]
            )
    names = unique_names("feishu_sync_bindings")
    if "uq_feishu_binding_user_scope" not in names:
        with op.batch_alter_table("feishu_sync_bindings") as batch:
            if "uq_feishu_binding_scope" in names:
                batch.drop_constraint("uq_feishu_binding_scope", type_="unique")
            batch.create_unique_constraint(
                "uq_feishu_binding_user_scope",
                ["workspace_id", "created_by", "scope_key"],
            )
    names = unique_names("feishu_sync_runs")
    if "uq_feishu_run_user_idempotency" not in names:
        with op.batch_alter_table("feishu_sync_runs") as batch:
            if "uq_feishu_run_idempotency" in names:
                batch.drop_constraint("uq_feishu_run_idempotency", type_="unique")
            batch.create_unique_constraint(
                "uq_feishu_run_user_idempotency",
                ["workspace_id", "created_by", "idempotency_key"],
            )

    # Shared application credentials must never remain usable after this migration.
    op.execute(
        sa.text(
            "UPDATE feishu_sync_bindings SET status = 'connection_invalid' "
            "WHERE connection_id IN (SELECT id FROM feishu_connections WHERE provider = 'openapi-v1')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE feishu_connections SET status = 'disconnected', "
            "credential_ref = 'disconnected://oauth-required' WHERE provider = 'openapi-v1'"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("feishu_sync_runs") as batch:
        batch.drop_constraint("uq_feishu_run_user_idempotency", type_="unique")
        batch.create_unique_constraint(
            "uq_feishu_run_idempotency", ["workspace_id", "idempotency_key"]
        )
    with op.batch_alter_table("feishu_sync_bindings") as batch:
        batch.drop_constraint("uq_feishu_binding_user_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_feishu_binding_scope", ["workspace_id", "scope_key"]
        )
    with op.batch_alter_table("feishu_connections") as batch:
        batch.drop_constraint("uq_feishu_connection_workspace_user", type_="unique")
        batch.create_unique_constraint(
            "uq_feishu_connection_workspace", ["workspace_id"]
        )
    op.drop_table("feishu_oauth_credentials")
