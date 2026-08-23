"""Attachment object store (ROADMAP 42.4 / SEC-006).

The storage adapter behind :class:`~app.attachments.AttachmentService`.
The disk backend is the reference implementation; production swaps in S3/GCS
with the same interface.

Contracts:

- **Object keys never derive from user filenames** — callers pass the
  generated ``storage_key``; the store additionally rejects any key that is
  not a single safe path segment (no separators, no ``..``, no unicode
  tricks), so an upload can never escape the bucket root or overwrite
  another object by path construction.
- **No silent overwrite** — ``put`` fails with :class:`AttachmentKeyConflict`
  if the key already exists; every object is immutable once written.
- **Checksums** — ``put`` returns the SHA-256 of the stored bytes so the
  database row can pin content integrity; downloads re-verify.
- **Atomic placement** — objects land via temp-file + rename inside the
  bucket root.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AttachmentKeyError(ValueError):
    """A storage key is malformed or attempts path traversal."""


class AttachmentKeyConflict(RuntimeError):
    """An object with this key already exists — keys are immutable."""


@dataclass(frozen=True)
class StoredObject:
    """Metadata returned by a successful ``put``."""

    key: str
    sha256: str
    size_bytes: int


def validate_key(key: str) -> str:
    """Enforce single-segment, traversal-proof storage keys."""
    if not _SAFE_KEY.fullmatch(key or ""):
        raise AttachmentKeyError(f"unsafe attachment storage key: {key!r}")
    return key


class DiskAttachmentStore:
    """Filesystem-backed attachment object store (reference adapter)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    def put(self, key: str, data: bytes) -> StoredObject:
        path = self._path(key)
        if path.exists():
            raise AttachmentKeyConflict(f"attachment object already exists: {key}")
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return StoredObject(
            key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    def path_for(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"attachment object missing: {key}")
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def verify(self, key: str, expected_sha256: str) -> bool:
        """Re-read the object and compare its digest (tamper detection)."""
        try:
            path = self.path_for(key)
        except FileNotFoundError:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(65536):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256