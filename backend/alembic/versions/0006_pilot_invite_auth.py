"""Add Pilot invitation codes and revocable bearer sessions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_pilot_invite_auth"
down_revision = "0005_phase4_feishu_sandbox"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "invitation_codes" not in tables:
        op.create_table(
            "invitation_codes",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(64),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("code_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("max_uses", sa.Integer(), nullable=False),
            sa.Column("use_count", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_by",
                sa.String(64),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("code_hash", name="uq_invitation_code_hash"),
        )
        for column in (
            "workspace_id",
            "user_id",
            "status",
            "expires_at",
            "created_by",
        ):
            op.create_index(
                f"ix_invitation_codes_{column}", "invitation_codes", [column]
            )
        op.create_index(
            "ix_invitation_workspace_status",
            "invitation_codes",
            ["workspace_id", "status"],
        )

    tables = _tables()
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(64),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column(
                "invitation_id",
                sa.String(64),
                sa.ForeignKey("invitation_codes.id"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "token_hash", name="uq_auth_session_token_hash"
            ),
        )
        for column in (
            "workspace_id",
            "user_id",
            "invitation_id",
            "status",
            "expires_at",
        ):
            op.create_index(f"ix_auth_sessions_{column}", "auth_sessions", [column])
        op.create_index(
            "ix_auth_session_workspace_user",
            "auth_sessions",
            ["workspace_id", "user_id"],
        )


def downgrade() -> None:
    tables = _tables()
    if "auth_sessions" in tables:
        op.drop_table("auth_sessions")
    if "invitation_codes" in tables:
        op.drop_table("invitation_codes")
