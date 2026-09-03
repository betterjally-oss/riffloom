from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class ByteStore(Protocol):
    def put_bytes(self, key: str, content: bytes) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def delete_bytes(self, key: str) -> None: ...


class FilesystemByteStore:
    """Byte-object storage backed by a local or pre-mounted filesystem path."""

    def __init__(self, root: str | Path, *, create_root: bool = True):
        self.root = Path(root).expanduser().resolve()
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)
        elif not self.root.is_dir():
            raise RuntimeError(
                f"对象存储挂载目录不存在：{self.root}；已拒绝回退到临时本地目录"
            )

    @staticmethod
    def _validate_key(key: str) -> tuple[str, ...]:
        if not key or key != key.strip() or "\\" in key or key.startswith("/"):
            raise ValueError("对象存储 key 无效")
        parts = tuple(key.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("对象存储 key 无效")
        return parts

    def _path(self, key: str) -> Path:
        target = self.root.joinpath(*self._validate_key(key)).resolve()
        if self.root not in target.parents:
            raise ValueError("对象存储路径越界")
        return target

    def put_bytes(self, key: str, content: bytes) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=".riffloom-object-",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def get_bytes(self, key: str) -> bytes:
        target = self._path(key)
        if not target.is_file():
            raise FileNotFoundError(key)
        return target.read_bytes()

    def delete_bytes(self, key: str) -> None:
        target = self._path(key)
        target.unlink(missing_ok=True)
