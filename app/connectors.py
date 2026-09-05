"""External connector contracts and their sandbox implementations.

The roadmap calls for real CRM / order / knowledge connectors behind stable
interfaces so integrations can be swapped without touching orchestration.
This module defines the Protocols those connectors must satisfy and provides
``Sandbox`` implementations backed by the application's own data store — the
same deterministic data the demo and golden set rely on.

Contract tests in ``tests/test_connectors.py`` enforce both the interface
shape and the behavioural rules the orchestration depends on (identity
binding, non-disclosure across customers, grounded retrieval), so a real
connector can be dropped in and its conformance verified the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class OrderDetails:
    """A customer's order, as returned by an order connector."""

    id: str
    status: str
    eta: str | None
    tracking_code: str | None


@dataclass(frozen=True)
class OrderLookup:
    """Outcome of an order lookup.

    ``code`` is ``ok``, ``identity_required``, ``not_found``, or ``unavailable``.
    """

    ok: bool
    code: str
    order: OrderDetails | None = None


@dataclass(frozen=True)
class KnowledgeHit:
    """A grounded knowledge article for an operator-facing query."""

    article_id: str
    title: str
    content: str
    score: float
    source_url: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class CustomerProfile:
    """A resolved customer identity."""

    customer_ref: str
    name: str


@dataclass(frozen=True)
class CustomerLookup:
    """Outcome of a CRM customer resolve.

    ``code`` is ``ok``, ``not_found``, or ``unavailable``.
    """

    ok: bool
    code: str
    profile: CustomerProfile | None = None


@runtime_checkable
class OrderConnector(Protocol):
    """Fetches order status for a customer.

    Must refuse to return an order to a caller who has not bound the customer's
    identity (``identity_required``) and must not leak another customer's order
    (``not_found``).
    """

    def lookup_order(
        self, tenant_id: str, customer_ref: str | None, order_id: str
    ) -> OrderLookup: ...


@runtime_checkable
class KnowledgeConnector(Protocol):
    """Retrieves grounded knowledge for an operator-facing query.

    ``draft_article`` is the ROADMAP 2.5.0 governed write: implementations
    create a ``draft``-status article that only human review can publish.
    """

    def search(self, tenant_id: str, query: str, *, limit: int = 3) -> list[KnowledgeHit]: ...

    def draft_article(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        tags: list[str],
        category: str,
        source_url: str,
        language: str | None,
        actor_id: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class CRMConnector(Protocol):
    """Resolves a customer reference to a profile, if one exists."""

    def resolve_customer(self, tenant_id: str, customer_ref: str) -> CustomerLookup: ...


class SandboxOrderConnector:
    """Order connector backed by the application's own order store."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def lookup_order(self, tenant_id: str, customer_ref: str | None, order_id: str) -> OrderLookup:
        if not customer_ref:
            return OrderLookup(ok=False, code="identity_required")
        order = self.database.get_order_for_customer(tenant_id, customer_ref, order_id)
        if order is None:
            return OrderLookup(ok=False, code="not_found")
        return OrderLookup(
            ok=True,
            code="ok",
            order=OrderDetails(
                id=order["id"],
                status=order["status"],
                eta=order.get("eta"),
                tracking_code=order.get("tracking_code"),
            ),
        )


class SandboxKnowledgeConnector:
    """Knowledge connector backed by the application's article store."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def draft_article(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        tags: list[str],
        category: str,
        source_url: str,
        language: str | None,
        actor_id: str,
    ) -> dict[str, Any]:
        """Create a ``pending_review`` draft (ROADMAP 2.5.0 mutating tool).

        The tool's whole point is a governed write; the sandbox writes
        through the same store the knowledge admin API uses, so the draft
        follows the identical review path before it can ever be retrieved.
        """
        return self.database.create_knowledge_draft(
            tenant_id,
            title,
            content,
            tags,
            category,
            source_url,
            actor_id=actor_id,
            language=language,
        )

    def search(self, tenant_id: str, query: str, *, limit: int = 3) -> list[KnowledgeHit]:
        rows = self.database.search_knowledge(tenant_id, query, limit=limit)
        return [
            KnowledgeHit(
                article_id=row["id"],
                title=row["title"],
                content=row["content"],
                score=float(
                    row.get("retrieval_score")
                    or row.get("score")
                    or row.get("fts_rank")
                    or row.get("match_rank")
                    or 0.0
                ),
                source_url=row.get("source_url"),
                version=str(row["version"]) if row.get("version") is not None else None,
            )
            for row in rows
        ]


class SandboxCRMConnector:
    """CRM connector backed by the application's customer/order store."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def resolve_customer(self, tenant_id: str, customer_ref: str) -> CustomerLookup:
        profile = self.database.get_customer_profile(tenant_id, customer_ref)
        if profile is None:
            return CustomerLookup(ok=False, code="not_found")
        return CustomerLookup(
            ok=True,
            code="ok",
            profile=CustomerProfile(
                customer_ref=profile["customer_ref"], name=profile["customer_name"]
            ),
        )
