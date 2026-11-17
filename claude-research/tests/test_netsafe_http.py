"""SSRF safeguards, retries/backoff, rate limits, timeouts, size caps, cache bounds."""

import gzip
import json
import socket

import pytest

from clpresearch import config, http, netsafe
from clpresearch.http import Client
from clpresearch.netsafe import NetError, is_public_ip, split_url


# ---- URL / address safety ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "javascript:alert(1)", "data:text/plain,hi",
    "http://user:pw@example.com/", "http://example.com:8080/", "https://example.com:22/", "http://localhost/", "http://x.internal/",
    "http://host.local/", "", "http://", "http://exa mple.com/", "https://example.com/\r\nX: y", "http://example.com/\x00",
])
def test_split_url_rejects_unsafe(url, monkeypatch):
    monkeypatch.setattr(netsafe.POLICY, "exempt", set())
    with pytest.raises(NetError) as e:
        split_url(url)
    assert e.value.kind == "blocked"


def test_split_url_accepts_plain_public_urls(monkeypatch):
    monkeypatch.setattr(netsafe.POLICY, "exempt", set())
    assert split_url("https://Example.COM./a?b=1") == ("https", "example.com", 443, "/a?b=1")
    assert split_url("http://example.com") == ("http", "example.com", 80, "/")


@pytest.mark.parametrize("ip,public", [
    ("8.8.8.8", True), ("1.1.1.1", True), ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False), ("::1", False), ("fe80::1", False),
    ("fc00::1", False), ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False), ("2606:4700::1111", True), ("not-an-ip", False),
])
def test_is_public_ip(ip, public):
    assert is_public_ip(ip) is public


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "[::1]", "0x7f000001", "2130706433"])
def test_literal_private_hosts_are_refused_at_resolution(host, monkeypatch):
    monkeypatch.setattr(netsafe.POLICY, "exempt", set())
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, **k: [(socket.AF_INET, 1, 6, "", ("127.0.0.1" if "7f" in h or h == "2130706433" else h.strip("[]"), p))])
    with pytest.raises(NetError) as e:
        _, h, p, _ = split_url(f"http://{host}/")
        netsafe.resolve_public(h, p)
    assert e.value.kind == "blocked"


def test_hostname_resolving_to_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(netsafe.POLICY, "exempt", set())
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, **k: [(socket.AF_INET, 1, 6, "", ("10.9.9.9", p))])
    with pytest.raises(NetError) as e:
        netsafe.resolve_public("evil.example", 443)
    assert "non-public" in e.value.message


def test_any_private_answer_among_several_blocks_the_host(monkeypatch):
    monkeypatch.setattr(netsafe.POLICY, "exempt", set())
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, **k: [(socket.AF_INET, 1, 6, "", ("8.8.8.8", p)), (socket.AF_INET, 1, 6, "", ("192.168.0.2", p))])
    with pytest.raises(NetError):
        netsafe.resolve_public("mixed.example", 443)


def test_client_refuses_private_target_before_any_request(ctx, mock, monkeypatch):
    monkeypatch.setattr(netsafe.POLICY, "exempt", set())  # the mock is on 127.0.0.1: now off limits
    mock.add("/x", {"ok": 1})
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/x", "web")
    assert e.value.kind == "blocked" and mock.log == []


