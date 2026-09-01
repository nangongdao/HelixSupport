"""ROADMAP 18.3 cold-tier reverse-lookup cost probe (archived search).

The queue-search fast path (``_query_conversations_windowed``), the FTS mirror,
the pg_trgm index and the ``updated``-sort conversation index all cover the HOT
tables only; ``archived=True`` deliberately walks the LIKE/CTE fallback over
the ``*_archive`` tables (their FTS mirror is evicted to bound volume).  This
probe quantifies that cold reverse-lookup cost at the §18.5 dataset scale so
the scale cost flagged in docs/CAPACITY §3.4 can be closed with a number.

Seeds the same conversation/message dataset as
``scripts/pagination_load_test.py``, bulk-copies the whole dataset into the
archive tier (one transaction per table — mirrors the per-row move's row
shape), then measures:

- ``queue.archive.list.page0``: cold queue listing without search;
- ``queue.archive.search.worst``: dense term matching every archived message;
- ``queue.archive.search.selective``: a marker present only in one
  conversation's messages (and absent from the archive conversation columns).

Runs against a throwaway SQLite file by default; set ``DATABASE_BACKEND=
postgresql`` and ``DATABASE_URL`` to measure the production backend (same as
the hot harness).  The full-scale (100k/1M) archive probes can be slow —
``--rounds`` scales the sample count.

Usage:
    python scripts/archive_search_load_test.py --scale smoke
    DATABASE_BACKEND=postgresql DATABASE_URL=... \\
        python scripts/archive_search_load_test.py --scale full --rounds 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._console import use_utf8_console  # noqa: E402

from scripts.pagination_load_test import (  # noqa: E402
    DENSE_MESSAGE_SEARCH_TERM,
    SELECTIVE_MESSAGE_SEARCH_TERM,
    BenchStats,
    SeedConfig,
    _analyze_postgres,
    measure,
    seed_dataset,
)
from app.database import Database, utc_now  # noqa: E402

# 42.3 REL-002: fixed cold-data tiers. The tier names are relative to the
# §18.5 hot baseline (2k conversations / 20k messages): the archive tier is
# seeded at 10× / 100× that volume so capacity numbers stay comparable
# across runs and backends.
ARCHIVE_TIERS = {
    "10x": SeedConfig(20_000, 10),
    "100x": SeedConfig(200_000, 10),
}

_ARCHIVE_TABLES = (
    "conversations_archive",
    "messages_archive",
    "conversation_labels_archive",
    "feedback_archive",
)


def measure_with_memory(
    name: str, fn, rounds: int
) -> tuple[BenchStats, dict[str, float]]:
    """Measure latency (existing harness) plus peak allocated memory."""
    stats = BenchStats(name=name)
    tracemalloc.start()
    try:
        for _ in range(rounds):
            started = time.perf_counter()
            fn()
            stats.observe(started)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return stats, {"peak_alloc_mb": round(peak / (1024 * 1024), 3)}


def archive_table_bytes(database: Database) -> dict[str, int]:
    """Byte footprint of the archive tier (dbstat when available)."""
    try:
        with database.connect() as connection:
            row = connection.execute(
                f"""SELECT SUM(pgsize) AS total FROM dbstat
                WHERE name IN ({','.join('?' for _ in _ARCHIVE_TABLES)})""",
                _ARCHIVE_TABLES,
            ).fetchone()
        total = int(row["total"]) if row and row["total"] else 0
        if total:
            return {"archive_bytes": total, "source": "dbstat"}
    except Exception:
        pass
    # Fallback: whole-file size (upper bound including hot tables/FTS).
    path = getattr(database, "path", None)
    if path:
        return {"archive_bytes": int(Path(str(path)).stat().st_size), "source": "file_size"}
    return {"archive_bytes": 0, "source": "unavailable"}


def archive_all(database: Database, tenant_id: str) -> None:
    """Bulk-copy the seeded hot dataset into the cold tier.

    One transaction per table.  Mirrors
    ``DatabaseArchiveMixin._move_conversation_to_archive``'s row shape so the
    probe reads a full-strength archive tier; the per-row move is deliberately
    not used because it is the WRITE path (not the lookup under measurement) and
    would take hours at the full scale.  The hot rows are left in place: the
    archive probes read the cold tables only, and the hot backlog is the steady
    state's future archival queue.
    """
    now = utc_now()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO conversations_archive
            (id, tenant_id, customer_name, customer_ref, channel, status, intent,
             assigned_agent, priority, handoff_reason, sla_due_at, last_confidence,
             version, preview, message_count, last_message_at, labels_json,
             claimed_by, claimed_at, claim_expires_at, needs_response, waiting_since,
             first_response_at, created_at, updated_at, resolved_at, archived_at)
            SELECT id, tenant_id, customer_name, customer_ref, channel, status, intent,
                   assigned_agent, priority, handoff_reason, sla_due_at, last_confidence,
                   version, preview, message_count, last_message_at, labels_json,
                   claimed_by, claimed_at, claim_expires_at, needs_response, waiting_since,
                   first_response_at, created_at, updated_at, resolved_at, ?
            FROM conversations WHERE tenant_id = ?""",
            (now, tenant_id),
        )
        connection.execute(
            """INSERT INTO messages_archive
            (id, tenant_id, conversation_id, turn_id, role, author, content,
             metadata_json, created_at, seq, channel_message_id, reply_to)
            SELECT id, tenant_id, conversation_id, turn_id, role, author, content,
                   metadata_json, created_at, seq, channel_message_id, reply_to
            FROM messages WHERE tenant_id = ?""",
            (tenant_id,),
        )
        connection.execute(
            """INSERT INTO conversation_labels_archive
            (tenant_id, conversation_id, label, created_by, created_at)
            SELECT tenant_id, conversation_id, label, created_by, created_at
            FROM conversation_labels WHERE tenant_id = ?""",
            (tenant_id,),
        )


