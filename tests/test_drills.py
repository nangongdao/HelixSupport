"""Backup/restore disaster-recovery drills.

OPERATIONS.md calls for a quarterly restore drill and a load test.  The
plain round-trip in ``test_concurrency.py`` only checks that a backup opens;
these drills prove the two things that matter before trusting a backup in an
incident:

* An online backup taken while the database is under a live write load is a
  *consistent* snapshot — not just readable, but internally coherent
  (every conversation's ``message_count``/``preview`` must match its rows,
  and the integrity check must pass).  SQLite's online-backup API is the
  mechanism the deployment docs rely on for zero-downtime backups.
* A PostgreSQL backup can be restored into a database whose data was
  deliberately destroyed, and the restore recovers the data — not merely
  recreates empty tables.  This is the production restore drill.

The SQLite drill runs always.  The PostgreSQL drill requires
``HELIX_PG_INTEGRATION=1`` and ``DATABASE_URL`` pointing at a disposable
database, and pipes to ``pg_dump``/``pg_restore`` from a local PostgreSQL
installation (``PG_BIN``).
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from app.database import Database
from scripts.backup import backup_database
from scripts.restore import restore_database


def _integrity_consistent(db_path: Path) -> tuple[bool, list[str]]:
    """Check SQLite integrity and that summary columns match their tables."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    issues: list[str] = []

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        issues.append(f"integrity_check = {integrity!r}")

    try:
        conversations = conn.execute(
            "SELECT id, message_count, preview FROM conversations"
        ).fetchall()
        for conv in conversations:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
                (conv["id"],),
            ).fetchone()["n"]
            if conv["message_count"] != count:
                issues.append(
                    f"conversation {conv['id']}: message_count={conv['message_count']} "
                    f"but {count} messages"
                )
            if (conv["preview"] is None) != (count == 0):
                preview = conv["preview"] or "<none>"
                issues.append(
                    f"conversation {conv['id']}: preview present/value mismatch "
                    f"(preview={preview!r}, count={count})"
                )
    except sqlite3.OperationalError:
        pass  # table may legitimately not exist in a partially populated DB

    conn.close()
    return not issues, issues


class BackupConsistencyTests(unittest.TestCase):
    """The online backup must survive a concurrent writer and stay coherent."""

    def _seed_db(self, db: Database) -> str:
        db.ensure_tenant("drill-tenant")
        conv = db.create_conversation(
            "drill-tenant", "Drill Customer", "CUST-DRILL", "web", "admin", 120
        )
        return conv["id"]

    def _writer_until_done(self, db: Database, conv_id: str, stop: threading.Event) -> None:
        i = 0
        while not stop.is_set():
            db.add_message("drill-tenant", conv_id, "customer", "Customer", f"msg-{i}")
            i += 1

    def test_backup_is_consistent_under_write_load(self) -> None:
        """The online backup must snapshot consistently while writes run."""
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source = work / "live.db"
            db = Database(source)
            db.initialize()
            conv_id = self._seed_db(db)

            stop = threading.Event()
            writer = threading.Thread(
                target=self._writer_until_done, args=(db, conv_id, stop), daemon=True
            )
            writer.start()
            try:
                out = work / "backup"
                manifest = backup_database(source, out, compress=True)
            finally:
                stop.set()
                writer.join(timeout=5)
            db.close()

            backup_file = out / manifest["backup_file"]
            with gzip.open(backup_file, "rb") as f:
                restored = work / "snapshot.db"
                restored.write_bytes(f.read())

            ok, issues = _integrity_consistent(restored)
            self.assertTrue(ok, "backup snapshot is not consistent: " + "; ".join(issues))

            # The snapshot must contain the writes that finished before the
            # backup started, so it must have messages at all.
            conn = sqlite3.connect(str(restored))
            n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            conn.close()
            self.assertGreater(n, 0, "backup captured no messages under load")

    def test_restore_roundtrip_recovers_data(self) -> None:
        """A restore must reproduce the exact rows, not merely open."""
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source = work / "orig.db"
            db = Database(source)
            db.initialize()
            conv_id = self._seed_db(db)
            for i in range(3):
                db.add_message("drill-tenant", conv_id, "assistant", "A", f"m-{i}")
            db.audit("drill-tenant", conv_id, "admin", "drill.seed", {"n": 1})
            db.close()

            with tempfile.TemporaryDirectory() as out_dir:
                manifest = backup_database(source, Path(out_dir), compress=True)
                backup_file = Path(out_dir) / manifest["backup_file"]

                target = work / "recovered.db"
                result = restore_database(backup_file, target, manifest_path=None)
                self.assertEqual(result["status"], "restored")

            conn = sqlite3.connect(str(target))
            messages = conn.execute(
                "SELECT content FROM messages ORDER BY created_at, rowid"
            ).fetchall()
            conn.close()
            # The drill proves data recovery: every seeded message present,
            # exactly once, in insertion order.  ``rowid`` is the monotonic
            # insertion counter, so a restored transcript that scrambles the
            # sequence would fail here.
            self.assertEqual([m[0] for m in messages], ["m-0", "m-1", "m-2"])

    def test_tampered_backup_fails_checksum(self) -> None:
        """Restore must refuse a backup whose checksum no longer matches."""
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source = work / "orig.db"
            db = Database(source)
            db.initialize()
            db.close()

            out = work / "out"
            manifest = backup_database(source, out, compress=True)
            manifest_path = next(out.glob("*.manifest.json"))
            backup_file = out / manifest["backup_file"]
            data = bytearray(backup_file.read_bytes())
            data[10] ^= 0xFF  # corrupt a byte
            tampered = work / "tampered.db.gz"
            tampered.write_bytes(bytes(data))

            target = work / "restored.db"
            self.assertRaisesRegex(
                RuntimeError,
                "checksum",
                restore_database,
                tampered,
                target,
                manifest_path=manifest_path,
            )
            self.assertFalse(target.exists())