def test_redirect_to_private_address_is_blocked(ctx, mock):
    mock.add("/r", b"", status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/r", "web")
    assert e.value.kind == "blocked"


def test_redirect_to_file_scheme_and_credentials_is_blocked(ctx, mock):
    for target in ("file:///etc/passwd", "http://user:pw@example.com/"):
        mock.routes.clear()
        mock.add("/r", b"", status=301, headers={"Location": target})
        with pytest.raises(NetError) as e:
            ctx.client.get(f"{mock.base}/r", "web", ttl=0)
        assert e.value.kind == "blocked"


def test_redirect_chain_followed_when_safe_and_capped(ctx, mock):
    mock.add("/a", b"", status=302, headers={"Location": "/b"})
    mock.add("/b", b"final", ctype="text/plain")
    r = ctx.client.get(f"{mock.base}/a", "web")
    assert r.body == b"final" and r.url.endswith("/b")
    mock.add("/loop", b"", status=302, headers={"Location": "/loop"})
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/loop", "web")
    assert "redirects" in e.value.message


def test_redirect_outside_allowed_host_suffixes_is_blocked(ctx, mock):
    mock.add("/r", b"", status=302, headers={"Location": "http://localhost:%d/x" % mock.port})
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/r", "sec", host_suffixes=("127.0.0.1",))
    assert e.value.kind == "blocked"


def test_connection_is_pinned_to_the_address_that_was_checked(ctx, mock, monkeypatch):
    """DNS rebinding: after validation the client connects to the validated IP, not a fresh lookup."""
    mock.add("/p", b"pinned", ctype="text/plain")
    calls = []
    real = socket.getaddrinfo

    def flip(host, port, *a, **k):
        if host == "127.0.0.1":  # the pinned connect uses the IP literal: no DNS involved
            return real(host, port, *a, **k)
        calls.append(host)
        return real("127.0.0.1", port, *a, **k) if len(calls) == 1 else [(socket.AF_INET, 1, 6, "", ("10.0.0.9", port))]

    monkeypatch.setattr(socket, "getaddrinfo", flip)
    monkeypatch.setattr(netsafe.POLICY, "exempt", {f"rebind.test:{mock.port}"})
    r = ctx.client.get(f"http://rebind.test:{mock.port}/p", "web", ttl=0)
    assert r.body == b"pinned" and len(calls) == 1  # one lookup, used for the connection


# ---- retries, rate limits, timeouts ----------------------------------------------------------------------
def test_success_is_cached_and_second_call_skips_network(ctx, mock):
    mock.add("/c", {"a": 1})
    a = ctx.client.get(f"{mock.base}/c", "web")
    b = ctx.client.get(f"{mock.base}/c", "web")
    assert a.json() == b.json() == {"a": 1} and b.from_cache and len(mock.hits("/c")) == 1


def test_cache_expires_after_ttl(ctx, mock, monkeypatch):
    mock.add("/c", {"a": 1})
    t = [1000.0]
    monkeypatch.setattr(http, "NOW", lambda: t[0])
    ctx.client.get(f"{mock.base}/c", "web", ttl=60)
    t[0] += 30
    assert ctx.client.get(f"{mock.base}/c", "web", ttl=60).from_cache
    t[0] += 100
    assert not ctx.client.get(f"{mock.base}/c", "web", ttl=60).from_cache
    assert len(mock.hits("/c")) == 2


def test_429_with_retry_after_is_honoured_then_succeeds(ctx, mock, sleeps):
    mock.add("/r", b"slow down", status=429, headers={"Retry-After": "7"}, ctype="text/plain")
    mock.add("/r", {"ok": True})
    r = ctx.client.get(f"{mock.base}/r", "web")
    assert r.json() == {"ok": True} and r.attempts == 2 and 7.0 in sleeps


def test_persistent_429_becomes_rate_limited_after_bounded_attempts(ctx, mock, sleeps):
    mock.add("/r", b"no", status=429, ctype="text/plain")
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/r", "web")
    assert e.value.kind == "rate_limited" and e.value.status == 429 and len(mock.hits("/r")) == config.ATTEMPTS


def test_5xx_retried_then_reported(ctx, mock, sleeps):
    mock.add("/s", b"boom", status=503, ctype="text/plain")
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/s", "web", attempts=2)
    assert e.value.kind == "http" and e.value.status == 503 and len(mock.hits("/s")) == 2


def test_4xx_is_not_retried(ctx, mock):
    mock.add("/n", b"nope", status=404, ctype="text/plain")
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/n", "web")
    assert e.value.status == 404 and len(mock.hits("/n")) == 1


def test_ok_statuses_allow_404_for_robots_style_probes(ctx, mock):
    mock.add("/n", b"", status=404)
    assert ctx.client.get(f"{mock.base}/n", "web", ok_statuses=(200, 404)).status == 404


def test_timeout_is_retried_then_reported(ctx, mock, sleeps):
    mock.add("/t", {"slow": 1}, delay=1.2)
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/t", "web", timeout=0.3, attempts=2, ttl=0)
    assert e.value.kind == "timeout" and len(sleeps) >= 1


def test_deadline_stops_further_retries(ctx, mock, monkeypatch):
    mock.add("/r", b"x", status=503, ctype="text/plain")
    t = [0.0]
    monkeypatch.setattr(http, "NOW", lambda: t[0])
    monkeypatch.setattr(http, "SLEEP", lambda s: t.__setitem__(0, t[0] + 60))
    with pytest.raises(NetError):
        ctx.client.get(f"{mock.base}/r", "web", attempts=5)
    assert len(mock.hits("/r")) < 5


def test_connection_refused_is_a_network_error(ctx):
    netsafe.POLICY.exempt.add("127.0.0.1:1")
    with pytest.raises(NetError) as e:
        ctx.client.get("http://127.0.0.1:1/", "web", attempts=1)
    assert e.value.kind == "network"


def test_rate_limiter_spaces_requests_per_provider(ctx, mock, monkeypatch, sleeps):
    monkeypatch.setattr(config, "MIN_INTERVAL", {"slowprov": 2.0, "web": 0})
    t = [100.0]
    monkeypatch.setattr(http, "NOW", lambda: t[0])
    mock.add("/q", {"a": 1})
    ctx.client.get(f"{mock.base}/q?1", "slowprov", ttl=0)
    t[0] += 0.5
    ctx.client.get(f"{mock.base}/q?2", "slowprov", ttl=0)
    assert sleeps and abs(sleeps[0] - 1.5) < 0.01
    sleeps.clear()
    ctx.client.get(f"{mock.base}/q?3", "web", ttl=0)
    assert sleeps == []


def test_user_agent_identifies_the_tool_with_contact(ctx, mock):
    mock.add("/ua", {})
    ctx.client.get(f"{mock.base}/ua", "web", ttl=0)
    assert "tester@example.com" in mock.log[0]["headers"]["user-agent"]
    ctx.sec_client.get(f"{mock.base}/ua", "sec", ttl=0)
    assert "tester@example.com" in mock.log[1]["headers"]["user-agent"] and "alnair-research" in mock.log[1]["headers"]["user-agent"]


# ---- size caps, encodings, malformed data ---------------------------------------------------------------
def test_declared_oversize_body_refused_without_reading(ctx, mock):
    mock.add("/big", b"x" * 5000, ctype="text/plain")
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/big", "web", max_bytes=1000)
    assert e.value.kind == "too_large"


def test_gzip_bomb_is_bounded(ctx, mock):
    mock.add("/z", b"\0" * 5_000_000, ctype="application/octet-stream", gz=True)
    with pytest.raises(NetError) as e:
        ctx.client.get(f"{mock.base}/z", "web", max_bytes=100_000, ttl=0)
    assert e.value.kind == "too_large"


def test_gzip_body_is_decoded(ctx, mock):
    mock.add("/g", {"hello": "world"}, gz=True)
    assert ctx.client.get(f"{mock.base}/g", "web", ttl=0).json() == {"hello": "world"}


def test_malformed_json_is_a_clean_error(ctx, mock):
    mock.add("/j", b"{not json", ctype="application/json")
    r = ctx.client.get(f"{mock.base}/j", "web", ttl=0)
    with pytest.raises(NetError) as e:
        r.json()
    assert "malformed JSON" in e.value.message


def test_cache_is_bounded_oldest_evicted(ctx, mock, monkeypatch):
    monkeypatch.setattr(config, "CACHE_MAX_BYTES", 3000)
    for i in range(8):
        mock.add(f"/e{i}", b"y" * 1000, ctype="text/plain")
        ctx.client.get(f"{mock.base}/e{i}", "web")
    total = sum(p.stat().st_size for p in ctx.client.cache_dir.iterdir())
    assert total <= 3000 + 1500  # last write may exceed by one entry before the next prune


def test_truncated_body_is_an_error_not_a_saved_source(ctx):
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nContent-Type: text/plain\r\n\r\n12345678")
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    netsafe.POLICY.exempt.add(f"127.0.0.1:{port}")
    with pytest.raises(NetError) as e:
        ctx.client.get(f"http://127.0.0.1:{port}/x", "web", attempts=1)
    assert e.value.kind == "network" and "truncated" in str(e.value)
    srv.close()


def test_slow_drip_body_hits_the_wall_clock_budget(ctx):
    import threading
    import time
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
        try:
            for _ in range(40):
                c.sendall(b"x")
                time.sleep(0.3)
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()
    netsafe.POLICY.exempt.add(f"127.0.0.1:{port}")
    t0 = time.monotonic()
    with pytest.raises(NetError) as e:
        ctx.client.get(f"http://127.0.0.1:{port}/x", "web", timeout=1, attempts=1)
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 5
    srv.close()
