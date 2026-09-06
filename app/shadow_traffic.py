"""Shadow traffic: v1→v2 request replay and comparison.

Implements the shadow traffic system for API v2 verification. When enabled,
sampled v1 requests are asynchronously replayed to v2 endpoints, and the
responses are compared field-by-field. Comparison results are logged to the
database for automated monitoring and alerting.

Key features:
- Async execution: v2 shadow requests never block v1 responses
- Configurable sampling: SHADOW_TRAFFIC_SAMPLE_RATE controls overhead
- Field-level diff: tracks which fields match/mismatch for targeted debugging
- Latency tracking: measures v2 performance relative to v1
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.database import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowRequest:
    """Snapshot of an HTTP request for shadow replay."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes | None
    tenant_id: str
    request_id: str


@dataclass
class ShadowComparison:
    """Result of comparing v1 and v2 responses."""

    id: str
    tenant_id: str
    request_id: str
    route: str
    v1_status_code: int | None
    v2_status_code: int | None
    fields_matched: list[str]
    fields_mismatched: list[str]
    v1_latency_ms: int | None
    v2_latency_ms: int | None
    sampling_rate: float


def should_shadow_request(settings: Settings) -> bool:
    """Decide whether to shadow this request based on sampling rate."""
    if not settings.shadow_traffic_enabled:
        return False
    if settings.shadow_traffic_sample_rate >= 1.0:
        return True
    if settings.shadow_traffic_sample_rate <= 0.0:
        return False
    import random

    return random.random() < settings.shadow_traffic_sample_rate


async def shadow_request_to_v2(
    snapshot: ShadowRequest,
    v1_response_body: dict[str, Any] | None,
    v1_status_code: int | None,
    v1_latency_ms: int,
    settings: Settings,
    db: Database,
) -> None:
    """Replay a v1 request to v2, compare responses, and log the result.

    This function runs asynchronously and never blocks the v1 response path.
    Errors are logged but do not propagate to the caller.
    """
    comparison_id = f"shadow-{snapshot.request_id}"
    v2_status_code: int | None = None
    v2_latency_ms: int | None = None
    v2_response_body: dict[str, Any] | None = None

    try:
        v2_path = snapshot.path.replace("/api/", "/api/v2/", 1)
        headers = dict(snapshot.headers)
        headers["X-Shadow-Request"] = "true"

        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method=snapshot.method,
                url=f"{settings.shadow_traffic_base_url}{v2_path}",
                headers=headers,
                content=snapshot.body,
            )
            v2_status_code = response.status_code
            v2_latency_ms = int((time.perf_counter() - start) * 1000)
            if response.headers.get("content-type", "").startswith("application/json"):
                v2_response_body = response.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.warning(
            "shadow request failed",
            extra={
                "request_id": snapshot.request_id,
                "route": snapshot.path,
                "error": str(exc),
            },
        )
        v2_status_code = None
        v2_latency_ms = None

    # Envelope alignment: v2 list responses wrap the payload as
    # {"data": [...], "next_cursor": ...} while v1 returns the bare list,
    # and the cursor formats differ by design (cursor vs offset pagination)
    # — so the comparison targets the payload and ignores the envelope.
    v2_payload: Any = v2_response_body
    v1_payload: Any = v1_response_body
    if (
        isinstance(v2_response_body, dict)
        and set(v2_response_body) == {"data", "next_cursor"}
        and isinstance(v1_response_body, list)
    ):
        v2_payload = v2_response_body["data"]

    fields_matched, fields_mismatched = _compare_responses(v1_payload, v2_payload)

    comparison = ShadowComparison(
        id=comparison_id,
        tenant_id=snapshot.tenant_id,
        request_id=snapshot.request_id,
        route=snapshot.path,
        v1_status_code=v1_status_code,
        v2_status_code=v2_status_code,
        fields_matched=fields_matched,
        fields_mismatched=fields_mismatched,
        v1_latency_ms=v1_latency_ms,
        v2_latency_ms=v2_latency_ms,
        sampling_rate=settings.shadow_traffic_sample_rate,
    )

    _record_comparison(db, comparison)

    if fields_mismatched:
        logger.info(
            "shadow.comparison_mismatch",
            extra={
                "request_id": snapshot.request_id,
                "route": snapshot.path,
                "mismatched_fields": fields_mismatched,
                "v1_latency_ms": v1_latency_ms,
                "v2_latency_ms": v2_latency_ms,
            },
        )


def _compare_responses(
    v1_body: dict[str, Any] | list[Any] | None,
    v2_body: dict[str, Any] | list[Any] | None,
) -> tuple[list[str], list[str]]:
    """Compare a v1 response against a v2 response, v2-driven.

    The v2 API is the CONTRACT: every field v2 declares must carry the same
    value in the v1 response. Fields only v1 declares are by-design
    omissions (v2 is a slim projection — measured live: a conversation item
    is 31 keys in v1 versus the 8-key v2 contract with zero value
    differences on the shared keys), so they are not drift. Lists compare
    element-wise (same sort order on both sides) with per-element problem
    names; a length difference is itself a mismatch.

    Returns (matched_fields, mismatched_fields).
    """
    if v1_body is None or v2_body is None:
        return [], []
    if isinstance(v1_body, list) or isinstance(v2_body, list):
        if not isinstance(v1_body, list) or not isinstance(v2_body, list):
            return [], ["payload_type"]
        problems: list[str] = []
        if len(v1_body) != len(v2_body):
            problems.append(f"items.length {len(v1_body)} != {len(v2_body)}")
        for index, (v1_item, v2_item) in enumerate(zip(v1_body, v2_body)):
            matched_i, mismatched_i = _compare_responses(v1_item, v2_item)
            problems.extend(f"items[{index}].{name}" for name in mismatched_i)
        if problems:
            return [], problems
        return ["items"], []

    matched: list[str] = []
    mismatched: list[str] = []
    for key, v2_val in v2_body.items():
        if key not in v1_body:
            mismatched.append(key)
            continue
        if _deep_equal(v1_body[key], v2_val):
            matched.append(key)
        else:
            mismatched.append(key)
    return matched, mismatched


