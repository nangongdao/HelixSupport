"""OIDC authorization-code flow hardening (ROADMAP 2.x M0 / SEC-001).

Replaces the previous decode-only identity extraction with a fully verified
flow:

- every ``/auth/login`` creates a one-time transaction binding ``state``,
  ``nonce``, the PKCE S256 ``code_verifier``, the redirect URI and an optional
  tenant hint; the callback consumes that row atomically and rejects expired,
  mismatched or replayed logins;
- the ID token signature is checked against the IdP JWKS (RS256 only, alg
  allowlist, fixed-length comparison) so ``alg=none``, HS256 confusion and
  unknown-kid tokens are rejected before any claim is trusted;
- ``iss``, ``aud``/``azp``, ``exp``, ``iat`` and ``nonce`` are enforced before
  claims are used;
- tenant, actor and role come only from validated claims plus the local
  ``tenant_members`` mapping; unknown members / illegal roles are rejected and
  there is no caller-controlled fallback to ``demo``/``admin``.

The RSA validation is implemented from the Python standard library so this
local-first product does not add a heavyweight native dependency on its
trusted-supply-chain critical path.  Only RS256 is accepted; verification is
pure arithmetic (``pow`` + fixed-template comparison), never a private-key
operation, so the Bleichenbacher-decryption class of vulnerabilities does not
apply.  Each IdP JWK is also constrained to RSA-2048+ public keys.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.session_auth import OIDCConfig

logger = logging.getLogger(__name__)

# PKCS#1 v1.5 DigestInfo prefix for SHA-256 (RFC 7518: RS256 = RSASSA-PKCS1-v1_5
# with SHA-256).  ``30 31 30 0d 06 09 60 86 48 01 65 03 04 02 01 05 00 04 20``
# followed by the 32-byte SHA-256 digest (RFC 8017 A.2.4 / RFC 3447).
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_DIGEST_INFO_LEN = len(_SHA256_DIGEST_INFO_PREFIX) + 32

# Login transactions older than this are pruned by the retention sweep and
# rejected by the callback even if still present.
AUTH_TX_TTL_SECONDS = 600


class OIDCFlowError(Exception):
    """A verified-OIDC failure that maps to a safe public response.

    ``public_message`` carries no provider internals, token fragments or
    secrets; internal detail is logged separately with the request id.
    """

    def __init__(
        self,
        public_message: str,
        *,
        internal_detail: str = "",
        status_code: int = 400,
    ) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.internal_detail = internal_detail
        self.status_code = status_code


# ---------------------------------------------------------------------------
# PKCE (RFC 7636)
# ---------------------------------------------------------------------------


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def generate_code_verifier() -> str:
    """Return a 64-char verifier (43 <= len <= 128, RFC 7636 §4.1)."""
    return secrets.token_urlsafe(48)


def derive_code_challenge(verifier: str) -> str:
    """S256 code_challenge for a verifier (RFC 7636 §4.2)."""
    return b64url_encode(hashlib.sha256(verifier.encode("utf-8")).digest())


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# One-time login transactions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OIDCTransaction:
    id: str
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    tenant_hint: str | None
    created_epoch: int = 0


class OIDCTransactionStore(Protocol):
    def create(self, tx: OIDCTransaction) -> None: ...

    def consume_by_state(self, state: str) -> OIDCTransaction | None:
        """Atomically fetch and remove a transaction by its ``state``."""
        ...

    def prune_expired(self, now: int) -> int:
        """Delete transactions older than the TTL; returns count deleted."""
        ...


class DatabaseOIDCTransactionStore:
    """Persist transactions in ``auth_transactions`` (migration 28)."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def create(self, tx: OIDCTransaction) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO auth_transactions
                   (id, state, nonce, code_verifier, redirect_uri, tenant_hint,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    tx.id,
                    tx.state,
                    tx.nonce,
                    tx.code_verifier,
                    tx.redirect_uri,
                    tx.tenant_hint,
                    _iso_from_epoch(tx.created_epoch),
                ),
            )

    def consume_by_state(self, state: str) -> OIDCTransaction | None:
        # One transaction may consume the row exactly once: read it inside the
        # same connection that removes it, so a concurrent callback for the
        # same state cannot observe a double-consume.
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, state, nonce, code_verifier, redirect_uri, tenant_hint, "
                "created_at "
                "FROM auth_transactions WHERE state = ? AND consumed_at IS NULL",
                (state,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE auth_transactions SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                (_iso_now(), row["id"]),
            )
        return OIDCTransaction(
            id=str(row["id"]),
            state=str(row["state"]),
            nonce=str(row["nonce"]),
            code_verifier=str(row["code_verifier"]),
            redirect_uri=str(row["redirect_uri"]),
            tenant_hint=str(row["tenant_hint"]) if row["tenant_hint"] else None,
            created_epoch=_parse_epoch(row["created_at"]),
        )

    def prune_expired(self, now: int) -> int:
        threshold = _iso_now_offset(-AUTH_TX_TTL_SECONDS)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM auth_transactions WHERE created_at < ?", (threshold,)
            )
            return cursor.rowcount


