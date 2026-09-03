"""Offline golden-set evaluation harness for Helix Support.

Runs a fixed set of end-to-end conversations through the application — the same
path the HTTP API serves — and checks each case against its expected routing
outcome.  The deployment plan treats this as the release quality gate: a model,
prompt, or routing change must not regress the golden set.

The cases run against the deterministic triage/policy path, so no live model
provider is required; ``AUTH_MODE=api_key`` maps every request to the demo
tenant.  Each case measures the request latency and records the assistant
metadata (agent, citations, quality approval, tool calls, risk categories) and
the resulting conversation state.

Usage:
    python scripts/evaluate.py                 # report to stdout, JSON + table
    python scripts/evaluate.py --format json   # machine-readable report only

Exit code is non-zero when any case fails or the pass rate is below
``--min-pass-rate``, so CI can gate on it.

The golden set lives in ``golden/set.json`` and is validated against a strict
schema before any case runs, so a malformed entry fails fast instead of
silently passing.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._console import use_utf8_console  # noqa: E402

from app.config import Settings
from app.main import create_app

GOLDEN_DEFAULT = Path(__file__).resolve().parent.parent / "golden" / "set.json"

# The API key the harness maps to the demo tenant / admin role.
EVAL_API_KEY = "golden-eval-key-0001"

_SCHEMA = {
    "id": str,
    "description": str,
    "conversation": dict,
    "expect": dict,
}

_EXPECT_KEYS = frozenset(
    {
        "agent",
        "status",
        "priority",
        "citations",
        "quality_approved",
        "tool_code",
        "in_content",
        "not_in_content",
        "risk_categories",
    }
)

# Pass-through keys (``conversation``) and allowed values.
_CONVERSATION_KEYS = frozenset({"customer_ref", "channel"})


def _fail(message: str) -> NoReturn:
    sys.exit(f"error: {message}")


def load_golden_set(path: Path) -> list[dict[str, Any]]:
    """Load and validate the golden set, returning the case list."""
    if not path.exists():
        _fail(f"golden set not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"golden set is not valid JSON: {exc}")
    if not isinstance(data, dict) or data.get("version") != 1:
        _fail("golden set must have version: 1")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail("golden set must contain a non-empty cases list")

    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            _fail("each golden case must be an object")
        for key, expected_type in _SCHEMA.items():
            if key not in case:
                _fail(f"golden case is missing '{key}': {case.get('id', '<no id>')}")
            if not isinstance(case[key], expected_type):
                _fail(f"golden case '{case.get('id')}' field '{key}' has wrong type")
        case_id = case["id"]
        if case_id in seen_ids:
            _fail(f"duplicate golden case id: {case_id}")
        seen_ids.add(case_id)

        # A case drives either a single message or an ordered multi-turn
        # sequence (``messages``) through one conversation. Exactly one form
        # is required.
        message = case.get("message")
        messages = case.get("messages")
        if message is not None and not isinstance(message, str):
            _fail(f"golden case '{case_id}' message must be a string")
        if messages is not None:
            if not isinstance(messages, list) or not messages:
                _fail(f"golden case '{case_id}' messages must be a non-empty list")
            if not all(isinstance(m, str) for m in messages):
                _fail(f"golden case '{case_id}' messages must all be strings")
        if not message and not messages:
            _fail(f"golden case '{case_id}' must have message or messages")
        if message and messages:
            _fail(f"golden case '{case_id}' cannot have both message and messages")

        conversation = case["conversation"]
        if set(conversation) - _CONVERSATION_KEYS:
            _fail(f"golden case '{case_id}' has unknown conversation keys")
        if not isinstance(conversation.get("customer_ref"), (str, type(None))):
            _fail(f"golden case '{case_id}' customer_ref must be a string or null")
        if not isinstance(conversation.get("channel"), str):
            _fail(f"golden case '{case_id}' channel must be a string")

        unknown_expect = set(case["expect"]) - _EXPECT_KEYS
        if unknown_expect:
            _fail(f"golden case '{case_id}' has unknown expect keys: {sorted(unknown_expect)}")
        if "in_content" in case["expect"] and "not_in_content" in case["expect"]:
            _fail(f"golden case '{case_id}' cannot expect both in_content and not_in_content")
    return cases


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    duration_ms: float
    detail: str
    assistant_metadata: dict[str, Any] = field(default_factory=dict)
    conversation_state: dict[str, Any] = field(default_factory=dict)


def _build_app(tmp_dir: Path) -> tuple[Settings, Any]:
    """Build a fresh in-process application with an ephemeral database."""
    settings = Settings(
        database_path=tmp_dir / "golden.db",
        auth_mode="api_key",
        api_keys_json=json.dumps(
            {
                EVAL_API_KEY: {
                    "tenant_id": "demo",
                    "actor_id": "golden.evaluator",
                    "role": "admin",
                }
            }
        ),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )
    return settings, create_app(settings)


def _check_expect(
    expect: dict[str, Any], metadata: dict[str, Any], state: dict[str, Any]
) -> list[str]:
    """Check a case's expectations against the observed turn outcome."""
    problems: list[str] = []

    expected_agent = expect.get("agent")
    if expected_agent is not None and metadata.get("agent") != expected_agent:
        problems.append(f"agent={metadata.get('agent')!r} (expected {expected_agent!r})")

    expected_status = expect.get("status")
    if expected_status is not None and state.get("status") != expected_status:
        problems.append(f"status={state.get('status')!r} (expected {expected_status!r})")

    expected_priority = expect.get("priority")
    if expected_priority is not None and state.get("priority") != expected_priority:
        problems.append(f"priority={state.get('priority')!r} (expected {expected_priority!r})")

    if "citations" in expect:
        observed = bool(metadata.get("citations"))
        if observed != expect["citations"]:
            problems.append(f"citations={observed} (expected {expect['citations']})")

    if "quality_approved" in expect:
        observed = bool(metadata.get("quality_approved"))
        if observed != expect["quality_approved"]:
            problems.append(f"quality_approved={observed} (expected {expect['quality_approved']})")

    expected_tool = expect.get("tool_code")
    tool_calls = metadata.get("tool_calls") or []
    order_call = next((call for call in tool_calls if call.get("tool") == "orders.lookup"), None)
    observed_tool = (order_call or (tool_calls[0] if tool_calls else {})).get("code")
    if expected_tool is not None and observed_tool != expected_tool:
        problems.append(f"tool_code={observed_tool!r} (expected {expected_tool!r})")

    for keyword in expect.get("in_content", []):
        if keyword not in metadata.get("content", ""):
            problems.append(f"assistant content missing {keyword!r}")
    for keyword in expect.get("not_in_content", []):
        if keyword in metadata.get("content", ""):
            problems.append(f"assistant content leaked {keyword!r}")

    expected_categories = expect.get("risk_categories")
    if expected_categories is not None:
        observed_categories = metadata.get("risk_categories") or []
        missing = set(expected_categories) - set(observed_categories)
        if missing:
            problems.append(f"risk_categories missing {sorted(missing)}")
    return problems


