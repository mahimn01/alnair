"""URL and address safety: only public http(s) hosts, never local/private networks.

Why: fetched URLs can come from untrusted text. Every request (and every redirect hop) is
checked here, and the connection is pinned to the address we checked so a DNS trick cannot
swap in a private one afterwards.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ALLOWED_PORTS = {80, 443}
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


class NetError(Exception):
    """A network-layer failure with a machine-readable kind: blocked, timeout, http, too_large,
    rate_limited, network, unavailable."""

    def __init__(self, kind: str, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.kind, self.message, self.status, self.body = kind, message, status, body
        self.retry_after: float | None = None   # seconds a 429 asked us to wait, when the provider said

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}"


@dataclass
class Policy:
    allow_private: bool = False                       # tests only
    exempt: set = field(default_factory=set)          # tests only: "host:port" pairs (the local mock server)

    def allows(self, host: str, port: int) -> bool:
        return self.allow_private or f"{host}:{port}" in self.exempt


POLICY = Policy()


def is_public_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False
    return bool(ip.is_global)


def split_url(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, path+query) of a safe URL, or NetError('blocked')."""
    if not isinstance(url, str) or not url or len(url) > 4000 or any(c in url for c in "\r\n\x00 "):
        raise NetError("blocked", "URL is empty, too long or contains control characters/spaces")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise NetError("blocked", f"URL scheme {parts.scheme!r} is not allowed (only http and https)")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise NetError("blocked", "URLs with embedded credentials are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise NetError("blocked", "URL has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise NetError("blocked", "URL has an invalid port") from None
    if not POLICY.allows(host, port):
        if port not in ALLOWED_PORTS:
            raise NetError("blocked", f"port {port} is not allowed (only 80 and 443)")
        if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
            raise NetError("blocked", f"host {host} is a local name")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port, path


def resolve_public(host: str, port: int) -> list[str]:
    """Addresses for host, all of which must be public (or NetError('blocked'))."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetError("network", f"could not resolve {host}: {exc}") from None
    addrs: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in addrs:
            addrs.append(ip)
    if not addrs:
        raise NetError("network", f"no address for {host}")
    if not POLICY.allows(host, port):
        bad = [a for a in addrs if not is_public_ip(a)]
        if bad:
            raise NetError("blocked", f"{host} resolves to a non-public address ({bad[0]}); refused")
    return addrs
