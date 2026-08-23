#!/usr/bin/env python3
"""42.2: migration discipline gate — expand/migrate/contract.

Validates the registered migration chain against the 42.2 release
discipline so a schema change that would break rolling N/N+1 deployments
fails in CI before any database runs it:

1. Chain integrity (``verify_migration_chain``): versions unique and
   contiguous.
2. Phase annotations (``check_migration_phases``): every migration from
   version 33 on declares ``expand``/``migrate``/``contract``, and a
   ``contract`` migration is always preceded by an ``expand`` one.

Usage:
    python scripts/migration_gate.py [--json]

Exit codes: 0 = discipline holds, 1 = violations found.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.migrations import (
    all_migrations,
    check_migration_phases,
    verify_migration_chain,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    args = parser.parse_args()

    migrations = all_migrations()
    problems = verify_migration_chain(migrations) + check_migration_phases(migrations)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not problems,
                    "migrations": len(migrations),
                    "phased": sum(1 for m in migrations if m.phase),
                    "problems": problems,
                },
                ensure_ascii=False,
            )
        )
    else:
        if problems:
            for problem in problems:
                print(f"migration gate: {problem}")
            print(f"migration gate: FAILED ({len(migrations)} migrations checked)")
        else:
            phased = sum(1 for m in migrations if m.phase)
            print(
                f"migration gate: OK — {len(migrations)} migrations, "
                f"chain contiguous, {phased} phase-annotated"
            )

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())