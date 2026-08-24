"""Load testing script for Helix Support.

Simulates the real operator workload — create a conversation, send a message
(which routes through the orchestrator), and optionally poll the resulting
turn job — while measuring throughput and latency.

Rate-limited responses (HTTP 429) are reported separately from genuine
failures, so the success rate reflects actual errors rather than the
application's own throttle.  Running many workers against a single demo tenant
will saturate that tenant's rate-limit bucket; raise ``RATE_LIMIT_PER_MINUTE``
on the server for a saturation run.  ``--tenants`` spreads load across several
buckets and is only meaningful under ``AUTH_MODE=api_key``, where each tenant
is mapped to its own API key (see ``API_KEYS_JSON``).

Usage:
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --duration 60
    python scripts/load_test.py --base-url http://127.0.0.1:8000 \
        --concurrency 10 --duration 30 --poll-turn-jobs

Requires a running Helix Support instance with AUTH_MODE=demo.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LoadTestConfig:
    base_url: str
    api_key: str = "helix-demo-key"
    concurrency: int = 5
    duration_seconds: int = 30
    ramp_up_seconds: int = 5
    tenants: list[str] = field(default_factory=lambda: ["demo"])
    poll_turn_jobs: bool = False
    base_urls: list[str] = field(default_factory=list)

    def tenant_for(self, worker_index: int) -> str:
        return self.tenants[worker_index % len(self.tenants)]

    def base_url_for(self, worker_index: int) -> str:
        """Round-robin across instances (Phase 30.3 multi-instance scenario)."""
        if self.base_urls:
            return self.base_urls[worker_index % len(self.base_urls)]
        return self.base_url


@dataclass
class RequestResult:
    operation: str
    status_code: int
    duration_ms: float
    success: bool
    rate_limited: bool = False
    error: str | None = None
    conversation_id: str | None = None
    job_id: str | None = None


@dataclass
class LoadTestReport:
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    rate_limited: int = 0
    results: list[RequestResult] = field(default_factory=list)
    by_operation: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def success_rate(self) -> float:
        return self.successful / self.total_requests if self.total_requests else 0.0

    @property
    def p50_latency_ms(self) -> float:
        durations = [r.duration_ms for r in self.results if r.success]
        return statistics.median(durations) if durations else 0.0

    @property
    def p95_latency_ms(self) -> float:
        durations = sorted(r.duration_ms for r in self.results if r.success)
        if not durations:
            return 0.0
        idx = int(len(durations) * 0.95)
        return durations[min(idx, len(durations) - 1)]

    @property
    def throughput_rps(self) -> float:
        return self.total_requests / max(1, sum(r.duration_ms for r in self.results) / 1000)

    def record(self, result: RequestResult) -> None:
        with self._lock:
            self.results.append(result)
            self.total_requests += 1
            if result.success:
                self.successful += 1
            elif result.rate_limited:
                self.rate_limited += 1
            else:
                self.failed += 1
            self.by_operation.setdefault(result.operation, []).append(result.duration_ms)

    def to_dict(self) -> dict[str, Any]:
        ops: dict[str, dict[str, float]] = {}
        for op, durations in sorted(self.by_operation.items()):
            ops[op] = {
                "count": len(durations),
                "p50_ms": round(statistics.median(durations), 2),
                "p95_ms": round(
                    sorted(durations)[int(len(durations) * 0.95)] if durations else 0, 2
                ),
                "avg_ms": round(statistics.mean(durations), 2),
            }
        return {
            "total_requests": self.total_requests,
            "successful": self.successful,
            "failed": self.failed,
            "rate_limited": self.rate_limited,
            "success_rate": round(self.success_rate, 4),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "throughput_rps": round(self.throughput_rps, 2),
            "by_operation": ops,
        }


def _headers(config: LoadTestConfig, tenant_id: str) -> dict[str, str]:
    return {
        "X-API-Key": config.api_key,
        "X-Tenant-Id": tenant_id,
        "Content-Type": "application/json",
    }


def _result(
    operation: str, status_code: int, duration_ms: float, error: str | None = None
) -> RequestResult:
    rate_limited = status_code == 429
    ok = status_code in (200, 201, 202)
    return RequestResult(
        operation=operation,
        status_code=status_code,
        duration_ms=duration_ms,
        success=ok,
        rate_limited=rate_limited,
        error=error,
    )


def create_conversation(
    client: httpx.Client,
    config: LoadTestConfig,
    run_id: str,
    index: int,
    tenant_id: str,
) -> RequestResult:
    start = time.monotonic()
    try:
        response = client.post(
            f"{config.base_url}/api/conversations",
            headers=_headers(config, tenant_id),
            json={
                "customer_name": f"loadtest-{run_id}-{index}",
                "customer_ref": f"CUST-LT-{run_id}-{index}",
                "channel": "api",
            },
            timeout=10,
        )
        duration = (time.monotonic() - start) * 1000
        result = _result("create_conversation", response.status_code, duration)
        if result.success:
            try:
                result.conversation_id = str(response.json().get("id") or "")
            except ValueError:
                pass
        return result
    except Exception as exc:
        return _result("create_conversation", 0, (time.monotonic() - start) * 1000, str(exc))


def send_message(
    client: httpx.Client, config: LoadTestConfig, tenant_id: str, conversation_id: str
) -> RequestResult:
    start = time.monotonic()
    try:
        response = client.post(
            f"{config.base_url}/api/conversations/{conversation_id}/messages",
            headers=_headers(config, tenant_id),
            json={"content": f"Load test message {uuid4().hex[:8]}"},
            timeout=30,
        )
        duration = (time.monotonic() - start) * 1000
        return _result("send_message", response.status_code, duration)
    except Exception as exc:
        return _result("send_message", 0, (time.monotonic() - start) * 1000, str(exc))


def enqueue_turn_job(
    client: httpx.Client, config: LoadTestConfig, tenant_id: str, conversation_id: str
) -> RequestResult:
    """Submit an asynchronous turn job, returning its id on success."""
    start = time.monotonic()
    try:
        response = client.post(
            f"{config.base_url}/api/conversations/{conversation_id}/turn-jobs",
            headers=_headers(config, tenant_id),
            json={"content": f"Load test job {uuid4().hex[:8]}"},
            timeout=30,
        )
        duration = (time.monotonic() - start) * 1000
        result = _result("enqueue_turn_job", response.status_code, duration)
        if result.success:
            try:
                result.job_id = str(response.json().get("id") or "")
            except ValueError:
                pass
        return result
    except Exception as exc:
        return _result("enqueue_turn_job", 0, (time.monotonic() - start) * 1000, str(exc))


def poll_turn_job(
    client: httpx.Client, config: LoadTestConfig, tenant_id: str, job_id: str, deadlines: int = 20
) -> RequestResult:
    """Poll a queued turn job until it reaches a terminal state."""
    start = time.monotonic()
    try:
        final_status = None
        for _ in range(deadlines):
            response = client.get(
                f"{config.base_url}/api/turn-jobs/{job_id}",
                headers=_headers(config, tenant_id),
                timeout=10,
            )
            if response.status_code != 200:
                return _result(
                    "poll_turn_job", response.status_code, (time.monotonic() - start) * 1000
                )
            status = response.json().get("status")
            if status in ("completed", "succeeded", "failed"):
                final_status = status
                break
            time.sleep(0.25)
        duration = (time.monotonic() - start) * 1000
        success = final_status in ("completed", "succeeded")
        return RequestResult("poll_turn_job", 200, duration, success, error=None)
    except Exception as exc:
        return _result("poll_turn_job", 0, (time.monotonic() - start) * 1000, str(exc))


def run_load_test(config: LoadTestConfig) -> LoadTestReport:
    """Simulate the operator workload: create, converse, optionally await the turn.

    Each worker uses a dedicated httpx client and a unique per-worker run id so
    customer refs never collide.  When ``poll_turn_jobs`` is enabled each turn is
    submitted to the asynchronous queue (the same path channel workers use) and
    polled to completion, rather than answered synchronously.
    """
    report = LoadTestReport()
    end_time = time.time() + config.duration_seconds

    def worker_task(worker_index: int) -> None:
        # Phase 30.3 multi-instance: each worker pins to a round-robin instance.
        from dataclasses import replace

        worker_config = replace(config, base_url=config.base_url_for(worker_index))
        run_id = uuid4().hex[:6]
        tenant_id = config.tenant_for(worker_index)
        with httpx.Client() as client:
            counter = 0
            while time.time() < end_time:
                # Ramp-up: stagger start times so the system isn't hit cold.
                elapsed = time.time() - (end_time - config.duration_seconds)
                if elapsed < (config.ramp_up_seconds * worker_index / max(1, config.concurrency)):
                    time.sleep(0.1)
                    continue

                create_result = create_conversation(
                    client, worker_config, run_id, counter, tenant_id
                )
                report.record(create_result)
                if create_result.success and create_result.conversation_id:
                    conversation_id: str = create_result.conversation_id
                    if config.poll_turn_jobs:
                        job = enqueue_turn_job(client, worker_config, tenant_id, conversation_id)
                        report.record(job)
                        if job.success and job.job_id:
                            report.record(
                                poll_turn_job(client, worker_config, tenant_id, job.job_id)
                            )
                        else:
                            # Queue endpoint unavailable; fall back to a synchronous turn.
                            report.record(
                                send_message(client, worker_config, tenant_id, conversation_id)
                            )
                    else:
                        report.record(
                            send_message(client, worker_config, tenant_id, conversation_id)
                        )
                counter += 1

    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures = [executor.submit(worker_task, i) for i in range(config.concurrency)]
        for future in as_completed(futures):
            future.result()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Helix Support load test")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="Base URL of the instance"
    )
    parser.add_argument(
        "--base-urls",
        default="",
        help="Comma-separated base URLs for multi-instance scenario (Phase 30.3); "
        "workers round-robin across them",
    )
    parser.add_argument("--api-key", default="helix-demo-key", help="API key for authentication")
    parser.add_argument(
        "--tenants",
        default="demo",
        help="Comma-separated tenant ids to spread load across rate-limit buckets",
    )
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent workers")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--ramp-up", type=int, default=5, help="Ramp-up seconds")
    parser.add_argument(
        "--poll-turn-jobs", action="store_true", help="Poll each turn job to completion"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = LoadTestConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        tenants=[t.strip() for t in args.tenants.split(",") if t.strip()] or ["demo"],
        concurrency=args.concurrency,
        duration_seconds=args.duration,
        ramp_up_seconds=args.ramp_up,
        poll_turn_jobs=args.poll_turn_jobs,
        base_urls=[u.strip() for u in args.base_urls.split(",") if u.strip()],
    )

    logger.info(
        "Starting load test: %s, concurrency=%d, duration=%ds, tenants=%s",
        config.base_url,
        config.concurrency,
        config.duration_seconds,
        config.tenants,
    )

    # Health check
    try:
        health = httpx.get(f"{config.base_url}/health/ready", timeout=5)
        if health.status_code != 200:
            logger.error("Health check failed: %s", health.status_code)
            return 1
    except Exception as exc:
        logger.error("Cannot connect to %s: %s", config.base_url, exc)
        return 1

    start = time.monotonic()
    report = run_load_test(config)
    elapsed = time.monotonic() - start

    summary = report.to_dict()
    summary["elapsed_seconds"] = round(elapsed, 2)
    summary["actual_rps"] = round(report.total_requests / max(1, elapsed), 2)
    stats = summary["by_operation"].get("send_message", {})
    if stats and stats["count"]:
        delta_ms = stats["p95_ms"] - stats["p50_ms"]
        summary["tail_risk_95_50_ms"] = round(delta_ms, 2)

    print(json.dumps(summary, indent=2))

    if report.failed and report.failed / max(1, report.total_requests) > 0.02:
        logger.warning("Failure rate above 2%%: %d failures", report.failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
