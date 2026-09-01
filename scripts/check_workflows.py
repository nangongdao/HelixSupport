"""CI workflow pin consistency gate (Phase 41.2 / ROADMAP 41.2 SEC-003).

Every ``uses: owner/repo@ref`` reference in ``.github/workflows`` must be
registered in ``supplychain/ci-pins.json`` with a matching ref; an unknown
action or a ref drift fails the gate.  An action whose pinned commit SHA is
still ``null`` is reported as a warning for the controlled-update robot to
fill in — supply-chain digest pinning is owned by the robot process, never by
an ad-hoc human edit (ROADMAP 41.2).

Local actions (``.github/actions/...``) and ``docker://`` references are not
third-party supplies and are skipped.

Usage:
    python scripts/check_workflows.py [--workflows-dir PATH] [--pins PATH]

Exit 0 on a consistent review; non-zero with one line per violation otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_DESCRIPTION = (__doc__ or "supply-chain gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts._console import use_utf8_console  # noqa: E402

DEFAULT_WORKFLOWS = ROOT / ".github" / "workflows"
DEFAULT_PINS = ROOT / "supplychain" / "ci-pins.json"

USES_RE = re.compile(r"uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([^\s]+)")


def _collect_uses(workflows_dir: Path) -> list[tuple[str, str, str]]:
    """Return ``(workflow_name, owner/repo, ref)`` for every non-local use."""
    references: list[tuple[str, str, str]] = []
    for workflow in sorted(workflows_dir.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        for line in text.splitlines():
            match = USES_RE.search(line)
            if not match:
                continue
            owner_repo, ref = match.group(1), match.group(2)
            if owner_repo.startswith(".") or ref.startswith("docker://"):
                continue  # local action or container reference
            references.append((workflow.name, owner_repo, ref))
    return references


def review(
    workflows_dir: Path = DEFAULT_WORKFLOWS, pins_path: Path = DEFAULT_PINS
) -> tuple[list[str], list[str]]:
    """Return ``(violations, warnings)``; an empty violation list means pass."""
    try:
        pins_payload = json.loads(pins_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取 {pins_path}: {exc}"], []
    pins = pins_payload.get("pins", {})

    violations: list[str] = []
    warnings: list[str] = []
    for workflow_name, owner_repo, ref in _collect_uses(workflows_dir):
        pin = pins.get(owner_repo)
        if pin is None:
            violations.append(
                f"{workflow_name}: {owner_repo}@{ref} 未登记到 ci-pins.json——必须经受控更新流程登记"
            )
            continue
        if pin.get("ref") != ref:
            violations.append(
                f"{workflow_name}: {owner_repo} 引用 {ref} 与登记 ref {pin.get('ref')} 漂移"
            )
        if not pin.get("sha"):
            warnings.append(f"{owner_repo}@{ref}：仍未固定到 commit SHA——留给受控更新机器人回填")
    return violations, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--workflows-dir", type=Path, default=DEFAULT_WORKFLOWS)
    parser.add_argument("--pins", type=Path, default=DEFAULT_PINS)
    args = parser.parse_args(argv)

    violations, warnings = review(args.workflows_dir, args.pins)
    for warning in warnings:
        print(f"check_workflows: warning — {warning}", file=sys.stderr)
    if not violations:
        print("check_workflows: 全部 actions 引用与 pinned 登记一致")
        return 0
    for violation in violations:
        print(f"check_workflows: {violation}", file=sys.stderr)
    print(f"check_workflows: {len(violations)} 项违规", file=sys.stderr)
    return 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
