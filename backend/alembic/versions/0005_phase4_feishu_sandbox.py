"""Add phase 4C Feishu sandbox connection and sync journal."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_phase4_feishu_sandbox"
down_revision = "0004_phase4_cover_assets"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "feishu_connections" not in tables:
        op.create_table(
            "feishu_connections",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("tenant_key", sa.String(160), nullable=False),
            sa.Column("tenant_name", sa.String(200), nullable=False),
            sa.Column("auth_type", sa.String(40), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("credential_ref", sa.String(300), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workspace_id", name="uq_feishu_connection_workspace"),
        )
        for column in ("workspace_id", "status", "created_by"):
            op.create_index(f"ix_feishu_connections_{column}", "feishu_connections", [column])

    if "feishu_sync_bindings" not in tables:
        op.create_table(
            "feishu_sync_bindings",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("connection_id", sa.String(64), sa.ForeignKey("feishu_connections.id"), nullable=False),
            sa.Column("scope_key", sa.String(64), nullable=False),
            sa.Column("target_base_id", sa.String(160), nullable=False),
            sa.Column("target_table_id", sa.String(160), nullable=False),
            sa.Column("target_table_name", sa.String(200), nullable=False),
            sa.Column("field_mapping", sa.JSON(), nullable=False),
            sa.Column("strategy", sa.String(32), nullable=False),
            sa.Column("cursor", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workspace_id", "scope_key", name="uq_feishu_binding_scope"),
        )
        for column in ("workspace_id", "connection_id", "scope_key", "status", "created_by"):
            op.create_index(f"ix_feishu_sync_bindings_{column}", "feishu_sync_bindings", [column])

    if "feishu_sync_runs" not in tables:
        op.create_table(
            "feishu_sync_runs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("binding_id", sa.String(64), sa.ForeignKey("feishu_sync_bindings.id"), nullable=False),
            sa.Column("task_id", sa.String(64), sa.ForeignKey("agent_tasks.id"), nullable=False, unique=True),
            sa.Column("parent_run_id", sa.String(64), sa.ForeignKey("feishu_sync_runs.id"), nullable=True),
            sa.Column("mode", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cursor_before", sa.JSON(), nullable=False),
            sa.Column("cursor_after", sa.JSON(), nullable=False),
            sa.Column("error", sa.JSON(), nullable=True),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_feishu_run_idempotency"),
        )
        for column in (
            "workspace_id", "binding_id", "task_id", "parent_run_id", "status", "created_by",
        ):
            op.create_index(f"ix_feishu_sync_runs_{column}", "feishu_sync_runs", [column])
        op.create_index(
            "ix_feishu_run_binding_created", "feishu_sync_runs", ["binding_id", "created_at"]
        )

    if "feishu_record_links" not in tables:
        op.create_table(
            "feishu_record_links",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("binding_id", sa.String(64), sa.ForeignKey("feishu_sync_bindings.id"), nullable=False),
            sa.Column("source_record_id", sa.String(64), nullable=False),
            sa.Column("remote_record_id", sa.String(160), nullable=False),
            sa.Column("source_version", sa.String(80), nullable=False),
            sa.Column("remote_fields", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("binding_id", "source_record_id", name="uq_feishu_link_source"),
        )
        for column in ("workspace_id", "binding_id", "source_record_id", "status"):
            op.create_index(f"ix_feishu_record_links_{column}", "feishu_record_links", [column])

    if "feishu_sync_items" not in tables:
        op.create_table(
            "feishu_sync_items",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("run_id", sa.String(64), sa.ForeignKey("feishu_sync_runs.id"), nullable=False),
            sa.Column("source_record_id", sa.String(64), nullable=False),
            sa.Column("source_version", sa.String(80), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("remote_record_id", sa.String(160), nullable=True),
            sa.Column("error", sa.JSON(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("run_id", "source_record_id", name="uq_feishu_item_source"),
        )
        for column in ("workspace_id", "run_id", "source_record_id", "status"):
            op.create_index(f"ix_feishu_sync_items_{column}", "feishu_sync_items", [column])


def downgrade() -> None:
    tables = _tables()
    for table in (
        "feishu_sync_items",
        "feishu_record_links",
        "feishu_sync_runs",
        "feishu_sync_bindings",
        "feishu_connections",
    ):
        if table in tables:
            op.drop_table(table)
