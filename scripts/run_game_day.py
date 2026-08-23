#!/usr/bin/env python3
"""42.6: monthly game-day orchestrator.

A game day rotates one primary failure domain per run — database, Redis,
model provider, connector credentials, object store, or webhook — and drives
the drills this repository already ships, recording the outcome in
``supplychain/game-days.json`` so the rotation is auditable over time:

- database      → scripts/run_restore_drill.py + scripts/run_pitr_drill.py
- redis         → scripts/redis_failure_drill.py (skips without redis-server)
- model         → tests/test_chaos.py (provider failure injection)
- connector     → scripts/run_rotation_drill.py
- objectstore   → tests/test_archive_store.py + tests/test_attachment_security.py
- webhook       → tests/test_provider_conformance.py (outage/DLQ case)

Usage:
    python scripts/run_game_day.py --focus database
    python scripts/run_game_day.py --focus all       # rotate through every domain
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# focus → [(label, argv), ...]; pytest targets are invoked via -m pytest.
GAME_PLAN: dict[str, list[tuple[str, list[str]]]] = {
    "database": [
        ("restore-drill", [sys.executable, "scripts/run_restore_drill.py"]),
        ("pitr-drill", [sys.executable, "scripts/run_pitr_drill.py"]),
    ],
    "redis": [
        ("redis-failure-drill", [sys.executable, "scripts/redis_failure_drill.py"]),
    ],
    "model": [
        (
            "chaos-provider-failure",
            [sys.executable, "-m", "pytest", "tests/test_chaos.py", "-q"],
        ),
    ],
    "connector": [
        ("rotation-drill", [sys.executable, "scripts/run_rotation_drill.py"]),
    ],
    "objectstore": [
        (
            "archive-store-integrity",
            [sys.executable, "-m", "pytest", "tests/test_archive_store.py", "-q"],
        ),
        (
            "attachment-isolation",
            [sys.executable, "-m", "pytest", "tests/test_attachment_security.py", "-q"],
        ),
    ],
    "webhook": [
        (
            "provider-conformance",
            [sys.executable, "-m", "pytest", "tests/test_provider_conformance.py", "-q"],
        ),
    ],
}


def _run(label: str, argv: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        argv,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = ((result.stdout or "") + (result.stderr or ""))[-400:]
    return result.returncode == 0, tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--focus",
        choices=sorted(GAME_PLAN) + ["all"],
        required=True,
        help="which failure domain to exercise this month",
    )
    parser.add_argument("--out", default="supplychain/game-days.json")
    args = parser.parse_args()

    focuses = sorted(GAME_PLAN) if args.focus == "all" else [args.focus]
    ledger_path = Path(args.out)
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {
            "schema_version": 1,
            "policy": "monthly game day rotation: db/redis/model/connector/objectstore/webhook",
        }

    failures: list[str] = []
    exercises: list[dict[str, object]] = []
    for focus in focuses:
        for label, argv in GAME_PLAN[focus]:
            ok, tail = _run(label, argv)
            exercises.append({"focus": focus, "exercise": label, "passed": ok})
            if not ok:
                failures.append(f"{focus}/{label}: {tail}")

    passed = not failures
    ledger.setdefault("game_days", []).append(
        {
            "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "focus": args.focus,
            "passed": passed,
            "exercises": exercises,
            "details": "; ".join(failures) if failures else "all exercises green",
        }
    )
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "focus": args.focus,
                "passed": passed,
                "exercises": len(exercises),
                "ledger": str(ledger_path),
            },
            ensure_ascii=False,
        )
    )
    if failures:
        print("\n".join(failures))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())