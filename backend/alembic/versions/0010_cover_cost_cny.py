"""Track cover generation cost in the provider billing currency."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_cover_cost_cny"
down_revision = "0009_collection_metadata"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("cover_assets")
    }


def upgrade() -> None:
    if "estimated_cost_cny" not in _columns():
        op.add_column(
            "cover_assets",
            sa.Column(
                "estimated_cost_cny", sa.Float(), nullable=False, server_default="0"
            ),
        )


def downgrade() -> None:
    if "estimated_cost_cny" in _columns():
        with op.batch_alter_table("cover_assets") as batch:
            batch.drop_column("estimated_cost_cny")