@unittest.skipUnless(
    os.getenv("HELIX_PG_INTEGRATION") and os.getenv("DATABASE_URL"),
    "Requires PostgreSQL instance and HELIX_PG_INTEGRATION=1",
)
class PostgresRestoreDrillTests(unittest.TestCase):
    """The production restore drill: destroy data, then bring it back."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.url = os.environ["DATABASE_URL"]
        cls.pg_bin = os.environ.get("PG_BIN", r"D:/PostgreSQL/18/bin")
        cls._reset()
        from app.postgres_db import PostgresDatabase

        cls.db = PostgresDatabase(cls.url)
        cls.db.initialize()
        cls.db.ensure_tenant("drill-tenant")
        cls.conv = cls.db.create_conversation(
            "drill-tenant", "PG Drill", "CUST-PGDRILL", "web", "admin", 120
        )
        for i in range(3):
            cls.db.add_message("drill-tenant", cls.conv["id"], "assistant", "A", f"pg-{i}")

    @classmethod
    def _reset(cls) -> None:
        import psycopg2

        connection = psycopg2.connect(cls.url)
        connection.autocommit = True
        connection.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        connection.close()

    @classmethod
    def _pg_dump(cls, output: Path) -> None:
        subprocess.run(
            [str(Path(cls.pg_bin) / "pg_dump"), "-f", str(output), cls.url],
            check=True,
            capture_output=True,
        )

    @classmethod
    def _pg_restore(cls, input_path: Path) -> None:
        subprocess.run(
            [str(Path(cls.pg_bin) / "psql"), "-d", cls.url, "-f", str(input_path)],
            check=True,
            capture_output=True,
        )

    def test_destroy_and_restore_recovers_data(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            dump = Path(work) / "drill.dump.sql"
            self._pg_dump(dump)

            # Destroy the data as an incident might.
            self._reset()
            count_after = self._count_rows()
            self.assertEqual(count_after, 0)

            # Restore.
            self._pg_restore(dump)

            self._verify_recovered()

    def _count_rows(self, table: str = "messages") -> int:
        import psycopg2

        connection = psycopg2.connect(self.url)
        try:
            cur = connection.cursor()
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            exists_row = cur.fetchone()
            assert exists_row is not None
            if not exists_row[0]:
                return 0
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count_row = cur.fetchone()
            assert count_row is not None
            return int(count_row[0])
        finally:
            connection.close()

    def _verify_recovered(self) -> None:
        import psycopg2

        connection = psycopg2.connect(self.url)
        try:
            cur = connection.cursor()
            cur.execute(
                "SELECT content FROM messages WHERE conversation_id = %s ORDER BY created_at, seq",
                (self.conv["id"],),
            )
            messages = cur.fetchall()
        finally:
            connection.close()
        # The monotonic ``seq`` column ties the restored transcript back to
        # insertion order, so the exact sequence must survive the restore.
        self.assertEqual([m[0] for m in messages], ["pg-0", "pg-1", "pg-2"])


if __name__ == "__main__":
    unittest.main()
