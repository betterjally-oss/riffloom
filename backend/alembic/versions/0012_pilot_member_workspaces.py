"""Add explicit parent-managed Pilot member workspaces."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012_pilot_member_workspaces"
down_revision = "0011_library_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "pilot_member_workspaces" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "pilot_member_workspaces",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "member_user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            name="uq_pilot_member_workspace_workspace",
        ),
        sa.UniqueConstraint(
            "parent_workspace_id",
            "member_user_id",
            name="uq_pilot_member_workspace_parent_user",
        ),
    )
    op.create_index(
        "ix_pilot_member_workspaces_workspace_id",
        "pilot_member_workspaces",
        ["workspace_id"],
    )
    op.create_index(
        "ix_pilot_member_workspaces_parent_workspace_id",
        "pilot_member_workspaces",
        ["parent_workspace_id"],
    )
    op.create_index(
        "ix_pilot_member_workspaces_member_user_id",
        "pilot_member_workspaces",
        ["member_user_id"],
    )


def downgrade() -> None:
    if "pilot_member_workspaces" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("pilot_member_workspaces")
