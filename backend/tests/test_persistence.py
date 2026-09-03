from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.asset_store import AssetStore
from app.services.object_store import FilesystemByteStore
from app.services.persistence import SQLiteBackupManager


def _write_marker(database_path, value: str) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker(value) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _read_markers(database_path) -> list[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute("SELECT value FROM marker ORDER BY rowid").fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def test_sqlite_backup_rotates_and_restores_verified_latest_snapshot(tmp_path):
    database_path = tmp_path / "runtime" / "riffloom.db"
    database_path.parent.mkdir()
    durable_root = tmp_path / "durable"
    store = FilesystemByteStore(durable_root)
    manager = SQLiteBackupManager(
        database_url=f"sqlite:///{database_path}",
        store=store,
        key_prefix="riffloom/database",
        backup_enabled=True,
        restore_policy="if_available",
    )

    _write_marker(database_path, "first")
    first = manager.backup_now()
    assert first is not None
    assert first.slot == "slot-a.sqlite3"

    _write_marker(database_path, "second")
    second = manager.backup_now()
    assert second is not None
    assert second.slot == "slot-b.sqlite3"
    assert first.checksum != second.checksum

    manifest = json.loads(
        (durable_root / "riffloom/database/latest.json").read_text()
    )
    assert manifest["slot"] == "slot-b.sqlite3"
    assert manifest["sha256"] == second.checksum

    original = database_path.with_suffix(".before-restore.db")
    database_path.rename(original)
    restore_manager = SQLiteBackupManager(
        database_url=f"sqlite:///{database_path}",
        store=store,
        key_prefix="riffloom/database",
        backup_enabled=False,
        restore_policy="required",
    )
    assert restore_manager.restore_before_startup() == "restored"
    assert _read_markers(database_path) == ["first", "second"]
    assert original.is_file()


def test_restore_fails_closed_when_manifest_or_snapshot_is_missing(tmp_path):
    store = FilesystemByteStore(tmp_path / "durable")
    database_path = tmp_path / "missing.db"
    manager = SQLiteBackupManager(
        database_url=f"sqlite:///{database_path}",
        store=store,
        key_prefix="database",
        backup_enabled=False,
        restore_policy="required",
    )
    with pytest.raises(RuntimeError, match="没有可恢复备份"):
        manager.restore_before_startup()

    store.put_bytes(
        "database/latest.json",
        json.dumps(
            {
                "version": 1,
                "slot": "slot-a.sqlite3",
                "byte_size": 1,
                "sha256": "0" * 64,
            }
        ).encode(),
    )
    with pytest.raises(RuntimeError, match="快照不存在"):
        manager.restore_before_startup()


def test_asset_store_uses_safe_prefixed_keys_on_existing_mount(tmp_path):
    mount = tmp_path / "tos-mount"
    mount.mkdir()
    byte_store = FilesystemByteStore(mount, create_root=False)
    asset_store = AssetStore(byte_store, key_prefix="riffloom/assets")

    key = asset_store.put(
        workspace_id="workspace-one",
        asset_id="asset_123",
        suffix=".png",
        content=b"approved-image",
    )
    assert key.endswith("/asset_123.png")
    assert asset_store.read(key) == b"approved-image"
    assert (mount / "riffloom/assets" / key).read_bytes() == b"approved-image"

    with pytest.raises(ValueError, match="key 无效"):
        asset_store.read("../outside")
    with pytest.raises(RuntimeError, match="挂载目录不存在"):
        FilesystemByteStore(tmp_path / "not-mounted", create_root=False)


def test_app_restores_before_migration_only_schema_check(tmp_path):
    database_path = tmp_path / "runtime" / "pilot.db"
    backup_dir = tmp_path / "backups"
    common = dict(
        database_url=f"sqlite:///{database_path}",
        database_backup_dir=str(backup_dir),
        asset_storage_dir=str(tmp_path / "uploads"),
        worker_step_delay=0.01,
        worker_threads=1,
        log_level="WARNING",
    )
    with TestClient(
        create_app(
            Settings(
                **common,
                database_backup_enabled=True,
                database_backup_interval_seconds=60,
                database_restore_policy="if_available",
            )
        )
    ) as client:
        assert client.get("/api/v1/health").status_code == 200

    original = database_path.with_suffix(".before-restore.db")
    database_path.rename(original)
    with TestClient(
        create_app(
            Settings(
                **common,
                database_schema_mode="migrations_only",
                database_restore_policy="required",
            )
        )
    ) as client:
        assert client.get("/api/v1/session", headers={
            "X-Riffloom-User": "user_demo",
            "X-Riffloom-Workspace": "ws_demo",
        }).status_code == 200
    assert original.is_file()


def test_tos_backup_requires_explicit_single_instance_acknowledgement(tmp_path):
    mount = tmp_path / "tos-mount"
    mount.mkdir()
    with pytest.raises(RuntimeError, match="SINGLE_INSTANCE_ACKNOWLEDGED"):
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'pilot.db'}",
                object_storage_provider="tos_mount",
                object_storage_mount_dir=str(mount),
                database_backup_enabled=True,
            )
        )
