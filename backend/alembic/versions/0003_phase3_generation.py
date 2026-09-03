"""Add phase 3 immutable generation versions, RAG index, and model traces."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0003_phase3_generation"
down_revision = "0002_phase2_collection"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "current_version_id" not in _columns("breakdown_records"):
        op.add_column(
            "breakdown_records", sa.Column("current_version_id", sa.String(64), nullable=True)
        )

    # SQLite needs batch mode to change nullability while retaining existing rows.
    source_column = next(
        (
            column
            for column in sa.inspect(op.get_bind()).get_columns("breakdown_records")
            if column["name"] == "source_record_id"
        ),
        None,
    )
    if source_column and not source_column["nullable"]:
        with op.batch_alter_table("breakdown_records") as batch:
            batch.alter_column(
                "source_record_id", existing_type=sa.String(64), nullable=True
            )

    if "breakdown_versions" not in _tables():
        op.create_table(
            "breakdown_versions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("record_id", sa.String(64), sa.ForeignKey("breakdown_records.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("source_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("source_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("observed_facts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("hook", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("structure", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("emotion", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("visual", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("interaction", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("reusable_methods", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("risks", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("skill_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("model_call_id", sa.String(64), nullable=True),
            sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("record_id", "version", name="uq_breakdown_version"),
        )
        op.create_index("ix_breakdown_versions_workspace_id", "breakdown_versions", ["workspace_id"])
        op.create_index("ix_breakdown_versions_record_id", "breakdown_versions", ["record_id"])
        op.create_index("ix_breakdown_versions_model_call_id", "breakdown_versions", ["model_call_id"])

    creation_additions = [
        sa.Column("title_candidates", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("risk_notes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("model_call_id", sa.String(64), nullable=True),
        sa.Column("similarity_report", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("input_snapshot_hash", sa.String(64), nullable=False, server_default=""),
    ]
    for column in creation_additions:
        if column.name not in _columns("creation_versions"):
            op.add_column("creation_versions", column)
    if "ix_creation_versions_model_call_id" not in {
        item["name"] for item in sa.inspect(op.get_bind()).get_indexes("creation_versions")
    }:
        op.create_index(
            "ix_creation_versions_model_call_id", "creation_versions", ["model_call_id"]
        )

    if "knowledge_chunks" not in _tables():
        op.create_table(
            "knowledge_chunks",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("library_type", sa.String(32), nullable=False),
            sa.Column("record_id", sa.String(64), nullable=False),
            sa.Column("field_name", sa.String(64), nullable=False),
            sa.Column("record_version", sa.String(40), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("text_hash", sa.String(64), nullable=False),
            sa.Column("embedding", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("embedding_model", sa.String(100), nullable=False),
            sa.Column("embedding_dimensions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "workspace_id", "library_type", "record_id", "field_name", "record_version", "text_hash",
                name="uq_knowledge_chunk_source",
            ),
        )
        op.create_index("ix_knowledge_chunks_workspace_id", "knowledge_chunks", ["workspace_id"])
        op.create_index("ix_knowledge_chunks_library_type", "knowledge_chunks", ["library_type"])
        op.create_index("ix_knowledge_chunks_record_id", "knowledge_chunks", ["record_id"])
        op.create_index("ix_knowledge_chunks_text_hash", "knowledge_chunks", ["text_hash"])
        op.create_index(
            "ix_knowledge_chunk_lookup", "knowledge_chunks", ["workspace_id", "library_type", "record_id"]
        )

    if "model_calls" not in _tables():
        op.create_table(
            "model_calls",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("task_id", sa.String(64), sa.ForeignKey("agent_tasks.id"), nullable=False),
            sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("model", sa.String(120), nullable=False),
            sa.Column("operation", sa.String(80), nullable=False),
            sa.Column("contract_version", sa.String(40), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("repair_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("provider_request_id", sa.String(160), nullable=True),
            sa.Column("error_code", sa.String(100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for column in ("task_id", "workspace_id", "provider", "model", "operation", "status"):
            op.create_index(f"ix_model_calls_{column}", "model_calls", [column])

    # Preserve phase 1 breakdowns as immutable v1 records.
    connection = op.get_bind()
    existing = connection.execute(sa.text("SELECT COUNT(*) FROM breakdown_versions")).scalar() or 0
    if existing == 0:
        rows = connection.execute(
            sa.text(
                "SELECT id, workspace_id, source_record_id, hook, structure, emotion, visual, "
                "reusable_methods, skill_version, created_at, updated_at FROM breakdown_records"
            )
        ).mappings()
        import json
        import uuid

        for row in rows:
            version_id = f"bver_{uuid.uuid4().hex[:20]}"
            connection.execute(
                sa.text(
                    "INSERT INTO breakdown_versions "
                    "(id, workspace_id, record_id, version, source_refs, source_snapshot, observed_facts, "
                    "hook, structure, emotion, visual, interaction, reusable_methods, risks, skill_snapshot, "
                    "model_call_id, created_by, created_at, updated_at) "
                    "VALUES (:id, :workspace_id, :record_id, 1, :source_refs, '{}', '[]', :hook, :structure, "
                    ":emotion, :visual, '[]', :methods, :risks, :skill, NULL, 'user_demo', :created_at, :updated_at)"
                ),
                {
                    "id": version_id,
                    "workspace_id": row["workspace_id"],
                    "record_id": row["id"],
                    "source_refs": json.dumps(
                        [{"type": "collection", "id": row["source_record_id"]}]
                        if row["source_record_id"] else []
                    ),
                    "hook": json.dumps({"type": "legacy", "expression": row["hook"], "why_effective": "阶段 1 迁移"}),
                    "structure": row["structure"],
                    "emotion": json.dumps({"target": row["emotion"], "turn": "", "action_driver": ""}),
                    "visual": json.dumps({"observed": row["visual"], "limitations": ["阶段 1 合成结果"]}),
                    "methods": row["reusable_methods"],
                    "risks": json.dumps(["阶段 1 确定性合成结果，仅用于链路验证"]),
                    "skill": json.dumps({"id": "viral_breakdown", "version": row["skill_version"]}),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
            )
            connection.execute(
                sa.text("UPDATE breakdown_records SET current_version_id = :version_id WHERE id = :id"),
                {"version_id": version_id, "id": row["id"]},
            )


def downgrade() -> None:
    for table in ("model_calls", "knowledge_chunks", "breakdown_versions"):
        if table in _tables():
            op.drop_table(table)
    for column in (
        "input_snapshot_hash", "similarity_report", "model_call_id", "source_refs",
        "risk_notes", "summary", "title_candidates",
    ):
        if column in _columns("creation_versions"):
            op.drop_column("creation_versions", column)
    if "current_version_id" in _columns("breakdown_records"):
        op.drop_column("breakdown_records", "current_version_id")
