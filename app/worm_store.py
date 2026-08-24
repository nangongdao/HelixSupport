"""Write-once-read-many (WORM) storage abstraction (Phase 41.3, SEC-005).

External audit anchors must land in storage that cannot be rewritten after the
fact — object-lock / WORM buckets in production.  On a dev workstation a disk
directory stands in: each object is created with O_EXCL so a second write to
the same name is refused, and its content hash plus first-write mtime are
recorded in an append-only journal so a later out-of-band replacement (rename
over the file, which resets the mtime) is exposed on the next ``read_all``.

The disk store *detects* replacement rather than preventing it; the real
anti-tamper boundary remains the Ed25519 signature on each anchor claim (see
``app/audit_anchor.py``).  WORM unavailability surfaces as
``WormUnavailableError`` and any detected inconsistency as
``WormIntegrityError``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)

_JOURNAL_NAME = "_worm.journal.jsonl"
_OBJECT_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class WormUnavailableError(RuntimeError):
    """WORM storage is unreachable or refuses a write right now."""


class WormIntegrityError(RuntimeError):
    """WORM content no longer matches what was committed on first write."""


class WormStoreProtocol(Protocol):
    def write_once(self, object_id: str, payload: Mapping[str, Any]) -> None:
        """Persist ``payload`` exactly once under ``object_id``."""
        ...

    def read_all(self) -> Sequence[dict[str, Any]]:
        """Return every previously-written payload, or raise on tamper."""

        ...


def _validate_object_id(object_id: str) -> str:
    if not object_id or any(ch not in _OBJECT_ID_CHARS for ch in object_id):
        raise ValueError(f"invalid WORM object_id: {object_id!r}")
    return object_id


@dataclass(frozen=True)
class DiskWormStore:
    """File-backed WORM store with an append-only integrity journal."""

    directory: Path

    def __post_init__(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WormUnavailableError(
                f"WORM store directory unavailable {self.directory}: {exc}"
            ) from exc

    # -- writing -------------------------------------------------------

    def write_once(self, object_id: str, payload: Mapping[str, Any]) -> None:
        object_id = _validate_object_id(object_id)
        if "object_id" in payload:
            raise ValueError("WORM payload must not carry an object_id field")
        obj_path = self.directory / f"{object_id}.json"
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        journal_entry: dict[str, Any] | None = None
        try:
            with open(obj_path, "xb") as handle:  # O_CREAT | O_EXCL
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            st = obj_path.stat()
            journal_entry = {
                "object_id": object_id,
                "sha256": hashlib.sha256(content).hexdigest(),
                "first_mtime_ns": st.st_mtime_ns,
            }
        except FileExistsError as exc:
            raise WormIntegrityError(
                f"WORM object {object_id} already exists (write-once violated)"
            ) from exc
        except OSError as exc:
            raise WormUnavailableError(f"WORM write failed for {object_id}: {exc}") from exc
        # Journal append happens after the object is durable; a failure here
        # leaves an orphan object (present but unjournaled), which read_all
        # flags as a tamper rather than silently ignoring.
        assert journal_entry is not None
        try:
            with open(self.directory / _JOURNAL_NAME, "a", encoding="utf-8") as journal:
                journal.write(json.dumps(journal_entry, sort_keys=True) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
        except OSError as exc:
            raise WormUnavailableError(
                f"WORM journal append failed for {object_id}: {exc}"
            ) from exc

    # -- reading -------------------------------------------------------

    def read_all(self) -> Sequence[dict[str, Any]]:
        journal = self._read_journal()
        try:
            paths = [
                p
                for p in sorted(self.directory.iterdir(), key=lambda p: p.name)
                if p.is_file() and not p.name.startswith("_") and p.name.endswith(".json")
            ]
        except OSError as exc:
            raise WormUnavailableError(f"WORM read failed: {exc}") from exc
        payloads: list[dict[str, Any]] = []
        for path in paths:
            object_id = path.name[: -len(".json")]
            try:
                raw = path.read_bytes()
                st = path.stat()
            except OSError as exc:
                raise WormUnavailableError(f"WORM read failed for {path.name}: {exc}") from exc
            entry = journal.get(object_id)
            if entry is None:
                raise WormIntegrityError(f"WORM object {object_id} present but not in the journal")
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise WormIntegrityError(f"WORM object {object_id} content hash mismatch")
            if st.st_mtime_ns != entry["first_mtime_ns"]:
                raise WormIntegrityError(
                    f"WORM object {object_id} was replaced after first write (mtime changed)"
                )
            try:
                payloads.append(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, ValueError) as exc:
                raise WormIntegrityError(f"WORM object {object_id} is not valid JSON") from exc
        missing = sorted(set(journal) - {p.name[: -len(".json")] for p in paths})
        if missing:
            raise WormIntegrityError(f"WORM objects lost from store: {missing}")
        return payloads

    def _read_journal(self) -> dict[str, dict[str, Any]]:
        journal_path = self.directory / _JOURNAL_NAME
        entries: dict[str, dict[str, Any]] = {}
        if not journal_path.exists():
            return entries
        try:
            lines = journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise WormUnavailableError(f"WORM journal read failed: {exc}") from exc
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise WormIntegrityError(f"WORM journal line is corrupt: {exc}") from exc
            object_id = entry.get("object_id")
            if not isinstance(object_id, str):
                raise WormIntegrityError("WORM journal entry has no object_id")
            if object_id in entries:
                raise WormIntegrityError(f"WORM journal lists object {object_id} more than once")
            entries[object_id] = entry
        return entries


def worm_path(*, base_dir: Path, prefix: str = "anchors") -> Path:
    """Derive a per-run WORM directory (kept small for dev stand-in use)."""
    return base_dir / f"{prefix}-{uuid4().hex[:8]}"


__all__ = [
    "DiskWormStore",
    "WormIntegrityError",
    "WormStoreProtocol",
    "WormUnavailableError",
    "worm_path",
]