def validate_archive_search_probe(database: Database, tenant_id: str) -> int:
    """Prove the selective archive probe exercises message search only.

    Same contract as the hot ``validate_message_search_probe`` but against the
    cold tier: zero hits in the archive conversation columns and at least one
    in the archive message bodies.  Fail before measuring if a seed change
    recreates the ambiguity the hot probe already guards against.
    """
    needle = f"%{SELECTIVE_MESSAGE_SEARCH_TERM}%"
    with database.connect() as connection:
        conversation_row = connection.execute(
            """SELECT COUNT(*) AS total FROM conversations_archive
            WHERE tenant_id = ? AND (
                customer_name LIKE ? OR id LIKE ? OR customer_ref LIKE ?
            )""",
            (tenant_id, needle, needle, needle),
        ).fetchone()
        message_row = connection.execute(
            "SELECT COUNT(*) AS total FROM messages_archive WHERE tenant_id = ? AND content LIKE ?",
            (tenant_id, needle),
        ).fetchone()
    conversation_hits = int(conversation_row["total"] if conversation_row else 0)
    message_hits = int(message_row["total"] if message_row else 0)
    if conversation_hits != 0 or message_hits < 1:
        raise RuntimeError(
            "invalid archive selective probe: expected zero conversation-field hits "
            f"and at least one message hit, got {conversation_hits} and {message_hits}"
        )
    return message_hits


def run_benchmarks(
    database: Database, tenant_id: str, page_size: int, rounds: int
) -> list[BenchStats]:
    results: list[BenchStats] = []

    # Cold queue listing without search (baseline for the lookup overhead).
    results.append(
        measure(
            "queue.archive.list.page0",
            lambda: database.list_conversations(
                tenant_id, sort="priority", limit=page_size, archived=True
            ),
            rounds=rounds,
        )
    )
    # Cold reverse-lookup: every archived message carries the dense term.
    results.append(
        measure(
            "queue.archive.search.worst",
            lambda: database.list_conversations(
                tenant_id,
                search=DENSE_MESSAGE_SEARCH_TERM,
                sort="updated",
                limit=page_size,
                archived=True,
            ),
            rounds=rounds,
        )
    )
    # Cold reverse-lookup: the marker exists in exactly one conversation's
    # archived messages and nowhere in the archive conversation columns.
    results.append(
        measure(
            "queue.archive.search.selective",
            lambda: database.list_conversations(
                tenant_id,
                search=SELECTIVE_MESSAGE_SEARCH_TERM,
                sort="updated",
                limit=page_size,
                archived=True,
            ),
            rounds=rounds,
        )
    )
    return results


