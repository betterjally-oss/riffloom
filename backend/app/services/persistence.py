from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url

from app.services.object_store import ByteStore


RestorePolicy = Literal["disabled", "if_available", "required"]


@dataclass(frozen=True)
class BackupResult:
    slot: str
    byte_size: int
    checksum: str


class SQLiteBackupManager:
    """Creates verified SQLite snapshots and rotates between two durable slots."""

    manifest_version = 1
    slots = ("slot-a.sqlite3", "slot-b.sqlite3")

    def __init__(
        self,
        *,
        database_url: str,
        store: ByteStore,
        key_prefix: str,
        backup_enabled: bool,
        restore_policy: RestorePolicy,
    ):
        self.database_path = self._sqlite_path(database_url)
        self.store = store
        self.key_prefix = key_prefix.strip("/")
        self.backup_enabled = backup_enabled
        self.restore_policy = restore_policy
        self._lock = threading.Lock()

        if restore_policy not in {"disabled", "if_available", "required"}:
            raise RuntimeError(
                "RIFFLOOM_DATABASE_RESTORE_POLICY 仅支持 disabled、if_available 或 required"
            )
        if (backup_enabled or restore_policy != "disabled") and self.database_path is None:
            raise RuntimeError("数据库备份恢复当前仅支持文件型 SQLite")

    @staticmethod
    def _sqlite_path(database_url: str) -> Path | None:
        url = make_url(database_url)
        if not url.drivername.startswith("sqlite") or not url.database:
            return None
        if url.database == ":memory:":
            return None
        return Path(url.database).expanduser().resolve()

    def _key(self, name: str) -> str:
        return f"{self.key_prefix}/{name}" if self.key_prefix else name

    @property
    def manifest_key(self) -> str:
        return self._key("latest.json")

    def _read_manifest(self) -> dict[str, object] | None:
        try:
            raw = self.store.get_bytes(self.manifest_key)
        except FileNotFoundError:
            return None
        try:
            manifest = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("数据库备份清单损坏，已拒绝恢复") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("version") != self.manifest_version
            or manifest.get("slot") not in self.slots
            or not isinstance(manifest.get("byte_size"), int)
            or not isinstance(manifest.get("sha256"), str)
        ):
            raise RuntimeError("数据库备份清单格式无效，已拒绝恢复")
        return manifest

    @staticmethod
    def _verify_database(path: Path) -> None:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise RuntimeError("SQLite 快照无法打开，已拒绝使用") from exc
        if row != ("ok",):
            raise RuntimeError("SQLite 快照完整性检查未通过，已拒绝使用")

    @staticmethod
    def _write_temp_file(directory: Path, content: bytes) -> Path:
        descriptor, name = tempfile.mkstemp(
            dir=directory,
            prefix=".riffloom-sqlite-",
            suffix=".sqlite3",
        )
        path = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def restore_before_startup(self) -> str:
        path = self.database_path
        if path is None:
            return "disabled"
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.restore_policy == "disabled":
            return "disabled"
        if path.is_file() and path.stat().st_size > 0:
            return "local_present"

        with self._lock:
            manifest = self._read_manifest()
            if manifest is None:
                if self.restore_policy == "required":
                    raise RuntimeError("本地数据库缺失且没有可恢复备份，已停止启动")
                return "not_available"

            slot = str(manifest["slot"])
            try:
                content = self.store.get_bytes(self._key(slot))
            except FileNotFoundError as exc:
                raise RuntimeError("数据库备份清单指向的快照不存在，已停止启动") from exc
            checksum = hashlib.sha256(content).hexdigest()
            if len(content) != manifest["byte_size"] or checksum != manifest["sha256"]:
                raise RuntimeError("数据库备份校验失败，已停止启动")

            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._write_temp_file(path.parent, content)
            try:
                self._verify_database(temporary)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return "restored"

    def backup_now(self) -> BackupResult | None:
        path = self.database_path
        if not self.backup_enabled:
            return None
        if path is None or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("本地 SQLite 数据库不存在，无法备份")

        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                dir=path.parent,
                prefix=".riffloom-backup-",
                suffix=".sqlite3",
            )
            os.close(descriptor)
            snapshot = Path(name)
            try:
                source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
                destination = sqlite3.connect(snapshot)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
                self._verify_database(snapshot)
                content = snapshot.read_bytes()
            finally:
                snapshot.unlink(missing_ok=True)

            current = self._read_manifest()
            current_slot = current.get("slot") if current else None
            slot = self.slots[1] if current_slot == self.slots[0] else self.slots[0]
            checksum = hashlib.sha256(content).hexdigest()
            self.store.put_bytes(self._key(slot), content)
            manifest = {
                "version": self.manifest_version,
                "slot": slot,
                "byte_size": len(content),
                "sha256": checksum,
            }
            self.store.put_bytes(
                self.manifest_key,
                json.dumps(manifest, ensure_ascii=True, sort_keys=True).encode("utf-8"),
            )
            return BackupResult(
                slot=slot,
                byte_size=len(content),
                checksum=checksum,
            )
