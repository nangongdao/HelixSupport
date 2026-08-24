"""ROADMAP 18.3 synthetic pagination load test.

Seeds a conversation/message dataset and measures P50/P95 latency of the three
hot pagination shapes the §18.2d audit cares about:

- queue first page and deep offset pages (`list_conversations`, priority sort);
- queue keyset continuation (`cursor`);
- message keyset paging forward and backward (`list_messages`).

Scales:

- ``--scale smoke``: 2_000 conversations / 20_000 messages — seconds, default.
- ``--scale mid``:   20_000 conversations / 200_000 messages — ~1 min SQLite.
- ``--scale full``:  100_000 conversations / 1_000_000 messages (§18.3 target).

Runs against a throwaway SQLite file by default; set ``DATABASE_BACKEND=postgresql``
and ``DATABASE_URL`` to run the same harness against PostgreSQL (see
DEPLOYMENT.md → PostgreSQL Tuning). The seed writes rows directly in bulk
executemany batches so the measurement reflects the query path, not
write-path trigger amplification; SQLite triggers still fire on direct
inserts, so seeding also populates the message projections and FTS mirror —
the script then forces a linear FTS rebuild so the search benchmark reads a
complete mirror either way.

Usage:
    python scripts/pagination_load_test.py --scale smoke --pages 5
"""

from __future__ import annotations

import argparse
import gc
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import quantiles
from typing import Any, Callable

from app.database import Database, utc_now


# Search probes used by the ROADMAP 18.5 gate.  The selective marker is
# deliberately absent from every conversation id/name/ref and appears only in
# messages belonging to one conversation, so that benchmark cannot be
# accidentally satisfied by a cheap conversation-column match.
DENSE_MESSAGE_SEARCH_TERM = "message"
SELECTIVE_MESSAGE_SEARCH_TERM = "selectiveneedle"
SELECTIVE_MESSAGE_CONVERSATION_INDEX = 7


@dataclass(frozen=True)
class SeedConfig:
    conversations: int
    messages_per_conversation: int


@dataclass
class BenchStats:
    name: str
    samples_ms: list[float] = field(default_factory=list)

    def observe(self, started: float) -> None:
        self.samples_ms.append((time.perf_counter() - started) * 1000)

    def report(self) -> dict[str, float]:
        values = sorted(self.samples_ms)
        n = len(values)
        if n == 0:
            return {"n": 0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
        q = quantiles(values, n=100, method="inclusive")
        return {
            "n": n,
            "avg": round(sum(values) / n, 2),
            "p50": round(q[49], 2),
            "p95": round(q[94], 2),
            "max": round(q[98], 2),
        }


def seed_dataset(database: Database, config: SeedConfig) -> str:
    """Create ``config.conversations`` conversations with N messages each.

    Uses bulk executemany inserts; SQLite still fires the write triggers on
    direct inserts, but one ``executemany`` replaces thousands of per-statement
    round trips, which is what makes the seed tractable at 100k/1M.  The
    message ``seq`` column ends up filled by the rowid trigger (rowid is
    monotonic across the insert order, so per-conversation keyset ordering
    still holds).  Returns the tenant id.
    """
    tenant_id = "bench"
    database.ensure_tenant(tenant_id)
    now = utc_now()
    conversation_rows: list[tuple[Any, ...]] = []
    message_rows: list[tuple[Any, ...]] = []
    seq_counter = 0

    for conv_index in range(config.conversations):
        conv_id = f"conv_{conv_index:06d}"
        conversation_rows.append(
            (
                conv_id,
                tenant_id,
                f"Customer {conv_index}",
                None,
                "web",
                "open",
                "general",
                "normal",
                None,
                f"conversation {conv_index}",
                "[]",
                None,
                None,
                now,
                now,
                None,
                0,
                None,
            )
        )
        # The first conversation carries 1_000 messages so the keyset paging
        # benchmarks can walk real deep pages; the rest stay shallow.
        per_conversation = (
            max(1_000, config.messages_per_conversation)
            if conv_index == 0
            else config.messages_per_conversation
        )
        for message_index in range(per_conversation):
            selective_marker = (
                f" {SELECTIVE_MESSAGE_SEARCH_TERM}"
                if conv_index == SELECTIVE_MESSAGE_CONVERSATION_INDEX
                else ""
            )
            message_rows.append(
                (
                    f"msg_{conv_id}_{message_index}",
                    tenant_id,
                    conv_id,
                    None,
                    "customer" if message_index % 2 == 0 else "assistant",
                    f"C{conv_index}",
                    f"message body {conv_index}-{message_index}{selective_marker} " * 4,
                    "{}",
                    now,
                    seq_counter,
                    None,
                    None,
                )
            )
            seq_counter += 1

        # Commit in conversation batches to bound transaction size and WAL log.
        if len(conversation_rows) >= 200:
            _flush_seed(database, conversation_rows, message_rows)
            conversation_rows.clear()
            message_rows.clear()
            gc.collect()

    if conversation_rows:
        _flush_seed(database, conversation_rows, message_rows)
    return tenant_id


def _analyze_postgres(database: Database) -> None:
    """Refresh PostgreSQL planner statistics after a bulk seed.

    A freshly inserted million rows carry no up-to-date statistics, so the
    planner misestimates and picks seq scans that skew the §18.5 numbers (and
    add run-to-run variance).  Production PostgreSQL runs autovacuum/analyze,
    so refreshing here keeps the benchmark an honest baseline.  SQLite does not
    need it.
    """
    url = getattr(database, "url", "")
    if "postgresql" not in str(url):
        return
    import psycopg2

    connection = psycopg2.connect(str(url))
    try:
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")
        connection.commit()
    finally:
        connection.close()


def validate_message_search_probe(database: Database, tenant_id: str) -> int:
    """Prove the selective benchmark term exercises message search only.

    A previous probe used a zero-padded conversation index that was absent from
    message content but present in ``conversations.id``.  The resulting green
    timing never touched the message-search path.  Fail before measuring if a
    future seed change recreates that ambiguity.  Returns the message hit count
    for the benchmark log.
    """
    needle = f"%{SELECTIVE_MESSAGE_SEARCH_TERM}%"
    with database.connect() as connection:
        conversation_row = connection.execute(
            """SELECT COUNT(*) AS total FROM conversations
            WHERE tenant_id = ? AND (
                customer_name LIKE ? OR id LIKE ? OR customer_ref LIKE ?
            )""",
            (tenant_id, needle, needle, needle),
        ).fetchone()
        message_row = connection.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE tenant_id = ? AND content LIKE ?",
            (tenant_id, needle),
        ).fetchone()
    conversation_hits = int(conversation_row["total"] if conversation_row else 0)
    message_hits = int(message_row["total"] if message_row else 0)
    if conversation_hits != 0 or message_hits < 1:
        raise RuntimeError(
            "invalid selective message-search probe: expected zero conversation-field "
            f"hits and at least one message hit, got {conversation_hits} and {message_hits}"
        )
    return message_hits


