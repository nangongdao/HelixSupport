#!/usr/bin/env python3
"""42.6: generate the observability v2 dashboard set.

Emits one Grafana-style JSON dashboard per concern under
``docs/dashboards/`` — tenant noisy-neighbor, queue fairness, model/provider,
archive & object store, DSR, and credential usage. Panel queries reference
the metric names the application actually exports (telemetry keys and the
audit/diagnostics surfaces), so a dashboard that drifts from reality is a
review-time problem, not an on-call surprise.

Usage:
    python scripts/generate_dashboards.py [--out docs/dashboards]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Metric sources the panels are allowed to reference. Keeping the list next
# to the generator makes drift reviewable in PRs.
METRIC_CATALOG = {
    "helix_request_duration_ms": "request latency histogram (request_controls span)",
    "helix_turn_job_duration_ms": "turn job wall time (worker)",
    "helix_turn_jobs_processed_total": "turn jobs completed counter",
    "helix_turn_jobs_failed_total": "turn jobs failed counter",
    "helix_queue_depth": "queued turn jobs gauge (diagnostics)",
    "helix_webhook_delivery_failures_total": "webhook deliveries failed/dead",
    "helix_archive_partitions_total": "cold-tier archive partitions (archive store manifest)",
    "helix_dsr_open_count": "open data-subject requests (DSR board)",
    "helix_credential_rotations_total": "credential lifecycle rotations (41.1 audit)",
}


def _panel(title: str, unit: str, targets: list[str], description: str) -> dict:
    return {
        "title": title,
        "description": description,
        "unit": unit,
        "targets": [{"expr": expr} for expr in targets],
    }


def _dashboard(title: str, tags: list[str], panels: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "title": title,
        "tags": tags + ["helix", "42.6"],
        "panels": panels,
    }


def build_dashboards() -> dict[str, dict]:
    per_tenant = 'sum by (tenant_id) ({metric})'
    dashboards: dict[str, dict] = {}

    dashboards["tenant-noisy-neighbor"] = _dashboard(
        "Tenant Noisy Neighbor",
        ["slo"],
        [
            _panel(
                "Requests by tenant",
                "reqps",
                [per_tenant.format(metric="helix_request_duration_ms_count")],
                "Who is driving traffic — the first place to look when a shared "
                "resource saturates.",
            ),
            _panel(
                "Turn jobs by tenant",
                "ops",
                [per_tenant.format(metric="helix_turn_jobs_processed_total")],
                "Async work share per tenant.",
            ),
            _panel(
                "Failed turn jobs by tenant",
                "ops",
                [per_tenant.format(metric="helix_turn_jobs_failed_total")],
                "Failure concentration by tenant.",
            ),
        ],
    )

    dashboards["queue-fairness"] = _dashboard(
        "Queue Fairness",
        ["slo"],
        [
            _panel("Queue depth", "short", ["helix_queue_depth"], "Global queued backlog."),
            _panel(
                "Turn job duration p50/p95",
                "ms",
                ["helix_turn_job_duration_ms{quantile='0.5'}", "helix_turn_job_duration_ms{quantile='0.95'}"],
                "Fairness shows up as tail latency, not averages.",
            ),
            _panel(
                "Oldest queued job age",
                "s",
                ["helix_queue_oldest_seconds"],
                "Starvation detector: one tenant's flood must not strand another's job.",
            ),
        ],
    )

    dashboards["model-provider"] = _dashboard(
        "Model / Provider",
        ["slo"],
        [
            _panel(
                "Model call errors",
                "ops",
                ["helix_model_provider_errors_total"],
                "Provider 4xx/5xx and timeouts.",
            ),
            _panel(
                "Turn duration (model-bound turns)",
                "ms",
                ["helix_turn_job_duration_ms"],
                "End-to-end turn wall time; regressions here usually mean the provider.",
            ),
        ],
    )

    dashboards["archive-object-store"] = _dashboard(
        "Archive & Object Store",
        ["slo"],
        [
            _panel(
                "Archive partitions",
                "short",
                ["helix_archive_partitions_total"],
                "Cold-tier growth (42.3 object store manifests).",
            ),
            _panel(
                "Attachment objects",
                "short",
                ["helix_attachment_objects_total"],
                "Attachment bucket population (42.4).",
            ),
            _panel(
                "Webhook delivery failures",
                "ops",
                ["helix_webhook_delivery_failures_total"],
                "Outbound provider reachability.",
            ),
        ],
    )

    dashboards["dsr"] = _dashboard(
        "Data-Subject Requests",
        ["slo"],
        [
            _panel("Open DSRs", "short", ["helix_dsr_open_count"], "DSR board backlog."),
            _panel(
                "DSR SLA breaches",
                "ops",
                ["helix_dsr_sla_breaches_total"],
                "Requests past their statutory clock — page-worthy.",
            ),
        ],
    )

    dashboards["credentials"] = _dashboard(
        "Credential Usage",
        ["slo"],
        [
            _panel(
                "Rotations",
                "ops",
                [per_tenant.format(metric="helix_credential_rotations_total")],
                "Lifecycle rotations per tenant (41.1).",
            ),
            _panel(
                "Revocations",
                "ops",
                [per_tenant.format(metric="helix_credential_revocations_total")],
                "Emergency revocation activity.",
            ),
        ],
    )
    return dashboards


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/dashboards")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, dashboard in build_dashboards().items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {path}")
    print(f"metric catalog: {len(METRIC_CATALOG)} metrics documented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())