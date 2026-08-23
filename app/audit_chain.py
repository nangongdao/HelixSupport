"""Audit hash chain (Phase 28.3).

Every audit event carries the SHA-256 of the canonical row plus the previous
event's hash, forming a tamper-evident chain. Modifying any historical row
changes its hash and breaks every subsequent link, so a verifier can detect
tampering.

Canonical row for hashing:

    f"{prev_hash}\n{id}\n{tenant_id}\n{conversation_id or ''}\n"
    f"{request_id or ''}\n{actor}\n{event_type}\n{payload_json}\n{created_at}"

``prev_hash`` of the first event is the empty string.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from typing import Any


def event_hash(
    *,
    prev_hash: str,
    event_id: str,
    tenant_id: str,
    conversation_id: str | None,
    request_id: str | None,
    actor: str,
    event_type: str,
    payload_json: str,
    created_at: str,
) -> str:
    canonical = (
        f"{prev_hash}\n{event_id}\n{tenant_id}\n{conversation_id or ''}\n"
        f"{request_id or ''}\n{actor}\n{event_type}\n{payload_json}\n{created_at}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_chain(rows: list[dict[str, Any]]) -> list[str]:
    """Verify a list of audit rows (dicts with the DB columns) in chain order.

    Returns a list of problems; empty means the chain is intact.
    """
    problems: list[str] = []
    expected_prev = ""
    for index, row in enumerate(rows):
        event_id = str(row["id"])
        computed = event_hash(
            prev_hash=expected_prev,
            event_id=event_id,
            tenant_id=str(row["tenant_id"]),
            conversation_id=row.get("conversation_id"),
            request_id=row.get("request_id"),
            actor=str(row.get("actor", "")),
            event_type=str(row.get("event_type", "")),
            payload_json=str(row.get("payload_json", "")),
            created_at=str(row.get("created_at", "")),
        )
        stored = row.get("event_hash")
        if stored is not None and stored != computed:
            problems.append(
                f"row {index} ({event_id}): hash mismatch (stored {stored}, computed {computed})"
            )
        prev_stored = row.get("prev_hash")
        if prev_stored is not None and prev_stored != expected_prev:
            problems.append(
                f"row {index} ({event_id}): prev_hash mismatch "
                f"(stored {prev_stored}, expected {expected_prev})"
            )
        expected_prev = computed
    return problems


def chain_head(rows: list[dict[str, Any]]) -> str | None:
    """Return the hash of the last row (the chain head), or None."""
    if not rows:
        return None
    return rows[-1].get("event_hash") or ""


def validate_audit_archive(archive: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate an archive manifest and return its original event rows."""
    item = dict(archive)
    archive_json = str(item.get("archive_json") or "")
    actual_digest = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
    if actual_digest != item.get("content_sha256"):
        raise ValueError("audit archive content hash mismatch")
    try:
        document = json.loads(archive_json)
    except json.JSONDecodeError as exc:
        raise ValueError("audit archive JSON is invalid") from exc
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise ValueError("audit archive schema mismatch")
    if item.get("tenant_id") is not None and document.get("tenant_id") != item["tenant_id"]:
        raise ValueError("audit archive tenant mismatch")
    for field in ("cutoff", "created_at"):
        if item.get(field) is not None and document.get(field) != item[field]:
            raise ValueError(f"audit archive {field} mismatch")
    events = document.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("audit archive events are missing")
    if len(events) != int(item.get("event_count") or 0):
        raise ValueError("audit archive event count mismatch")
    if any(not isinstance(event, dict) for event in events):
        raise ValueError("audit archive event shape mismatch")
    typed_events = [dict(event) for event in events]
    required_fields = {
        "id",
        "tenant_id",
        "conversation_id",
        "request_id",
        "actor",
        "event_type",
        "payload_json",
        "created_at",
        "seq",
        "prev_hash",
        "event_hash",
    }
    if any(not required_fields.issubset(event) for event in typed_events):
        raise ValueError("audit archive event shape mismatch")
    event_ids = [str(event["id"]) for event in typed_events]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("audit archive event id uniqueness mismatch")
    sequences = [int(event.get("seq") or 0) for event in typed_events]
    if (
        any(sequence <= 0 for sequence in sequences)
        or sequences != sorted(sequences)
        or len(sequences) != len(set(sequences))
    ):
        raise ValueError("audit archive sequence order mismatch")
    if sequences[0] != int(item.get("first_seq") or 0):
        raise ValueError("audit archive first sequence mismatch")
    if sequences[-1] != int(item.get("last_seq") or 0):
        raise ValueError("audit archive last sequence mismatch")
    if (typed_events[0].get("event_hash") or "") != item.get("first_event_hash"):
        raise ValueError("audit archive first hash mismatch")
    if (typed_events[-1].get("event_hash") or "") != item.get("last_event_hash"):
        raise ValueError("audit archive last hash mismatch")
    tenant_id = document.get("tenant_id")
    if any(event.get("tenant_id") != tenant_id for event in typed_events):
        raise ValueError("audit archive event tenant mismatch")
    for index, event in enumerate(typed_events):
        computed = event_hash(
            prev_hash=str(event.get("prev_hash") or ""),
            event_id=str(event["id"]),
            tenant_id=str(event["tenant_id"]),
            conversation_id=event.get("conversation_id"),
            request_id=event.get("request_id"),
            actor=str(event["actor"]),
            event_type=str(event["event_type"]),
            payload_json=str(event["payload_json"]),
            created_at=str(event["created_at"]),
        )
        if computed != event.get("event_hash"):
            raise ValueError(f"audit archive event hash mismatch at index {index}")
    return typed_events


