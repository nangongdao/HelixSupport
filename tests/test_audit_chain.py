"""Audit hash chain verification (Phase 28.3) — direct unit coverage.

verify_chain / chain_head / validate_audit_archive /
validate_audit_archive_stream are the tamper-evidence core of the audit
trail: every modification of a historical row must be detected. These tests
drive each failure branch with a purpose-built chain so the fail-closed
behavior is pinned (a mismatch raises/returns a problem, never silently
passes).
"""

from __future__ import annotations

import hashlib
import json
import unittest
from typing import Any

from app.audit_chain import (
    chain_head,
    event_hash,
    validate_audit_archive,
    validate_audit_archive_stream,
    verify_chain,
)


def _event(
    index: int,
    *,
    tenant_id: str = "demo",
    event_type: str = "conversation.created",
    prev_hash: str = "",
) -> dict[str, Any]:
    event = {
        "id": f"evt_{index}",
        "tenant_id": tenant_id,
        "conversation_id": None,
        "request_id": None,
        "actor": "admin",
        "event_type": event_type,
        "payload_json": json.dumps({"index": index}),
        "created_at": f"2026-09-01T00:00:{index:02d}Z",
        "seq": index + 1,
        "prev_hash": prev_hash,
        "event_hash": "",
    }
    event["event_hash"] = event_hash(
        prev_hash=event["prev_hash"],
        event_id=event["id"],
        tenant_id=event["tenant_id"],
        conversation_id=event["conversation_id"],
        request_id=event["request_id"],
        actor=event["actor"],
        event_type=event["event_type"],
        payload_json=event["payload_json"],
        created_at=event["created_at"],
    )
    return event


