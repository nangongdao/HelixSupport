"""M0 SEC-001: verified OIDC authorization-code flow.

Negative tests pin the released blockers: unsigned/alg-confused tokens,
missing/rotated JWKS kids, wrong issuer/audience/azp/exp/iat/nonce, unknown or
deactivated roster members, replay/mismatch of one-time login transactions and
redirect swaps.  The fixture RSA key below signs only test tokens.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app.database import Database
from app.oidc_flow import (
    _SHA256_DIGEST_INFO_PREFIX,
    AUTH_TX_TTL_SECONDS,
    DatabaseOIDCTransactionStore,
    JwkResolver,
    OIDCFlow,
    OIDCFlowError,
    OIDCTransaction,
    RazorThinJwtError,
    _assert_rs256_algorithm,
    derive_code_challenge,
    enforce_token_claims,
    generate_code_verifier,
    resolve_oidc_identity,
    verify_rs256_signature,
)
from app.session_auth import OIDCConfig

# Fixed RSA-2048 fixture key (public n/e, private d for signing only).
_RSA_N = 0x9285D721FE80DDDFD7A7C19CD1FD601E2F374B89363FBD08D4AEAC620EB38C268043AF9A22EA5BB59F65EAC13BEE24E019222E6BFEAE94023DFD525710A84CDC88ACAF078F4136FC097E6BE7DF58BCB0D30A01C3E8531DD55293715A88E03A59DB81BB15099E30869980AD35B06297CD40BC56EFC1820D3767B7B7A3BBBA0209B1A82C055B5E0ED8969A9C56D4E725670C57183CF6C9F50199842792DF7960F7356B1E2AA7B1043B21EAAC3818A4B91A59CB15207433142A50665F4F7833356247DE99BF0E318CFADDB1AEBE7DB347806FF7B61167446B34047C4FDC811EDDED42F1A006290E8662005E9A4436B7A65030DEDA24FA62B127BFE723A27572D8BB
_RSA_E = 65537
_RSA_D = 0x3BF0062E40C7047C41E2BCFFE2A2CC83EC7AA92AAB076DD3C3F4E44D84880C27DFC6507A34183C85D27BC5896073ADA002880A07617A96CF47FE6D857229F6AF2C35BFAFCEF9357DEA804DB1DF9A942D9D56F59BE758C0D677DE1ABB974C6A7241AA1316AB058C02339F00BBFAB88A328B6DBFFE79E13278DCB3B9AA7FEBBA53106621C77DFE2B94202CA5EE1098121BEA46C0F94D508D9FB187CDE8C8BCE1B05C029DD27A4964DFA3FB9C9047849772B4A12DA95053CDA9A94CCADBC8C652E62F53ACF366FB06C72FBA13C93D8F5D35F2733A61DF829BF536A306DFBBBBE49B4518C39BC8A66A19BDD443CBD28E365D2868CA7DCA1DFB7CB193377F12C87B99

_TEST_JWK = {
    "kty": "RSA",
    "use": "sig",
    "kid": "test-key-1",
    "alg": "RS256",
    "n": (
        "koXXIf6A3d_Xp8Gc0f1gHi83S4k2P70I1K6sYg6zjCaAQ6-aIupbtZ9l6sE77iTgGSIua_6u"
        "lAI9_VJXEKhM3IisrwePQTb8CX5r599YvLDTCgHD6FMd1VKTcVqI4DpZ24G7FQmeMIaZgK01"
        "sGKXzUC8Vu_Bgg03Z7e3o7u6AgmxqCwFW14O2JaanFbU5yVnDFcYPPbJ9QGZhCeS33lg9zVr"
        "HiqnsQQ7IeqsOBikuRpZyxUgdDMUKlBmX094MzViR96Zvw4xjPrdsa6-fbNHgG_3thFnRGs0"
        "BHxP3IEe3e1C8aAGKQ6GYgBemkQ2t6ZQMN7aJPpisSe_5yOidXLYuw"
    ),
    "e": "AQAB",
}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_int(value: int, length: int) -> str:
    return _b64u(value.to_bytes(length, "big"))


def _sign_jwt(header: dict[str, Any], claims: dict[str, Any]) -> str:
    signing_input = "{}.{}".format(
        _b64u(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64u(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
    )
    digest = hashlib.sha256(signing_input.encode("ascii")).digest()
    block = _SHA256_DIGEST_INFO_PREFIX + digest
    k = (_RSA_N.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(block) - 3) + b"\x00" + block
    signature = pow(int.from_bytes(em, "big"), _RSA_D, _RSA_N).to_bytes(k, "big")
    return f"{signing_input}.{_b64u(signature)}"


def _id_token(
    claims: dict[str, Any],
    *,
    header: dict[str, Any] | None = None,
) -> str:
    return _sign_jwt(
        {"alg": "RS256", "typ": "JWT", "kid": "test-key-1"} if header is None else header,
        claims,
    )


def _base_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://idp.example.com",
        "aud": "test-client",
        "sub": "sub-123",
        "preferred_username": "alice",
        "helix_tenant": "acme",
        "nonce": "fixed-nonce",
        "exp": now + 600,
        "iat": now,
    }
    claims.update(overrides)
    return claims


def _flow_config() -> OIDCConfig:
    return OIDCConfig(
        client_id="test-client",
        client_secret="test-secret",
        discovery_url="https://idp.example.com",
        redirect_uri="https://app.example.com/callback",
        session_secret="test-session-secret",
        session_ttl_minutes=60,
    )


_DISCOVERY = {
    "issuer": "https://idp.example.com",
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
}


class PkceTests(unittest.TestCase):
    def test_verifier_length_and_challenge_determinism(self) -> None:
        verifier = generate_code_verifier()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(derive_code_challenge(verifier), derive_code_challenge(verifier))
        self.assertNotEqual(
            derive_code_challenge(verifier), derive_code_challenge(generate_code_verifier())
        )


class Rs256VerifierTests(unittest.TestCase):
    def test_valid_signature_passes(self) -> None:
        header, claims = verify_rs256_signature(
            _id_token(_base_claims()),
            modulus=_RSA_N,
            exponent=_RSA_E,
        )
        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(claims["sub"], "sub-123")

    def test_alg_none_rejected(self) -> None:
        with self.assertRaises(RazorThinJwtError):
            _assert_rs256_algorithm({"alg": "none"})
        token = _id_token(_base_claims(), header={"alg": "none", "typ": "JWT"})
        with self.assertRaises(RazorThinJwtError):
            verify_rs256_signature(token, modulus=_RSA_N, exponent=_RSA_E)

    def test_hs256_confusion_rejected(self) -> None:
        with self.assertRaises(RazorThinJwtError):
            _assert_rs256_algorithm({"alg": "HS256"})
        token = _id_token(_base_claims(), header={"alg": "HS256", "kid": "test-key-1"})
        with self.assertRaises(RazorThinJwtError):
            verify_rs256_signature(token, modulus=_RSA_N, exponent=_RSA_E)

    def test_tampered_claims_rejected(self) -> None:
        token = _id_token(_base_claims())
        header, payload = token.rsplit(".", 1)
        forged = _base_claims(preferred_username="mallory", role="admin")
        forged_segment = _b64u(json.dumps(forged, separators=(",", ":")).encode("utf-8"))
        with self.assertRaises(RazorThinJwtError):
            verify_rs256_signature(
                f"{header}.{forged_segment}.{payload}", modulus=_RSA_N, exponent=_RSA_E
            )

    def test_wrong_signature_rejected(self) -> None:
        token = _id_token(_base_claims())
        signing_input, _old = token.rsplit(".", 1)
        bogus = _b64u(b"\x01" * ((_RSA_N.bit_length() + 7) // 8))
        with self.assertRaises(RazorThinJwtError):
            verify_rs256_signature(f"{signing_input}.{bogus}", modulus=_RSA_N, exponent=_RSA_E)

    def test_short_modulus_rejected(self) -> None:
        token = _id_token(_base_claims())
        small = 2**1024 + 1
        with self.assertRaises(RazorThinJwtError):
            verify_rs256_signature(token, modulus=small, exponent=_RSA_E)

    def test_malformed_token_rejected(self) -> None:
        for bad in ("", "a.b", "a.b.c.d", "not-a-jwt", ".."):
            with self.assertRaises(RazorThinJwtError):
                verify_rs256_signature(bad, modulus=_RSA_N, exponent=_RSA_E)


class TokenClaimsTests(unittest.TestCase):
    def test_required_claims(self) -> None:
        now = int(time.time())
        claims = _base_claims(exp=now - 10, iat=now - 100)
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                claims,
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=now,
            )

    def test_wrong_issuer_rejected(self) -> None:
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(iss="https://evil.example.com"),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=int(time.time()),
            )

    def test_wrong_audience_rejected(self) -> None:
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(aud="other-client"),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=int(time.time()),
            )

    def test_audience_list_contains_client_passes(self) -> None:
        now = int(time.time())
        enforce_token_claims(
            _base_claims(aud=["test-client", "another"], azp="test-client"),
            expected_issuer="https://idp.example.com",
            expected_audience="test-client",
            expected_nonce="fixed-nonce",
            now=now,
        )

    def test_azp_mismatch_rejected(self) -> None:
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(aud=["test-client", "evil"], azp="evil"),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=int(time.time()),
            )

    def test_expired_token_rejected(self) -> None:
        now = int(time.time())
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(exp=now - 5),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=now,
            )

    def test_future_iat_rejected(self) -> None:
        now = int(time.time())
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(iat=now + 120),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=now,
            )

    def test_stale_token_rejected(self) -> None:
        now = int(time.time())
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(iat=now - 600),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=now,
            )

    def test_nonce_mismatch_rejected(self) -> None:
        with self.assertRaises(RazorThinJwtError):
            enforce_token_claims(
                _base_claims(nonce="other-nonce"),
                expected_issuer="https://idp.example.com",
                expected_audience="test-client",
                expected_nonce="fixed-nonce",
                now=int(time.time()),
            )


class IdentityMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("acme")

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _active_member(self, actor: str, role: str) -> None:
        self.db.invite_member("acme", actor, role, "admin")
        self.db.update_member_role("acme", actor, role, "admin")

    def test_active_member_resolves(self) -> None:
        self._active_member("alice", "operator")
        identity = resolve_oidc_identity(_base_claims(), self.db, tenant_hint="acme")
        self.assertEqual(identity.tenant_id, "acme")
        self.assertEqual(identity.actor_id, "alice")
        self.assertEqual(identity.role, "operator")

    def test_unknown_member_rejected(self) -> None:
        with self.assertRaises(OIDCFlowError):
            resolve_oidc_identity(_base_claims(), self.db, tenant_hint="acme")

    def test_deactivated_member_rejected(self) -> None:
        self._active_member("alice", "operator")
        self.db.deactivate_member("acme", "alice", "admin")
        with self.assertRaises(OIDCFlowError):
            resolve_oidc_identity(_base_claims(), self.db, tenant_hint="acme")

    def test_illegal_role_rejected(self) -> None:
        self.db.invite_member("acme", "alice", "root", "admin")
        self.db.update_member_role("acme", "alice", "root", "admin")
        with self.assertRaises(OIDCFlowError):
            resolve_oidc_identity(_base_claims(), self.db, tenant_hint="acme")

    def test_email_actor_uses_local_part(self) -> None:
        self._active_member("alice", "viewer")
        identity = resolve_oidc_identity(
            _base_claims(preferred_username=None, email="alice@example.com"),
            self.db,
            tenant_hint="acme",
        )
        self.assertEqual(identity.actor_id, "alice")

    def test_no_tenant_hint_resolves_single_member(self) -> None:
        self._active_member("alice", "operator")
        claims = _base_claims(helix_tenant=None)
        identity = resolve_oidc_identity(claims, self.db, tenant_hint=None)
        self.assertEqual(identity.tenant_id, "acme")


class TransactionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.store = DatabaseOIDCTransactionStore(self.db)

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _tx(self) -> OIDCTransaction:
        return OIDCTransaction(
            id="tx-1",
            state="state-1",
            nonce="nonce-1",
            code_verifier="verifier-1" * 8,
            redirect_uri="https://app.example.com/callback",
            tenant_hint="acme",
            created_epoch=int(time.time()),
        )

    def test_consume_once(self) -> None:
        self.store.create(self._tx())
        self.assertIsNotNone(self.store.consume_by_state("state-1"))
        self.assertIsNone(self.store.consume_by_state("state-1"))

    def test_unknown_state_returns_none(self) -> None:
        self.assertIsNone(self.store.consume_by_state("missing"))

    def test_prune_expired(self) -> None:
        old = OIDCTransaction(
            id="tx-old",
            state="state-old",
            nonce="n",
            code_verifier="v" * 43,
            redirect_uri="https://app.example.com/callback",
            tenant_hint=None,
            created_epoch=int(time.time()) - AUTH_TX_TTL_SECONDS - 60,
        )
        self.store.create(old)
        self.store.create(self._tx())
        deleted = self.store.prune_expired(int(time.time()))
        self.assertEqual(deleted, 1)
        self.assertIsNone(self.store.consume_by_state("state-old"))


class JwkResolverTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, payload: dict[str, Any]) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_unknown_kid_rejected(self) -> None:
        client = self._client({"keys": [_TEST_JWK]})
        resolver = JwkResolver("https://idp.example.com/.well-known/jwks.json", http_client=client)
        with self.assertRaises(RazorThinJwtError):
            await resolver.key_for_kid("other-kid")
        await client.aclose()

    async def test_rotation_replaces_kid(self) -> None:
        keys = [_TEST_JWK]
        client = self._client({"keys": keys})
        resolver = JwkResolver("https://idp.example.com/.well-known/jwks.json", http_client=client)
        _key, nums = await resolver.key_for_kid("test-key-1")
        self.assertEqual(nums["e"], _RSA_E)
        # Simulate IdP rotation: new JWKS with a fresh kid, old kid gone.
        rotated = dict(_TEST_JWK)
        rotated["kid"] = "test-key-2"
        keys.clear()
        keys.append(rotated)
        # Cache is still valid, so key_for_kid must refresh and accept kid 2.
        _key2, _ = await resolver.key_for_kid("test-key-2")
        await client.aclose()


class OIDCFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("acme")
        self.db.invite_member("acme", "alice", "operator", "admin")
        self.db.update_member_role("acme", "alice", "operator", "admin")
        self.config = _flow_config()
        self.store = DatabaseOIDCTransactionStore(self.db)
        self.now = int(time.time())

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _client(
        self, tokens: dict[str, Any], jwks: dict[str, Any] | None = None
    ) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json=_DISCOVERY)
            if path.endswith("/token"):
                return httpx.Response(200, json=tokens)
            if path.endswith("/jwks.json"):
                return httpx.Response(200, json=jwks or {"keys": [_TEST_JWK]})
            return httpx.Response(404, text="not found")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _idp_handler(self, tokens: dict[str, Any]) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json=_DISCOVERY)
            if path.endswith("/token"):
                return httpx.Response(200, json=tokens)
            if path.endswith("/jwks.json"):
                return httpx.Response(200, json={"keys": [_TEST_JWK]})
            return httpx.Response(404, text="not found")

        return handler

    def _flow(self, client: httpx.AsyncClient) -> OIDCFlow:
        return OIDCFlow(
            self.config,
            self.store,
            database=self.db,
            http_client=client,
            clock=lambda: self.now,
            discovery=dict(_DISCOVERY),
        )

    async def test_full_login_success(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        query = parse_qs(urlsplit(url).query)
        self.assertIn("code_challenge", query)
        self.assertIn("code_challenge_method", query)
        self.assertIn("nonce", query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        client._transport.handler = self._idp_handler(  # type: ignore[attr-defined]
            {
                "id_token": _id_token(
                    _base_claims(nonce=tx.nonce, sub="sub-123", preferred_username="alice")
                )
            }
        )
        identity = await flow.complete_login(
            state=tx.state, code="auth-code", redirect_uri=self.config.redirect_uri
        )
        self.assertEqual(identity.actor_id, "alice")
        self.assertEqual(identity.tenant_id, "acme")
        self.assertEqual(identity.role, "operator")

    async def test_replay_rejected(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        _ = url
        client._transport.handler = self._idp_handler(  # type: ignore[attr-defined]
            {"id_token": _id_token(_base_claims(nonce=tx.nonce))}
        )
        await flow.complete_login(
            state=tx.state, code="auth-code", redirect_uri=self.config.redirect_uri
        )
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state=tx.state, code="auth-code", redirect_uri=self.config.redirect_uri
            )
        await client.aclose()

    async def test_unknown_state_rejected(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state="forged-state",
                code="code",
                redirect_uri=self.config.redirect_uri,
            )
        await client.aclose()

    async def test_expired_transaction_rejected(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        _ = url
        self.now += AUTH_TX_TTL_SECONDS + 30
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state=tx.state, code="code", redirect_uri=self.config.redirect_uri
            )
        await client.aclose()

    async def test_redirect_mismatch_rejected(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        _ = url
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state=tx.state,
                code="code",
                redirect_uri="https://evil.example.com/callback",
            )
        await client.aclose()

    async def test_nonce_mismatch_rejected(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        _ = url
        client._transport.handler = self._idp_handler(  # type: ignore[attr-defined]
            {"id_token": _id_token(_base_claims(nonce="wrong-nonce"))}
        )
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state=tx.state, code="code", redirect_uri=self.config.redirect_uri
            )
        await client.aclose()

    async def test_unsigned_token_rejected(self) -> None:
        client = self._client({})
        flow = self._flow(client)
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        _ = url
        client._transport.handler = self._idp_handler(  # type: ignore[attr-defined]
            {
                "id_token": _id_token(
                    _base_claims(nonce=tx.nonce),
                    header={"alg": "none", "typ": "JWT"},
                )
            }
        )
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state=tx.state, code="code", redirect_uri=self.config.redirect_uri
            )
        await client.aclose()

    async def test_token_exchange_failure(self) -> None:
        client = self._client({})
        flow = self._flow(client)

        def failing_handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json=_DISCOVERY)
            if path.endswith("/token"):
                return httpx.Response(400, text="invalid_grant")
            if path.endswith("/jwks.json"):
                return httpx.Response(200, json={"keys": [_TEST_JWK]})
            return httpx.Response(404, text="not found")

        client._transport.handler = failing_handler  # type: ignore[attr-defined]
        url, tx = await flow.build_authorization_url(tenant_hint="acme")
        _ = url
        with self.assertRaises(OIDCFlowError):
            await flow.complete_login(
                state=tx.state, code="bad-code", redirect_uri=self.config.redirect_uri
            )
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
