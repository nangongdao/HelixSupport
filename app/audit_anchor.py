"""Audit evidence external anchoring (Phase 41.3, SEC-005).

Periodically sign the audit-chain tip ``{last_seq, last_hash, timestamp,
environment}`` with an asymmetric KMS key and write the anchored claim to
WORM / object-lock storage.  Recovery verification then checks three
independent layers — the local chain, the retention archive manifests, and
the external anchors — so that recomputing the local chain or rewriting a
manifest is not sufficient to launder an edited history.

The claim is a small canonical document:

    schema_version / environment / last_seq / last_hash / timestamp

signed with Ed25519.  Each anchor carries the signer's ``kid`` (the sha256
of the public key) plus the public key itself: verification is trust-on-first-
use, and the embedded key lets recovery still verify historical anchors even
if the KMS key was rotated away.  ``kid`` also detects a swapped signing key,
so a rotated key keeps signing but the new kid is only trusted once its
rotation is recorded (``trusted_kids``).

No real KMS or WORM bucket exists on a dev workstation, so both are swappable
protocols: the signer is any object exposing ``sign()`` (the Ed25519 default
stands in for KMS), and WORM storage is any object exposing ``write_once()`` /
``read_all()`` (``DiskWormStore`` stands in for object lock).
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ANCHOR_SCHEMA_VERSION = 1
DEFAULT_SIGNATURE_SCHEME = "ed25519"


def compute_claim_digest(
    *,
    environment: str,
    last_seq: int,
    last_hash: str,
    timestamp: str,
) -> str:
    """Canonical claim string, stable across sign and verify."""
    return (
        f"schema_version={ANCHOR_SCHEMA_VERSION}\n"
        f"environment={environment}\n"
        f"last_seq={last_seq}\n"
        f"last_hash={last_hash}\n"
        f"timestamp={timestamp}"
    )


def public_key_id(public_key: bytes) -> str:
    """Stable ``kid`` for a raw Ed25519 public key (sha256 prefix)."""
    return f"ed25519-{hashlib.sha256(public_key).hexdigest()[:12]}"


class KmsSignerProtocol(Protocol):
    def sign(
        self,
        *,
        environment: str,
        last_seq: int,
        last_hash: str,
        timestamp: str = "",
    ) -> tuple[str, bytes, bytes]:
        """Return ``(kid, raw_public_key_bytes, raw_signature_bytes)``."""
        raise NotImplementedError


@dataclass(frozen=True)
class Ed25519KmsSigner:
    """Stand-in for a KMS asymmetric signing key (accepts rotated keys)."""

    private_key: bytes
    signature_scheme: str = DEFAULT_SIGNATURE_SCHEME

    @classmethod
    def generate(cls) -> "Ed25519KmsSigner":
        return cls(
            private_key=ed25519.Ed25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    @property
    def public_key(self) -> bytes:
        key = ed25519.Ed25519PrivateKey.from_private_bytes(self.private_key)
        return key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def kid(self) -> str:
        return public_key_id(self.public_key)

    def sign(
        self,
        *,
        environment: str,
        last_seq: int,
        last_hash: str,
        timestamp: str = "",
    ) -> tuple[str, bytes, bytes]:
        if timestamp == "":
            timestamp = utc_now()
        digest = compute_claim_digest(
            environment=environment,
            last_seq=last_seq,
            last_hash=last_hash,
            timestamp=timestamp,
        )
        key = ed25519.Ed25519PrivateKey.from_private_bytes(self.private_key)
        signature = key.sign(digest.encode("utf-8"))
        return self.kid, self.public_key, signature


def build_anchor_claim(
    *,
    environment: str,
    last_seq: int,
    last_hash: str,
    signer: KmsSignerProtocol,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Construct an immutable anchored claim document.

    The timestamp is determined here (deterministic when pinned, otherwise a
    UTC clock read) and handed to the signer, so the stored claim always
    carries exactly the timestamp that was signed.  The document is
    self-verifiable offline: it embeds the public key so recovery does not
    depend on KMS availability.
    """
    signed_timestamp = timestamp or utc_now()
    kid, public_key, signature = signer.sign(
        environment=environment,
        last_seq=last_seq,
        last_hash=last_hash,
        timestamp=signed_timestamp,
    )
    return {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "kind": "audit_anchor",
        "anchor_id": f"anc_{uuid4().hex}",
        "signature_scheme": getattr(signer, "signature_scheme", DEFAULT_SIGNATURE_SCHEME),
        "environment": environment,
        "last_seq": int(last_seq),
        "last_hash": str(last_hash),
        "timestamp": signed_timestamp,
        "kid": kid,
        "public_key": base64.b64encode(public_key).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_signed_anchor(
    claim: Mapping[str, Any],
    *,
    trusted_kids: Sequence[str] = (),
) -> list[str]:
    """Validate one exported anchor against its embedded signature.

    Returns a list of problems; empty means structurally valid.  ``kid`` must
    match the embedded public key (rotation integrity) and be trusted.
    """
    problems: list[str] = []
    required = {
        "schema_version",
        "environment",
        "last_seq",
        "last_hash",
        "timestamp",
        "kid",
        "public_key",
        "signature",
        "anchor_id",
    }
    missing = sorted(required - set(claim))
    if missing:
        return [f"anchor is missing fields: {', '.join(missing)}"]
    try:
        last_seq = int(claim["last_seq"])
    except (TypeError, ValueError):
        return ["anchor last_seq is not an integer"]
    if claim["schema_version"] != ANCHOR_SCHEMA_VERSION:
        problems.append(
            f"anchor schema_version {claim['schema_version']} != {ANCHOR_SCHEMA_VERSION}"
        )
    if claim.get("signature_scheme") != DEFAULT_SIGNATURE_SCHEME:
        problems.append(f"anchor signature_scheme {claim.get('signature_scheme')!r} unsupported")
    try:
        public_key = base64.b64decode(str(claim["public_key"]))
        signature = base64.b64decode(str(claim["signature"]))
    except Exception:
        problems.append("anchor public_key/signature are not valid base64")
        return problems
    if len(public_key) != 32:
        problems.append("anchor public_key is not an Ed25519 key")
    if len(signature) != 64:
        problems.append("anchor signature is not an Ed25519 signature")
    computed_kid = public_key_id(public_key)
    if computed_kid != str(claim["kid"]):
        problems.append(
            f"anchor kid {claim['kid']} does not match embedded public key ({computed_kid})"
        )
    if trusted_kids and str(claim["kid"]) not in set(trusted_kids):
        problems.append(f"anchor kid {claim['kid']} is not in the trusted set")
    digest = compute_claim_digest(
        environment=str(claim["environment"]),
        last_seq=last_seq,
        last_hash=str(claim["last_hash"]),
        timestamp=str(claim["timestamp"]),
    )
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, digest.encode("utf-8")
        )
    except InvalidSignature:
        problems.append("anchor signature does not match the claim")
    except Exception as exc:
        problems.append(f"anchor signature check failed: {exc}")
    return problems