def validate_audit_archive_stream(
    meta: Mapping[str, Any], lines: Iterable[bytes | str]
) -> Iterator[dict[str, Any]]:
    """Validate an object-store archive partition incrementally (42.3).

    ``lines`` is the canonical JSONL stream: line 0 is the document header
    (schema/tenant/cutoff/created_at), every following line one event row.
    Each event is validated lazily — per-event hash chain, running sequence
    and tenant checks — and the count/bound checks run once the stream ends,
    so memory stays O(1) regardless of archive size. Yields the validated
    event dicts; raises :class:`ValueError` on any mismatch, *before*
    yielding the offending event's successors.
    """
    stream = iter(lines)
    try:
        header_line = next(stream)
    except StopIteration as exc:
        raise ValueError("audit archive stream is empty") from exc
    header = json.loads(header_line.decode("utf-8") if isinstance(header_line, bytes) else header_line)
    if not isinstance(header, dict) or header.get("record") != "header" or header.get("schema") != 1:
        raise ValueError("audit archive schema mismatch")
    if meta.get("tenant_id") is not None and header.get("tenant_id") != meta["tenant_id"]:
        raise ValueError("audit archive tenant mismatch")
    for field in ("cutoff", "created_at"):
        if meta.get(field) is not None and header.get(field) != meta[field]:
            raise ValueError(f"audit archive {field} mismatch")

    expected_count = int(meta.get("event_count") or 0)
    required_fields = {
        "id",
        "tenant_id",
        "conversation_id",
        "request_id",
        "actor",
        "event_type",
        "payload_json",
        "created_at",
        "seq",
        "prev_hash",
        "event_hash",
    }
    seen = 0
    first_seq: int | None = None
    last_seq: int | None = None
    last_hash: str | None = None
    seen_ids: set[str] = set()
    for raw in stream:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        event = json.loads(text)
        if not isinstance(event, dict) or not required_fields.issubset(event):
            raise ValueError("audit archive event shape mismatch")
        if len(seen_ids) >= expected_count:
            raise ValueError("audit archive event count mismatch")
        event_id = str(event["id"])
        if event_id in seen_ids:
            raise ValueError("audit archive event id uniqueness mismatch")
        seen_ids.add(event_id)
        sequence = int(event.get("seq") or 0)
        if sequence <= 0 or (last_seq is not None and sequence <= int(last_seq or 0)):
            raise ValueError("audit archive sequence order mismatch")
        if first_seq is None:
            first_seq = sequence
            if sequence != int(meta.get("first_seq") or 0):
                raise ValueError("audit archive first sequence mismatch")
            if (event.get("event_hash") or "") != meta.get("first_event_hash"):
                raise ValueError("audit archive first hash mismatch")
        if event.get("tenant_id") != header.get("tenant_id"):
            raise ValueError("audit archive event tenant mismatch")
        computed = event_hash(
            prev_hash=str(event.get("prev_hash") or ""),
            event_id=event_id,
            tenant_id=str(event["tenant_id"]),
            conversation_id=event.get("conversation_id"),
            request_id=event.get("request_id"),
            actor=str(event["actor"]),
            event_type=str(event["event_type"]),
            payload_json=str(event["payload_json"]),
            created_at=str(event["created_at"]),
        )
        if computed != event.get("event_hash"):
            raise ValueError(f"audit archive event hash mismatch at index {seen}")
        last_seq = sequence
        last_hash = str(event.get("event_hash") or "")
        seen += 1
        yield event
    if seen != expected_count:
        raise ValueError("audit archive event count mismatch")
    if first_seq is None or first_seq != int(meta.get("first_seq") or 0):
        raise ValueError("audit archive first sequence mismatch")
    if last_seq != int(meta.get("last_seq") or 0):
        raise ValueError("audit archive last sequence mismatch")
    if last_hash != meta.get("last_event_hash"):
        raise ValueError("audit archive last hash mismatch")


__all__ = [
    "chain_head",
    "event_hash",
    "validate_audit_archive",
    "validate_audit_archive_stream",
    "verify_chain",
]
