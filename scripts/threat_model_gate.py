"""Threat-model delta / security governance gate (Phase 41.7 / SEC-008).

Every release commits a threat-model delta: new entry points, assets and
trust boundaries, closed and new risks, the controls applied and the
verification evidence.  A release gate fails (exit 1/2) when any of:

- a delta is missing a required field (release / date / owner / approved_by /
  controls / verification_evidence),
- ``controls`` or ``verification_evidence`` is an empty list,
- an owner or approver is unnamed or a placeholder (``security@helix.example``,
  ``TBD``, ``<...>``, empty, ...),
- with ``--release X``, the release ``X`` has no delta or its delta date is in
  the future, or
- with ``--check-today``, the latest quarterly security drill is older than
  ``--drill-max-days`` (default 90), is dated in the future, or no drill has
  ever been recorded.

Without ``--release``/``--check-today`` the gate is nil-tolerant in CI: an
empty delta/drill registry is not itself a violation — the placeholder red
light and the drill-recency check are release-time/controlled checks.

Usage:
    python scripts/threat_model_gate.py [--deltas PATH] [--drills PATH]
    python scripts/threat_model_gate.py --release 1.4.0
    python scripts/threat_model_gate.py --check-today --drill-max-days 90

Exit 0 on a clean review; 1 with one line per violation; 2 on unreadable or
malformed configuration files (missing registry / bad JSON).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_DESCRIPTION = (__doc__ or "security governance gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts._console import use_utf8_console  # noqa: E402

DEFAULT_DELTAS = ROOT / "supplychain" / "threat-model-deltas.json"
DEFAULT_DRILLS = ROOT / "supplychain" / "security-drills.json"

DELTA_REQUIRED = ("release", "date", "owner", "approved_by", "controls", "verification_evidence")
DELTA_LISTS = ("controls", "verification_evidence")
DRILL_TYPES = (
    "report_intake",
    "dependency_vuln",
    "key_compromise",
    "cross_tenant_alarm",
    # Automated drills (Phase 41/Gate B): a scripted drill run by CI or a
    # deployer. ``reference`` must point at the ledger record that proves
    # the run, so the recency check is backed by evidence, not a claim.
    "automated_rotation",
    "automated_restore",
    "automated_patch_release",
    # 42.2 REL-001: point-in-time recovery drill with RPO/RTO evidence.
    "automated_pitr",
    # 43.2 contract (a): PostgreSQL row-level tenant isolation drill.
    "automated_rls",
)
DRILL_REQUIRED = ("drill_type", "started_at", "owner", "scenario", "duration_minutes")
DRILL_AUTOMATED_TYPES = (
    "automated_rotation",
    "automated_restore",
    "automated_patch_release",
    "automated_pitr",
    "automated_rls",
)

_PLACEHOLDER_PATTERNS = re.compile(
    r"security@helix\.example|example\.com|security@example|tbd|todo|placeholder|"
    r"待定|占位|以配置|<[^>]*>",
    re.IGNORECASE,
)


class ReviewError(Exception):
    """Unreadable/malformed governance configuration (maps to exit 2)."""


def _load_payload(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"无法读取治理记录文件 {path}: {exc}") from exc
    key = "deltas" if path.name.startswith("threat") else "drills"
    entries = payload.get(key)
    if not isinstance(entries, list):
        raise ReviewError(f"治理记录文件 {path} 缺少 {key} 列表")
    return [entry for entry in entries if isinstance(entry, dict)]


def _is_placeholder(value: object) -> bool:
    if not isinstance(value, str):
        return True
    stripped = value.strip()
    if not stripped:
        return True
    return _PLACEHOLDER_PATTERNS.search(stripped) is not None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _validate_delta(entry: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    release = entry.get("release") or "<未命名>"
    for field in DELTA_REQUIRED:
        if field not in entry or entry.get(field) in (None, ""):
            violations.append(f"{release}: 缺少必填字段 {field}")
            continue
        if field in DELTA_LISTS and not isinstance(entry[field], list):
            violations.append(f"{release}: {field} 必须是列表")
            continue
        if field in DELTA_LISTS and not entry[field]:
            violations.append(f"{release}: {field} 不能为空列表（须附控制项与验证证据）")
    if (
        entry.get("date") is not None
        and entry.get("date") != ""
        and _parse_date(entry["date"]) is None
    ):
        violations.append(f"{release}: date 不是有效 ISO 日期: {entry.get('date')!r}")
    for field in ("owner", "approved_by"):
        if _is_placeholder(entry.get(field)):
            violations.append(
                f"{release}: {field} 未命名或为占位符——安全治理必须落到具名 owner/审批人"
            )
    return violations


def _validate_drill(entry: dict[str, Any], today: date) -> tuple[list[str], date | None]:
    violations: list[str] = []
    drill_type = entry.get("drill_type") or "<未命名>"
    for field in DRILL_REQUIRED:
        if field not in entry or entry.get(field) in (None, ""):
            violations.append(f"{drill_type}: 缺少必填字段 {field}")
    if drill_type not in DRILL_TYPES:
        violations.append(f"{drill_type}: drill_type 必须是 {'/'.join(DRILL_TYPES)} 之一")
    if _is_placeholder(entry.get("owner")):
        violations.append(f"{drill_type}: owner 未命名或为占位符——演练必须落到具名负责人")
    started = _parse_datetime(entry.get("started_at"))
    if entry.get("started_at") and started is None:
        violations.append(
            f"{drill_type}: started_at 不是有效 ISO 日期时间: {entry.get('started_at')!r}"
        )
    duration = entry.get("duration_minutes")
    if isinstance(duration, (int, float)) and duration < 0:
        violations.append(f"{drill_type}: duration_minutes 不能为负")
    if drill_type in DRILL_AUTOMATED_TYPES:
        reference = entry.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            violations.append(f"{drill_type}: 自动化演练必须提供 reference(台账路径)")
        elif any(
            marker in reference for marker in ("security@helix.example", "example.com", "TBD")
        ):
            violations.append(f"{drill_type}: reference 不能是占位符")
    return violations, started.date() if started else None


def review(
    deltas_path: Path = DEFAULT_DELTAS,
    drills_path: Path = DEFAULT_DRILLS,
    *,
    today: date | None = None,
    required_release: str | None = None,
    check_today: bool = False,
    drill_max_days: int = 90,
) -> list[str]:
    """Return a list of violations (empty = pass); raises ReviewError on config errors."""
    today = today or date.today()
    violations: list[str] = []

    deltas = _load_payload(deltas_path)
    for entry in deltas:
        violations.extend(_validate_delta(entry))

    if required_release:
        release_dates: dict[str, date] = {}
        for entry in deltas:
            parsed = _parse_date(entry.get("date"))
            if parsed is not None:
                release_dates[entry.get("release", "")] = parsed
        if required_release not in release_dates:
            violations.append(
                f"release {required_release} 缺失 threat-model delta——发布前必须先提交"
            )
        elif release_dates[required_release] > today:
            violations.append(
                f"release {required_release} 的 delta 日期 {release_dates[required_release]} 在未来（今天 {today}）"
            )

    drills = _load_payload(drills_path)
    latest_started: date | None = None
    for entry in drills:
        drill_violations, started = _validate_drill(entry, today)
        violations.extend(drill_violations)
        if started is not None:
            if started > today:
                violations.append(
                    f"{entry.get('drill_type')}: 演练日期 {started} 在未来（今天 {today}）"
                )
            latest_started = started if latest_started is None else max(latest_started, started)

    if check_today:
        if not drills:
            violations.append("尚无任何安全演练记录——发布前至少完成一次季度桌面演练")
        elif latest_started is None or today - latest_started > timedelta(days=drill_max_days):
            violations.append(
                f"最近一次安全演练 {latest_started} 距今超过 {drill_max_days} 天（今天 {today}）——演练过期"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--deltas", type=Path, default=DEFAULT_DELTAS)
    parser.add_argument("--drills", type=Path, default=DEFAULT_DRILLS)
    parser.add_argument("--today", help="today as YYYY-MM-DD (defaults to real today)")
    parser.add_argument("--release", help="release to check has a non-future threat-model delta")
    parser.add_argument(
        "--check-today", action="store_true", help="check the latest drill is fresh"
    )
    parser.add_argument("--drill-max-days", type=int, default=90)
    args = parser.parse_args(argv)

    try:
        violations = review(
            args.deltas,
            args.drills,
            today=date.fromisoformat(args.today) if args.today else None,
            required_release=args.release,
            check_today=args.check_today,
            drill_max_days=args.drill_max_days,
        )
    except ReviewError as exc:
        print(f"threat_model_gate: {exc}", file=sys.stderr)
        return 2

    if not violations:
        print("threat_model_gate: 威胁模型治理记录完整，无占位符与过期演练")
        return 0
    for violation in violations:
        print(f"threat_model_gate: {violation}", file=sys.stderr)
    print(f"threat_model_gate: {len(violations)} 项违规", file=sys.stderr)
    return 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
