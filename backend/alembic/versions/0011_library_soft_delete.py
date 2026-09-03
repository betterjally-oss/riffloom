"""Add recoverable deletion to library records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_library_soft_delete"
down_revision = "0010_cover_cost_cny"
branch_labels = None
depends_on = None


TABLES = (
    "collection_records",
    "blogger_records",
    "breakdown_records",
    "creation_records",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        if "deleted_at" not in {column["name"] for column in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in reversed(TABLES):
        if "deleted_at" in {column["name"] for column in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("deleted_at")
