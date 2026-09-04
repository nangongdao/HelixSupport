"""HTTP connector reference implementation (Phase 20.3).

A template for wiring real CRM/order systems behind the connector contracts
defined in :mod:`app.connectors`. It shows the shape an integration takes:
base URL + secrets, HMAC-signed requests, enforced timeouts, and HTTP-status
to connector-result mapping that lets the resilient guard (Phase 20.1) treat
timeouts and 5xx as retryable transient errors.

This is a reference, not a hard dependency: the sandbox connectors remain the
default, and a real integration supplies its own config + secrets (via
environment, never committed).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors import CustomerLookup, CustomerProfile, KnowledgeHit, OrderDetails, OrderLookup
from app.connectors_runtime import TransientConnectorError

logger = logging.getLogger(__name__)


class HttpConnectorError(RuntimeError):
    """A non-transient HTTP connector failure (4xx other than 404)."""


@dataclass(frozen=True)
class HttpConnectorConfig:
    """Configuration for an HTTP connector integration (Phase 20.3)."""

    base_url: str
    timeout_seconds: float = 10.0
    signature_secret: str = ""
    signature_header: str = "X-Helix-Signature"
    signature_timestamp_header: str = "X-Helix-Timestamp"
    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


# A transport returns (status_code, parsed_json_or_empty_dict). Injectable so
# tests can simulate any response without a live server.
Transport = Callable[[str, str, dict[str, str], dict[str, str], float], tuple[int, dict[str, Any]]]


def _default_transport(
    method: str,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    """Send a real HTTP request via httpx and return (status, json)."""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise TransientConnectorError(f"http transport error: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _canonical_query(params: dict[str, str] | None) -> str:
    """Stable ``k=v`` encoding so HMAC covers query parameters in key order."""
    if not params:
        return ""
    return "&".join(f"{key}={value}" for key, value in sorted(params.items()))


def _hmac_sign(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    body: bytes = b"",
    params: dict[str, str] | None = None,
) -> str:
    """HMAC-SHA256 over method/path/canonical-query/timestamp/body.

    Query parameters (tenant_id, customer_ref, q) are part of the signed
    message so a relay cannot swap them without invalidating the signature.
    """
    message = f"{method}\n{path}\n{_canonical_query(params)}\n{timestamp}\n".encode() + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class HttpOrderConnector:
    """Reference OrderConnector backed by an HTTP order system (Phase 20.3).

    GET {base_url}/orders/{order_id}?tenant_id=&customer_ref=
    - 200 -> OrderLookup(ok, code=ok, order=...)
    - 404 -> not_found
    - 429/5xx / timeout / network -> TransientConnectorError (retryable)
    - other 4xx -> HttpConnectorError (non-transient)
    - missing customer_ref -> identity_required (local, no call made)
    """

    def __init__(self, config: HttpConnectorConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _default_transport

    def lookup_order(self, tenant_id: str, customer_ref: str | None, order_id: str) -> OrderLookup:
        if not customer_ref:
            return OrderLookup(ok=False, code="identity_required")
        path = f"/orders/{order_id}"
        params = {"tenant_id": tenant_id, "customer_ref": customer_ref}
        timestamp = str(int(time.time()))
        signature = _hmac_sign(self.config.signature_secret, "GET", path, timestamp, params=params)
        headers = {
            self.config.signature_header: signature,
            self.config.signature_timestamp_header: timestamp,
        }
        url = self.config.base_url.rstrip("/") + path
        status, payload = self._transport("GET", url, params, headers, self.config.timeout_seconds)
        if status == 200:
            if not isinstance(payload, dict):
                raise HttpConnectorError("order lookup failed: invalid payload")
            raw = payload.get("order")
            order = raw if isinstance(raw, dict) else payload
            if not isinstance(order, dict) or not str(order.get("status", "")).strip():
                # A 200 with an empty/unknown body is not a verified order;
                # telling the customer the order exists with an empty status
                # would be fabricated data. Treat as not found.
                return OrderLookup(ok=False, code="not_found")
            return OrderLookup(
                ok=True,
                code="ok",
                order=OrderDetails(
                    id=str(order.get("id", order_id)),
                    status=str(order.get("status", "")),
                    eta=order.get("eta"),
                    tracking_code=order.get("tracking_code"),
                ),
            )
        if status == 404:
            return OrderLookup(ok=False, code="not_found")
        if status in self.config.retryable_status_codes:
            raise TransientConnectorError(f"order lookup transient: {status}")
        raise HttpConnectorError(f"order lookup failed: HTTP {status}")


class HttpCRMConnector:
    """Reference CRMConnector backed by an HTTP customer profile system.

    GET {base_url}/customers/{customer_ref}?tenant_id=
    - 200 -> CustomerLookup(ok, code=ok, profile=...)
    - 404 -> CustomerLookup(ok=False, code=not_found)
    - 429/5xx / timeout / network -> TransientConnectorError
    - other 4xx -> HttpConnectorError
    """

    def __init__(self, config: HttpConnectorConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _default_transport

    def resolve_customer(self, tenant_id: str, customer_ref: str) -> CustomerLookup:
        path = f"/customers/{customer_ref}"
        params = {"tenant_id": tenant_id}
        timestamp = str(int(time.time()))
        signature = _hmac_sign(self.config.signature_secret, "GET", path, timestamp, params=params)
        headers = {
            self.config.signature_header: signature,
            self.config.signature_timestamp_header: timestamp,
        }
        url = self.config.base_url.rstrip("/") + path
        status, payload = self._transport("GET", url, params, headers, self.config.timeout_seconds)
        if status == 200:
            if not isinstance(payload, dict):
                raise HttpConnectorError("crm lookup failed: invalid payload")
            if not str(payload.get("name", "")).strip():
                # A 200 with no profile data is not a verified identity;
                # downstream code treats ok as verified and would proceed with
                # a fabricated customer ref. Treat as not found.
                return CustomerLookup(ok=False, code="not_found")
            return CustomerLookup(
                ok=True,
                code="ok",
                profile=CustomerProfile(
                    customer_ref=str(payload.get("customer_ref", customer_ref)),
                    name=str(payload.get("name", "")),
                ),
            )
        if status == 404:
            return CustomerLookup(ok=False, code="not_found")
        if status in self.config.retryable_status_codes:
            raise TransientConnectorError(f"crm lookup transient: {status}")
        raise HttpConnectorError(f"crm lookup failed: HTTP {status}")


class HttpKnowledgeConnector:
    """Reference KnowledgeConnector backed by an HTTP knowledge service.

    GET {base_url}/knowledge?tenant_id=&q=&limit=
    - 200 -> list[KnowledgeHit] (empty list is a valid no-match)
    - 429/5xx / timeout / network -> TransientConnectorError
    - other 4xx -> HttpConnectorError
    """

    def __init__(self, config: HttpConnectorConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _default_transport

    def search(self, tenant_id: str, query: str, *, limit: int = 3) -> list[KnowledgeHit]:
        path = "/knowledge"
        params = {"tenant_id": tenant_id, "q": query, "limit": str(limit)}
        timestamp = str(int(time.time()))
        signature = _hmac_sign(self.config.signature_secret, "GET", path, timestamp, params=params)
        headers = {
            self.config.signature_header: signature,
            self.config.signature_timestamp_header: timestamp,
        }
        url = self.config.base_url.rstrip("/") + path
        status, payload = self._transport("GET", url, params, headers, self.config.timeout_seconds)
        if status == 200:
            hits = payload.get("hits") if isinstance(payload, dict) else payload
            if not isinstance(hits, list):
                hits = []
            return [
                KnowledgeHit(
                    article_id=str(hit.get("article_id", hit.get("id", ""))),
                    title=str(hit.get("title", "")),
                    content=str(hit.get("content", "")),
                    score=float(hit.get("score") or 0.0),
                    source_url=hit.get("source_url"),
                    version=str(hit["version"]) if hit.get("version") is not None else None,
                )
                for hit in hits
                if isinstance(hit, dict)
            ]
        if status in self.config.retryable_status_codes:
            raise TransientConnectorError(f"knowledge search transient: {status}")
        raise HttpConnectorError(f"knowledge search failed: HTTP {status}")
