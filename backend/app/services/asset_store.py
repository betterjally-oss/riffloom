from __future__ import annotations

import hashlib

from app.services.object_store import ByteStore


class AssetStore:
    def __init__(self, store: ByteStore, *, key_prefix: str = ""):
        self.store = store
        self.key_prefix = key_prefix.strip("/")

    def _key(self, storage_key: str) -> str:
        return f"{self.key_prefix}/{storage_key}" if self.key_prefix else storage_key

    @staticmethod
    def _storage_key(workspace_id: str, asset_id: str, suffix: str) -> str:
        workspace_key = hashlib.sha256(workspace_id.encode()).hexdigest()[:16]
        return f"{workspace_key}/{asset_id}{suffix}"

    def storage_key(self, *, workspace_id: str, asset_id: str, suffix: str) -> str:
        return self._storage_key(workspace_id, asset_id, suffix)

    def object_key(self, *, workspace_id: str, asset_id: str, suffix: str) -> str:
        return self._key(self._storage_key(workspace_id, asset_id, suffix))

    def put(
        self,
        *,
        workspace_id: str,
        asset_id: str,
        suffix: str,
        content: bytes,
    ) -> str:
        storage_key = self._storage_key(workspace_id, asset_id, suffix)
        self.store.put_bytes(self._key(storage_key), content)
        return storage_key

    def read(self, storage_key: str) -> bytes:
        return self.store.get_bytes(self._key(storage_key))

    def delete(self, storage_key: str) -> None:
        self.store.delete_bytes(self._key(storage_key))