def print_report(results: list[BenchStats]) -> None:
    print(f"{'benchmark':<30}{'n':>6}{'avg ms':>9}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}")
    for stats in results:
        report = stats.report()
        print(
            f"{stats.name:<30}{report['n']:>6}{report['avg']:>9.1f}"
            f"{report['p50']:>9.1f}{report['p95']:>9.1f}{report['max']:>9.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Archived-search reverse-lookup cost probe (ROADMAP 18.3 / 42.3)"
    )
    scale_group = parser.add_mutually_exclusive_group()
    scale_group.add_argument(
        "--scale",
        choices=("smoke", "mid", "full"),
        default=None,
        help="dataset size: smoke 2k/20k, mid 20k/200k, full 100k/1M",
    )
    scale_group.add_argument(
        "--tier",
        choices=sorted(ARCHIVE_TIERS),
        default=None,
        help="42.3 fixed cold-data tier: 10x = 20k/200k, 100x = 200k/2M",
    )
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument(
        "--rounds",
        type=int,
        default=20,
        help="samples per benchmark (default: 20; lower for slow full-scale runs)",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="skip seeding/archiving (re-measure an existing populated archive tier)",
    )
    parser.add_argument("--json", help="write the machine-readable report to this path")
    args = parser.parse_args()

    if args.tier:
        scale = ARCHIVE_TIERS[args.tier]
        tier_name = args.tier
    else:
        scale_name = args.scale or "smoke"
        scale = {
            "smoke": SeedConfig(2_000, 10),
            "mid": SeedConfig(20_000, 10),
            "full": SeedConfig(100_000, 10),
        }[scale_name]
        tier_name = scale_name
    print(
        f"seeding {scale.conversations:,} conversations / "
        f"{scale.conversations * scale.messages_per_conversation:,} messages "
        f"({tier_name}) ..."
    )

    db_path = (
        Path(args.db) if getattr(args, "db", "") else Path(tempfile.mkdtemp()) / "archive_bench.db"
    )
    if os.environ.get("DATABASE_BACKEND", "sqlite").strip().lower() == "postgresql":
        from app.postgres_db import PostgresDatabase

        database: Database = PostgresDatabase(os.environ.get("DATABASE_URL", ""))
        print(f"backend: postgresql ({database.url})")
    else:
        database = Database(db_path)
        print(f"backend: sqlite ({db_path})")

    database.initialize()
    tenant_id = "bench"
    try:
        if not args.no_seed:
            tenant_id = seed_dataset(database, scale)
            print("seeding complete; copying dataset into the archive tier ...")
            archive_all(database, tenant_id)
            print("archive copy complete")
        else:
            print(f"no-seed mode: benchmarking existing archive tier on {db_path}")
        print("refreshing planner statistics ...")
        _analyze_postgres(database)
        selective_hits = validate_archive_search_probe(database, tenant_id)
        print(
            "archive selective probe validated: "
            f"{selective_hits} message hits, 0 conversation-field hits"
        )

        results: list[BenchStats] = []
        memory: dict[str, dict[str, float]] = {}
        for name, fn in (
            (
                "queue.archive.list.page0",
                lambda: database.list_conversations(
                    tenant_id, sort="priority", limit=args.page_size, archived=True
                ),
            ),
            (
                "queue.archive.search.worst",
                lambda: database.list_conversations(
                    tenant_id,
                    search=DENSE_MESSAGE_SEARCH_TERM,
                    sort="updated",
                    limit=args.page_size,
                    archived=True,
                ),
            ),
            (
                "queue.archive.search.selective",
                lambda: database.list_conversations(
                    tenant_id,
                    search=SELECTIVE_MESSAGE_SEARCH_TERM,
                    sort="updated",
                    limit=args.page_size,
                    archived=True,
                ),
            ),
        ):
            stats, mem = measure_with_memory(name, fn, args.rounds)
            results.append(stats)
            memory[name] = mem
        print_report(results)

        if args.json:
            footprint = archive_table_bytes(database)
            report = {
                "tier": tier_name,
                "conversations": scale.conversations,
                "messages": scale.conversations * scale.messages_per_conversation,
                "page_size": args.page_size,
                "rounds": args.rounds,
                "benchmarks": {stats.name: stats.report() for stats in results},
                "memory": memory,
                "footprint": footprint,
                "generated_at": utc_now(),
                "target": "archive query p95 < 2000ms on an indexed time window (42.3)",
            }
            Path(args.json).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"report written: {args.json}")
    finally:
        database.close()
        if "postgresql" not in str(db_path):
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
