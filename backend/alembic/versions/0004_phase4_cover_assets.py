"""Add phase 4B local media and cover assets."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_phase4_cover_assets"
down_revision = "0003_phase3_generation"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "media_assets" not in _tables():
        op.create_table(
            "media_assets",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
            sa.Column("original_name", sa.String(240), nullable=False),
            sa.Column("mime_type", sa.String(80), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("rights_status", sa.String(32), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "workspace_id", "content_hash", "role", name="uq_media_asset_content_role"
            ),
        )
        for column in ("workspace_id", "role", "rights_status", "content_hash", "created_by"):
            op.create_index(f"ix_media_assets_{column}", "media_assets", [column])

    if "cover_assets" not in _tables():
        op.create_table(
            "cover_assets",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("task_id", sa.String(64), sa.ForeignKey("agent_tasks.id"), nullable=False),
            sa.Column("media_asset_id", sa.String(64), sa.ForeignKey("media_assets.id"), nullable=False),
            sa.Column("creation_id", sa.String(64), sa.ForeignKey("creation_records.id"), nullable=True),
            sa.Column("creation_version_id", sa.String(64), sa.ForeignKey("creation_versions.id"), nullable=True),
            sa.Column("parent_asset_id", sa.String(64), sa.ForeignKey("cover_assets.id"), nullable=True),
            sa.Column("revision_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("variant_no", sa.Integer(), nullable=False),
            sa.Column("prompt_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("model", sa.String(120), nullable=False),
            sa.Column("provider_request_id", sa.String(160), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("result_ref", sa.String(500), nullable=False),
            sa.Column("mime_type", sa.String(80), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("ratio", sa.String(20), nullable=False),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("task_id", "variant_no", name="uq_cover_task_variant"),
        )
        for column in (
            "workspace_id", "task_id", "media_asset_id", "creation_id",
            "creation_version_id", "parent_asset_id", "provider", "status", "created_by",
        ):
            op.create_index(f"ix_cover_assets_{column}", "cover_assets", [column])
        op.create_index(
            "ix_cover_workspace_created", "cover_assets", ["workspace_id", "created_at"]
        )


def downgrade() -> None:
    tables = _tables()
    if "cover_assets" in tables:
        op.drop_table("cover_assets")
    if "media_assets" in tables:
        op.drop_table("media_assets")
