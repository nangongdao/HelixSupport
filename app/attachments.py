"""Rich-media attachments (backlog: 语音/富媒体消息, feasible subset).

Operator-uploaded attachments are stored on disk under a unique storage key
with safe metadata (filename/content type/size) in the ``attachments`` table.
Upload enforces a content-type/extension allowlist, a per-file cap, and a
per-tenant cumulative quota; a deterministic scanner (magic bytes + extension
rules) rejects executable or mismatched files before they reach the
conversation stream. An operator reply can reference uploaded attachments
via ``attachment_ids``, which are backfilled onto the attachment rows so the
message stream can render chips. Voice-to-text and ClamAV are out of scope
for this slice — the scanner is the interface to swap in later.
"""

from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.db._util import utc_now

logger = logging.getLogger("helix")


class AttachmentTypeError(ValueError):
    """Unsupported content type or empty payload -> 415."""


class AttachmentLimitError(ValueError):
    """Per-file or per-tenant quota exceeded -> 413."""


# Content types accepted at upload; anything else is rejected with 415.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "application/pdf",
        "text/plain",
        "text/csv",
        "text/markdown",
        "application/json",
    }
)

# Extensions that never pass, regardless of the declared content type.
_DENIED_EXTENSIONS = frozenset(
    {"exe", "bat", "cmd", "com", "scr", "ps1", "sh", "bash", "js", "py", "php", "jar", "msi", "dll"}
)

# ``extension -> magic byte prefix`` pairs used to reject renamed files.
_MAGIC_PATTERNS = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "pdf": (b"%PDF-",),
}

_EXECUTABLE_MAGIC = ((b"MZ", "Windows executable"), (b"\x7fELF", "ELF executable"))


def sanitize_filename(filename: str) -> str:
    """Keep only the base name and strip characters that are unsafe in a
    Content-Disposition header (quotes, backslashes, control characters)."""
    cleaned = Path(filename or "attachment").name.strip()
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 0x20 and ch not in {'"', "\\", "\x7f"})
    return cleaned[:160] or "attachment"


def content_type_for(filename: str) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    return guessed or "application/octet-stream"


class AttachmentScanner:
    """Deterministic rule-based scanner (no external AV service in this slice).

    Returns ``(clean, verdict)``: clean=True means the file passed; verdict
    is a human-readable reason when rejected.

    42.4 SEC-006 hardening: EICAR (and polymorphic variants), disguised-MIME
    binary magic, and compression-bomb bounds are all fail-closed rejections.
    """

    # The EICAR test string's tail is fixed by the spec; only three characters
    # near the head may vary, so matching the fixed suffix catches every
    # polymorphic variant without false positives on ordinary text.
    _EICAR_SUFFIX = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
    # Any archive payload may not expand beyond this multiple of the blob
    # itself (compression-bomb guard for future archive content types).
    _BOMB_RATIO = 20
    def scan(self, data: bytes, filename: str, content_type: str) -> tuple[bool, str]:
        extension = Path(filename).suffix.lower().lstrip(".")
        if extension in _DENIED_EXTENSIONS:
            return False, f"extension .{extension} is not allowed"
        if self._EICAR_SUFFIX in data:
            return False, "EICAR antivirus test signature detected"
        head = data[:8]
        if head.startswith(b"MZ") or head.startswith(b"\x7fELF"):
            return False, "executable magic bytes detected"
        if head.startswith(b"#!"):
            return False, "script shebang detected"
        expected = _MAGIC_PATTERNS.get(extension)
        if expected is not None and not any(head.startswith(prefix) for prefix in expected):
            return False, f"content does not match .{extension} signature"
        # Disguised MIME: a text/* claim with binary container magic is a
        # mismatch regardless of extension.
        if content_type.startswith("text/") and (
            head.startswith(b"PK\x03\x04") or head.startswith(b"\x1f\x8b")
        ):
            return False, f"binary container bytes declared as {content_type}"
        bomb_verdict = self._check_compression_bomb(data)
        if bomb_verdict:
            return bomb_verdict
        if content_type in ALLOWED_CONTENT_TYPES and not data:
            return False, "empty file"
        return True, "clean"

    def _check_compression_bomb(self, data: bytes) -> tuple[bool, str] | None:
        """Reject archives whose expansion exceeds the bomb ratio."""
        import gzip
        import io
        import zipfile

        limit = max(len(data), 1) * self._BOMB_RATIO
        if data[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                    total = sum(info.file_size for info in bundle.infolist())
            except Exception as exc:
                return False, f"unreadable zip container: {exc}"
            if total > limit:
                return False, f"zip expansion {total} bytes exceeds safety bound"
        elif data[:2] == b"\x1f\x8b":
            decompressed = 0
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                    while chunk := gz.read(65536):
                        decompressed += len(chunk)
                        if decompressed > limit:
                            return False, "gzip stream exceeds safety bound"
            except Exception as exc:
                return False, f"unreadable gzip stream: {exc}"
        return None


class TimeoutMalwareScanner:
    """Wraps any scanner with a wall-clock budget; timeout fails closed.

    External AV/CDR engines can hang; per SEC-006 an unavailable verdict must
    never pass as clean.
    """

    def __init__(self, inner: Any, timeout_seconds: float = 10.0) -> None:
        self.inner = inner
        self.timeout_seconds = timeout_seconds

    def scan(self, data: bytes, filename: str, content_type: str) -> tuple[bool, str]:
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self.inner.scan, data, filename, content_type)
            try:
                clean, verdict = future.result(timeout=self.timeout_seconds)
            except Exception as exc:  # timeout or engine crash — fail closed
                return False, f"scan unavailable ({exc.__class__.__name__}); file quarantined"
        finally:
            # Never block on a hung engine thread; the budget already expired.
            pool.shutdown(wait=False, cancel_futures=True)
        if not isinstance(clean, bool) or (clean and str(verdict).lower() not in {"clean", "ok", "pass"}):
            # Unknown/ambiguous verdicts never pass (fail closed).
            return False, f"unrecognized scanner verdict: {verdict!r}"
        return clean, str(verdict)


