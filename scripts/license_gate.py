"""License policy gate (Phase 41.2 / ROADMAP 41.2 SEC-003).

The runtime dependency closure is reviewed package-by-package into
``supplychain/license-policy.json``.  A package that is not yet reviewed
fails the gate, so every new dependency is a conscious licensing decision
instead of an accidental one.  A package can carry a temporary
``LicenseRef-TBD`` approval with an ``approved_until`` date; the gate fails
once that date passes (the reviewer must have confirmed by then).

The closure spans **both** shipped tracks: ``requirements.lock`` (the Python
service) and ``frontend/package.json`` ``dependencies`` (the React islands,
bundled by Vite into ``app/static/dist/assets`` and served to every
operator).  Only the Python side was reviewed until now, so eight shipped
npm packages had entered without a licensing decision — the exact accident
this gate exists to prevent.  ``devDependencies`` stay out of scope: vite,
vitest, jsdom and the testing library never reach a user, which is the same
line the Python side draws by reading the lock file rather than dev extras.

Usage:
    python scripts/license_gate.py [--lock PATH] [--policy PATH] [--npm PATH]
                                   [--today YYYY-MM-DD]

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
sys.path.insert(0, str(ROOT))

from scripts._console import use_utf8_console

DEFAULT_LOCK = ROOT / "requirements.lock"
DEFAULT_POLICY = ROOT / "supplychain" / "license-policy.json"
# The React island track. Its `dependencies` are bundled by Vite into
# app/static/dist/assets and shipped to every operator, so they are part of
# the same runtime closure as requirements.lock — but only the Python side
# was ever reviewed, so eight shipped packages (react, react-dom, zustand,
# @tanstack/react-query, 4x @xterm/*) entered without a licensing decision.
DEFAULT_NPM_MANIFEST = ROOT / "frontend" / "package.json"


def _lock_package_names(lock_path: Path) -> list[str]:
    """Parse exact-version runtime dependencies from the lock file."""
    names: list[str] = []
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(Requirement(line).name.lower())
    return names


def _npm_package_names(manifest_path: Path) -> list[str]:
    """Parse runtime (non-dev) dependencies from a package.json.

    Only ``dependencies`` is read. ``devDependencies`` (vite, vitest, jsdom,
    the testing library) are build/test tooling: they never reach a user, so
    they are outside the "runtime dependency closure" this gate reviews —
    the same line the Python side draws by reading requirements.lock rather
    than the dev extras.
    """
    if not manifest_path.is_file():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    deps = manifest.get("dependencies")
    if not isinstance(deps, dict):
        return []
    return sorted(str(name).lower() for name in deps)


def review(
    lock_path: Path = DEFAULT_LOCK,
    policy_path: Path = DEFAULT_POLICY,
    *,
    today: date | None = None,
    npm_path: Path | None = None,
) -> list[str]:
    """Validate every runtime package against the policy; return violations.

    ``npm_path`` is opt-in: when omitted only the Python lock file is
    reviewed, so a caller testing with a synthetic lock+policy pair does not
    silently inherit the repository's real manifest. :func:`main` passes
    ``DEFAULT_NPM_MANIFEST``, so the shipped gate always covers both tracks.
    """
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

    def check(name: str, label: str) -> None:
        entry = policy_by_name.get(name)
        if entry is None:
            violations.append(
                f"{name}: 未登记许可证——必须加入 license-policy.json 并人工核对后登记"
                f"（来源 {label}）"
            )
            return
        if isinstance(entry, str):
            license_name, approved_until = entry, None
        else:
            license_name = entry.get("license", "")
            approved_until = entry.get("approved_until")
        if license_name.lower() in allowed:
            return
        if approved_until:
            try:
                approved = date.fromisoformat(str(approved_until))
            except ValueError:
                violations.append(f"{name}: approved_until 不是有效 ISO 日期")
                return
            if approved < today:
                violations.append(f"{name}: 临时许可批准已于 {approved_until} 到期（今天 {today}）")
            return
        violations.append(f"{name}: 许可证 {license_name!r} 不在允许列表 {sorted(allowed)}")

    for name in sorted(_lock_package_names(lock_path)):
        check(name, "requirements.lock")
    # No default: a caller that passes a synthetic lock+policy must not have
    # the real frontend/package.json pulled in behind its back. main() owns
    # the default paths; review() reads only what it is handed.
    if npm_path is not None:
        for name in _npm_package_names(npm_path):
            check(name, npm_path.name)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--npm", type=Path, default=DEFAULT_NPM_MANIFEST)
    parser.add_argument("--today", help="today as YYYY-MM-DD (defaults to real today)")
    args = parser.parse_args(argv)

    try:
        violations = review(
            args.lock,
            args.policy,
            today=date.fromisoformat(args.today) if args.today else None,
            npm_path=args.npm,
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"license_gate: {exc}", file=sys.stderr)
        return 2

    if not violations:
        counted = len(_lock_package_names(args.lock)) + len(_npm_package_names(args.npm))
        print(f"license_gate: {counted} 个运行时依赖包（Python + npm）全部登记且合规")
        return 0
    for violation in violations:
        print(f"license_gate: {violation}", file=sys.stderr)
    print(f"license_gate: {len(violations)} 项违规", file=sys.stderr)
    return 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
