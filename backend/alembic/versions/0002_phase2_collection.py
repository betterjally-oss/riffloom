"""Add phase 2 collection provider, result journal, and four-library fields."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_phase2_collection"
down_revision = "0001_phase1_core"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _add_index(name: str, table: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def upgrade() -> None:
    _add_column(
        "agent_tasks",
        sa.Column("result_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    collection_columns = [
        sa.Column("collection_kind", sa.String(length=32), nullable=False, server_default="single"),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("content_type", sa.String(length=40), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_external_id", sa.String(length=160), nullable=True),
        sa.Column("cover_url", sa.String(length=500), nullable=True),
        sa.Column("media_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("topics", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "provider",
            sa.String(length=80),
            nullable=False,
            server_default="deterministic-phase1",
        ),
        sa.Column(
            "raw_schema_version",
            sa.String(length=40),
            nullable=False,
            server_default="1.1-phase1",
        ),
        sa.Column(
            "last_collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            # SQLite cannot ALTER TABLE ADD COLUMN with CURRENT_TIMESTAMP.
            # A constant keeps the migration portable; existing rows are
            # immediately backfilled from their phase 1 updated_at value.
            server_default=sa.text("'1970-01-01 00:00:00'"),
        ),
    ]
    for column in collection_columns:
        _add_column("collection_records", column)
    op.execute(
        sa.text(
            "UPDATE collection_records "
            "SET last_collected_at = updated_at "
            "WHERE last_collected_at = '1970-01-01 00:00:00'"
        )
    )
    _add_index("ix_collection_records_collection_kind", "collection_records", ["collection_kind"])
    _add_index("ix_collection_records_external_id", "collection_records", ["external_id"])
    _add_index(
        "ix_collection_records_author_external_id", "collection_records", ["author_external_id"]
    )
    _add_index("ix_collection_records_provider", "collection_records", ["provider"])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "blogger_records" not in tables:
        op.create_table(
            "blogger_records",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("task_id", sa.String(length=64), sa.ForeignKey("agent_tasks.id"), nullable=False),
            sa.Column("platform", sa.String(length=64), nullable=False),
            sa.Column("external_id", sa.String(length=160), nullable=False),
            sa.Column("profile_url", sa.String(length=500), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("avatar_url", sa.String(length=500), nullable=True),
            sa.Column("bio", sa.Text(), nullable=False, server_default=""),
            sa.Column("followers", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("likes_and_collects", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("benchmark", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "workspace_id", "platform", "external_id", name="uq_blogger_platform_external"
            ),
        )
        op.create_index("ix_blogger_records_workspace_id", "blogger_records", ["workspace_id"])
        op.create_index("ix_blogger_records_task_id", "blogger_records", ["task_id"])
        op.create_index("ix_blogger_records_platform", "blogger_records", ["platform"])
        op.create_index("ix_blogger_records_external_id", "blogger_records", ["external_id"])
        op.create_index("ix_blogger_records_provider", "blogger_records", ["provider"])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "collection_task_items" not in tables:
        op.create_table(
            "collection_task_items",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("task_id", sa.String(length=64), sa.ForeignKey("agent_tasks.id"), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False),
            sa.Column("source_key", sa.String(length=320), nullable=False),
            sa.Column("entity_type", sa.String(length=40), nullable=True),
            sa.Column("entity_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("is_new", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("duplicate_of", sa.String(length=64), nullable=True),
            sa.Column("error", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "task_id", "attempt_no", "source_key", name="uq_collection_task_item_source"
            ),
        )
        op.create_index("ix_collection_task_items_task_id", "collection_task_items", ["task_id"])
        op.create_index(
            "ix_collection_task_items_workspace_id", "collection_task_items", ["workspace_id"]
        )
        op.create_index("ix_collection_task_items_entity_id", "collection_task_items", ["entity_id"])
        op.create_index("ix_collection_task_items_status", "collection_task_items", ["status"])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "provider_calls" not in tables:
        op.create_table(
            "provider_calls",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("task_id", sa.String(length=64), sa.ForeignKey("agent_tasks.id"), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("operation", sa.String(length=80), nullable=False),
            sa.Column("provider_request_id", sa.String(length=160), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("http_status", sa.Integer(), nullable=True),
            sa.Column("error_type", sa.String(length=100), nullable=True),
            sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rate_limit", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_provider_calls_task_id", "provider_calls", ["task_id"])
        op.create_index("ix_provider_calls_workspace_id", "provider_calls", ["workspace_id"])
        op.create_index("ix_provider_calls_provider", "provider_calls", ["provider"])
        op.create_index("ix_provider_calls_status", "provider_calls", ["status"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("provider_calls", "collection_task_items", "blogger_records"):
        if table in tables:
            op.drop_table(table)
    for index in (
        "ix_collection_records_provider",
        "ix_collection_records_author_external_id",
        "ix_collection_records_external_id",
        "ix_collection_records_collection_kind",
    ):
        if index in _indexes("collection_records"):
            op.drop_index(index, table_name="collection_records")
    for column in (
        "last_collected_at",
        "raw_schema_version",
        "provider",
        "metrics",
        "topics",
        "media_refs",
        "cover_url",
        "author_external_id",
        "published_at",
        "content_type",
        "external_id",
        "collection_kind",
    ):
        if column in _columns("collection_records"):
            op.drop_column("collection_records", column)
    if "result_summary" in _columns("agent_tasks"):
        op.drop_column("agent_tasks", "result_summary")
