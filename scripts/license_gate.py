"""License policy gate (Phase 41.2 / ROADMAP 41.2 SEC-003).

The runtime dependency closure is reviewed package-by-package into
``supplychain/license-policy.json``.  A package in ``requirements.lock`` that
is not yet reviewed fails the gate, so every new dependency is a conscious
licensing decision instead of an accidental one.  A package can carry a
temporary ``LicenseRef-TBD`` approval with an ``approved_until`` date; the
gate fails once that date passes (the reviewer must have confirmed by then).

Usage:
    python scripts/license_gate.py [--lock PATH] [--policy PATH] [--today YYYY-MM-DD]

Exit 0 on a clean review; non-zero with one line per violation otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from packaging.requirements import Requirement

_DESCRIPTION = (__doc__ or "supply-chain gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "requirements.lock"
DEFAULT_POLICY = ROOT / "supplychain" / "license-policy.json"


def _lock_package_names(lock_path: Path) -> list[str]:
    """Parse exact-version runtime dependencies from the lock file."""
    names: list[str] = []
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(Requirement(line).name.lower())
    return names


def review(
    lock_path: Path = DEFAULT_LOCK,
    policy_path: Path = DEFAULT_POLICY,
    *,
    today: date | None = None,
) -> list[str]:
    """Validate every locked package against the policy; return violations."""
    today = today or date.today()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    allowed: set[str] = {str(item).lower() for item in policy.get("allowed_licenses", [])}
    packages = policy.get("packages", {})
    if not isinstance(packages, dict):
        return ["license-policy.json 缺少 packages 映射"]

    violations: list[str] = []
    # Package names are matched case-insensitively (lock entries and policy
    # keys may differ in casing, e.g. ``PyYAML`` vs ``pyyaml``).
    policy_by_name = {str(key).lower(): value for key, value in packages.items()}
    for name in sorted(_lock_package_names(lock_path)):
        entry = policy_by_name.get(name)
        if entry is None:
            violations.append(
                f"{name}: 未登记许可证——必须加入 license-policy.json 并人工核对后登记"
            )
            continue
        if isinstance(entry, str):
            license_name, approved_until = entry, None
        else:
            license_name = entry.get("license", "")
            approved_until = entry.get("approved_until")
        if license_name.lower() not in allowed:
            if approved_until:
                try:
                    approved = date.fromisoformat(str(approved_until))
                except ValueError:
                    violations.append(f"{name}: approved_until 不是有效 ISO 日期")
                    continue
                if approved < today:
                    violations.append(
                        f"{name}: 临时许可批准已于 {approved_until} 到期（今天 {today}）"
                    )
                continue
            violations.append(f"{name}: 许可证 {license_name!r} 不在允许列表 {sorted(allowed)}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--today", help="today as YYYY-MM-DD (defaults to real today)")
    args = parser.parse_args(argv)

    try:
        violations = review(
            args.lock, args.policy, today=date.fromisoformat(args.today) if args.today else None
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"license_gate: {exc}", file=sys.stderr)
        return 2

    if not violations:
        print(f"license_gate: {len(_lock_package_names(args.lock))} 个依赖包全部登记且合规")
        return 0
    for violation in violations:
        print(f"license_gate: {violation}", file=sys.stderr)
    print(f"license_gate: {len(violations)} 项违规", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