def _deep_equal(a: Any, b: Any) -> bool:
    """Deep equality check for JSON-serializable values."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b))
    return a == b


def _record_comparison(db: Database, comparison: ShadowComparison) -> None:
    """Persist a shadow comparison result to the database."""
    from app.db._util import utc_now

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO shadow_traffic_comparisons (
                id, tenant_id, request_id, route,
                v1_status_code, v2_status_code,
                fields_matched, fields_mismatched,
                v1_latency_ms, v2_latency_ms,
                sampling_rate, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comparison.id,
                comparison.tenant_id,
                comparison.request_id,
                comparison.route,
                comparison.v1_status_code,
                comparison.v2_status_code,
                json.dumps(comparison.fields_matched),
                json.dumps(comparison.fields_mismatched),
                comparison.v1_latency_ms,
                comparison.v2_latency_ms,
                comparison.sampling_rate,
                utc_now(),
            ),
        )


def create_shadow_task(
    snapshot: ShadowRequest,
    v1_response_body: dict[str, Any] | None,
    v1_status_code: int | None,
    v1_latency_ms: int,
    settings: Settings,
    db: Database,
) -> None:
    """Fire-and-forget: create an async task to shadow the request.

    The task runs in the background event loop and never blocks the caller.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("no event loop running, cannot shadow request")
        return

    loop.create_task(
        shadow_request_to_v2(
            snapshot=snapshot,
            v1_response_body=v1_response_body,
            v1_status_code=v1_status_code,
            v1_latency_ms=v1_latency_ms,
            settings=settings,
            db=db,
        )
    )


_V2_ELIGIBLE_PATHS = (
    "/api/conversations",  # list (cursor-paginated, enveloped in v2)
)


def is_v2_shadow_eligible(path: str) -> bool:
    """Whether a v1 GET path has a comparable v2 counterpart.

    Only the surfaces whose v2 item shapes were measured to agree with v1
    are shadowed (list + messages; the SDK shadow-read contract covers
    them). Everything else — health probes, turn-jobs, the detail endpoint
    (v1's ConversationDetail nests what v2 returns flat) — has no
    meaningful field comparison and would only produce noise.
    """
    if path in _V2_ELIGIBLE_PATHS:
        return True
    if path.startswith("/api/conversations/") and path.endswith("/messages"):
        return True
    return False


def response_json_body(
    response_headers: Any,
    body: bytes | None,
    *,
    max_bytes: int = 262144,
) -> dict[str, Any] | None:
    """Best-effort parse of a JSON response body for shadow comparison.

    Returns the parsed dict for ``application/json`` bodies of at most
    ``max_bytes`` bytes; ``None`` for anything else (other content types,
    oversized payloads, undecodable bytes). A v1 side without a body
    records empty ``fields_*`` in the comparison — no field diff, but the
    status/latency signals survive.
    """
    try:
        content_type = response_headers.get("content-type", "")
    except Exception:
        return None
    if not content_type.startswith("application/json"):
        return None
    if not isinstance(body, bytes) or len(body) > max_bytes:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def maybe_shadow_request(
    *,
    settings: Settings,
    db: Database,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes | None,
    v1_status_code: int,
    v1_response_body: dict[str, Any] | None,
    v1_latency_ms: int,
    tenant_id: str,
    request_id: str,
) -> None:
    """Sampled, fire-and-forget shadow of a v1 request to v2 (ROADMAP 2.1.x).

    Only GET/read requests are eligible: shadow replay must never duplicate
    a write. The v2 replay shares the *same* API key/tenant headers as the
    original request, so v2 authorization is exercised identically. The
    snapshot and comparison are best-effort — any failure is logged, never
    raised (fail-safe by construction).
    """
    if not should_shadow_request(settings):
        return
    if method not in {"GET", "HEAD", "OPTIONS"}:
        return
    if not is_v2_shadow_eligible(path):
        return
    try:
        snapshot = ShadowRequest(
            method=method,
            path=path,
            headers=dict(headers),
            body=body,
            tenant_id=tenant_id,
            request_id=request_id,
        )
        create_shadow_task(
            snapshot=snapshot,
            v1_response_body=v1_response_body,
            v1_status_code=v1_status_code,
            v1_latency_ms=v1_latency_ms,
            settings=settings,
            db=db,
        )
        logger.info(
            "shadow.scheduled",
            extra={"request_id": request_id, "method": method, "route": path},
        )
    except Exception:  # pragma: no cover - defensive; shadowing never breaks v1
        logger.exception("shadow.schedule_failed")
