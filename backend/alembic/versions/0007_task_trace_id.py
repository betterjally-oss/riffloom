"""Add durable trace correlation to agent tasks."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_task_trace_id"
down_revision = "0006_pilot_invite_auth"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "trace_id" not in _columns("agent_tasks"):
        op.add_column("agent_tasks", sa.Column("trace_id", sa.String(96), nullable=True))
        op.execute(
            "UPDATE agent_tasks "
            "SET trace_id = 'trc_' || substr(replace(id, 'task_', ''), 1, 24) "
            "WHERE trace_id IS NULL"
        )
        op.create_index("ix_agent_tasks_trace_id", "agent_tasks", ["trace_id"])


def downgrade() -> None:
    if "trace_id" in _columns("agent_tasks"):
        with op.batch_alter_table("agent_tasks") as batch:
            batch.drop_index("ix_agent_tasks_trace_id")
            batch.drop_column("trace_id")