def _chain(count: int = 3, **kwargs: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    prev = ""
    for index in range(count):
        event = _event(index, prev_hash=prev, **kwargs)
        events.append(event)
        prev = event["event_hash"]
    return events


def _archive_document(events: list[dict[str, Any]]) -> dict[str, Any]:
    doc = {
        "schema": 1,
        "tenant_id": events[0]["tenant_id"],
        "cutoff": "2026-09-01T00:00:00Z",
        "created_at": "2026-09-01T01:00:00Z",
        "events": events,
    }
    archive_json = json.dumps(doc, sort_keys=True)
    return {
        "archive_json": archive_json,
        "content_sha256": hashlib.sha256(archive_json.encode("utf-8")).hexdigest(),
        "tenant_id": "demo",
        "cutoff": "2026-09-01T00:00:00Z",
        "created_at": "2026-09-01T01:00:00Z",
        "event_count": len(events),
        "first_seq": events[0]["seq"],
        "last_seq": events[-1]["seq"],
        "first_event_hash": events[0]["event_hash"],
        "last_event_hash": events[-1]["event_hash"],
    }


class EventHashTests(unittest.TestCase):
    def test_event_hash_is_deterministic(self) -> None:
        first = event_hash(
            prev_hash="",
            event_id="evt_1",
            tenant_id="demo",
            conversation_id=None,
            request_id=None,
            actor="admin",
            event_type="conversation.created",
            payload_json="{}",
            created_at="2026-09-01T00:00:00Z",
        )
        second = event_hash(
            prev_hash="",
            event_id="evt_1",
            tenant_id="demo",
            conversation_id=None,
            request_id=None,
            actor="admin",
            event_type="conversation.created",
            payload_json="{}",
            created_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(first, second)


class VerifyChainTests(unittest.TestCase):
    def test_intact_chain_returns_no_problems(self) -> None:
        self.assertEqual(verify_chain(_chain(3)), [])

    def test_single_row_chain_is_valid(self) -> None:
        self.assertEqual(verify_chain(_chain(1)), [])

    def test_tampered_hash_is_detected(self) -> None:
        rows = _chain(3)
        rows[1]["event_hash"] = "f" * 64
        problems = verify_chain(rows)
        self.assertEqual(len(problems), 1)
        self.assertIn("hash mismatch", problems[0])

    def test_tampered_prev_hash_is_detected(self) -> None:
        rows = _chain(3)
        rows[2]["prev_hash"] = "f" * 64
        problems = verify_chain(rows)
        self.assertIn("prev_hash mismatch", problems[0])

    def test_chain_head_returns_last_hash(self) -> None:
        rows = _chain(2)
        self.assertEqual(chain_head(rows), rows[-1]["event_hash"])

    def test_chain_head_empty_is_none(self) -> None:
        self.assertIsNone(chain_head([]))


class ValidateArchiveTests(unittest.TestCase):
    def test_valid_archive_returns_events(self) -> None:
        events = _chain(3)
        result = validate_audit_archive(_archive_document(events))
        self.assertEqual(len(result), 3)

    def test_archive_hash_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["content_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            validate_audit_archive(item)

    def test_archive_invalid_json_raises(self) -> None:
        # content_sha256 must still match so the check reaches the JSON parse.
        item = _archive_document(_chain(2))
        archive_json = "{not json"
        item["archive_json"] = archive_json
        item["content_sha256"] = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "JSON is invalid"):
            validate_audit_archive(item)

    def test_archive_non_dict_document_raises(self) -> None:
        item = _archive_document(_chain(2))
        archive_json = json.dumps(["not", "a", "dict"])
        item["archive_json"] = archive_json
        item["content_sha256"] = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            validate_audit_archive(item)

    def test_archive_schema_mismatch_raises(self) -> None:
        events = _chain(2)
        doc = {
            "schema": 2,
            "tenant_id": events[0]["tenant_id"],
            "cutoff": "2026-09-01T00:00:00Z",
            "created_at": "2026-09-01T01:00:00Z",
            "events": events,
        }
        archive_json = json.dumps(doc, sort_keys=True)
        item = _archive_document(events)
        item["archive_json"] = archive_json
        item["content_sha256"] = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            validate_audit_archive(item)

    def test_archive_tenant_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["tenant_id"] = "acme"
        with self.assertRaisesRegex(ValueError, "tenant mismatch"):
            validate_audit_archive(item)

    def test_archive_event_count_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["event_count"] = 99
        with self.assertRaisesRegex(ValueError, "event count mismatch"):
            validate_audit_archive(item)

    def test_archive_sequence_order_mismatch_raises(self) -> None:
        events = _chain(2)
        events[0]["seq"], events[1]["seq"] = events[1]["seq"], events[0]["seq"]
        item = _archive_document(events)
        with self.assertRaisesRegex(ValueError, "sequence order mismatch"):
            validate_audit_archive(item)

    def test_archive_first_seq_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["first_seq"] = 99
        with self.assertRaisesRegex(ValueError, "first sequence mismatch"):
            validate_audit_archive(item)

    def test_archive_event_hash_mismatch_raises(self) -> None:
        events = _chain(2)
        events[0]["event_hash"] = "f" * 64
        item = _archive_document(events)
        with self.assertRaisesRegex(ValueError, "event hash mismatch"):
            validate_audit_archive(item)

    def test_archive_empty_events_raises(self) -> None:
        events = _chain(1)
        doc = {
            "schema": 1,
            "tenant_id": events[0]["tenant_id"],
            "cutoff": "2026-09-01T00:00:00Z",
            "created_at": "2026-09-01T01:00:00Z",
            "events": [],
        }
        archive_json = json.dumps(doc, sort_keys=True)
        item = _archive_document(events)
        item["archive_json"] = archive_json
        item["content_sha256"] = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
        item["event_count"] = 0
        with self.assertRaisesRegex(ValueError, "events are missing"):
            validate_audit_archive(item)

    def test_archive_cutoff_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["cutoff"] = "2025-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "cutoff mismatch"):
            validate_audit_archive(item)

    def test_archive_non_dict_event_raises(self) -> None:
        doc = {
            "schema": 1,
            "tenant_id": "demo",
            "cutoff": "2026-09-01T00:00:00Z",
            "created_at": "2026-09-01T01:00:00Z",
            "events": ["not-a-dict"],
        }
        archive_json = json.dumps(doc, sort_keys=True)
        item = _archive_document(_chain(1))
        item["archive_json"] = archive_json
        item["content_sha256"] = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "event shape mismatch"):
            validate_audit_archive(item)

    def test_archive_missing_required_field_raises(self) -> None:
        events = _chain(1)
        del events[0]["actor"]
        doc = {
            "schema": 1,
            "tenant_id": "demo",
            "cutoff": "2026-09-01T00:00:00Z",
            "created_at": "2026-09-01T01:00:00Z",
            "events": events,
        }
        archive_json = json.dumps(doc, sort_keys=True)
        item = _archive_document(_chain(1))
        item["archive_json"] = archive_json
        item["content_sha256"] = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
        with self.assertRaisesRegex(ValueError, "event shape mismatch"):
            validate_audit_archive(item)

    def test_archive_duplicate_event_id_raises(self) -> None:
        events = _chain(2)
        events[1]["id"] = events[0]["id"]
        item = _archive_document(events)
        with self.assertRaisesRegex(ValueError, "id uniqueness"):
            validate_audit_archive(item)

    def test_archive_last_seq_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["last_seq"] = 99
        with self.assertRaisesRegex(ValueError, "last sequence mismatch"):
            validate_audit_archive(item)

    def test_archive_last_hash_mismatch_raises(self) -> None:
        item = _archive_document(_chain(2))
        item["last_event_hash"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "last hash mismatch"):
            validate_audit_archive(item)

    def test_archive_event_tenant_mismatch_raises(self) -> None:
        events = _chain(2)
        events[1]["tenant_id"] = "acme"
        item = _archive_document(events)
        with self.assertRaisesRegex(ValueError, "event tenant mismatch"):
            validate_audit_archive(item)


class ValidateArchiveStreamTests(unittest.TestCase):
    def _stream_lines(self, events: list[dict[str, Any]]) -> list[str]:
        header = {
            "record": "header",
            "schema": 1,
            "tenant_id": events[0]["tenant_id"],
            "cutoff": "2026-09-01T00:00:00Z",
            "created_at": "2026-09-01T01:00:00Z",
        }
        lines = [json.dumps(header)]
        lines.extend(json.dumps(event) for event in events)
        return lines

    def test_valid_stream_yields_events(self) -> None:
        events = _chain(3)
        meta = _archive_document(events)
        result = list(validate_audit_archive_stream(meta, self._stream_lines(events)))
        self.assertEqual(len(result), 3)

    def test_empty_stream_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            list(validate_audit_archive_stream({}, []))

    def test_stream_duplicate_id_raises(self) -> None:
        events = _chain(2)
        events[1]["id"] = events[0]["id"]
        meta = _archive_document(events)
        with self.assertRaisesRegex(ValueError, "id uniqueness"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_count_mismatch_raises(self) -> None:
        events = _chain(2)
        meta = _archive_document(events)
        meta["event_count"] = 5
        with self.assertRaisesRegex(ValueError, "event count mismatch"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_final_count_mismatch_raises(self) -> None:
        # Fewer lines than the declared count: the mismatch surfaces at the
        # stream end, after every event has been consumed.
        events = _chain(3)
        meta = _archive_document(events)
        meta["event_count"] = 2
        with self.assertRaisesRegex(ValueError, "event count mismatch"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_header_schema_mismatch_raises(self) -> None:
        events = _chain(2)
        meta = _archive_document(events)
        lines = self._stream_lines(events)
        lines[0] = lines[0].replace('"schema": 1', '"schema": 2')
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            list(validate_audit_archive_stream(meta, lines))

    def test_stream_tenant_mismatch_raises(self) -> None:
        events = _chain(2)
        meta = _archive_document(events)
        meta["tenant_id"] = "acme"
        with self.assertRaisesRegex(ValueError, "tenant mismatch"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_sequence_order_mismatch_raises(self) -> None:
        events = _chain(2)
        events[1]["seq"] = events[0]["seq"]
        meta = _archive_document(events)
        with self.assertRaisesRegex(ValueError, "sequence order mismatch"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_first_seq_mismatch_raises(self) -> None:
        events = _chain(2)
        meta = _archive_document(events)
        meta["first_seq"] = 99
        with self.assertRaisesRegex(ValueError, "first sequence mismatch"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_events_exceeding_count_raises(self) -> None:
        # More events than declared: the guard trips before consuming all.
        events = _chain(3)
        meta = _archive_document(events)
        meta["event_count"] = 2
        with self.assertRaisesRegex(ValueError, "event count mismatch"):
            list(validate_audit_archive_stream(meta, self._stream_lines(events)))

    def test_stream_raises_before_yielding_successors(self) -> None:
        # A tampered middle event must raise before its successor is yielded
        # — the O(1)-memory fail-closed contract of the streaming verifier.
        events = _chain(3)
        events[1]["event_hash"] = "f" * 64
        meta = _archive_document(events)
        yielded = []
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            for event in validate_audit_archive_stream(meta, self._stream_lines(events)):
                yielded.append(event)
        self.assertEqual(len(yielded), 1)


if __name__ == "__main__":
    unittest.main()
