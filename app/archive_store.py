"""Archive object store (ROADMAP 42.3 / REL-002).

Cold-tier payloads (audit-retention archives today, conversation archives
later) live as **compressed partitions on an object store** instead of JSON
blobs inside the database. The disk backend here is the reference adapter:
production swaps in S3/GCS with the same interface.

Design contracts:

- **Compressed partitions by tenant/date** — each partition is a gzip JSONL
  object (one record per line), so reading streams line-by-line with bounded
  memory; nothing ever materialises a whole partition.
- **Manifest index per tenant** — small metadata entries (object id, time
  span, sha256, sizes, record count) enable time-range queries without
  touching payload bytes.
- **Fail closed** — every read recomputes the digest while streaming; a
  missing object or a checksum mismatch raises :class:`ArchiveIntegrityError`
  instead of returning partial data.
- **Atomic writes** — objects land via temp-file + rename, so a crash never
  leaves a half-written partition behind a manifest entry.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("helix")

_MANIFEST_NAME = "manifest.json"


class ArchiveIntegrityError(RuntimeError):
    """A cold-tier object is missing or fails its digest — fail closed."""


@dataclass(frozen=True)
class ManifestEntry:
    """One partition's metadata in the tenant manifest."""

    object_id: str
    partition_key: str
    first_ts: str
    last_ts: str
    sha256: str
    size_bytes: int
    record_count: int
    created_at: str
    # Digest of the *uncompressed* canonical JSONL stream — recomputable
    # while streaming, so logical integrity is verifiable without ever
    # materialising the partition.
    content_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "partition_key": self.partition_key,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "created_at": self.created_at,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ManifestEntry":
        return cls(
            object_id=str(raw["object_id"]),
            partition_key=str(raw["partition_key"]),
            first_ts=str(raw["first_ts"]),
            last_ts=str(raw["last_ts"]),
            sha256=str(raw["sha256"]),
            size_bytes=int(raw["size_bytes"]),
            record_count=int(raw["record_count"]),
            created_at=str(raw["created_at"]),
            content_sha256=str(raw.get("content_sha256") or ""),
        )


