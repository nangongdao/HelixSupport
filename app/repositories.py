"""Repository protocol interfaces for persistence-layer abstraction.

These protocols define the contracts that the current SQLite ``Database``
class implicitly satisfies.  Future PostgreSQL or other backends implement
the same protocols, enabling a clean swap without touching the service layer.

The protocols are intentionally split by domain to keep each focused and
under 200 lines, following the small-file principle.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TenantRepository(Protocol):
    def ensure_tenant(self, tenant_id: str, name: str | None = None) -> None: ...
    def tenant_exists(self, tenant_id: str) -> bool: ...


@runtime_checkable
class ConversationRepository(Protocol):
    def create_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        customer_name: str,
        customer_ref: str | None,
        channel: str,
        priority: str,
        sla_due_at: str | None,
    ) -> dict[str, Any]: ...

    def get_conversation(self, tenant_id: str, conversation_id: str) -> dict[str, Any] | None: ...

    def list_conversations(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        channel: str | None = None,
        label: str | None = None,
        mine: str | None = None,
        claimed_by: str | None = None,
        unassigned: bool = False,
        unclaimed: bool = False,
        sla_breached: bool = False,
        needs_response: bool = False,
        search: str | None = None,
        sort: str = "priority",
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]: ...

    def transition_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        target: str,
        actor: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]: ...

    def update_priority(
        self,
        tenant_id: str,
        conversation_id: str,
        priority: str,
        actor: str,
    ) -> dict[str, Any]: ...

    def claim_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        actor: str,
        ttl_seconds: int,
    ) -> dict[str, Any]: ...

    def release_conversation_claim(
        self,
        tenant_id: str,
        conversation_id: str,
        actor: str,
    ) -> dict[str, Any]: ...

    def assign_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        assignee_id: str,
        actor: str,
    ) -> dict[str, Any]: ...

    def replace_conversation_labels(
        self,
        tenant_id: str,
        conversation_id: str,
        labels: list[str],
        actor: str,
    ) -> dict[str, Any]: ...

    def list_conversation_labels(self, tenant_id: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class MessageRepository(Protocol):
    def add_message(
        self,
        tenant_id: str,
        conversation_id: str,
        message_id: str,
        role: str,
        author: str,
        content: str,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def list_messages(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        limit: int = 50,
        before_cursor: str | None = None,
        after_cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, str | None]: ...

    def get_message(self, tenant_id: str, message_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class KnowledgeRepository(Protocol):
    def search_knowledge(
        self, tenant_id: str, query: str, limit: int = 3
    ) -> list[dict[str, Any]]: ...

    def list_knowledge(
        self,
        tenant_id: str,
        *,
        active: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def create_knowledge(
        self,
        tenant_id: str,
        article_id: str,
        title: str,
        content: str,
        tags: str,
        source_url: str,
        category: str,
        actor: str,
    ) -> dict[str, Any]: ...

    def update_knowledge(
        self,
        tenant_id: str,
        article_id: str,
        title: str,
        content: str,
        tags: str,
        source_url: str,
        category: str,
        actor: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class TurnJobRepository(Protocol):
    def enqueue_turn_job(
        self,
        tenant_id: str,
        job_id: str,
        conversation_id: str,
        idempotency_key: str,
        actor_id: str,
        content: str,
        max_attempts: int,
        available_at: str,
    ) -> dict[str, Any]: ...

    def get_turn_job(self, tenant_id: str, job_id: str) -> dict[str, Any] | None: ...

    def list_turn_jobs(
        self,
        tenant_id: str,
        conversation_id: str | None = None,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    def claim_next_turn_job(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None: ...

    def complete_turn_job(
        self,
        tenant_id: str,
        job_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]: ...

    def fail_turn_job(
        self,
        tenant_id: str,
        job_id: str,
        error_code: str,
        retry_base_seconds: int,
    ) -> dict[str, Any]: ...

    def recover_turn_jobs(self, lease_seconds: int) -> dict[str, int]: ...
    def prune_turn_jobs(self, retention_days: int, batch_size: int = 5000) -> int: ...
    def turn_job_stats(self, tenant_id: str | None = None) -> dict[str, Any]: ...


@runtime_checkable
class AuditRepository(Protocol):
    def audit(
        self,
        tenant_id: str,
        conversation_id: str | None,
        request_id: str | None,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    def audit_many(self, events: list[dict[str, Any]]) -> int: ...

    def list_audit(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]: ...

    def export_audit_events(
        self,
        tenant_id: str,
        *,
        event_type: str | None = None,
        conversation_id: str | None = None,
        actor: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]: ...

    def list_audit_archives(
        self, tenant_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]: ...

    def get_audit_archive(self, tenant_id: str, archive_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class FeedbackRepository(Protocol):
    def record_feedback(
        self,
        tenant_id: str,
        conversation_id: str,
        message_id: str,
        actor: str,
        rating: int,
        reason: str | None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class RetentionRepository(Protocol):
    """Data retention and data-subject-request operations."""

    def set_retention_policy(
        self, tenant_id: str, data_type: str, retention_days: int, actor: str
    ) -> dict[str, Any]: ...

    def get_retention_policies(self, tenant_id: str) -> list[dict[str, Any]]: ...

    def enforce_retention(self, tenant_id: str, data_type: str, retention_days: int) -> int: ...

    def create_data_subject_request(
        self,
        tenant_id: str,
        customer_ref: str,
        request_type: str,
        requested_by: str,
    ) -> dict[str, Any]: ...

    def execute_data_subject_deletion(
        self, tenant_id: str, customer_ref: str
    ) -> dict[str, int]: ...

    def execute_data_subject_export(self, tenant_id: str, customer_ref: str) -> dict[str, Any]: ...
