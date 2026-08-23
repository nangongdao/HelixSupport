from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.config import Settings


class Role(StrEnum):
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    OPERATOR = "operator"
    CHANNEL = "channel"
    VIEWER = "viewer"
    AUDITOR = "auditor"


ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset(
        {
            "conversation:read",
            "conversation:write",
            "operator:act",
            "knowledge:write",
            "metrics:read",
            "admin:manage",
            "tenant:manage",
            # M0 SEC-002: data-subject request permissions. Only admin holds
            # them; operators hold none of the three by default.
            "privacy:request",
            "privacy:approve",
            "privacy:execute",
            # Phase 41.4 (DATA): privacy operations console — approval board,
            # deletion-proof attestations, SLA breach re-scan. Admin-only.
            "privacy:manage",
        }
    ),
    Role.SUPERVISOR: frozenset(
        {
            "conversation:read",
            "conversation:write",
            "operator:act",
            "knowledge:write",
            "metrics:read",
        }
    ),
    Role.OPERATOR: frozenset({"conversation:read", "conversation:write", "operator:act"}),
    Role.CHANNEL: frozenset({"conversation:write"}),
    Role.VIEWER: frozenset({"conversation:read", "metrics:read"}),
    # Phase 22.3: read-only auditor — sees conversations, metrics, and the
    # audit trail, but cannot act on anything.
    Role.AUDITOR: frozenset({"conversation:read", "metrics:read", "audit:read"}),
}


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    actor_id: str
    role: Role
    credential_id: str

    def can(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


class Authenticator:
    def __init__(self, settings: Settings, credential_store: Any | None = None) -> None:
        self.settings = settings
        self.credential_store = credential_store
        self._principals = self._parse_principals(settings.effective_api_keys_json)
        # Phase 28.2: runtime-revoked credentials (by credential_id). The set
        # is seeded from the database at startup and updated by the revoke API
        # so a leaked key can be disabled without redeploying.
        self._revoked: set[str] = set()
        if settings.auth_mode == "api_key" and not self._principals:
            raise ValueError("API keys must contain at least one valid principal")

    def load_revoked(self, revoked_ids: list[str]) -> None:
        """Seed the in-memory revoked set from persisted state (startup)."""
        self._revoked = {str(item) for item in revoked_ids}

    def revoke(self, credential_id: str) -> None:
        """Revoke a credential id; all its keys stop authenticating immediately."""
        self._revoked.add(str(credential_id))

    def is_revoked(self, credential_id: str) -> bool:
        return credential_id in self._revoked

    def create_credential_with_key_ref(self, api_key: str, principal: Principal) -> None:
        """Register a live runtime credential (e.g. a newly issued API key).

        Phase 41 / SEC-004: whenever a secret is issued at runtime the registry
        must own its lifecycle row (with the *full* deterministic fingerprint)
        so rotation, expiry and future version selection all apply to it.
        """
        if not self.credential_store:
            return
        from app.credentials import key_ref_for

        key_ref = key_ref_for(api_key)
        self.credential_store.register(
            credential_id=key_ref[:12],
            type="api_key",
            tenant_id=principal.tenant_id,
            key_ref=key_ref,
            not_before="",
            expires_at=None,
            version=1,
            created_at=self._now(),
        )

    def _now(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat(timespec="microseconds")

    @staticmethod
    def _parse_principals(raw: str) -> dict[str, Principal]:
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("API_KEYS_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("API_KEYS_JSON must be a JSON object keyed by API key")

        principals: dict[str, Principal] = {}
        for api_key, value in payload.items():
            if not isinstance(api_key, str) or len(api_key) < 12 or not isinstance(value, dict):
                raise ValueError("Each API key must be at least 12 characters and map to an object")
            try:
                tenant_id = str(value["tenant_id"]).strip()
                actor_id = str(value["actor_id"]).strip()
                role = Role(str(value["role"]).strip().lower())
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    "Each API key requires tenant_id, actor_id, and a valid role"
                ) from exc
            if not tenant_id or not actor_id:
                raise ValueError("tenant_id and actor_id cannot be blank")
            principals[api_key] = Principal(
                tenant_id=tenant_id,
                actor_id=actor_id,
                role=role,
                credential_id=hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12],
            )
        return principals

    @property
    def configured_tenants(self) -> set[str]:
        if self.settings.auth_mode == "demo":
            return {"demo"}
        return {principal.tenant_id for principal in self._principals.values()}

    def authenticate(self, api_key: str | None, requested_tenant: str | None) -> Principal:
        if self.settings.auth_mode == "demo":
            if api_key and not hmac.compare_digest(api_key, self.settings.demo_api_key):
                raise AuthenticationError("Invalid API key")
            principal = Principal("demo", "demo.admin", Role.ADMIN, "demo")
        else:
            if not api_key:
                raise AuthenticationError("X-API-Key is required")
            principal = self._lookup_api_key(api_key)
            # Phase 28.2: a revoked credential stops authenticating immediately,
            # even though the key still exists in config (rotation window).
            if self.is_revoked(principal.credential_id):
                raise AuthenticationError("API key revoked")
            # Phase 41 / SEC-004: the registry is the persistent arbiter of the
            # credential lifecycle — cross-instance revocation, expiry windows
            # and not_before scheduling all resolve here on every request. For
            # unregistered keys (e.g. an old instance that never seeded this
            # table) the behavior degrades to the legacy in-memory check above.
            credential = self._credential_for_key(api_key)
            if credential is not None and not self._credential_allowed(credential):
                raise AuthenticationError("API key not allowed")

        tenant = requested_tenant.strip() if requested_tenant else principal.tenant_id
        if tenant != principal.tenant_id:
            raise AuthorizationError("The API key is not authorized for the requested tenant")
        return principal

    def _credential_for_key(self, api_key: str) -> dict[str, Any] | None:
        """Look up the registry row for a candidate key by its fingerprint."""
        if not self.credential_store:
            return None
        credential_id = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
        credential = self.credential_store.get(credential_id)
        if credential is not None and credential["key_ref"] != self.key_ref_for(api_key):
            return None
        return credential

    @staticmethod
    def key_ref_for(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def _credential_allowed(self, credential: dict[str, Any]) -> bool:
        from app.credentials import Credential

        return Credential(**credential).is_allowed(int(time.time()))

    def _lookup_api_key(self, candidate: str) -> Principal:
        for configured_key, principal in self._principals.items():
            if hmac.compare_digest(candidate, configured_key):
                return principal
        raise AuthenticationError("Invalid API key")


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int, int]:
        now = time.monotonic()
        boundary = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= boundary:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - events[0])) + 1)
                return False, 0, retry_after
            events.append(now)
            return True, self.limit - len(events), 0


def sanitize_for_audit(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower() in {"api_key", "authorization", "token", "secret"}
            else sanitize_for_audit(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_audit(item) for item in value]
    return value
