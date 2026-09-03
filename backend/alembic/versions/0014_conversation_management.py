"""Add rename and soft-delete state to conversations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_conversation_management"
down_revision = "0013_feishu_user_oauth"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns("conversations")
    }


def upgrade() -> None:
    columns = _columns()
    if "custom_title" not in columns:
        op.add_column(
            "conversations", sa.Column("custom_title", sa.String(120), nullable=True)
        )
    if "deleted_at" not in columns:
        op.add_column(
            "conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("conversations") as batch:
        if "deleted_at" in columns:
            batch.drop_column("deleted_at")
        if "custom_title" in columns:
            batch.drop_column("custom_title")
