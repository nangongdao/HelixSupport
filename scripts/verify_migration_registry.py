"""Migration registry sanity gate (Phase 41.6 / ARC-001).

Since Phase 41.6 the numbered migrations live in per-version modules under
``app/migrations/`` (``v01_*.py`` ... ``v32_*.py``) and register themselves
through the ``@migration`` decorator in ``app/migrations/__init__.py``.  A
mis-registered migration — wrong module, duplicate version, version gap, or
an orphan version module that registers nothing — must fail before any
database runs it.

This gate wraps ``app.migrations.verify_migration_registry`` and reports one
line per problem.  It is not nil-tolerant: the registry is code, not
configuration, and an unverifiable chain is a build error.

Usage:
    python scripts/verify_migration_registry.py

Exit 0 on a clean registry; 1 with one line per violation; 2 on an import or
registration failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.migrations import all_migrations, verify_migration_registry

ROOT = Path(__file__).resolve().parent.parent
_DESCRIPTION = (__doc__ or "migration registry gate").strip().splitlines()[0]


def review() -> list[str]:
    """Return one string per registry problem; empty list means sound."""
    return verify_migration_registry()


def main(argv: list[str] | None = None) -> int:
    try:
        problems = review()
    except Exception as error:  # pragma: no cover - import failures
        print(f"error: registry gate cannot run: {error}", file=sys.stderr)
        return 2
    migrations = all_migrations()
    if problems:
        print(f"migration registry gate failed ({len(migrations)} migrations):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"migration registry gate passed: {len(migrations)} migrations, contiguous chain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
