"""Tenant control plane (ROADMAP 43.1 / Phase 2.0).

Splits policy from execution: the **control plane** is the authoritative
source for per-tenant plan/region/cell/feature/model policy and speaks only
in *signed, versioned configuration snapshots*; the **data plane** verifies
those snapshots and keeps serving the last-known-good policy when the
control plane is unavailable — never accepting a *policy change* it cannot
verify, and never serving a snapshot past its expiry.

Signing is HMAC-SHA256 over the canonical snapshot document with a
dedicated secret; production swaps the HMAC for KMS-backed signatures
without touching the consumers (the verify hook is one function).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.db._util import utc_now

logger = logging.getLogger("helix")

SNAPSHOT_SCHEMA = 1
DEFAULT_SNAPSHOT_TTL_SECONDS = 3600
# Plans are an explicit enum so a typo cannot silently create a tier.
PLANS = ("free", "standard", "enterprise")
# Changes to these policy facets while the control plane is unreachable are
# "high-risk" and rejected by the data plane (43.1).
_HIGH_RISK_FIELDS = ("plan", "region", "deployment_cell", "model_policy")


class ControlPlaneError(RuntimeError):
    """Base class for control-plane failures."""


class SnapshotVerificationError(ControlPlaneError):
    """A snapshot failed signature or structural verification — fail closed."""


class PolicyUnavailableError(ControlPlaneError):
    """No last-known-good policy exists (or it expired) for this tenant."""


@dataclass(frozen=True)
class TenantPolicy:
    """The full declarative policy for one tenant."""

    plan: str = "standard"
    region: str = "local"
    deployment_cell: str = "cell-default"
    feature_policy: frozenset[str] = field(default_factory=frozenset)
    model_policy: dict[str, Any] = field(default_factory=dict)
    credential_reference: str | None = None

    def __post_init__(self) -> None:
        if self.plan not in PLANS:
            raise ValueError(f"unknown plan {self.plan!r}; allowed: {list(PLANS)}")

    def canonical(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "region": self.region,
            "deployment_cell": self.deployment_cell,
            "feature_policy": sorted(self.feature_policy),
            "model_policy": self.model_policy,
            "credential_reference": self.credential_reference,
        }

    @classmethod
    def from_canonical(cls, raw: dict[str, Any]) -> "TenantPolicy":
        return cls(
            plan=str(raw.get("plan") or "standard"),
            region=str(raw.get("region") or "local"),
            deployment_cell=str(raw.get("deployment_cell") or "cell-default"),
            feature_policy=frozenset(raw.get("feature_policy") or []),
            model_policy=dict(raw.get("model_policy") or {}),
            credential_reference=raw.get("credential_reference"),
        )

    def differs_in_high_risk_fields(self, other: "TenantPolicy") -> bool:
        return any(self.canonical()[name] != other.canonical()[name] for name in _HIGH_RISK_FIELDS)


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sign_snapshot(secret: bytes, document: dict[str, Any]) -> str:
    return hmac.new(secret, _canonical_bytes(document), hashlib.sha256).hexdigest()


def verify_snapshot_signature(secret: bytes, document: dict[str, Any], signature: str) -> bool:
    expected = sign_snapshot(secret, document)
    return hmac.compare_digest(expected, signature or "")


@dataclass(frozen=True)
class ConfigSnapshot:
    """One signed, versioned policy document for one tenant."""

    tenant_id: str
    version: int
    issued_at: str
    expires_at: str
    policy: TenantPolicy
    signature: str

    def to_document(self) -> dict[str, Any]:
        return {
            "schema": SNAPSHOT_SCHEMA,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "policy": self.policy.canonical(),
        }

    @classmethod
    def from_row(cls, row: Any) -> "ConfigSnapshot":
        return cls(
            tenant_id=str(row["tenant_id"]),
            version=int(row["version"]),
            issued_at=str(row["issued_at"]),
            expires_at=str(row["expires_at"]),
            policy=TenantPolicy.from_canonical(json.loads(row["policy_json"])),
            signature=str(row["signature"]),
        )


class TenantControlPlane:
    """Authoritative per-tenant policy source; issues signed snapshots."""

    def __init__(self, database: Any, signing_secret: str) -> None:
        if len(signing_secret.encode("utf-8")) < 32:
            raise ValueError("control plane signing secret must be at least 32 bytes")
        self.database = database
        self._secret = signing_secret.encode("utf-8")

    def set_policy(
        self,
        tenant_id: str,
        policy: TenantPolicy,
        *,
        ttl_seconds: int = DEFAULT_SNAPSHOT_TTL_SECONDS,
    ) -> ConfigSnapshot:
        """Persist a new policy version and return its signed snapshot."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM tenant_control_policies "
                "WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            version = int(row["v"]) + 1
            now = utc_now()
            expiry = _shift(now, ttl_seconds)
            snapshot = ConfigSnapshot(
                tenant_id=tenant_id,
                version=version,
                issued_at=now,
                expires_at=expiry,
                policy=policy,
                signature="",
            )
            document = snapshot.to_document()
            signed = ConfigSnapshot(
                tenant_id=tenant_id,
                version=version,
                issued_at=now,
                expires_at=expiry,
                policy=policy,
                signature=sign_snapshot(self._secret, document),
            )
            connection.execute(
                """INSERT INTO tenant_control_policies
                (tenant_id, version, plan, region, deployment_cell, features_json,
                 model_policy_json, credential_reference, issued_at, expires_at, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    version,
                    policy.plan,
                    policy.region,
                    policy.deployment_cell,
                    json.dumps(sorted(policy.feature_policy)),
                    json.dumps(policy.model_policy),
                    policy.credential_reference,
                    now,
                    expiry,
                    signed.signature,
                ),
            )
        return signed

    def current_version(self, tenant_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM tenant_control_policies "
                "WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return int(row["v"])

    def issue_snapshot(self, tenant_id: str) -> ConfigSnapshot:
        """Re-issue the newest stored version as a fresh signed snapshot."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT tenant_id, version, plan, region, deployment_cell,
                          features_json, model_policy_json, credential_reference,
                          issued_at, expires_at, signature
                FROM tenant_control_policies WHERE tenant_id = ?
                ORDER BY version DESC LIMIT 1""",
                (tenant_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"no control-plane policy for tenant {tenant_id!r}")
        snapshot = _row_to_snapshot(row)
        document = snapshot.to_document()
        if not verify_snapshot_signature(self._secret, document, snapshot.signature):
            raise SnapshotVerificationError(
                f"stored policy for {tenant_id!r} fails signature verification"
            )
        return snapshot


def _row_to_snapshot(row: Any) -> ConfigSnapshot:
    """Rebuild a snapshot from the v36 column layout (policy is stored
    across typed columns, not as one JSON blob)."""
    policy = TenantPolicy(
        plan=str(row["plan"]),
        region=str(row["region"]),
        deployment_cell=str(row["deployment_cell"]),
        feature_policy=frozenset(json.loads(row["features_json"])),
        model_policy=dict(json.loads(row["model_policy_json"])),
        credential_reference=row["credential_reference"],
    )
    return ConfigSnapshot(
        tenant_id=str(row["tenant_id"]),
        version=int(row["version"]),
        issued_at=str(row["issued_at"]),
        expires_at=str(row["expires_at"]),
        policy=policy,
        signature=str(row["signature"]),
    )


class DataPlaneConfig:
    """Verifies snapshots and serves last-known-good within their TTL."""

    def __init__(
        self,
        database: Any,
        signing_secret: str,
        *,
        clock: Any = None,
    ) -> None:
        if len(signing_secret.encode("utf-8")) < 32:
            raise ValueError("control plane signing secret must be at least 32 bytes")
        self.database = database
        self._secret = signing_secret.encode("utf-8")
        self._clock = clock or _epoch_seconds
        # tenant_id → snapshot (the durable LKG store is the DB table; this
        # in-memory view mirrors what this process has accepted).
        self._accepted: dict[str, ConfigSnapshot] = {}
        self.control_plane_available = True

    # ------------------------------------------------------------- apply

    def apply_snapshot(self, snapshot: ConfigSnapshot) -> None:
        """Verify and accept a snapshot; enforces the degraded-mode rules.

        Rules (43.1): the signature must verify against the canonical
        document; an expired-at-arrival snapshot is rejected outright; and
        while the control plane is marked unavailable, a snapshot whose
        policy *changes* high-risk fields versus last-known-good is refused —
        only identical re-applies go through.
        """
        document = snapshot.to_document()
        if not verify_snapshot_signature(self._secret, document, snapshot.signature):
            raise SnapshotVerificationError(
                f"snapshot v{snapshot.version} for {snapshot.tenant_id!r} "
                "fails signature verification"
            )
        if _epoch_of(snapshot.expires_at) <= self._clock():
            raise SnapshotVerificationError(
                f"snapshot v{snapshot.version} for {snapshot.tenant_id!r} is expired"
            )
        previous = self._accepted.get(snapshot.tenant_id) or self._load_stored(snapshot.tenant_id)
        if (
            not self.control_plane_available
            and previous is not None
            and snapshot.policy.differs_in_high_risk_fields(previous.policy)
        ):
            raise ControlPlaneError(
                f"high-risk policy change for {snapshot.tenant_id!r} rejected: "
                "control plane unavailable"
            )
        self._accepted[snapshot.tenant_id] = snapshot
        logger.info(
            "control_plane.snapshot_applied tenant=%s version=%s",
            snapshot.tenant_id,
            snapshot.version,
        )

    def _load_stored(self, tenant_id: str) -> ConfigSnapshot | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT tenant_id, version, plan, region, deployment_cell,
                          features_json, model_policy_json, credential_reference,
                          issued_at, expires_at, signature
                FROM tenant_control_policies WHERE tenant_id = ?
                ORDER BY version DESC LIMIT 1""",
                (tenant_id,),
            ).fetchone()
        return _row_to_snapshot(row) if row else None

    # ------------------------------------------------------------- serve

    def effective_policy(self, tenant_id: str) -> TenantPolicy:
        """Serve last-known-good; fail closed when nothing valid remains."""
        snapshot = self._accepted.get(tenant_id) or self._load_stored(tenant_id)
        if snapshot is None:
            raise PolicyUnavailableError(f"no policy snapshot for tenant {tenant_id!r}")
        if _epoch_of(snapshot.expires_at) <= self._clock():
            raise PolicyUnavailableError(
                f"last-known-good policy for {tenant_id!r} expired at {snapshot.expires_at}"
            )
        self._accepted[tenant_id] = snapshot
        return snapshot.policy

    def set_control_plane_available(self, available: bool) -> None:
        self.control_plane_available = available


# ---------------------------------------------------------------------------
# time helpers (ISO-with-offset strings everywhere else in the codebase)
# ---------------------------------------------------------------------------


def _shift(iso_when: str, seconds: int) -> str:
    from datetime import datetime, timedelta

    moment = datetime.fromisoformat(iso_when)
    return (moment + timedelta(seconds=seconds)).isoformat()


def _epoch_of(iso_when: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso_when).timestamp()


def _epoch_seconds() -> float:
    import time

    return time.time()