def run_case(client: TestClient, headers: dict[str, str], case: dict[str, Any]) -> CaseResult:
    """Run one golden case and return its result."""
    conversation: dict[str, Any] = case["conversation"]
    payload: dict[str, str] = {
        "customer_name": f"golden-{case['id'][:20]}",
        "channel": conversation.get("channel") or "web",
    }
    if conversation.get("customer_ref"):
        payload["customer_ref"] = conversation["customer_ref"]

    created = client.post("/api/conversations", json=payload, headers=headers)
    if created.status_code != 201:
        return CaseResult(
            case["id"], False, 0.0, f"conversation create failed: HTTP {created.status_code}"
        )
    conversation_id = created.json()["id"]

    messages = case.get("messages") or [case["message"]]
    started = time.monotonic()
    last_response = None
    for index, content in enumerate(messages):
        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            headers=dict(headers, **{"Idempotency-Key": f"golden-{uuid4().hex[:12]}-{index}"}),
            json={"content": content},
        )
        if response.status_code != 200:
            duration_ms = (time.monotonic() - started) * 1000
            return CaseResult(
                case["id"],
                False,
                duration_ms,
                f"message {index} failed: HTTP {response.status_code}",
            )
        last_response = response
    assert last_response is not None  # messages is non-empty
    duration_ms = (time.monotonic() - started) * 1000
    result = last_response.json()
    assistant = result.get("assistant_message") or {}
    state = result.get("conversation") or {}
    metadata = assistant.get("metadata") or {}
    metadata["content"] = assistant.get("content", "")

    problems = _check_expect(case["expect"], metadata, state)
    detail = "; ".join(problems) if problems else "ok"
    return CaseResult(
        case_id=case["id"],
        passed=not problems,
        duration_ms=duration_ms,
        detail=detail,
        assistant_metadata=metadata,
        conversation_state=state,
    )


