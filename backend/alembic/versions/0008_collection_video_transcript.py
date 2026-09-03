"""Add real collection provenance and mandatory video transcript fields."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_collection_video_transcript"
down_revision = "0007_task_trace_id"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    columns = _columns("collection_records")
    additions = [
        sa.Column("actual_upstream", sa.String(120), nullable=True),
        sa.Column("video_transcript", sa.Text(), nullable=True),
        sa.Column("video_transcript_corrected", sa.Text(), nullable=True),
        sa.Column(
            "video_transcript_status",
            sa.String(40),
            nullable=False,
            server_default="not_applicable",
        ),
        sa.Column("video_transcript_source", sa.String(80), nullable=True),
        sa.Column("video_transcript_confidence", sa.Float(), nullable=True),
        sa.Column(
            "video_transcript_segments",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column("collection_records", column)
    if "ix_collection_records_video_transcript_status" not in _indexes(
        "collection_records"
    ):
        op.create_index(
            "ix_collection_records_video_transcript_status",
            "collection_records",
            ["video_transcript_status"],
        )
    media_columns = _columns("media_assets")
    if "expires_at" not in media_columns:
        op.add_column(
            "media_assets", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "purged_at" not in media_columns:
        op.add_column(
            "media_assets", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "ix_media_assets_expires_at" not in _indexes("media_assets"):
        op.create_index("ix_media_assets_expires_at", "media_assets", ["expires_at"])


def downgrade() -> None:
    if "ix_media_assets_expires_at" in _indexes("media_assets"):
        op.drop_index("ix_media_assets_expires_at", table_name="media_assets")
    for column in ("purged_at", "expires_at"):
        if column in _columns("media_assets"):
            op.drop_column("media_assets", column)
    if "ix_collection_records_video_transcript_status" in _indexes(
        "collection_records"
    ):
        op.drop_index(
            "ix_collection_records_video_transcript_status",
            table_name="collection_records",
        )
    for column in (
        "video_transcript_segments",
        "video_transcript_confidence",
        "video_transcript_source",
        "video_transcript_status",
        "video_transcript_corrected",
        "video_transcript",
        "actual_upstream",
    ):
        if column in _columns("collection_records"):
            op.drop_column("collection_records", column)
