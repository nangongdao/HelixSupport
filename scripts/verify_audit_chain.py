"""Verify the audit hash chain and external evidence (Phase 28.3 + 41.3).

Reads all audit events in chain order and verifies each event's hash and
prev_hash link. A tampered historical row (edited payload, actor, or any
column) changes its hash and breaks every subsequent link.

Phase 41.3 (SEC-005) extends the check to the two external evidence layers:
the database ``audit_anchors`` frontier tips and the signed claims exported
to WORM / object-lock storage.  Each anchor must reference a sequence and
hash that match the recomputed chain, the newest DB anchor must equal the
chain head, and every WORM claim must carry a valid Ed25519 signature.

Usage:
    python scripts/verify_audit_chain.py [--db PATH] [--worm-dir DIR] \
        [--environment NAME] [--trusted-kids KID[,KID...]]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.audit_anchor import verify_signed_anchor
from app.audit_chain import chain_head, validate_audit_archive, verify_chain
from app.audit_gap import HIGH_RISK_EVENT_TYPES
from app.worm_store import DiskWormStore, WormIntegrityError, WormUnavailableError


def load_rows(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    hot_rows = connection.execute(
        "SELECT id, tenant_id, conversation_id, request_id, actor, event_type, "
        "payload_json, created_at, seq, prev_hash, event_hash "
        "FROM audit_events ORDER BY seq ASC, rowid ASC"
    ).fetchall()
    has_archive_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_archives'"
    ).fetchone()
    archived_rows = (
        connection.execute(
            "SELECT id, tenant_id, cutoff, archive_json, content_sha256, event_count, "
            "first_seq, last_seq, first_event_hash, last_event_hash, created_at "
            "FROM audit_archives ORDER BY first_seq ASC, id ASC"
        ).fetchall()
        if has_archive_table
        else []
    )
    rows_by_id: dict[str, dict] = {}
    ids_by_seq: dict[int, str] = {}

    def add_row(row: dict) -> None:
        event_id = str(row["id"])
        seq = int(row.get("seq", 0))
        if event_id in rows_by_id:
            raise ValueError(f"duplicate audit event id: {event_id}")
        if seq in ids_by_seq:
            raise ValueError(f"duplicate audit sequence {seq}: {ids_by_seq[seq]} and {event_id}")
        rows_by_id[event_id] = row
        ids_by_seq[seq] = event_id

    for archive in archived_rows:
        for row in validate_audit_archive(dict(archive)):
            add_row(row)
    for row in hot_rows:
        add_row(dict(row))
    return sorted(rows_by_id.values(), key=lambda row: (int(row.get("seq", 0)), str(row["id"])))


def load_db_anchors(connection: sqlite3.Connection) -> list[dict]:
    """Return ``audit_anchors`` frontier rows ordered by sequence, or [].

    The table appears in migration 31; a pre-upgrade database simply has no
    frontier tips, which is reported separately rather than treated as a
    tamper (the caller decides the exit code).
    """
    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_anchors'"
    ).fetchone()
    if not has_table:
        return []
    rows = connection.execute(
        "SELECT anchor_id, seq, chain_hash, event_type, reason, created_at "
        "FROM audit_anchors ORDER BY seq ASC, anchor_id ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def high_risk_events_in_rows(rows: list[dict]) -> list[dict]:
    """Subset of audit rows whose event type must carry a DB frontier anchor."""
    return [row for row in rows if str(row.get("event_type", "")) in HIGH_RISK_EVENT_TYPES]


def verify_db_anchors(rows: list[dict], *, rows_by_seq: dict[int, str], head_seq: int) -> list[str]:
    """Check each frontier tip against the recomputed chain.

    A tip must reference an existing local sequence, and its recorded hash
    must equal the recomputed hash of that sequence (a recomputed/edited chain
    no longer agrees with the persisted evidence tip).
    """
    problems: list[str] = []
    for row in rows:
        anchored_seq = int(row.get("seq") or 0)
        if anchored_seq > head_seq:
            problems.append(
                f"DB anchor {row.get('anchor_id')}: seq {anchored_seq} is ahead of the chain head {head_seq}"
            )
            continue
        if anchored_seq not in rows_by_seq:
            # A valid tip is always written right after its event; a sequence
            # gap here means evidence for a position the chain no longer has.
            problems.append(
                f"DB anchor {row.get('anchor_id')}: seq {anchored_seq} does not exist on the chain"
            )
            continue
        recomputed = rows_by_seq[anchored_seq]
        if row.get("chain_hash") != recomputed:
            problems.append(
                f"DB anchor {row.get('anchor_id')}: seq {anchored_seq} hash "
                f"{row.get('chain_hash')!r} != recomputed {recomputed!r}"
            )
    return problems


def latest_anchor_note(rows: list[dict], *, head_seq: int) -> str | None:
    """Human note on how far the evidence tips trail the chain head.

    Anchor rows are written at the tip of each high-risk mutation, so ordinary
    telemetry advancing past the newest tip is expected, not a tamper.  Only a
    tip *ahead* of the head means rows below it were lost.
    """
    if not rows:
        return None
    newnest_seq = int(max(rows, key=lambda row: int(row.get("seq") or 0)).get("seq") or 0)
    if newnest_seq < head_seq:
        return f"newest DB anchor pins seq {newnest_seq}; chain has advanced to seq {head_seq}"
    return None


def load_worm_anchors(directory: str) -> list[dict] | None:
    """Return signed claims from the WORM store, or None when not configured.

    ``None`` (no --worm-dir) means WORM verification was not requested; an
    empty list means the store exists but holds no anchors yet.  WORM tamper
    and unavailability are surfaced verbatim so ``main`` treats them as
    verification output rather than a crash.
    """
    if not directory:
        return None
    store = DiskWormStore(Path(directory))
    return list(store.read_all())


def verify_worm_claims(
    claims: list[dict],
    *,
    rows_by_seq: dict[int, str],
    head_seq: int,
    head_hash: str,
    environment: str,
    trusted_kids: tuple[str, ...],
) -> list[str]:
    """Check signature, kid trust, environment and agreement with the chain."""
    problems: list[str] = []
    sorted_claims = sorted(
        [
            c
            for c in claims
            if isinstance(c.get("last_seq"), int) and isinstance(c.get("last_hash"), str)
        ],
        key=lambda claim: int(claim["last_seq"]),
    )
    previous_seq: int | None = None
    for index, claim in enumerate(sorted_claims):
        label = str(claim.get("anchor_id") or f"worm_anchor_{index}")
        problems.extend(
            f"WORM {label}: {problem}"
            for problem in verify_signed_anchor(claim, trusted_kids=trusted_kids)
        )
        if str(claim.get("environment")) != environment:
            problems.append(f"WORM {label}: environment mismatch")
        anchored_seq = int(claim["last_seq"])
        anchored_hash = str(claim["last_hash"])
        if anchored_seq > head_seq:
            problems.append(
                f"WORM {label}: seq {anchored_seq} is ahead of the chain head {head_seq}"
            )
            continue
        if anchored_seq not in rows_by_seq:
            problems.append(f"WORM {label}: seq {anchored_seq} does not exist on the chain")
            continue
        if anchored_hash != rows_by_seq[anchored_seq]:
            problems.append(
                f"WORM {label}: seq {anchored_seq} hash {anchored_hash!r} != recomputed {rows_by_seq[anchored_seq]!r}"
            )
        if previous_seq is not None and anchored_seq <= previous_seq:
            problems.append(
                f"WORM {label}: seq {anchored_seq} does not advance past the previous anchor ({previous_seq})"
            )
        previous_seq = anchored_seq
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/support.db", help="SQLite database path")
    parser.add_argument("--worm-dir", default="", help="WORM anchor directory (skip when empty)")
    parser.add_argument("--environment", default="development", help="expected anchor environment")
    parser.add_argument(
        "--trusted-kids",
        default="",
        help="comma-separated kid allow-list (means trust-on-first-use)",
    )
    args = parser.parse_args()
    path = Path(args.db)
    if not path.exists():
        print(f"database not found: {path}", file=sys.stderr)
        return 2
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = load_rows(connection)
        db_anchors = load_db_anchors(connection)
        worm_claims = load_worm_anchors(args.worm_dir)
    except (
        ValueError,
        sqlite3.DatabaseError,
        OSError,
        WormIntegrityError,
        WormUnavailableError,
    ) as exc:
        print(f"TAMPER: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    if not rows:
        print("no audit events to verify")
        return 0
    problems = verify_chain(rows)
    high_risk_rows = high_risk_events_in_rows(rows)
    if db_anchors and high_risk_rows:
        for event in high_risk_rows:
            seq = int(event.get("seq") or 0)
            if not any(int(row.get("seq") or 0) == seq for row in db_anchors):
                problems.append(f"missing DB anchor for event {event.get('id')} at seq {seq}")
    head = chain_head(rows) or ""
    rows_by_seq = {int(row["seq"]): str(row.get("event_hash") or "") for row in rows}
    head_seq = max(rows_by_seq) if rows_by_seq else 0
    if not problems:
        problems = verify_db_anchors(db_anchors, rows_by_seq=rows_by_seq, head_seq=head_seq)
    if worm_claims is not None:
        trusted_kids = tuple(kid.strip() for kid in args.trusted_kids.split(",") if kid.strip())
        problems += verify_worm_claims(
            worm_claims,
            rows_by_seq=rows_by_seq,
            head_seq=head_seq,
            head_hash=head,
            environment=args.environment,
            trusted_kids=trusted_kids,
        )
    notes: list[str] = []
    if not db_anchors:
        if high_risk_rows:
            problems.append("no DB anchors have been recorded")
        else:
            notes.append("no DB anchors recorded (no high-risk events on the chain)")
    if problems:
        for problem in problems:
            print(f"TAMPER: {problem}", file=sys.stderr)
        return 1
    report = [f"audit chain intact: {len(rows)} events, head {head[:16]}..."]
    if db_anchors:
        report.append(f"DB anchors match: {len(db_anchors)} tips on chain")
    if worm_claims is not None:
        report.append(f"WORM anchors verified: {len(worm_claims)} claims signed")
    elif not args.worm_dir:
        report.append("WORM anchors not checked (no --worm-dir)")
    report.extend(notes)
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