def verify_anchor_vs_head(
    claim: Mapping[str, Any],
    *,
    chain_head: str,
    chain_last_seq: int,
    environment: str,
    previous_last_seq: int | None = None,
) -> list[str]:
    """Validate an anchor against the locally recomputed chain.

    Chain ``last_seq``/``last_hash`` are the authoritative tip; the anchor must
    match it, belong to this environment, and advance the anchored sequence
    monotonically past the previous anchor.
    """
    problems: list[str] = []
    if str(claim.get("environment")) != environment:
        problems.append(f"anchor environment {claim.get('environment')!r} != {environment!r}")
    try:
        anchored_seq = int(claim["last_seq"])
    except (TypeError, ValueError):
        return problems + ["anchor last_seq is not an integer"]
    if str(claim.get("last_hash", "")) != chain_head:
        problems.append(
            f"anchor last_hash {claim.get('last_hash', '')!r} != local chain head {chain_head!r}"
        )
        return problems
    if anchored_seq != chain_last_seq:
        problems.append(f"anchor last_seq {anchored_seq} != local chain last_seq {chain_last_seq}")
    if previous_last_seq is not None and anchored_seq <= previous_last_seq:
        problems.append(
            f"anchor last_seq {anchored_seq} does not advance past "
            f"the previous anchor ({previous_last_seq})"
        )
    return problems


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds")


__all__ = [
    "ANCHOR_SCHEMA_VERSION",
    "DEFAULT_SIGNATURE_SCHEME",
    "Ed25519KmsSigner",
    "build_anchor_claim",
    "compute_claim_digest",
    "public_key_id",
    "verify_anchor_vs_head",
    "verify_signed_anchor",
]
