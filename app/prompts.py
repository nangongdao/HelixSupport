"""Prompt and model version registry for Phase 19.1.

Supports versioned prompts, canary deployment, and rollback.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.database import Database, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptVersion:
    """A versioned prompt with its associated model configuration."""

    id: str
    tenant_id: str | None
    name: str
    version: str
    body: str
    model_ref: str
    status: str  # draft, active, canary, retired
    created_by: str
    created_at: str
    updated_at: str
    activated_at: str | None


class PromptRegistry:
    """Manages prompt and model version lifecycle."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_version(
        self,
        tenant_id: str | None,
        name: str,
        version: str,
        body: str,
        model_ref: str,
        actor_id: str,
    ) -> PromptVersion:
        """Create a new prompt version in draft status."""
        from uuid import uuid4

        version_id = str(uuid4())
        now = utc_now()

        with self.database.audit_transaction() as conn:
            conn.execute(
                """
                INSERT INTO prompt_versions
                (id, tenant_id, name, version, body, model_ref, status,
                 created_by, created_at, updated_at, activated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, NULL)
                """,
                (version_id, tenant_id, name, version, body, model_ref, actor_id, now, now),
            )
            if tenant_id:
                payload = {"version_id": version_id, "name": name, "version": version}
                self.database.audit_in_transaction(
                    conn,
                    tenant_id,
                    None,
                    actor_id,
                    "prompt_version.created",
                    payload,
                    created_at=now,
                )

        return PromptVersion(
            id=version_id,
            tenant_id=tenant_id,
            name=name,
            version=version,
            body=body,
            model_ref=model_ref,
            status="draft",
            created_by=actor_id,
            created_at=now,
            updated_at=now,
            activated_at=None,
        )

    def get_version(self, tenant_id: str | None, version_id: str) -> PromptVersion | None:
        """Retrieve a specific prompt version by ID."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE id = ? AND (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                """,
                (version_id, tenant_id, tenant_id),
            ).fetchone()

        if not row:
            return None

        return PromptVersion(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            version=row["version"],
            body=row["body"],
            model_ref=row["model_ref"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            activated_at=row["activated_at"],
        )

    def list_versions(self, tenant_id: str | None, name: str | None = None) -> list[PromptVersion]:
        """List all prompt versions for a tenant, optionally filtered by name."""
        with self.database.connect() as conn:
            if name:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, name, version, body, model_ref, status,
                           created_by, created_at, updated_at, activated_at
                    FROM prompt_versions
                    WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                      AND name = ?
                    ORDER BY updated_at DESC
                    """,
                    (tenant_id, tenant_id, name),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, tenant_id, name, version, body, model_ref, status,
                           created_by, created_at, updated_at, activated_at
                    FROM prompt_versions
                    WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                    ORDER BY name, updated_at DESC
                    """,
                    (tenant_id, tenant_id),
                ).fetchall()

        return [
            PromptVersion(
                id=row["id"],
                tenant_id=row["tenant_id"],
                name=row["name"],
                version=row["version"],
                body=row["body"],
                model_ref=row["model_ref"],
                status=row["status"],
                created_by=row["created_by"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                activated_at=row["activated_at"],
            )
            for row in rows
        ]

    def activate(self, tenant_id: str | None, version_id: str, actor_id: str) -> PromptVersion:
        """Activate a prompt version, deactivating any previous active version."""
        with self.database.audit_transaction() as conn:
            # Get the target version
            target = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE id = ? AND (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                """,
                (version_id, tenant_id, tenant_id),
            ).fetchone()

            if not target:
                raise LookupError("Prompt version not found")

            name = target["name"]
            now = utc_now()

            # Retire any previous active version for this name
            conn.execute(
                """
                UPDATE prompt_versions
                SET status = 'retired', updated_at = ?
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'active'
                """,
                (now, tenant_id, tenant_id, name),
            )

            # Activate the target version
            conn.execute(
                """
                UPDATE prompt_versions
                SET status = 'active', updated_at = ?, activated_at = ?
                WHERE id = ?
                """,
                (now, now, version_id),
            )

            if tenant_id:
                payload = {"version_id": version_id, "name": name, "version": target["version"]}
                self.database.audit_in_transaction(
                    conn,
                    tenant_id,
                    None,
                    actor_id,
                    "prompt_version.activated",
                    payload,
                    created_at=now,
                )

        return PromptVersion(
            id=target["id"],
            tenant_id=target["tenant_id"],
            name=name,
            version=target["version"],
            body=target["body"],
            model_ref=target["model_ref"],
            status="active",
            created_by=target["created_by"],
            created_at=target["created_at"],
            updated_at=now,
            activated_at=now,
        )

    def set_canary(self, tenant_id: str | None, version_id: str, actor_id: str) -> PromptVersion:
        """Mark a prompt version as canary for A/B testing."""
        with self.database.audit_transaction() as conn:
            target = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE id = ? AND (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                """,
                (version_id, tenant_id, tenant_id),
            ).fetchone()

            if not target:
                raise LookupError("Prompt version not found")

            name = target["name"]
            now = utc_now()

            # Promoting the currently-active version would leave the name with
            # no active prompt at all (the canary only serves bucketed traffic),
            # silently disabling the registered prompt for everyone else.
            if target["status"] == "active":
                raise ValueError(
                    "cannot mark the active version as canary; activate a different version first"
                )

            # Clear any previous canary for this name
            conn.execute(
                """
                UPDATE prompt_versions
                SET status = 'draft', updated_at = ?
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'canary'
                """,
                (now, tenant_id, tenant_id, name),
            )

            # Set the target as canary
            conn.execute(
                """
                UPDATE prompt_versions
                SET status = 'canary', updated_at = ?
                WHERE id = ?
                """,
                (now, version_id),
            )

            if tenant_id:
                payload = {"version_id": version_id, "name": name, "version": target["version"]}
                self.database.audit_in_transaction(
                    conn,
                    tenant_id,
                    None,
                    actor_id,
                    "prompt_version.canary",
                    payload,
                    created_at=now,
                )

        return PromptVersion(
            id=target["id"],
            tenant_id=target["tenant_id"],
            name=name,
            version=target["version"],
            body=target["body"],
            model_ref=target["model_ref"],
            status="canary",
            created_by=target["created_by"],
            created_at=target["created_at"],
            updated_at=now,
            activated_at=target["activated_at"],
        )

    def clear_canary(
        self,
        tenant_id: str | None,
        name: str,
        actor_id: str,
        *,
        reason: str = "",
    ) -> PromptVersion | None:
        """Revert the canary version of ``name`` back to draft (ROADMAP 43.5).

        This is the inverse of :meth:`set_canary` and the automated half of
        drift response: when live signals breach their thresholds the canary
        channel is closed so 100% of traffic returns to the active version.
        The cleared version keeps its body/history; re-canarying it later is a
        normal operator action. Returns the cleared version, or None when the
        name had no canary.
        """
        with self.database.audit_transaction() as conn:
            target = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'canary'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (tenant_id, tenant_id, name),
            ).fetchone()
            if target is None:
                return None
            now = utc_now()
            conn.execute(
                "UPDATE prompt_versions SET status = 'draft', updated_at = ? WHERE id = ?",
                (now, target["id"]),
            )
            payload = {
                "version_id": target["id"],
                "name": name,
                "version": target["version"],
                "reason": reason,
            }
            self.database.audit_in_transaction(
                conn,
                tenant_id or "",
                None,
                actor_id,
                "prompt_version.canary_cleared",
                payload,
                created_at=now,
            )
            return PromptVersion(
                id=target["id"],
                tenant_id=target["tenant_id"],
                name=name,
                version=target["version"],
                body=target["body"],
                model_ref=target["model_ref"],
                status="draft",
                created_by=target["created_by"],
                created_at=target["created_at"],
                updated_at=now,
                activated_at=target["activated_at"],
            )

    def rollback(self, tenant_id: str | None, name: str, actor_id: str) -> PromptVersion | None:
        """Rollback to the most recently retired active version."""
        with self.database.audit_transaction() as conn:
            # Find the most recently retired version that was previously active
            retired = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'retired'
                  AND activated_at IS NOT NULL
                ORDER BY activated_at DESC
                LIMIT 1
                """,
                (tenant_id, tenant_id, name),
            ).fetchone()

            if not retired:
                return None

            now = utc_now()

            # Retire the current active version
            conn.execute(
                """
                UPDATE prompt_versions
                SET status = 'retired', updated_at = ?
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'active'
                """,
                (now, tenant_id, tenant_id, name),
            )

            # Reactivate the retired version
            conn.execute(
                """
                UPDATE prompt_versions
                SET status = 'active', updated_at = ?
                WHERE id = ?
                """,
                (now, retired["id"]),
            )

            if tenant_id:
                payload = {
                    "version_id": retired["id"],
                    "name": name,
                    "version": retired["version"],
                }
                self.database.audit_in_transaction(
                    conn,
                    tenant_id,
                    None,
                    actor_id,
                    "prompt_version.rollback",
                    payload,
                    created_at=now,
                )

        return PromptVersion(
            id=retired["id"],
            tenant_id=retired["tenant_id"],
            name=name,
            version=retired["version"],
            body=retired["body"],
            model_ref=retired["model_ref"],
            status="active",
            created_by=retired["created_by"],
            created_at=retired["created_at"],
            updated_at=now,
            activated_at=retired["activated_at"],
        )

    def get_active_prompt(self, tenant_id: str | None, name: str) -> PromptVersion | None:
        """Get the currently active prompt version for a given name."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'active'
                ORDER BY activated_at DESC
                LIMIT 1
                """,
                (tenant_id, tenant_id, name),
            ).fetchone()

        if not row:
            return None

        return PromptVersion(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            version=row["version"],
            body=row["body"],
            model_ref=row["model_ref"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            activated_at=row["activated_at"],
        )

    def get_canary_prompt(self, tenant_id: str | None, name: str) -> PromptVersion | None:
        """Get the canary prompt version for a given name, if any."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, name, version, body, model_ref, status,
                       created_by, created_at, updated_at, activated_at
                FROM prompt_versions
                WHERE (tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL))
                  AND name = ?
                  AND status = 'canary'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (tenant_id, tenant_id, name),
            ).fetchone()

        if not row:
            return None

        return PromptVersion(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            version=row["version"],
            body=row["body"],
            model_ref=row["model_ref"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            activated_at=row["activated_at"],
        )

    @staticmethod
    def canary_bucket(conversation_id: str) -> float:
        """Map a conversation id to a stable [0, 1) bucket.

        Deterministic across processes and restarts, so the same conversation
        always lands on the same prompt channel -- the property the canary
        routing contract depends on (Phase 19.2).
        """
        digest = hashlib.sha256(conversation_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / (2**64)

    def resolve_prompt(
        self,
        tenant_id: str | None,
        name: str,
        conversation_id: str,
        canary_ratio: float,
    ) -> tuple[PromptVersion | None, str]:
        """Resolve the prompt version a turn should use.

        Tenant-scoped versions take precedence over global (tenant_id=NULL)
        ones for both active and canary, so a tenant can override a global
        prompt without touching it. When a canary exists and ``canary_ratio``
        is positive, conversations hashed below the ratio are routed to canary
        and the rest use active. Returns ``(version, channel)`` where channel
        is ``"canary"``, ``"active"``, or ``"default"`` -- the last means no
        version is registered and the caller must fall back to the built-in
        agent default (backward compatibility).
        """
        active = self.get_active_prompt(tenant_id, name)
        canary = self.get_canary_prompt(tenant_id, name)
        if tenant_id is not None:
            if active is None:
                active = self.get_active_prompt(None, name)
            if canary is None:
                canary = self.get_canary_prompt(None, name)

        if canary is not None and canary_ratio > 0:
            if self.canary_bucket(conversation_id) < canary_ratio:
                return canary, "canary"
        if active is not None:
            return active, "active"
        return None, "default"
