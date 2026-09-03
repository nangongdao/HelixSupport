"""Vulnerability exception review gate (Phase 41.2 / ROADMAP 41.2 SEC-003).

Every vulnerability exception is a dated, owned commitment: CVE id, affected
component and version, evidence that the vulnerable path is unreachable, a
compensating control, an owner and a due date.  The gate fails when an open
exception:

- is missing a required field,
- is past its ``due_date`` (automatic red light for expired exceptions), or
- with ``--require-coverage``, a vulnerability reported by ``pip-audit`` is
  not registered as an exception at all.

Usage:
    python scripts/vuln_review.py [--exceptions PATH] [--today YYYY-MM-DD]
    python scripts/vuln_review.py --audit pip-audit.json --require-coverage

Exit 0 on a clean review; non-zero with one line per violation otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_DESCRIPTION = (__doc__ or "supply-chain gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts._console import use_utf8_console

DEFAULT_EXCEPTIONS = ROOT / "supplychain" / "vulnerability-exceptions.json"

VALID_STATUSES = {"open", "closed"}


class ReviewError(Exception):
    """A single gate violation with a human-readable message."""


def _load_exceptions(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"无法读取漏洞例外文件 {path}: {exc}") from exc
    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list):
        raise ReviewError("漏洞例外文件缺少 exceptions 列表")
    return [entry for entry in exceptions if isinstance(entry, dict)]


def _validate_date(value: object, context: str) -> None:
    if not isinstance(value, str):
        raise ReviewError(f"{context} 必须是 ISO 日期字符串")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ReviewError(f"{context} 不是有效 ISO 日期: {value!r}") from exc


def review(
    path: Path = DEFAULT_EXCEPTIONS,
    *,
    today: date | None = None,
) -> list[str]:
    """Validate the exception file; return a list of violations (empty = pass)."""
    violations: list[str] = []
    today = today or date.today()
    exceptions = _load_exceptions(path)

    for index, entry in enumerate(exceptions, start=1):
        entry_id = entry.get("id", f"#{index}")
        status = entry.get("status")
        if status not in VALID_STATUSES:
            violations.append(f"{entry_id}: status 必须是 open/closed（当前 {status!r}）")
            continue
        for field in ("id", "component", "owner"):
            if not entry.get(field):
                violations.append(f"{entry_id}: 缺少必填字段 {field}")
        due_date = entry.get("due_date")
        due: date | None = None
        if due_date:
            try:
                due = date.fromisoformat(str(due_date))
            except ValueError:
                violations.append(f"{entry_id}: due_date 不是有效 ISO 日期")
        if status == "open":
            if not due_date:
                violations.append(f"{entry_id}: open 例外缺少必填字段 due_date")
            elif due is not None and due < today:
                violations.append(
                    f"{entry_id}: 例外已于 {due_date} 到期（今天 {today}），必须修复或续期"
                )
            for field in ("not_reachable_evidence", "compensating_control"):
                if not entry.get(field):
                    violations.append(f"{entry_id}: open 例外缺少 {field}")
    return violations


def _registered_ids(exceptions_path: Path) -> set[str]:
    ids: set[str] = set()
    for entry in _load_exceptions(exceptions_path):
        entry_id = entry.get("id")
        if isinstance(entry_id, str):
            ids.add(entry_id)
    return ids


def audit_coverage(
    audit_report: Path,
    exceptions_path: Path = DEFAULT_EXCEPTIONS,
) -> list[str]:
    """Every pip-audit vulnerability id must be registered as an exception."""
    try:
        payload = json.loads(audit_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取 pip-audit 报告 {audit_report}: {exc}"]
    reported: set[str] = set()
    for dependency in payload.get("dependencies", []):
        # pip-audit の JSON formatter が出すのは "vulns"。"vulnerabilities" は
        # 誤読のままカバレッジ門が一度も落ちなかった原因なので、正しい方を先に
        # 見つつ、既存の成果物を壊さないため旧キーも読む。
        for key in ("vulns", "vulnerabilities"):
            for vulnerability in dependency.get(key, []):
                vuln_id = vulnerability.get("id")
                if isinstance(vuln_id, str):
                    reported.add(vuln_id)
    registered = _registered_ids(exceptions_path)
    return [
        f"未登记漏洞 {vuln_id}：必须登记到 {exceptions_path.name} 并附不可达证据/补偿控制/owner/到期日"
        for vuln_id in sorted(reported - registered)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--exceptions", type=Path, default=DEFAULT_EXCEPTIONS)
    parser.add_argument("--today", help="today as YYYY-MM-DD (defaults to real today)")
    parser.add_argument("--audit", type=Path, help="pip-audit JSON report to check coverage")
    parser.add_argument("--require-coverage", action="store_true")
    args = parser.parse_args(argv)

    try:
        violations = review(
            args.exceptions,
            today=date.fromisoformat(args.today) if args.today else None,
        )
        if args.audit:
            if not args.require_coverage:
                parser.error("--audit 需要 --require-coverage")
            violations.extend(audit_coverage(args.audit, args.exceptions))
    except ReviewError as exc:
        print(f"vuln_review: {exc}", file=sys.stderr)
        return 2

    if not violations:
        print("vuln_review: 无到期例外，漏洞覆盖完整")
        return 0
    for violation in violations:
        print(f"vuln_review: {violation}", file=sys.stderr)
    print(f"vuln_review: {len(violations)} 项违规", file=sys.stderr)
    return 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
