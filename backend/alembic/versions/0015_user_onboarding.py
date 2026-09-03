"""Track first-run onboarding completion per user."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0015_user_onboarding"
down_revision = "0014_conversation_management"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }


def upgrade() -> None:
    if "onboarding_completed_at" in _columns():
        return
    op.add_column(
        "users",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing members already know the product; only users created after this
    # rollout should receive the automatic first-run tour.
    op.execute(
        sa.text(
            "UPDATE users SET onboarding_completed_at = CURRENT_TIMESTAMP "
            "WHERE onboarding_completed_at IS NULL"
        )
    )


def downgrade() -> None:
    if "onboarding_completed_at" in _columns():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("onboarding_completed_at")