# ---------------------------------------------------------------------------
# RS256 JWT signature verification (Python standard library)
# ---------------------------------------------------------------------------


class RazorThinJwtError(Exception):
    """Signature/token-structure validation failure."""


def _decode_json_segment(segment: str, what: str) -> dict[str, Any]:
    if not segment:
        raise RazorThinJwtError(f"{what} segment is empty")
    try:
        raw = b64url_decode(segment)
        parsed: Any = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RazorThinJwtError(f"{what} segment is not valid base64url JSON") from exc
    if not isinstance(parsed, dict):
        raise RazorThinJwtError(f"{what} must be a JSON object")
    return parsed


def _assert_rs256_algorithm(header: dict[str, Any]) -> str:
    alg = header.get("alg")
    if not isinstance(alg, str) or alg != "RS256":
        raise RazorThinJwtError("ID token alg must be RS256")
    return alg


def _check_rsa_public_key(modulus: int, exponent: int) -> None:
    if modulus.bit_length() < 2048:
        raise RazorThinJwtError("RSA modulus too small (<2048 bits)")
    if exponent < 3 or exponent % 2 == 0:
        raise RazorThinJwtError("Invalid RSA public exponent")


def verify_rs256_signature(
    token: str,
    *,
    modulus: int,
    exponent: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify an RS256 JWT with a raw RSA public key; return (header, claims).

    Pure ``pow`` modular exponentiation followed by a fixed-length template
    comparison (RFC 8017 §8.2.2).  No secret material and no variable-length
    decryption are involved, so the comparison is constant-time in the digest.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise RazorThinJwtError("ID token must have three JWT segments")
    header = _decode_json_segment(parts[0], "header")
    claims = _decode_json_segment(parts[1], "claims")
    _assert_rs256_algorithm(header)
    _check_rsa_public_key(modulus, exponent)
    if not parts[2]:
        raise RazorThinJwtError("ID token signature is empty")
    signature = int.from_bytes(b64url_decode(parts[2]), "big")
    if signature >= modulus:
        raise RazorThinJwtError("Signature out of range")
    em = pow(signature, exponent, modulus).to_bytes(
        (modulus.bit_length() + 7) // 8, "big", signed=False
    )
    prefix_ok = hmac.compare_digest(em[:2], b"\x00\x01")
    if not prefix_ok:
        raise RazorThinJwtError("PKCS#1 v1.5 padding header missing")
    separator = 2
    while separator < len(em) and em[separator] == 0xFF:
        separator += 1
    if separator <= 2 or separator >= len(em) or em[separator] != 0x00:
        raise RazorThinJwtError("PKCS#1 v1.5 padding malformed")
    digest = hashlib.sha256(f"{parts[0]}.{parts[1]}".encode("ascii")).digest()
    expected = _SHA256_DIGEST_INFO_PREFIX + digest
    tail = em[separator + 1 :]
    if len(tail) != _DIGEST_INFO_LEN or not hmac.compare_digest(tail, expected):
        raise RazorThinJwtError("ID token signature verification failed")
    return header, claims


# ---------------------------------------------------------------------------
# JWKS discovery / cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JwkSet:
    keys: tuple[dict[str, Any], ...]
    fetched_at: int


class JwkResolver:
    """Fetch and cache an IdP JWKS, refreshing on expiry or unknown kid."""

    def __init__(
        self,
        jwks_uri: str,
        *,
        http_client: Any = None,
        clock: Any = time.time,
        ttl_seconds: int = 3600,
    ) -> None:
        self.jwks_uri = jwks_uri
        self._http_client = http_client
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._cache: JwkSet | None = None

    async def _fetch(self) -> JwkSet:
        import httpx

        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10)
        try:
            response = await client.get(self.jwks_uri)
            response.raise_for_status()
            payload = response.json()
            raw_keys = payload.get("keys")
            if not isinstance(raw_keys, list) or not raw_keys:
                raise RazorThinJwtError("JWKS contains no keys")
            keys = tuple(dict(k) for k in raw_keys if isinstance(k, dict))
            if not keys:
                raise RazorThinJwtError("JWKS contains no usable keys")
            self._cache = JwkSet(keys=keys, fetched_at=int(self._clock()))
        except httpx.HTTPError as exc:
            raise RazorThinJwtError("JWKS fetch failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        assert self._cache is not None
        return self._cache

    def _rsa_key(self, keys: tuple[dict[str, Any], ...], kid: str) -> dict[str, Any]:
        for key in keys:
            if key.get("kty") != "RSA" or key.get("use") not in (None, "sig"):
                continue
            try:
                modulus = int.from_bytes(b64url_decode(str(key["n"])), "big")
                exponent = int.from_bytes(b64url_decode(str(key["e"])), "big")
            except (KeyError, ValueError) as exc:
                raise RazorThinJwtError("JWK is not a well-formed RSA key") from exc
            _check_rsa_public_key(modulus, exponent)
            if not kid:
                return key
            if key.get("kid") == kid:
                return key
        raise RazorThinJwtError("No matching RSA signing key for token kid")

    async def key_for_kid(self, kid: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the RSA JWK for ``kid``, refreshing the set on cache miss."""
        now = int(self._clock())
        cache = self._cache
        if cache is None or now - cache.fetched_at >= self._ttl_seconds:
            await self._fetch()
            cache = self._cache
        assert cache is not None
        try:
            key = self._rsa_key(cache.keys, kid)
        except RazorThinJwtError:
            # A rotated-out kid (or a cache fetched before rotation) justifies
            # one refresh; a second failure is a genuine unknown kid.
            await self._fetch()
            cache = self._cache
            assert cache is not None
            key = self._rsa_key(cache.keys, kid)
        modulus = int.from_bytes(b64url_decode(str(key["n"])), "big")
        exponent = int.from_bytes(b64url_decode(str(key["e"])), "big")
        return key, {"n": modulus, "e": exponent}


# ---------------------------------------------------------------------------
# Issuer / audience / time / nonce enforcement + identity mapping
# ---------------------------------------------------------------------------


def _as_int(value: Any, claim: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise RazorThinJwtError(f"ID token {claim} claim is missing or invalid")
    return value


def enforce_token_claims(
    claims: dict[str, Any],
    *,
    expected_issuer: str,
    expected_audience: str,
    expected_nonce: str,
    now: int,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Enforce issuer, audience/azp, exp, iat and nonce on validated claims."""
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or issuer != expected_issuer:
        raise RazorThinJwtError("ID token issuer does not match")
    audience = claims.get("aud")
    azp = claims.get("azp")
    if isinstance(audience, str):
        aud_ok = audience == expected_audience
    elif isinstance(audience, list):
        aud_ok = expected_audience in audience
    else:
        aud_ok = False
    if not aud_ok:
        raise RazorThinJwtError("ID token audience does not match")
    if azp is not None and azp != expected_audience:
        raise RazorThinJwtError("ID token authorized party does not match")
    exp = _as_int(claims.get("exp"), "exp")
    iat = _as_int(claims.get("iat"), "iat")
    if now >= exp:
        raise RazorThinJwtError("ID token is expired")
    if iat > now + 60:
        raise RazorThinJwtError("ID token issued in the future")
    if now - iat > max_age_seconds:
        raise RazorThinJwtError("ID token is too old")
    nonce = claims.get("nonce")
    if nonce != expected_nonce:
        raise RazorThinJwtError("ID token nonce does not match")
    return claims


@dataclass(frozen=True)
class OIDCIdentity:
    """Authoritative identity resolved from validated claims + tenant roster."""

    tenant_id: str
    actor_id: str
    role: str


def _resolve_actor(claims: dict[str, Any]) -> str:
    for key in ("preferred_username", "email", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            if key == "email":
                # Only the local part of an email is a stable actor id; full
                # emails may collide with another tenant's operator.
                return value.partition("@")[0]
            return value
    raise OIDCFlowError(
        "OIDC login rejected: no usable subject", internal_detail="no sub/preferred_username/email"
    )


def resolve_oidc_identity(
    claims: dict[str, Any],
    database: Any,
    *,
    tenant_hint: str | None = None,
) -> OIDCIdentity:
    """Map validated claims + optional hint onto an active ``tenant_members`` row.

    The roster is the only source of tenant, role and membership truth: unknown
    members, deactivated members, non-roster roles and tenant mismatches are
    rejected.  There is intentionally no fallback to ``demo``/``admin``.
    """
    actor_id = _resolve_actor(claims)
    claimed_tenant = claims.get("helix_tenant")
    tenant_hint_effective = tenant_hint or claimed_tenant
    member: dict[str, Any] | None = None
    if isinstance(tenant_hint_effective, str) and tenant_hint_effective:
        for tenant_id in (tenant_hint_effective, actor_id):
            found = database.get_member(tenant_id, actor_id)
            if found and found.get("status") == "active":
                member = found
                break
    else:
        # No tenant signal at all: the actor must be an active member of
        # exactly one tenant (single-tenant operator accounts).
        matches = database.find_active_members_by_actor(actor_id)
        if len(matches) != 1:
            raise OIDCFlowError(
                "OIDC login rejected: account is not an active member of this deployment",
                internal_detail=(
                    f"no tenant signal and actor {actor_id!r} matches "
                    f"{len(matches)} active roster rows (expected exactly 1)"
                ),
            )
        member = matches[0]
    if member is None:
        raise OIDCFlowError(
            "OIDC login rejected: account is not an active member of this deployment",
            internal_detail=f"no active tenant_members row for actor {actor_id!r}",
        )
    role = str(member.get("role") or "")
    if role not in {"admin", "supervisor", "operator", "channel", "viewer", "auditor"}:
        raise OIDCFlowError("OIDC login rejected: member role is not authorized")
    return OIDCIdentity(
        tenant_id=str(member["tenant_id"]),
        actor_id=actor_id,
        role=role,
    )


# ---------------------------------------------------------------------------
# Discovery + token exchange orchestration
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    from app.database import utc_now

    return utc_now()


def _iso_from_epoch(epoch: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds")


def _iso_now_offset(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _parse_epoch(value: str) -> int:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return 0


class OIDCFlow:
    """End-to-end verified OIDC authorization-code flow (SEC-001)."""

    def __init__(
        self,
        config: OIDCConfig,
        store: OIDCTransactionStore,
        *,
        database: Any,
        http_client: Any = None,
        clock: Any = time.time,
        discovery: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._database = database
        self._http_client = http_client
        self._clock = clock
        self._discovery = discovery
        self._discovery_fetched_at = 0
        self._jwk_resolver: JwkResolver | None = None

    async def _discover(self) -> dict[str, Any]:
        if self._discovery is not None and int(self._clock()) - self._discovery_fetched_at < 3600:
            return self._discovery
        import httpx

        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10)
        url = self.config.discovery_url.rstrip("/")
        if not url.endswith("/.well-known/openid-configuration"):
            url = f"{url}/.well-known/openid-configuration"
        try:
            response = await client.get(url)
            response.raise_for_status()
            parsed: Any = response.json()
            if not isinstance(parsed, dict):
                raise OIDCFlowError("OIDC discovery response is malformed")
            for required in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(parsed.get(required), str) or not parsed[required]:
                    raise OIDCFlowError("OIDC discovery response is incomplete")
            self._discovery = parsed
            self._discovery_fetched_at = int(self._clock())
        except httpx.HTTPError as exc:
            logger.error("OIDC discovery fetch failed: %s", exc)
            raise OIDCFlowError("OIDC discovery failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        return self._discovery

    async def build_authorization_url(
        self,
        *,
        tenant_hint: str | None = None,
    ) -> tuple[str, OIDCTransaction]:
        """Create a one-time login transaction and the IdP authorization URL."""
        from urllib.parse import urlencode

        discovery = await self._discover()
        state = secrets.token_urlsafe(24)
        nonce = new_nonce()
        code_verifier = generate_code_verifier()
        redirect_uri = self.config.redirect_uri
        tx = OIDCTransaction(
            id=secrets.token_hex(16),
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            tenant_hint=tenant_hint,
        )
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": derive_code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        self.store.create(tx)
        return f"{discovery['authorization_endpoint']}?{urlencode(params)}", tx

    async def exchange_code(
        self,
        code: str,
        tx: OIDCTransaction,
    ) -> dict[str, Any]:
        """Exchange an authorization code with the IdP, sending the PKCE
        verifier and the exact redirect bound to the transaction."""
        import httpx

        discovery = await self._discover()
        token_endpoint = discovery["token_endpoint"]
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": tx.redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code_verifier": tx.code_verifier,
        }
        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10)
        try:
            response = await client.post(token_endpoint, data=payload)
            response.raise_for_status()
            tokens: Any = response.json()
        except httpx.HTTPError as exc:
            logger.error("OIDC token exchange failed: %s", exc)
            raise OIDCFlowError(
                "Login provider did not accept the authorization code",
                internal_detail=str(exc),
                status_code=502,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
        if not isinstance(tokens, dict) or not isinstance(tokens.get("id_token"), str):
            raise OIDCFlowError("Login provider returned no ID token")
        return tokens

    async def complete_login(
        self,
        *,
        state: str,
        code: str,
        redirect_uri: str,
    ) -> OIDCIdentity:
        """Consume a login transaction and return the verified identity.

        Every failure raises :class:`OIDCFlowError`; on success the transaction
        has been consumed exactly once and the claims have passed signature,
        issuer/audience/time/nonce and roster checks.
        """
        tx = self.store.consume_by_state(state)
        if tx is None:
            raise OIDCFlowError(
                "Login rejected: invalid or already-used state",
                internal_detail="no unconsumed auth_transactions row for state",
                status_code=400,
            )
        if tx.redirect_uri != redirect_uri:
            raise OIDCFlowError(
                "Login rejected: invalid redirect",
                internal_detail="redirect_uri does not match transaction",
            )
        now = int(self._clock())
        if tx.created_epoch and now - tx.created_epoch > AUTH_TX_TTL_SECONDS:
            raise OIDCFlowError("Login rejected: transaction expired")
        discovery = await self._discover()
        tokens = await self.exchange_code(code, tx)
        id_token = tokens["id_token"]
        header, claims = await self._verify_id_token(id_token, discovery)
        _ = header
        try:
            enforce_token_claims(
                claims,
                expected_issuer=discovery["issuer"],
                expected_audience=self.config.client_id,
                expected_nonce=tx.nonce,
                now=now,
            )
        except RazorThinJwtError as exc:
            logger.debug("ID token claim rejection: %s", exc)
            raise OIDCFlowError(
                "Login rejected: ID token claims failed validation",
                internal_detail=str(exc),
                status_code=400,
            ) from exc
        return resolve_oidc_identity(claims, self._database, tenant_hint=tx.tenant_hint)

    async def _verify_id_token(
        self,
        id_token: str,
        discovery: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        jwks_uri = discovery["jwks_uri"]
        # Outbound allowlist: JWKS requests may only target the host the IdP's
        # own discovery document declares.
        self._validate_outbound_host(jwks_uri, discovery)
        resolver = self._jwk_resolver
        if resolver is None or resolver.jwks_uri != jwks_uri:
            resolver = JwkResolver(jwks_uri, http_client=self._http_client, clock=self._clock)
            self._jwk_resolver = resolver
        try:
            header = _decode_json_segment(id_token.split(".")[0], "header")
            kid = header.get("kid")
            _key, numbers = await resolver.key_for_kid(kid if isinstance(kid, str) else "")
        except RazorThinJwtError as exc:
            logger.debug("ID token JWKS rejection: %s", exc)
            raise OIDCFlowError(
                "Login rejected: provider signature could not be verified",
                internal_detail=str(exc),
            ) from exc
        try:
            return verify_rs256_signature(id_token, modulus=numbers["n"], exponent=numbers["e"])
        except RazorThinJwtError as exc:
            logger.debug("ID token signature rejection: %s", exc)
            raise OIDCFlowError(
                "Login rejected: provider signature could not be verified",
                internal_detail=str(exc),
            ) from exc

    def _validate_outbound_host(self, target: str, discovery: dict[str, Any]) -> None:
        """Allow outbound requests only to hosts the IdP discovery declares."""
        from urllib.parse import urlsplit

        target_host = urlsplit(target).hostname
        for field in ("issuer", "jwks_uri", "token_endpoint", "authorization_endpoint"):
            value = discovery.get(field)
            if not isinstance(value, str):
                continue
            host = urlsplit(value).hostname
            if host and target_host == host:
                return
        raise OIDCFlowError(
            "Login rejected: provider endpoint is not authorized",
            internal_detail=f"outbound host {target_host!r} not in discovery allowlist",
        )
