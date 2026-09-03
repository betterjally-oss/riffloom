"""Add collection benchmark and category tags."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_collection_metadata"
down_revision = "0008_collection_video_transcript"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("collection_records")
    }


def upgrade() -> None:
    columns = _columns()
    if "benchmark" not in columns:
        op.add_column(
            "collection_records",
            sa.Column("benchmark", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "category_tags" not in columns:
        op.add_column(
            "collection_records",
            sa.Column("category_tags", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("collection_records") as batch:
        if "category_tags" in columns:
            batch.drop_column("category_tags")
        if "benchmark" in columns:
            batch.drop_column("benchmark")