def _report_table(results: list[CaseResult]) -> str:
    lines = ["", "Golden-set evaluation"]
    lines.append("-" * 78)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(
            f"  {status}  {result.case_id:<34} {result.duration_ms:7.1f} ms  {result.detail}"
        )
    lines.append("-" * 78)
    passed = sum(1 for r in results if r.passed)
    durations = [r.duration_ms for r in results]
    lines.append(
        f"  {passed}/{len(results)} passed  |  "
        f"mean {statistics.mean(durations):.1f} ms  |  "
        f"p95 {sorted(durations)[max(0, int(len(durations) * 0.95) - 1)]:.1f} ms"
    )
    return "\n".join(lines)


def _register_prompt_version(app: Any, body: str) -> str:
    """Register and activate a triage_prompt version on the eval app."""
    from app.prompts import PromptRegistry

    database = app.state.services.database
    registry = PromptRegistry(database)
    version = registry.create_version(
        "demo", "triage_prompt", "v-eval", body, "eval-model", "golden.evaluator"
    )
    registry.activate("demo", version.id, "golden.evaluator")
    return version.id


def evaluate(golden_path: Path, prompt_body: str | None = None) -> dict[str, Any]:
    """Load the golden set, run every case, and return a machine report.

    When ``prompt_body`` is given, a triage_prompt version is registered and
    activated before the cases run, so the report reflects routing under that
    registered version -- the comparison the release gate uses to prove a
    prompt/model change does not regress the deterministic golden set.
    """
    cases = load_golden_set(golden_path)
    with tempfile.TemporaryDirectory(prefix="golden-") as tmp_dir:
        _, app = _build_app(Path(tmp_dir))
        if prompt_body:
            _register_prompt_version(app, prompt_body)
        headers = {"X-API-Key": EVAL_API_KEY, "X-Tenant-Id": "demo"}
        try:
            with TestClient(app) as client:
                results = [run_case(client, headers, case) for case in cases]
        finally:
            try:
                app.state.services.database.close()
            except Exception:
                pass

    passed = sum(1 for r in results if r.passed)
    durations = sorted(r.duration_ms for r in results)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
    confidences = [
        r.assistant_metadata["confidence"]
        for r in results
        if r.assistant_metadata.get("confidence") is not None
    ]
    cited = sum(1 for r in results if r.assistant_metadata.get("citations"))
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "mean_confidence": round(statistics.mean(confidences), 4) if confidences else 0.0,
        "citation_coverage": round(cited / len(results), 4) if results else 0.0,
        "mean_latency_ms": round(statistics.mean(durations), 2) if durations else 0.0,
        "p95_latency_ms": round(p95, 2),
        # The deterministic eval path makes no provider calls; the cost of the
        # golden run is the same flat zero the adversarial harness reports.
        "estimated_cost_usd": 0.0,
        "cases": [
            {
                "id": r.case_id,
                "passed": r.passed,
                "duration_ms": round(r.duration_ms, 2),
                "detail": r.detail,
            }
            for r in results
        ],
    }


