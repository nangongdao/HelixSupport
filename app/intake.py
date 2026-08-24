"""Shared admission controls for asynchronous customer-message intake."""

from __future__ import annotations

from typing import Any

from app.config import Settings


def backpressure_reason(database: Any, settings: Settings, tenant_id: str) -> str | None:
    global_stats = database.turn_job_stats()
    if global_stats["queued"] > settings.queue_depth_threshold:
        return f"Queue overloaded ({global_stats['queued']} queued)"
    tenant_stats = database.turn_job_stats(tenant_id)
    in_flight = tenant_stats["queued"] + tenant_stats["processing"]
    if in_flight > settings.tenant_concurrent_turn_cap:
        return f"Tenant turn cap exceeded ({in_flight})"
    return None