class ArchiveObjectStore:
    """Object-store adapter for cold-tier archive payloads (disk backend)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _tenant_dir(self, tenant_id: str) -> Path:
        return self.root / _safe_segment(tenant_id)

    def _object_path(self, tenant_id: str, object_id: str) -> Path:
        return self._tenant_dir(tenant_id) / f"{_safe_segment(object_id)}.jsonl.gz"

    def _manifest_path(self, tenant_id: str) -> Path:
        return self._tenant_dir(tenant_id) / _MANIFEST_NAME

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def put_partition(
        self,
        tenant_id: str,
        *,
        partition_key: str,
        records: list[dict[str, Any]],
        time_key: str = "created_at",
        created_at: str,
    ) -> ManifestEntry:
        """Compress ``records`` into one JSONL.gz partition and register it.

        The caller supplies the partition key (convention: ``YYYY-MM-DD`` or
        ``YYYY-MM-DD/<first>-<last>``); the store owns object ids and atomic
        placement. Returns the manifest entry that was appended.
        """
        if not records:
            raise ValueError("refusing to write an empty archive partition")
        first_ts = str(records[0].get(time_key, ""))
        last_ts = str(records[-1].get(time_key, ""))

        tenant_dir = self._tenant_dir(tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)
        object_id = f"part_{os.urandom(8).hex()}"
        final_path = self._object_path(tenant_id, object_id)

        digest = hashlib.sha256()
        content_digest = hashlib.sha256()
        size_bytes = 0
        fd, tmp_name = tempfile.mkstemp(dir=str(tenant_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as raw_tmp:
                with gzip.GzipFile(fileobj=raw_tmp, mode="wb", mtime=0) as gz:
                    for record in records:
                        line = json.dumps(
                            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                        gz.write(line + b"\n")
                        content_digest.update(line + b"\n")
                raw_tmp.flush()
                os.fsync(raw_tmp.fileno())
            size_bytes = Path(tmp_name).stat().st_size
            # Stream the digest in chunks — never materialise the object.
            with open(tmp_name, "rb") as hashed:
                while chunk := hashed.read(65536):
                    digest.update(chunk)
            os.replace(tmp_name, final_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

        entry = ManifestEntry(
            object_id=object_id,
            partition_key=partition_key,
            first_ts=first_ts,
            last_ts=last_ts,
            sha256=digest.hexdigest(),
            size_bytes=int(size_bytes),
            record_count=len(records),
            created_at=created_at,
            content_sha256=content_digest.hexdigest(),
        )
        self._append_manifest(tenant_id, entry)
        return entry

    def _load_manifest(self, tenant_id: str) -> list[ManifestEntry]:
        path = self._manifest_path(tenant_id)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [ManifestEntry.from_dict(item) for item in raw.get("partitions", [])]

    def _append_manifest(self, tenant_id: str, entry: ManifestEntry) -> None:
        path = self._manifest_path(tenant_id)
        entries = self._load_manifest(tenant_id)
        entries.append(entry)
        payload = json.dumps(
            {"schema": 1, "partitions": [e.to_dict() for e in entries]},
            ensure_ascii=False,
            indent=2,
        )
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # ------------------------------------------------------------------
    # Read path (streaming, fail closed)
    # ------------------------------------------------------------------

    def _open_verified(self, tenant_id: str, entry: ManifestEntry) -> Iterator[bytes]:
        """Yield raw decompressed lines after verifying the stored digest.

        The compressed bytes are streamed through the digest first; only when
        it matches the manifest does decompression start. A missing object or
        a tampered partition therefore raises :class:`ArchiveIntegrityError`
        *before* any record is yielded — callers never see partial data.
        """
        path = self._object_path(tenant_id, entry.object_id)
        if not path.exists():
            raise ArchiveIntegrityError(
                f"archive object missing: tenant={tenant_id} object={entry.object_id}"
            )
        running = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                running.update(chunk)
            if running.hexdigest() != entry.sha256:
                raise ArchiveIntegrityError(
                    f"archive object digest mismatch: tenant={tenant_id} "
                    f"object={entry.object_id} expected={entry.sha256[:12]} "
                    f"got={running.hexdigest()[:12]}"
                )
            handle.seek(0)
            gz = gzip.GzipFile(fileobj=handle, mode="rb")
            for line in gz:
                yield line

    def iter_lines(self, tenant_id: str, object_id: str) -> Iterator[bytes]:
        """Stream one partition's raw decompressed JSONL lines (verified).

        Like :meth:`iter_records` but without parsing — used by callers whose
        validation is itself streaming (e.g. audit-chain verification).
        """
        entry = self._find_entry(tenant_id, object_id)
        yield from self._open_verified(tenant_id, entry)

    def iter_records(
        self, tenant_id: str, object_id: str, *, expected: ManifestEntry | None = None
    ) -> Iterator[dict[str, Any]]:
        """Stream one partition's records; fail closed on missing/tampered."""
        entry = expected or self._find_entry(tenant_id, object_id)
        for line in self._open_verified(tenant_id, entry):
            if not line.strip():
                continue
            yield json.loads(line.decode("utf-8"))

    def iter_range(
        self,
        tenant_id: str,
        *,
        from_dt: str | None = None,
        to_dt: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream records across partitions overlapping [from_dt, to_dt].

        Partitions are visited in manifest order (append = chronological by
        construction); ``limit`` caps the total records yielded so a range
        query can never stream unbounded data.
        """
        remaining = limit
        for entry in self._load_manifest(tenant_id):
            # Bounds are matched by prefix so a date-only filter
            # ("2026-08-21") covers the whole day of full ISO timestamps.
            if from_dt and entry.last_ts[: len(from_dt)] < from_dt:
                continue
            if to_dt and entry.first_ts[: len(to_dt)] > to_dt:
                continue
            for record in self.iter_records(tenant_id, entry.object_id, expected=entry):
                if remaining is not None and remaining <= 0:
                    return
                yield record
                if remaining is not None:
                    remaining -= 1

    def verify(self, tenant_id: str, *, object_id: str | None = None) -> dict[str, int]:
        """Recompute every (or one) partition digest; fail closed on drift."""
        entries = self._load_manifest(tenant_id)
        if object_id is not None:
            entries = [e for e in entries if e.object_id == object_id]
            if not entries:
                raise ArchiveIntegrityError(
                    f"archive object not in manifest: tenant={tenant_id} object={object_id}"
                )
        verified = 0
        records = 0
        for entry in entries:
            for _line in self._open_verified(tenant_id, entry):
                records += 1
            verified += 1
        if verified != len(entries):
            raise ArchiveIntegrityError("archive verification incomplete")
        return {"partitions": verified, "records": records}

    def _find_entry(self, tenant_id: str, object_id: str) -> ManifestEntry:
        for entry in self._load_manifest(tenant_id):
            if entry.object_id == object_id:
                return entry
        raise ArchiveIntegrityError(
            f"archive object not in manifest: tenant={tenant_id} object={object_id}"
        )


def _safe_segment(value: str) -> str:
    """Restrict a path segment to filesystem-safe characters."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"unsafe archive path segment: {value!r}")
    return cleaned