def _populate_message_fts(database: Database) -> None:
    """Rebuild the message FTS mirror from the messages table.

    The synthetic seed writes message rows directly; the insert triggers fire
    and keep the mirror populated, but a replay-safe full rebuild guarantees
    the search benchmark searches a complete mirror regardless of trigger
    state.  The rebuild is delete + bulk insert (linear) — deliberately NOT a
    ``WHERE NOT EXISTS`` anti-join, which is O(n^2) because ``message_fts``
    declares ``message_id UNINDEXED`` (the FTS5 mirror has no usable join
    index; at the 1M-message ``full`` scale that pattern stalls for tens of
    minutes).  SQLite-only by design — on PostgreSQL the FTS mirror does not
    exist and the search benchmark measures the LIKE fallback instead.
    """
    if not getattr(database, "_message_fts_enabled", False):
        return
    with database.connect() as connection:
        connection.execute("DELETE FROM message_fts")
        connection.execute(
            """INSERT INTO message_fts(
                message_id, tenant_id, conversation_id, content, search_terms
            )
            SELECT m.id, m.tenant_id, m.conversation_id, m.content,
                   helix_search_terms(m.content)
            FROM messages m"""
        )


def _flush_seed(
    database: Database,
    conversation_rows: list[tuple[Any, ...]],
    message_rows: list[tuple[Any, ...]],
) -> None:
    with database.connect() as connection:
        if conversation_rows:
            connection.executemany(
                """INSERT INTO conversations
                (id, tenant_id, customer_name, customer_ref, channel, status, intent,
                 priority, sla_due_at, preview, labels_json, claimed_by, claim_expires_at,
                 created_at, updated_at, first_response_at, needs_response, waiting_since)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                conversation_rows,
            )
        if message_rows:
            connection.executemany(
                """INSERT INTO messages
                (id, tenant_id, conversation_id, turn_id, role, author, content,
                 metadata_json, created_at, seq, channel_message_id, reply_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                message_rows,
            )


def measure(name: str, fn: Callable[[], Any], rounds: int = 20) -> BenchStats:
    stats = BenchStats(name)
    for _ in range(rounds):
        started = time.perf_counter()
        fn()
        stats.observe(started)
    return stats


def run_benchmarks(
    database: Database, tenant_id: str, page_size: int, pages: int
) -> list[BenchStats]:
    results: list[BenchStats] = []

    # 1. Queue: current (offset) page vs a deep offset page.
    results.append(
        measure(
            "queue.offset.page0",
            lambda: database.list_conversations(
                tenant_id, sort="priority", limit=page_size, offset=0
            ),
        )
    )
    results.append(
        measure(
            "queue.offset.depth",
            lambda: database.list_conversations(
                tenant_id, sort="priority", limit=page_size, offset=pages * page_size
            ),
        )
    )

    # 2. Queue: keyset continuation across ``pages`` pages, resetting when the
    # cursor exhausts so the walk stays repeatable.
    def queue_keyset_walk() -> None:
        cursor: tuple[str, int | None, str | None, str, str] | None = None
        for _ in range(pages):
            rows = database.list_conversations(
                tenant_id, sort="priority", limit=page_size, cursor=cursor
            )
            if not rows:
                cursor = None
                continue
            last = rows[-1]
            cursor = (
                "priority",
                0 if str(last["priority"]) == "high" else 1,
                None,
                str(last["updated_at"]),
                str(last["id"]),
            )

    results.append(measure("queue.keyset.walk", queue_keyset_walk))

    # 3. Messages: keyset forward across pages on the seeded deep conversation.
    deep_conv = "conv_000000"
    newest = database.list_messages(tenant_id, deep_conv, limit=page_size)
    if newest:

        def messages_forward_walk() -> None:
            cursor: tuple[str, int] | None = None
            for _ in range(pages):
                page = database.list_messages(tenant_id, deep_conv, limit=page_size, cursor=cursor)
                if not page:
                    break
                last = page[-1]
                cursor = (str(last["created_at"]), int(last["seq"]))

        results.append(measure("messages.keyset.forward", messages_forward_walk))

        def messages_backward_page() -> None:
            database.list_messages(
                tenant_id,
                deep_conv,
                limit=page_size,
                cursor=(str(newest[-1]["created_at"]), int(newest[-1]["seq"])),
                before=True,
            )

        results.append(measure("messages.keyset.backward", messages_backward_page))

    # 4. Queue: FTS message search (ROADMAP 18.5 "消息搜索" gate).  Every
    # seeded message body carries a dense common term plus a marker that exists
    # only in conv_000007's messages.  The selective marker is absent from the
    # conversation columns, so both probes must exercise message search:
    #   - DENSE_MESSAGE_SEARCH_TERM matches every row (worst case).
    #   - SELECTIVE_MESSAGE_SEARCH_TERM matches only one conversation's rows.
    results.append(
        measure(
            "queue.search.fts.worst",
            lambda: database.list_conversations(
                tenant_id, search=DENSE_MESSAGE_SEARCH_TERM, sort="updated", limit=page_size
            ),
        )
    )
    results.append(
        measure(
            "queue.search.fts.selective",
            lambda: database.list_conversations(
                tenant_id,
                search=SELECTIVE_MESSAGE_SEARCH_TERM,
                sort="updated",
                limit=page_size,
            ),
        )
    )
    return results


def print_report(results: list[BenchStats]) -> None:
    print(f"{'benchmark':<28}{'n':>6}{'avg ms':>9}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}")
    for stats in results:
        report = stats.report()
        print(
            f"{stats.name:<28}{report['n']:>6}{report['avg']:>9.1f}"
            f"{report['p50']:>9.1f}{report['p95']:>9.1f}{report['max']:>9.1f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic pagination load test (ROADMAP 18.3)")
    parser.add_argument(
        "--scale",
        choices=("smoke", "mid", "full"),
        default="smoke",
        help="dataset size: smoke 2k/20k, mid 20k/200k, full 100k/1M (default: smoke)",
    )
    parser.add_argument("--pages", type=int, default=5, help="offset pages for depth reads")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--db", default="", help="SQLite file path (default: temp file)")
    parser.add_argument("--keep", action="store_true", help="keep the database file after the run")
    args = parser.parse_args()

    scale = {
        "smoke": SeedConfig(2_000, 10),
        "mid": SeedConfig(20_000, 10),
        "full": SeedConfig(100_000, 10),
    }[args.scale]
    print(
        f"seeding {scale.conversations:,} conversations / "
        f"{scale.conversations * scale.messages_per_conversation:,} messages "
        f"({args.scale}) ..."
    )

    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "pagination_bench.db"
    if os.environ.get("DATABASE_BACKEND", "sqlite").strip().lower() == "postgresql":
        from app.postgres_db import PostgresDatabase

        database: Database = PostgresDatabase(os.environ.get("DATABASE_URL", ""))
        print(f"backend: postgresql ({database.url})")
    else:
        database = Database(db_path)
        print(f"backend: sqlite ({db_path})")

    database.initialize()
    try:
        tenant_id = seed_dataset(database, scale)
        print("seeding complete; backfilling message FTS ...")
        _populate_message_fts(database)
        print("backfill complete; running benchmarks ...")
        _analyze_postgres(database)
        selective_hits = validate_message_search_probe(database, tenant_id)
        print(
            "selective message-search probe validated: "
            f"{selective_hits} message hits, 0 conversation-field hits"
        )
        results = run_benchmarks(database, tenant_id, args.page_size, args.pages)
        print_report(results)
    finally:
        database.close()
        if not args.keep and not args.db and "postgresql" not in str(db_path):
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
