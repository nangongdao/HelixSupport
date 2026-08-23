"""Phase 42.2: migration discipline gate — expand/migrate/contract.

The registry must stay chain-contiguous (Phase 27.4) and, from version 33
on, every migration declares a release phase so rolling N/N+1 deployments
stay safe: ``expand`` is additive DDL, ``migrate`` backfills data, and
``contract`` removes schema only after the expand landed everywhere.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.migrations import (
    PHASE_REQUIRED_FROM_VERSION,
    Migration,
    all_migrations,
    check_migration_phases,
    pending_migration_versions,
    verify_migration_chain,
)


def _mig(version: int, phase: str | None) -> Migration:
    return Migration(
        version=version,
        description=f"synthetic {version}",
        up=lambda connection: None,
        down=None,
        phase=phase,
    )


class RegistryGateTests(unittest.TestCase):
    def test_registered_chain_passes_gate(self) -> None:
        migrations = all_migrations()
        self.assertGreaterEqual(len(migrations), 32)
        self.assertEqual(verify_migration_chain(migrations), [])
        self.assertEqual(check_migration_phases(migrations), [])

    def test_grandfathered_versions_need_no_phase(self) -> None:
        # v1..v32 predate the discipline; a synthetic v33 without a phase fails.
        legacy = [_mig(v, None) for v in range(1, 33)]
        self.assertEqual(check_migration_phases(legacy), [])
        problems = check_migration_phases(legacy + [_mig(33, None)])
        self.assertEqual(len(problems), 1)
        self.assertIn("phase is required", problems[0])
        self.assertIn(str(PHASE_REQUIRED_FROM_VERSION), problems[0])


class PhaseRuleTests(unittest.TestCase):
    def test_unknown_phase_rejected(self) -> None:
        problems = check_migration_phases([_mig(1, "extend")])
        self.assertEqual(len(problems), 1)
        self.assertIn("unknown phase", problems[0])

    def test_contract_requires_earlier_expand(self) -> None:
        good = [
            _mig(1, None),
            _mig(2, "expand"),
            _mig(3, "migrate"),
            _mig(4, "contract"),
        ]
        self.assertEqual(check_migration_phases(good), [])

        bad = [_mig(1, None), _mig(2, "contract")]
        problems = check_migration_phases(bad)
        self.assertEqual(len(problems), 1)
        self.assertIn("requires an earlier expand", problems[0])

    def test_expand_only_chain_is_valid(self) -> None:
        chain = [_mig(v, "expand") for v in range(1, 6)]
        self.assertEqual(check_migration_phases(chain), [])


class PendingVersionsTests(unittest.TestCase):
    def test_fresh_database_reports_everything_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            connection = sqlite3.connect(Path(tmp) / "fresh.db")
            try:
                connection.row_factory = sqlite3.Row
                pending = pending_migration_versions(connection, all_migrations())
                self.assertEqual(pending, [m.version for m in all_migrations()])
            finally:
                connection.close()

    def test_current_database_reports_nothing_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "current.db")
            try:
                database.initialize()
                with database.connect() as connection:
                    self.assertEqual(pending_migration_versions(connection), [])
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()