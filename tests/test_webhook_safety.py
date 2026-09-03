"""Webhook URL safety (SSRF guard) — direct unit coverage.

assert_public_webhook_url rejects loopback / private / link-local / IPv6-ULA
targets both by literal hostname and by resolved address, with an injectable
resolver so no live DNS is needed. These cases are the security-critical
edges of webhook registration and delivery (app/webhooks.py calls this on
both paths).
"""

from __future__ import annotations

import unittest

import ipaddress

from app.webhook_safety import _is_blocked_ip, assert_public_webhook_url


class WebhookUrlSafetyTests(unittest.TestCase):
    def test_accepts_public_url_without_resolver(self) -> None:
        # A literal public IP needs no DNS; this passes without a resolver.
        assert_public_webhook_url("https://93.184.216.34/hook")

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be http"):
            assert_public_webhook_url("ftp://example.com/hook")

    def test_rejects_missing_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "host is required"):
            assert_public_webhook_url("https:///hook")

    def test_rejects_literal_loopback_hostname(self) -> None:
        for host in ("localhost", "localhost.localdomain", "ip6-localhost"):
            with self.assertRaisesRegex(ValueError, "private or loopback"):
                assert_public_webhook_url(f"http://{host}/hook")

    def test_rejects_metadata_google_internal(self) -> None:
        # IMDS endpoint: the highest-value SSRF target.
        with self.assertRaisesRegex(ValueError, "private or loopback"):
            assert_public_webhook_url("http://metadata.google.internal/hook")

    def test_rejects_local_suffixes(self) -> None:
        for host in ("api.localhost", "svc.local"):
            with self.assertRaisesRegex(ValueError, "private or loopback"):
                assert_public_webhook_url(f"http://{host}/hook")

    def test_rejects_resolved_private_ip(self) -> None:
        # Hostname that *resolves* to a private address is caught via the
        # injected resolver — the DNS-rebinding protection path.
        def resolve(_host: str, _port: int) -> list[str]:
            return ["10.0.0.8"]

        with self.assertRaisesRegex(ValueError, "private or loopback"):
            assert_public_webhook_url("http://public.example.com/hook", resolve_host=resolve)

    def test_rejects_resolved_ipv6_ula(self) -> None:
        def resolve(_host: str, _port: int) -> list[str]:
            return ["fd00::1"]

        with self.assertRaisesRegex(ValueError, "private or loopback"):
            assert_public_webhook_url("http://public.example.com/hook", resolve_host=resolve)

    def test_accepts_resolved_public_ip(self) -> None:
        def resolve(_host: str, _port: int) -> list[str]:
            return ["93.184.216.34"]

        assert_public_webhook_url("http://public.example.com/hook", resolve_host=resolve)

    def test_injected_resolver_oserror_propagates(self) -> None:
        # Exception translation (OSError -> ValueError) lives inside
        # _default_resolve_host: an injected resolver's exceptions propagate
        # as-is — the caller (WebhookService) owns that error surface.
        def resolve(_host: str, _port: int) -> list[str]:
            raise OSError("NXDOMAIN")

        with self.assertRaisesRegex(OSError, "NXDOMAIN"):
            assert_public_webhook_url("http://nonexistent.invalid/hook", resolve_host=resolve)

    def test_default_resolver_oserror_becomes_value_error(self) -> None:
        # The default resolver translates DNS failure into the ValueError the
        # registration API surfaces to callers.
        import unittest.mock as mock

        with mock.patch("app.webhook_safety.socket.getaddrinfo", side_effect=OSError("NXDOMAIN")):
            with self.assertRaisesRegex(ValueError, "could not be resolved"):
                assert_public_webhook_url("http://nonexistent.invalid/hook")

    def test_unparseable_address_is_skipped_not_fatal(self) -> None:
        # A resolver returning garbage must not crash the check; the host is
        # then treated as not provably public by the caller.
        def resolve(_host: str, _port: int) -> list[str]:
            return ["not-an-ip"]

        assert_public_webhook_url("http://public.example.com/hook", resolve_host=resolve)

    def test_is_blocked_ip_maps_ipv4_to_ipv4(self) -> None:
        mapped = ipaddress.ip_address("::ffff:127.0.0.1")
        self.assertTrue(_is_blocked_ip(mapped))


if __name__ == "__main__":
    unittest.main()