def normalize_verdict(clean: Any, verdict: Any) -> tuple[bool, str]:
    """Coerce an external engine's answer; unknown shapes fail closed."""
    if not isinstance(clean, bool):
        return False, f"non-boolean clean flag: {clean!r}"
    text = str(verdict or "").strip().lower()
    if clean and text not in {"clean", "ok", "pass"}:
        return False, f"unrecognized scanner verdict: {verdict!r}"
    return clean, str(verdict or "unspecified")


class AttachmentService:
    def __init__(
        self,
        database: Any,
        settings: Any,
        scanner: AttachmentScanner | None = None,
        store: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.scanner = scanner or AttachmentScanner()
        # 42.4 SEC-006: object store adapter (disk reference implementation).
        self.store = store
        self.storage_dir = Path(settings.attachment_storage_dir)

    def _put_object(self, key: str, data: bytes) -> tuple[str, int]:
        """Store the blob and return ``(sha256, size_bytes)``.

        Uses the configured adapter when present; falls back to the legacy
        direct-disk write so existing deployments keep working unchanged.
        """
        if self.store is not None:
            stored = self.store.put(key, data)
            return stored.sha256, stored.size_bytes
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        target = self.storage_dir / key
        target.write_bytes(data)
        import hashlib

        return hashlib.sha256(data).hexdigest(), len(data)

    def _remove_object(self, key: str) -> None:
        if self.store is not None:
            self.store.delete(key)
            return
        try:
            (self.storage_dir / key).unlink(missing_ok=True)
        except OSError:
            logger.warning("attachment.unlink_failed", extra={"storage_key": key})

    # ---------------------------------------------------------------- upload

    def upload(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        filename = sanitize_filename(filename)
        if not content_type or content_type not in ALLOWED_CONTENT_TYPES:
            raise AttachmentTypeError("attachment content type is not allowed")
        if not data:
            raise AttachmentTypeError("attachment is empty")
        max_bytes = self.settings.attachment_max_mb * 1024 * 1024
        if len(data) > max_bytes:
            raise AttachmentLimitError(
                f"attachment exceeds the {self.settings.attachment_max_mb} MB per-file limit"
            )
        quota_bytes = self.settings.attachment_quota_mb * 1024 * 1024
        if self.quota_used(tenant_id) + len(data) > quota_bytes:
            raise AttachmentLimitError(
                f"attachment would exceed the {self.settings.attachment_quota_mb} MB tenant quota"
            )
        scan_mode = getattr(self.settings, "attachment_scan_mode", "sync")
        clean = True
        verdict = "clean"
        scanned = False
        status = "stored"
        if self.settings.attachment_scan_enabled:
            if scan_mode == "external":
                # SEC-006 quarantine flow: the external AV/CDR engine issues
                # its verdict asynchronously; until then the object exists but
                # is neither downloadable nor bindable to a message.
                status = "quarantined"
                verdict = "awaiting external scanner"
            else:
                clean, verdict = normalize_verdict(*self.scanner.scan(data, filename, content_type))
                scanned = True
                status = "stored" if clean else "rejected"
        attachment_id = f"att_{uuid4().hex[:12]}"
        storage_key = f"{attachment_id}_{uuid4().hex[:8]}"
        now = utc_now()
        sha256 = ""
        try:
            # Quarantined/rejected payloads are never written to the served
            # bucket — only clean-or-pending objects land in the store.
            if status in {"stored", "quarantined"}:
                sha256, _size = self._put_object(storage_key, data)
        except Exception as exc:
            logger.exception("attachment.store_failed")
            raise RuntimeError(f"attachment storage failed: {exc}") from exc
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO attachments
                    (id, tenant_id, conversation_id, message_id, filename, content_type,
                     size_bytes, storage_key, uploader, status, scanned, verdict, created_at,
                     sha256)
                    VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        attachment_id,
                        tenant_id,
                        conversation_id,
                        filename,
                        content_type,
                        len(data),
                        storage_key,
                        actor_id,
                        status,
                        1 if scanned else 0,
                        verdict,
                        now,
                        sha256 or None,
                    ),
                )
        except Exception:
            # Never leave an orphaned file behind when the row insert fails.
            self._remove_object(storage_key)
            raise
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "attachment.uploaded",
            {
                "attachment_id": attachment_id,
                "status": status,
                "verdict": verdict,
                "size_bytes": len(data),
                "sha256": sha256 or None,
            },
        )
        return self.get(tenant_id, attachment_id) or {}

    # ------------------------------------------------------------- quarantine

    def finalize_verdict(
        self, tenant_id: str, attachment_id: str, clean: bool, verdict: str, actor_id: str
    ) -> dict[str, Any] | None:
        """Apply the external engine's verdict to a quarantined attachment.

        Only a ``quarantined`` row may transition; ``clean`` promotes it to
        ``stored`` (downloadable/bindable), anything else rejects it. The
        rejected payload's object is removed immediately.
        """
        attachment = self.get(tenant_id, attachment_id)
        if attachment is None:
            return None
        if attachment["status"] != "quarantined":
            raise ValueError(f"attachment {attachment_id} is not quarantined")
        clean, verdict = normalize_verdict(clean, verdict)
        new_status = "stored" if clean else "rejected"
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE attachments SET status = ?, verdict = ? "
                "WHERE tenant_id = ? AND id = ? AND status = 'quarantined'",
                (new_status, verdict, tenant_id, attachment_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"attachment {attachment_id} is not quarantined")
        if not clean:
            self._remove_object(str(attachment["storage_key"]))
        self.database.audit(
            tenant_id,
            attachment["conversation_id"],
            actor_id,
            "attachment.verdict",
            {
                "attachment_id": attachment_id,
                "status": new_status,
                "verdict": verdict,
                "storage_key": attachment["storage_key"],
            },
        )
        return self.get(tenant_id, attachment_id)

    # ------------------------------------------------- signed download URLs

    def sign_download_url_token(
        self,
        tenant_id: str,
        attachment_id: str,
        *,
        ttl_seconds: int = 600,
        secret: str | None = None,
        now_epoch: int | None = None,
    ) -> tuple[str, int]:
        """Return ``(token, expires_at)`` for a ≤10-minute download URL.

        HMAC over ``tenant.attachment.expiry`` with the server-side widget
        secret; replay after expiry (or any tampering with any component)
        fails verification.
        """
        import hashlib
        import hmac

        ttl = max(1, min(int(ttl_seconds), 600))
        expires_at = (now_epoch if now_epoch is not None else int(time.time())) + ttl
        signing_secret = secret or getattr(self.settings, "widget_secret", "")
        payload = f"{tenant_id}.{attachment_id}.{expires_at}"
        token = hmac.new(
            signing_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return token, expires_at

    def verify_download_url_token(
        self,
        tenant_id: str,
        attachment_id: str,
        token: str,
        expires_at: int,
        *,
        secret: str | None = None,
        now_epoch: int | None = None,
    ) -> bool:
        import hashlib
        import hmac

        if expires_at < (now_epoch if now_epoch is not None else int(time.time())):
            return False
        signing_secret = secret or getattr(self.settings, "widget_secret", "")
        payload = f"{tenant_id}.{attachment_id}.{expires_at}"
        expected = hmac.new(
            signing_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, token or "")

    # ------------------------------------------------------------------ read

    def get(self, tenant_id: str, attachment_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM attachments WHERE tenant_id = ? AND id = ?",
                (tenant_id, attachment_id),
            ).fetchone()
        return dict(row) if row else None

    def list_for_conversation(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM attachments WHERE tenant_id = ? AND conversation_id = ? "
                "AND status = 'stored' ORDER BY created_at DESC",
                (tenant_id, conversation_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def download(self, tenant_id: str, attachment_id: str) -> tuple[Path, str, str] | None:
        attachment = self.get(tenant_id, attachment_id)
        if attachment is None or attachment["status"] != "stored":
            return None
        storage_key = str(attachment["storage_key"])
        expected = attachment["sha256"] if "sha256" in attachment.keys() else None
        if self.store is not None:
            # Object tampering is detectable: the stored digest must match.
            if expected and not self.store.verify(storage_key, str(expected)):
                logger.error(
                    "attachment.integrity_failed", extra={"storage_key": storage_key}
                )
                return None
            try:
                path = self.store.path_for(storage_key)
            except FileNotFoundError:
                return None
            return path, attachment["filename"], attachment["content_type"]
        path = self.storage_dir / storage_key
        if not path.is_file():
            return None
        if expected:
            import hashlib

            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(65536):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                logger.error("attachment.integrity_failed", extra={"storage_key": storage_key})
                return None
        return path, attachment["filename"], attachment["content_type"]

    def delete(self, tenant_id: str, attachment_id: str, actor_id: str) -> bool:
        attachment = self.get(tenant_id, attachment_id)
        if attachment is None:
            return False
        storage_key = str(attachment["storage_key"])
        sha256 = attachment["sha256"] if "sha256" in attachment.keys() else None
        self._remove_object(storage_key)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM attachments WHERE tenant_id = ? AND id = ?",
                (tenant_id, attachment_id),
            )
        deleted = cursor.rowcount == 1
        if deleted:
            # Deletion proof: the audit row records what was destroyed so the
            # removal itself is independently verifiable.
            self.database.audit(
                tenant_id,
                attachment["conversation_id"],
                actor_id,
                "attachment.deleted",
                {
                    "attachment_id": attachment_id,
                    "storage_key": storage_key,
                    "sha256": sha256,
                    "size_bytes": int(attachment["size_bytes"]),
                },
            )
        return deleted

    def quota_used(self, tenant_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM attachments "
                "WHERE tenant_id = ? AND status = 'stored'",
                (tenant_id,),
            ).fetchone()
        return int(row["total"])

    # --------------------------------------------------------- message linking

    def validate_for_message(
        self, tenant_id: str, conversation_id: str, attachment_ids: list[str]
    ) -> list[str]:
        """Return the ids that belong to this tenant+conversation and are
        stored; raise when any requested id is missing or foreign."""
        if not attachment_ids:
            return []
        placeholders = ", ".join("?" for _ in attachment_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT id FROM attachments
                WHERE tenant_id = ? AND conversation_id = ? AND status = 'stored'
                  AND id IN ({placeholders})""",
                (tenant_id, conversation_id, *attachment_ids),
            ).fetchall()
        found = {row["id"] for row in rows}
        missing = [aid for aid in attachment_ids if aid not in found]
        if missing:
            raise LookupError(f"attachments not found in this conversation: {', '.join(missing)}")
        return list(found)

    def attach_message(self, tenant_id: str, message_id: str, attachment_ids: list[str]) -> int:
        """Backfill ``message_id`` onto stored attachment rows; returns count."""
        if not attachment_ids:
            return 0
        placeholders = ", ".join("?" for _ in attachment_ids)
        with self.database.connect() as connection:
            cursor = connection.execute(
                f"""UPDATE attachments SET message_id = ?
                WHERE tenant_id = ? AND status = 'stored' AND id IN ({placeholders})""",
                (message_id, tenant_id, *attachment_ids),
            )
        return cursor.rowcount
