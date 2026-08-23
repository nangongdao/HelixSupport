"""Webhook URL safety: reject loopback / private / link-local targets.

Registration and delivery both call :func:`assert_public_webhook_url` so a
DNS rebinding or a row inserted outside the API cannot POST to IMDS or an
internal host. Public names such as ``example.com`` still resolve and pass.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse

# Host resolver returns IP strings for (hostname, port). Injectable so SSRF
# tests do not depend on live DNS.
HostResolver = Callable[[str, int], list[str]]

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "metadata.google.internal",
    }
)


def _default_resolve_host(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("webhook url host could not be resolved") from exc
    return [str(info[4][0]) for info in infos]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_public_webhook_url(url: str, *, resolve_host: HostResolver | None = None) -> None:
    """Reject loopback, link-local, private, and IPv6 ULA webhook targets."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("webhook url must be http(s)")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("webhook url host is required")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("webhook url must not target a private or loopback address")
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = (resolve_host or _default_resolve_host)(host, port)
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValueError("webhook url must not target a private or loopback address")
