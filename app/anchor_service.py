"""Periodic external audit anchoring (Phase 41.3, SEC-005).

``AnchorService`` signs the current audit-chain tip with an asymmetric KMS
stand-in (``KmsSignerProtocol``) and writes the claim to WORM / object-lock
storage (``WormStoreProtocol``).  Recovery verification then compares the
local chain, the archive manifests, and these external anchors: recomputing
the local chain or rewriting a manifest is not enough to launder an edited
history, because the anchors were signed and stored out-of-band.

Each claim is self-verifiable offline (it embeds the signer's public key and
kid), so historical anchors stay verifiable even after a KMS key rotation.
The signer and store are swappable protocols — ``Ed25519KmsSigner`` and
``DiskWormStore`` are the development stand-ins.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from app.audit_anchor import (
    build_anchor_claim,
    verify_anchor_vs_head,
    verify_signed_anchor,
)
from app.db._util import utc_now
from app.worm_store import WormStoreProtocol

logger = logging.getLogger(__name__)


class AnchorSinkProtocol(Protocol):
    def write_once(self, object_id: str, payload: Mapping[str, Any]) -> None:
        """Persist an anchor claim exactly once under ``object_id``."""

        ...


@dataclass
class AnchorService:
    """Periodically export signed chain-tip claims to external WORM storage.

    ``anchor_if_due`` is the housekeeping entry point (windowed so it fires at
    most once per cadence); ``anchor_now`` does the work and is what tests and
    drills call directly.  Restarting is idempotent: when the newest stored
    claim already covers the current chain tip, nothing new is written.
    """

    database: Any
    worm_store: WormStoreProtocol
    signer: Any
    environment: str
    cadence_hours: int = 24
    batch: int = 1
    trusted_kids: Sequence[str] = ()

    def __post_init__(self) -> None:
        self._next_run_at = monotonic()
        self._last_anchor: dict[str, Any] | None = None

    def anchor_if_due(self, *, force: bool = False) -> dict[str, Any] | None:
        """Export a fresh anchor when the cadence window has elapsed.

        Returns the claim written, or ``None`` when the window has not elapsed
        or the chain tip is already covered by the newest stored anchor.
        """
        now = monotonic()
        if not force and now < self._next_run_at:
            return None
        self._next_run_at = now + self.cadence_hours * 3600
        return self.anchor_now()

    def anchor_now(self) -> dict[str, Any] | None:
        """Sign the current chain tip and write it to WORM exactly once."""
        tail_hash, tail_seq = self._chain_tail()
        existing = self.read_anchors()
        if existing:
            newest = max(existing, key=lambda claim: int(claim.get("last_seq", -1) or -1))
            if (
                int(newest.get("last_seq", -1) or -1) == tail_seq
                and newest.get("last_hash") == tail_hash
            ):
                # Already anchored at this tip (e.g. after a restart); nothing
                # new to sign, so do not mint a duplicate WORM object.
                self._last_anchor = dict(newest)
                return None
        claim = build_anchor_claim(
            environment=self.environment,
            last_seq=tail_seq,
            last_hash=tail_hash or "",
            signer=self.signer,
        )
        try:
            self.worm_store.write_once(object_id=str(claim["anchor_id"]), payload=claim)
        except Exception as exc:
            logger.exception(
                "anchor.worm_write_failed",
                extra={
                    "last_seq": tail_seq,
                    "last_hash": tail_hash or "",
                    "error": str(exc),
                },
            )
            raise
        self._last_anchor = claim
        return claim

    def read_anchors(self) -> Sequence[Mapping[str, Any]]:
        """Return every stored anchor claim (raises on WORM tamper)."""
        return self.worm_store.read_all()

    def verify_anchors(self) -> dict[str, Any]:
        """Check every stored anchor: signature, kid trust, chain, monotonic.

        Returns a stable report object: ``total``, ``verified``, and a
        ``problems`` list of per-anchor messages.  ``chain_head`` the local tip
        is loaded here so callers (and the admin verify endpoint) need no DB
        access.
        """
        chain_head, chain_last_seq = self._chain_tail()
        anchors = sorted(
            self.read_anchors(),
            key=lambda claim: int(claim.get("last_seq") or 0),
        )
        problems: list[str] = []
        previous_seq: int | None = None
        verified = 0
        for claim in anchors:
            signed_problems = verify_signed_anchor(claim, trusted_kids=tuple(self.trusted_kids))
            head_problems = verify_anchor_vs_head(
                claim,
                chain_head=chain_head,
                chain_last_seq=chain_last_seq,
                environment=self.environment,
                previous_last_seq=previous_seq,
            )
            combined = signed_problems + head_problems
            if not combined:
                verified += 1
            else:
                anchor_id = str(claim.get("anchor_id") or "?")
                problems.append(f"{anchor_id}: " + "; ".join(combined))
            previous_seq = int(claim.get("last_seq") or 0)
        return {
            "environment": self.environment,
            "chain_head": chain_head,
            "chain_last_seq": chain_last_seq,
            "total": len(anchors),
            "verified": verified,
            "problems": problems,
            "checked_at": utc_now(),
        }

    def _chain_tail(self) -> tuple[str, int]:
        with self.database.connect() as connection:
            return self.database._audit_chain_tail(connection)

    @property
    def last_anchor(self) -> dict[str, Any] | None:
        return self._last_anchor


__all__ = ["AnchorService"]
