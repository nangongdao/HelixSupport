"""API deprecation lifecycle (ROADMAP 43.3 / docs/API_POLICY.md §2).

A breaking change never deletes an endpoint outright: it is *marked*
deprecated first — every response carries the ``Deprecation`` header (the
date deprecation began) and the ``Sunset`` header (the planned removal
date) — and only removed after the migration window has passed. The
registry below is the single source of truth: one entry per deprecated
operation, enforced by tests so a stale or expired sunset cannot linger.

Design:

* entries are keyed like OpenAPI operations (``"GET /api/conversations"``)
  so both the response-header middleware and the spec enrichment can look
  them up with the same key;
* :func:`active_deprecations` returns only entries whose window still
  applies (sunset in the future); an expired sunset raises in
  ``validate_registry`` so the release gate catches an endpoint that should
  already be gone;
* headers follow the dates format from ``docs/API_POLICY.md``
  (IMF-fixdate, e.g. ``Tue, 14 Aug 2026 00:00:00 GMT``).

The registry is empty today — v1 endpoints are all current. The mechanism,
header injection, and gate tests land now so marking any future endpoint is
a one-line registry change, not new infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any
from starlette.datastructures import MutableHeaders

logger = logging.getLogger("helix")

DEPRECATION_HEADER = "Deprecation"
SUNSET_HEADER = "Sunset"


@dataclass(frozen=True)
class Deprecation:
    """One deprecated operation and its removal timeline."""

    operation: str  # e.g. "GET /api/conversations"
    deprecated_on: str  # ISO date the Deprecation header advertises
    sunset_on: str  # ISO date the endpoint will be removed
    successor: str  # path/method clients must migrate to
    reason: str = ""


# v1 → v2 migration window policy (43.3): once /api/v2 goes GA, a v1
# operation marked here keeps serving for at least 12 months past its
# deprecation notice and at least 6 months past admin notification — the
# dates below are the contract; commercial commitments may extend them.
_DEPRECATIONS: tuple[Deprecation, ...] = ()

_REGISTRY: dict[str, Deprecation] = {entry.operation: entry for entry in _DEPRECATIONS}


def _iso_to_http(iso_date: str) -> str:
    """Render an ISO date as the IMF-fixdate the headers require."""
    parsed = datetime.fromisoformat(f"{iso_date}T00:00:00+00:00")
    return format_datetime(parsed.replace(tzinfo=UTC), usegmt=True)


def deprecation_for(operation: str) -> Deprecation | None:
    """The registered deprecation for an operation key, if any."""
    return _REGISTRY.get(operation)


def active_deprecations(*, today: str | None = None) -> list[Deprecation]:
    """Entries whose sunset has not passed yet (sorted by sunset date)."""
    now = today or datetime.now(UTC).date().isoformat()
    return sorted(
        (entry for entry in _DEPRECATIONS if entry.sunset_on >= now),
        key=lambda entry: entry.sunset_on,
    )


def validate_registry(*, today: str | None = None) -> list[str]:
    """Gate violations: expired sunsets or inverted windows.

    An empty list means the registry is coherent; a non-empty list blocks
    release (an expired sunset means the endpoint should already be gone).
    """
    now = today or datetime.now(UTC).date().isoformat()
    problems: list[str] = []
    for entry in _DEPRECATIONS:
        if entry.sunset_on < entry.deprecated_on:
            problems.append(
                f"{entry.operation}: sunset {entry.sunset_on} precedes "
                f"deprecation {entry.deprecated_on}"
            )
        if entry.sunset_on < now:
            problems.append(
                f"{entry.operation}: sunset {entry.sunset_on} has passed but the "
                "endpoint is still served — remove it or extend the window explicitly"
            )
    return problems


def apply_deprecation_headers(
    operation: str, headers: MutableHeaders | dict[str, str]
) -> bool:
    """Stamp Deprecation/Sunset onto ``headers`` when the op is deprecated.

    Returns True when headers were added. Called from response paths that
    know their operation key (middleware/dependency layer).
    """
    entry = _REGISTRY.get(operation)
    if entry is None:
        return False
    headers[DEPRECATION_HEADER] = _iso_to_http(entry.deprecated_on)
    headers[SUNSET_HEADER] = _iso_to_http(entry.sunset_on)
    if entry.successor:
        headers.setdefault("Link", f'<{entry.successor}>; rel="successor-version"')
    return True


def enrich_openapi_with_deprecations(paths: dict[str, Any]) -> int:
    """Mark deprecated operations in the OpenAPI document.

    Returns the number of operations marked. Runs inside the snapshot
    builder so ``api/openapi.json`` carries ``deprecated: true`` plus a
    description note pointing at the successor.
    """
    marked = 0
    for entry in _DEPRECATIONS:
        method, _, path = entry.operation.partition(" ")
        item = paths.get(path)
        if not isinstance(item, dict):
            continue
        operation = item.get(method.lower())
        if not isinstance(operation, dict):
            continue
        operation["deprecated"] = True
        note = (
            f"Deprecated since {entry.deprecated_on}; removed on {entry.sunset_on}. "
            f"Migrate to {entry.successor}."
        )
        if entry.reason:
            note = f"{note} {entry.reason}"
        description = str(operation.get("description") or "").rstrip()
        operation["description"] = f"{description}\n\n{note}".strip()
        marked += 1
    return marked


__all__ = [
    "DEPRECATION_HEADER",
    "SUNSET_HEADER",
    "Deprecation",
    "active_deprecations",
    "apply_deprecation_headers",
    "deprecation_for",
    "enrich_openapi_with_deprecations",
    "validate_registry",
]