def compare(golden_path: Path, prompt_body: str) -> dict[str, Any]:
    """Run the golden set under baseline and under a registered prompt version.

    Returns both reports plus a diff of pass rate and latency. The
    deterministic routing path does not depend on the registered prompt body,
    so a clean diff (zero pass-rate regression) is the canary-safe gate.
    """
    baseline = evaluate(golden_path)
    with_prompt = evaluate(golden_path, prompt_body=prompt_body)
    return {
        "baseline": baseline,
        "with_prompt": with_prompt,
        "diff": {
            "pass_rate": round(with_prompt["pass_rate"] - baseline["pass_rate"], 4),
            "mean_latency_ms": round(
                with_prompt["mean_latency_ms"] - baseline["mean_latency_ms"], 2
            ),
            "p95_latency_ms": round(with_prompt["p95_latency_ms"] - baseline["p95_latency_ms"], 2),
        },
    }


def _compare_report(report: dict[str, Any]) -> str:
    """Human-readable comparison of baseline vs registered-prompt runs."""
    b = report["baseline"]
    w = report["with_prompt"]
    d = report["diff"]
    lines = ["", "Golden-set prompt-version comparison"]
    lines.append("-" * 78)
    lines.append(
        f"  baseline     {b['passed']}/{b['total']} passed  "
        f"pass_rate={b['pass_rate']:.4f}  "
        f"mean={b['mean_latency_ms']:.1f}ms  p95={b['p95_latency_ms']:.1f}ms"
    )
    lines.append(
        f"  with_prompt  {w['passed']}/{w['total']} passed  "
        f"pass_rate={w['pass_rate']:.4f}  "
        f"mean={w['mean_latency_ms']:.1f}ms  p95={w['p95_latency_ms']:.1f}ms"
    )
    lines.append("-" * 78)
    lines.append(
        f"  diff         pass_rate={d['pass_rate']:+.4f}  "
        f"mean={d['mean_latency_ms']:+.1f}ms  p95={d['p95_latency_ms']:+.1f}ms"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set evaluation")
    parser.add_argument("--golden", default=str(GOLDEN_DEFAULT), help="Path to golden set JSON")
    parser.add_argument(
        "--format", choices=("report", "json"), default="report", help="Output format"
    )
    parser.add_argument("--min-pass-rate", type=float, default=1.0, help="Minimum pass rate")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include request logs")
    parser.add_argument(
        "--prompt-version",
        metavar="PATH",
        default=None,
        help="Compare baseline against a registered prompt version (body read from PATH)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.prompt_version:
        body = Path(args.prompt_version).read_text(encoding="utf-8")
        report = compare(Path(args.golden), body)
        if args.format == "json":
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(_compare_report(report))
        baseline = report["baseline"]
        with_prompt = report["with_prompt"]
        failed = baseline["failed"] or with_prompt["failed"]
        low = (
            baseline["pass_rate"] < args.min_pass_rate
            or with_prompt["pass_rate"] < args.min_pass_rate
        )
        if failed or low:
            print("\nerror: golden set comparison failed", file=sys.stderr)
            return 1
        return 0

    report = evaluate(Path(args.golden))

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        results = [
            CaseResult(
                case_id=item["id"],
                passed=item["passed"],
                duration_ms=item["duration_ms"],
                detail=item["detail"],
            )
            for item in report["cases"]
        ]
        print(_report_table(results))

    if report["failed"] or report["pass_rate"] < args.min_pass_rate:
        print(
            f"\nerror: golden set failed: {report['failed']} of {report['total']} cases",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
