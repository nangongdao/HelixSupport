"""N/N+1 migration additivity gate (Gate C: rolling-upgrade coexistence).

``expand`` migrations commit to a rolling window: the N-1 binary keeps
reading the same database while the N binary rolls out, so an expand must
be a pure superset of the previous schema. The annotation gate
(``check_migration_phases``) validates labels; this suite validates the
*runtime effect* — every expand migration is executed against a scratch
backend and its schema delta must be additive (no dropped tables, no
dropped columns, no column type changes).
"""

from __future__ import annotations

import unittest

from app.migrations import (
    Migration,
    check_expand_additivity,
)


def _chain(*migrations: Migration) -> list[Migration]:
    return list(migrations)


class RealChainTests(unittest.TestCase):
    def test_every_expand_migration_is_additive(self) -> None:
        problems = check_expand_additivity()
        self.assertEqual(problems, [])


class RedLightTests(unittest.TestCase):
    def test_dropping_a_table_is_rejected(self) -> None:
        def good(connection) -> None:
            connection.execute("CREATE TABLE IF NOT EXISTS victim (id TEXT PRIMARY KEY, junk TEXT)")

        def bad(connection) -> None:
            connection.execute("DROP TABLE victim")

        problems = check_expand_additivity(
            _chain(
                Migration(1, "good", up=good, phase="expand"),
                Migration(2, "bad", up=bad, phase="expand"),
            )
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("dropped table victim", problems[0])

    def test_dropping_a_column_is_rejected(self) -> None:
        def good(connection) -> None:
            connection.execute("CREATE TABLE IF NOT EXISTS victim (id TEXT PRIMARY KEY, junk TEXT)")

        def bad(connection) -> None:
            connection.execute("ALTER TABLE victim DROP COLUMN junk")

        problems = check_expand_additivity(
            _chain(
                Migration(1, "good", up=good, phase="expand"),
                Migration(2, "bad", up=bad, phase="expand"),
            )
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("removed column junk from victim", problems[0])

    def test_table_rebuild_type_change_is_rejected(self) -> None:
        def good(connection) -> None:
            connection.execute("CREATE TABLE IF NOT EXISTS victim (id TEXT PRIMARY KEY, junk TEXT)")

        def rebuild(connection) -> None:
            connection.execute("DROP TABLE victim")
            connection.execute("CREATE TABLE victim (id TEXT PRIMARY KEY, junk INTEGER)")

        problems = check_expand_additivity(
            _chain(
                Migration(1, "good", up=good, phase="expand"),
                Migration(2, "rebuild", up=rebuild, phase="expand"),
            )
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("changed type of victim.junk", problems[0])

    def test_execution_failure_is_reported(self) -> None:
        def broken(connection) -> None:
            connection.execute("THIS IS NOT SQL")

        problems = check_expand_additivity(
            _chain(Migration(1, "broken", up=broken, phase="expand"))
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("execution failed on scratch backend", problems[0])


class GreenLightTests(unittest.TestCase):
    def test_pure_additive_chain_passes(self) -> None:
        def good(connection) -> None:
            connection.execute("CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY, name TEXT)")
            connection.execute("ALTER TABLE t ADD COLUMN extra TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_t_name ON t(name)")

        problems = check_expand_additivity(_chain(Migration(1, "good", up=good, phase="expand")))
        self.assertEqual(problems, [])

    def test_contract_migration_can_remove(self) -> None:
        def good(connection) -> None:
            connection.execute("CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY, legacy TEXT)")

        def contract(connection) -> None:
            connection.execute("ALTER TABLE t DROP COLUMN legacy")

        problems = check_expand_additivity(
            _chain(
                Migration(1, "good", up=good, phase="expand"),
                Migration(2, "contract", up=contract, phase="contract"),
            )
        )
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
