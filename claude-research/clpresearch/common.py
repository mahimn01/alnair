"""Shared plumbing for providers: errors, capability memory, save-and-preview."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, filelock
from .http import Client
from .memory import fence
from .netsafe import NetError
from .store import SourceRef, Store, StoreError, atomic_write


class ToolError(Exception):
    """Bad input or an operational failure the model should see (exit 2)."""


class Unavailable(Exception):
    """A provider cannot be used right now (needs a key, blocks public access, rate-limited). Exit 3."""

    def __init__(self, provider: str, reason: str, alternatives: str = ""):
        super().__init__(reason)
        self.provider, self.reason, self.alternatives = provider, reason, alternatives

    def render(self) -> str:
        text = f"UNAVAILABLE[{self.provider}]: {self.reason}"
        if self.alternatives:
            text += f"\nLegitimate alternatives: {self.alternatives}"
        return text + "\nNo results were invented; this capability is not live right now."


@dataclass
class Ctx:
    store: Store = field(default_factory=Store)
    client: Client | None = None
    sec_client: Client | None = None
    saved: list = field(default_factory=list)   # every SourceRef this context saved, in order (the batch tools read it)
    gaps: list = field(default_factory=list)    # providers that answered UNAVAILABLE during this context
    cancel: threading.Event = field(default_factory=threading.Event)   # set when a batch stops waiting: no new save may start

    def __post_init__(self):
        if self.client is None:
            self.client = Client(self.store.home)
        if self.sec_client is None:  # SEC requires a descriptive User-Agent with contact details
            self.sec_client = Client(self.store.home, user_agent=config.SEC_UA)

    def _not_cancelled(self) -> None:
        if self.cancel.is_set():
            raise StoreError("not saved: the batch stopped waiting for this source (deadline passed)")

    # capability memory: remember "this provider said no" so we do not hammer it
    def cap_path(self) -> Path:
        return self.store.home / "cache" / "capability.json"

    def cap_read(self) -> dict:
        try:
            return json.loads(self.cap_path().read_text())
        except (OSError, ValueError):
            return {}

    def cap_set(self, provider: str, status: str, note: str, now: float | None = None, retry_after: float | None = None) -> None:
        """Remember a provider's last answer. Read-modify-write under a file lock (parallel jobs and other
        processes update the same file). A rate limit also records how long to stay away: what the provider
        asked for, else 60 s doubling with each consecutive rate limit, capped at 15 minutes."""
        path = self.cap_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name("capability.lock"), "a+") as lock:
            filelock.lock(lock)
            try:
                data = self.cap_read()
                prev = data.get(provider) or {}
                rec = {"status": status, "note": note, "at": now if now is not None else time.time()}
                if status == "rate_limited":
                    strikes = int(prev.get("strikes", 0)) + 1 if prev.get("status") == "rate_limited" else 1
                    rec["strikes"] = strikes
                    rec["ttl"] = min(config.COOLDOWN_MAX, max(retry_after or 0.0, config.COOLDOWN_BASE * 2 ** (strikes - 1)))
                data[provider] = rec
                atomic_write(path, json.dumps(data, indent=1))
            finally:
                filelock.unlock(lock)

    def cap_blocked(self, provider: str, ttl_rate: float | None = None, now: float | None = None) -> str | None:
        rec = self.cap_read().get(provider)
        if not rec or rec.get("status") == "ok":
            return None
        ttl = (ttl_rate if ttl_rate is not None else rec.get("ttl", 300.0)) if rec["status"] == "rate_limited" else config.CAPABILITY_TTL
        if (now if now is not None else time.time()) - rec["at"] < ttl:
            return rec["note"]
        return None

    def cooling(self, provider: str) -> bool:
        """True while a provider is in its remembered rate-limit/block window (callers route elsewhere instead of asking)."""
        return self.cap_blocked(provider) is not None

    def guarded(self, provider: str, alternatives: str, fn):
        """Run fn(); turn auth/blocks/persistent rate limits into Unavailable and remember them briefly."""
        cached = self.cap_blocked(provider)
        if cached:
            self.gaps.append(provider)
            raise Unavailable(provider, cached + " (remembered from a recent attempt; not retried yet)", alternatives)
        try:
            out = fn()
        except NetError as exc:
            if exc.kind == "rate_limited" or exc.status in (401, 403, 451):
                note = {"rate_limited": "rate-limited (HTTP 429) on the keyless tier"}.get(exc.kind, f"HTTP {exc.status}: access refused or needs authentication")
                self.cap_set(provider, "rate_limited" if exc.kind == "rate_limited" else "blocked", note, retry_after=getattr(exc, "retry_after", None))
                self.gaps.append(provider)
                raise Unavailable(provider, note, alternatives) from None
            raise ToolError(f"{provider}: {exc}") from None
        if (self.cap_read().get(provider) or {}).get("status") != "ok":   # write only on a change of state (fewer lock round-trips)
            self.cap_set(provider, "ok", "ok")
        return out

    def save(self, kind: str, url: str, title: str, text: str, raw: bytes | None = None, provider: str = "",
             extra: dict | None = None) -> SourceRef:
        ref = self.store.put(kind, url, title, text, raw_bytes=raw, provider=provider, extra=extra, precheck=self._not_cancelled)
        self.saved.append(ref)
        return ref


def untrusted_preview(ctx: Ctx, ref: SourceRef, lines: int = 18, chars: int = 1800) -> str:
    """Saved-source summary plus a short fenced preview of its first lines (data, not instructions)."""
    path = ctx.store.docs / ref.parts[0]
    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    head = "\n".join(body.splitlines()[:lines])[:chars]
    flag = " (already saved; unchanged)" if ref.deduped else (f" (NEW REVISION {ref.revision}; older text preserved)" if ref.revision > 1 else "")
    info = (f"saved: {ref.id}{flag}\n  file: {ref.parts[0]}" + (f" (+{len(ref.parts) - 1} more parts)" if len(ref.parts) > 1 else "") +
            f"\n  url: {ref.url}\n  retrieved_at: {ref.retrieved_at} | text_sha256: {ref.sha256[:16]}… | {ref.lines} lines"
            + (" | TRUNCATED" if ref.truncated else ""))
    return (info + "\n" + fence(head, source=ref.url) +
            f"\nNext: research_read {ref.parts[0]}:1-60 (or research_lookup \"terms\") to get a citable address+hash.")


def fenced_table(text: str, ref: SourceRef, note: str) -> str:
    """Search/list results: provider-supplied titles and snippets are untrusted data, so fence the whole table."""
    return fence(text.rstrip(), source=ref.url) + f"\n{note}"


def short(text: str | None, n: int = 160) -> str:
    return " ".join((text or "").split())[:n]
