"""Polite HTTP: per-provider rate limits, retry with backoff, timeouts, size caps, a bounded disk cache.

Plain English: one function, `Client.get`, that every provider uses. It waits its turn for
the provider, retries rate limits and server errors a few times, refuses private addresses
and oversized bodies, and caches successful answers for a few hours (cache is size-capped).
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import random
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from . import config, filelock
from .netsafe import NetError, resolve_public, split_url

SLEEP = time.sleep   # tests replace these two to run instantly
NOW = time.time


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    from_cache: bool = False
    attempts: int = 1
    cookies: list[str] = field(default_factory=list)      # "name=value" of each Set-Cookie header; never cached or stored
    length_mismatch: tuple[int, int] | None = None        # (bytes received, bytes declared) when a short body was accepted
    length_unconfirmed: bool = False                      # short identity body: the caller must confirm it with a second fetch

    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip().strip('"') or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise NetError("http", f"provider returned malformed JSON ({exc.__class__.__name__})", self.status) from None


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout, tls):
        super().__init__(host, port, timeout=timeout)
        self._ip, self._tls = ip, tls

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        if self._tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        self.sock = sock


class RateLimiter:
    """Cross-process minimum gap between requests to one provider (file-locked timestamp)."""

    def __init__(self, directory: Path):
        self.dir = directory

    def wait(self, provider: str) -> float:
        gap = config.MIN_INTERVAL.get(provider, 1.0)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{provider}.ts"
        with open(path, "a+") as fh:
            filelock.lock(fh)
            try:
                fh.seek(0)
                try:
                    last = float(fh.read().strip() or 0)
                except ValueError:
                    last = 0.0
                waited = max(0.0, last + gap - NOW())
                if waited:
                    SLEEP(waited)
                fh.seek(0)
                fh.truncate()
                fh.write(str(NOW()))
                fh.flush()
                return waited
            finally:
                filelock.unlock(fh)


def _decode_full(body: bytes, encoding: str, limit: int) -> tuple[bytes, bool]:
    """(decoded bytes, complete). `complete` is True when a compressed stream reached its own end marker,
    which proves the payload arrived whole whatever the Content-Length header said."""
    enc = (encoding or "").lower()
    if enc in ("", "identity"):
        return body, False
    if enc not in ("gzip", "x-gzip", "deflate"):
        raise NetError("http", f"unsupported content-encoding {enc}")
    d = zlib.decompressobj(16 + zlib.MAX_WBITS if "gzip" in enc else zlib.MAX_WBITS)
    try:
        out = d.decompress(body, limit + 1)
    except zlib.error as exc:
        raise NetError("http", f"bad compressed body: {exc}") from None
    if len(out) > limit:
        raise NetError("too_large", f"decompressed body exceeds {limit} bytes")
    return out, bool(d.eof)


def _decode(body: bytes, encoding: str, limit: int) -> bytes:
    return _decode_full(body, encoding, limit)[0]


class Client:
    def __init__(self, home: Path | None = None, user_agent: str | None = None):
        self.home = home or config.research_home()
        self.ua = user_agent or config.UA
        self.limiter = RateLimiter(self.home / "cache" / "rl")
        self.cache_dir = self.home / "cache" / "http"

    # -- one request, following safe redirects ---------------------------------
    def _once(self, url: str, headers: dict[str, str], timeout: float, max_bytes: int,
              host_suffixes: tuple[str, ...] | None, method: str = "GET", body: bytes | None = None,
              tolerate_length: bool = False) -> Response:
        current = url
        budget_end = time.monotonic() + max(timeout * 2, 2)  # wall-clock cap for the whole request, redirects included
        for _hop in range(config.MAX_REDIRECTS + 1):
            if time.monotonic() > budget_end:
                raise NetError("timeout", f"request exceeded its {max(timeout * 2, 2):.0f}s wall-clock budget")
            scheme, host, port, path = split_url(current)
            if host_suffixes and not any(host == s or host.endswith("." + s) for s in host_suffixes):
                raise NetError("blocked", f"redirect/host {host} is outside the allowed hosts for this provider")
            ip = resolve_public(host, port)[0]
            conn = _Pinned(host, port, ip, timeout, scheme == "https")
            try:
                # "Connection: close": we never reuse a connection, and a server that closes after the body lets a
                # short body show up as end-of-stream instead of a hang waiting for bytes that never come.
                hdrs = {"User-Agent": self.ua, "Accept-Encoding": "gzip", "Accept": "*/*", "Connection": "close", **headers}
                conn.request(method, path, body=body, headers=hdrs)
                resp = conn.getresponse()
                status = resp.status
                pairs = resp.getheaders()
                rh = {k.lower(): v for k, v in pairs}
                cookies = [v.split(";", 1)[0].strip() for k, v in pairs if k.lower() == "set-cookie" and "=" in v]
                if status in (301, 302, 303, 307, 308) and rh.get("location"):
                    resp.read(0)
                    current = urljoin(current, rh["location"])
                    if method != "GET" and status in (301, 302, 303):
                        method, body = "GET", None
                    continue
                declared = rh.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes and rh.get("content-encoding", "") in ("", "identity"):
                    raise NetError("too_large", f"body is {declared} bytes; limit {max_bytes}")
                chunks, total = [], 0
                while True:
                    if time.monotonic() > budget_end:
                        raise NetError("timeout", f"download exceeded its {max(timeout * 2, 2):.0f}s wall-clock budget")
                    left = budget_end - time.monotonic()
                    if conn.sock is not None:
                        conn.sock.settimeout(max(0.1, min(timeout, left)))
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise NetError("too_large", f"body exceeds {max_bytes} bytes; stopped reading")
                    chunks.append(chunk)
                payload, complete = _decode_full(b"".join(chunks), rh.get("content-encoding", ""), max_bytes)
                out = Response(status, rh, payload, current, cookies=cookies)
                if declared and declared.isdigit() and total != int(declared):
                    # The declared length and the bytes received disagree. Two cases are safe to accept:
                    #  1. a compressed stream that reached its own end marker (the payload is whole; the header is wrong);
                    #  2. opt-in only (archive replays, which copy the ORIGINAL page's length header onto a rewritten
                    #     body): the caller confirms with a second fetch that the same bytes come back.
                    # Everything else is still a truncated download.
                    if complete:
                        out.length_mismatch = (total, int(declared))
                    elif tolerate_length and total > 0:
                        out.length_mismatch, out.length_unconfirmed = (total, int(declared)), True
                    else:
                        raise NetError("network", f"truncated body: got {total} of {declared} declared bytes")
                return out
            except NetError:
                raise
            except socket.timeout:
                raise NetError("timeout", f"timed out after {timeout:.0f}s") from None
            except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
                raise NetError("network", f"{exc.__class__.__name__}: {exc}") from None
            finally:
                conn.close()
        raise NetError("blocked", f"more than {config.MAX_REDIRECTS} redirects")

    # -- cache ------------------------------------------------------------------
    def _key(self, url: str, headers: dict[str, str]) -> str:
        return hashlib.sha256((url + "\n" + json.dumps(sorted(headers.items()))).encode()).hexdigest()

    def _cache_get(self, key: str, ttl: float) -> Response | None:
        meta, body = self.cache_dir / f"{key}.json", self.cache_dir / f"{key}.body"
        try:
            info = json.loads(meta.read_text())
            if NOW() - info["fetched_at"] > ttl:
                return None
            return Response(info["status"], info["headers"], body.read_bytes(), info["url"], from_cache=True)
        except (OSError, ValueError, KeyError):
            return None

    def _cache_put(self, key: str, resp: Response) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in resp.headers.items() if k in ("content-type", "content-disposition")}
        from .store import atomic_write  # atomic so a parallel reader never sees half a cache entry
        atomic_write(self.cache_dir / f"{key}.body", resp.body)
        atomic_write(self.cache_dir / f"{key}.json", json.dumps(
            {"status": resp.status, "headers": keep, "url": resp.url, "fetched_at": NOW()}))
        self.prune()

    def prune(self) -> int:
        """Delete oldest cache entries until the cache is under CACHE_MAX_BYTES; returns bytes freed."""
        try:
            files = [(p.stat().st_mtime, p.stat().st_size, p) for p in self.cache_dir.iterdir() if p.is_file()]
        except OSError:
            return 0
        total = sum(s for _, s, _ in files)
        freed = 0
        for _, size, path in sorted(files):
            if total <= config.CACHE_MAX_BYTES:
                break
            path.unlink(missing_ok=True)
            total -= size
            freed += size
        return freed

    # -- public ------------------------------------------------------------------
    def get(self, url: str, provider: str, **kw) -> Response:
        return self.request("GET", url, provider, **kw)

    def post(self, url: str, provider: str, body: bytes | str, **kw) -> Response:
        """POST for read-only public search endpoints that only speak POST (never cached, never used to change anything)."""
        return self.request("POST", url, provider, body=body.encode("utf-8") if isinstance(body, str) else body, **{"ttl": 0, **kw})

    def request(self, method: str, url: str, provider: str, *, headers: dict[str, str] | None = None, body: bytes | None = None,
                max_bytes: int = config.MAX_BODY, ttl: float = config.CACHE_TTL,
                timeout: float = config.HTTP_TIMEOUT, attempts: int = config.ATTEMPTS,
                host_suffixes: tuple[str, ...] | None = None, ok_statuses: tuple[int, ...] = (200,),
                tolerate_length: bool = False) -> Response:
        headers = headers or {}
        if method not in ("GET", "POST"):
            raise NetError("blocked", f"HTTP method {method} is not allowed (read-only GET/POST only)")
        split_url(url)  # fail fast on a bad URL before touching cache or limiter
        if method != "GET" or "Cookie" in headers:
            ttl = 0  # never cache POSTs or anything that carried a session cookie
        key = self._key(url, headers)
        if ttl > 0:
            hit = self._cache_get(key, ttl)
            if hit:
                return hit
        start = NOW()
        last: NetError | None = None
        for attempt in range(1, attempts + 1):
            self.limiter.wait(provider)
            delay: float | None = None
            try:
                resp = self._once(url, headers, timeout, max_bytes, host_suffixes, method, body, tolerate_length)
                if resp.length_unconfirmed:
                    # A short uncompressed body: accept it only if a second, uncompressed fetch returns the same bytes
                    # (a wrong header is repeatable; a dropped connection is not).
                    self.limiter.wait(provider)
                    again = self._once(url, {**headers, "Accept-Encoding": "identity"}, timeout, max_bytes, host_suffixes, method, body, True)
                    if again.body != resp.body:
                        raise NetError("network", f"truncated body: got {resp.length_mismatch[0]} of {resp.length_mismatch[1]} declared bytes (two fetches disagreed)")
                    resp.length_unconfirmed = False
            except NetError as exc:
                if exc.kind not in ("timeout", "network") or attempt == attempts:
                    raise
                last, delay = exc, 2 ** attempt + random.random()
            else:
                resp.attempts = attempt
                if resp.status in ok_statuses:
                    if ttl > 0 and resp.status == 200:
                        self._cache_put(key, resp)
                    return resp
                snippet = resp.body[:300].decode("utf-8", errors="replace")
                if resp.status in (429, 500, 502, 503, 504, 408):
                    kind = "rate_limited" if resp.status == 429 else "http"
                    last = NetError(kind, f"HTTP {resp.status} from {provider}", resp.status, snippet)
                    retry_after = resp.headers.get("retry-after", "")
                    asked = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else None
                    last.retry_after = asked  # how long the provider asked us to stay away (None if it did not say)
                    delay = asked if asked is not None else 2 ** attempt + random.random()
                    delay = min(delay, 30.0)
                    if attempt == attempts:
                        raise last
                else:
                    raise NetError("http", f"HTTP {resp.status} from {provider}", resp.status, snippet)
            if NOW() - start + (delay or 0) > config.HTTP_DEADLINE:
                raise last  # type: ignore[misc]
            SLEEP(delay or 0)
        raise last or NetError("network", "request failed")  # pragma: no cover
