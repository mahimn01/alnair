"""The research memory: every fetched source saved as exact text, never silently overwritten.

Layout under ~/.clp/research (override RESEARCH_HOME):
  docs/<id>[.pNN].txt   the text CLP indexes and you quote (header + body, one file per part)
  raw/<sha256>.bin      the bytes exactly as received (audit trail)
  meta/<id>.json        url, retrieved_at, sha256 of the text and raw bytes, provider, revision links
  urls.json             canonical url -> ids in time order (revision history)
  clp/                  the CLP index (corpus 'research') over docs/
  cache/                bounded HTTP cache and rate-limit clocks
Same text again = same id (deduped, last_seen updated). Changed text for a URL = new id; the
old one stays and is marked superseded_by, so an old citation still verifies against its words.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .extract import clean


class StoreError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit].strip("-")) or "x"


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


@dataclass
class SourceRef:
    id: str
    kind: str
    url: str
    title: str
    sha256: str
    parts: list[str]
    deduped: bool
    revision: int
    supersedes: str | None
    truncated: bool
    retrieved_at: str
    lines: int


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}-{threading.get_ident()}-{os.urandom(3).hex()}")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    tmp.replace(path)


class Store:
    def __init__(self, home: Path | None = None):
        self.home = (home or config.research_home())
        self.docs, self.meta, self.raw, self.clp = (self.home / n for n in ("docs", "meta", "raw", "clp"))
        self.urls_path = self.home / "urls.json"

    # -- sizes -------------------------------------------------------------------
    def usage(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.home):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    # -- write -------------------------------------------------------------------
    def _urls(self) -> dict[str, list[str]]:
        try:
            return json.loads(self.urls_path.read_text())
        except (OSError, ValueError):
            return {}

    def put(self, kind: str, url: str, title: str, text: str, *, raw_bytes: bytes | None = None,
            provider: str = "", extra: dict | None = None, precheck=None) -> SourceRef:
        """precheck (optional) runs once the store lock is held and may raise to abandon the save: a batch
        that has passed its deadline uses it so that no save starts after the caller stopped waiting."""
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.home / ".lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if precheck is not None:
                    precheck()
                return self._put(kind, url, title, text, raw_bytes, provider, extra or {})
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _put(self, kind, url, title, text, raw_bytes, provider, extra) -> SourceRef:
        body = clean(text) if text.strip() else ""
        if not body.strip():
            raise StoreError("nothing to store: the source text is empty")
        truncated = False
        if len(body.encode("utf-8")) > config.MAX_TEXT:
            body = body.encode("utf-8")[: config.MAX_TEXT].decode("utf-8", errors="ignore")
            body = body[: body.rfind("\n") + 1] + "[TRUNCATED by clp-research at the stored-text size limit]\n"
            truncated = True
        text_sha = sha256(body)
        ident = sha256(f"{url}\n{text_sha}")[:12]   # same text under another URL is a separate, separately-cited source
        found = list(self.meta.glob(f"*-{ident}.json")) if self.meta.is_dir() else []
        sid = found[0].stem if found else f"{slugify(kind, 14)}-{slugify(title or url, 40)}-{ident}"
        meta_path = self.meta / f"{sid}.json"
        now = now_iso()
        urls = self._urls()
        history = urls.get(url, [])
        if meta_path.exists():  # exactly this text from this URL is already saved: dedupe
            m = json.loads(meta_path.read_text())
            m["last_seen"], m["fetch_count"] = now, m.get("fetch_count", 1) + 1
            if history and history[-1] != sid:  # the page went back to an earlier text: record it as the current revision
                prev_id = history[-1]
                if (self.meta / f"{prev_id}.json").exists():
                    pm = json.loads((self.meta / f"{prev_id}.json").read_text())
                    pm["superseded_by"] = sid
                    atomic_write(self.meta / f"{prev_id}.json", json.dumps(pm, indent=1, sort_keys=True))
                m["superseded_by"] = None
                urls[url] = history + [sid]
                atomic_write(self.urls_path, json.dumps(urls, indent=1, sort_keys=True))
            atomic_write(meta_path, json.dumps(m, indent=1, sort_keys=True))
            return self._ref(m, deduped=True)
        if self.usage() > config.STORE_MAX_BYTES:
            raise StoreError(f"research store is over its {config.STORE_MAX_BYTES // 2**30} GB limit; prune {self.home}")
        parts = self._split(body)
        names, file_hashes = [], []
        for i, chunk in enumerate(parts, 1):
            name = f"{sid}.txt" if len(parts) == 1 else f"{sid}.p{i:02d}.txt"
            header = (f"[research-source] id={sid} kind={kind}" + (f" part={i}/{len(parts)}" if len(parts) > 1 else "") + "\n"
                      f"url: {url}\ntitle: {' '.join((title or '').split())[:200]}\n---\n")
            path = self.docs / name
            content = header + chunk
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise StoreError(f"{name} already exists with different content; refusing to overwrite a cited source")
            atomic_write(path, content)
            names.append(name)
            file_hashes.append(sha256(content))
        raw_sha = None
        if raw_bytes is not None and len(raw_bytes) <= config.MAX_BODY:
            raw_sha = sha256(raw_bytes)
            if not (self.raw / f"{raw_sha}.bin").exists():
                atomic_write(self.raw / f"{raw_sha}.bin", raw_bytes)
        prev = history[-1] if history else None
        m = {
            "id": sid, "kind": kind, "url": url, "title": title, "provider": provider,
            "retrieved_at": now, "first_seen": now, "last_seen": now, "fetch_count": 1,
            "text_sha256": text_sha, "identity": ident, "raw_sha256": raw_sha, "bytes": len(body.encode("utf-8")),
            "parts": names, "file_sha256": file_hashes, "truncated": truncated, "revision": len(history) + 1,
            "supersedes": prev, "superseded_by": None, "lines": body.count("\n"), "extra": extra,
        }
        atomic_write(meta_path, json.dumps(m, indent=1, sort_keys=True))
        if prev and (self.meta / f"{prev}.json").exists():
            pm = json.loads((self.meta / f"{prev}.json").read_text())
            pm["superseded_by"] = sid
            atomic_write(self.meta / f"{prev}.json", json.dumps(pm, indent=1, sort_keys=True))
        urls[url] = history + [sid]
        atomic_write(self.urls_path, json.dumps(urls, indent=1, sort_keys=True))
        (self.home / "dirty").write_text(now)
        return self._ref(m, deduped=False)

    @staticmethod
    def _split(body: str) -> list[str]:
        if len(body.encode("utf-8")) <= config.PART_BYTES:
            return [body]
        parts, cur, size = [], [], 0
        for line in body.split("\n"):
            n = len(line.encode("utf-8")) + 1
            if size + n > config.PART_BYTES and cur:
                parts.append("\n".join(cur) + "\n")
                cur, size = [], 0
            cur.append(line)
            size += n
        if cur:
            parts.append("\n".join(cur).rstrip("\n") + "\n")
        if len(parts) > config.MAX_PARTS:
            parts = parts[: config.MAX_PARTS]
            parts[-1] += "[TRUNCATED by clp-research: too many parts]\n"
        return parts

    @staticmethod
    def _ref(m: dict, deduped: bool) -> SourceRef:
        return SourceRef(m["id"], m["kind"], m["url"], m["title"], m["text_sha256"], m["parts"], deduped,
                         m["revision"], m.get("supersedes"), m["truncated"], m["retrieved_at"], m["lines"])

    def settle(self) -> None:
        """Wait for any save in progress to finish (take and release the store lock)."""
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.home / ".lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fcntl.flock(lock, fcntl.LOCK_UN)

    # -- read --------------------------------------------------------------------
    def get_meta(self, sid: str) -> dict | None:
        if not sid or "/" in sid or "\\" in sid or ".." in sid:      # an id is a file name inside meta/, never a path
            return None
        try:
            return json.loads((self.meta / f"{sid}.json").read_text())
        except (OSError, ValueError):
            return None

    def meta_for_file(self, name: str) -> dict | None:
        base = re.sub(r"(\.p\d\d)?\.txt$", "", name.rsplit("/", 1)[-1])
        return self.get_meta(base)

    def list_sources(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        metas = []
        if self.meta.is_dir():
            for p in self.meta.glob("*.json"):
                try:
                    m = json.loads(p.read_text())
                except ValueError:
                    continue
                if kind is None or m.get("kind") == kind:
                    metas.append(m)
        metas.sort(key=lambda m: m.get("retrieved_at", ""), reverse=True)
        return metas[:limit]

    def source_problem(self, m: dict) -> str | None:
        """None if the stored parts of one source still hash to the recorded sha256, else a reason."""
        texts = []
        fh = m.get("file_sha256") or []
        for i, name in enumerate(m["parts"]):
            try:
                full = (self.docs / name).read_text(encoding="utf-8")
            except OSError:
                return f"{m['id']}: missing file {name}"
            if i < len(fh) and sha256(full) != fh[i]:
                return f"{m['id']}: {name} was modified after saving (header or body no longer matches its recorded sha256)"
            texts.append(full.split("\n---\n", 1)[-1])
        if sha256("".join(texts)) != m["text_sha256"]:
            return f"{m['id']}: text no longer matches its recorded sha256 (the saved file was modified)"
        return None

    def check_integrity(self) -> list[str]:
        """Compare every stored doc part with its recorded hashes (header and body). Returns problems (empty = intact)."""
        known = set()
        problems = []
        for m in self.list_sources(limit=10**6):
            known.update(m["parts"])
            p = self.source_problem(m)
            if p:
                problems.append(p)
        if self.docs.is_dir():
            problems += [f"{n.name}: file in docs/ that no saved source references" for n in sorted(self.docs.glob("*.txt")) if n.name not in known]
        return problems

    # -- CLP index ---------------------------------------------------------------
    def ensure_index(self, timeout: int = 300) -> None:
        """(Re)build the CLP index over docs/ when something was added. Offline."""
        if not any(self.docs.glob("*.txt")) if self.docs.is_dir() else True:
            raise StoreError("the research store is empty: fetch a source first (scholar_*, sec_*, wayback_fetch, research_fetch ...)")
        dirty = (self.home / "dirty").exists() or not (self.clp / "research" / "corpus.json").exists()
        if not dirty:
            return
        bad = self.check_integrity()
        if bad:
            raise StoreError("refusing to (re)index: stored sources were modified after saving: " + "; ".join(bad[:3]))
        with open(self.home / ".index.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if not ((self.home / "dirty").exists() or not (self.clp / "research" / "corpus.json").exists()):
                    return
                first = not (self.clp / "research" / "corpus.json").exists()
                cmd = [config.clp_python(), str(config.clp_tool_path()), "init" if first else "refresh",
                       *([str(self.docs)] if first else []), "--store", str(self.clp), "--name", "research"]
                env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR")}
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
                except subprocess.TimeoutExpired:
                    raise StoreError(f"indexing exceeded {timeout}s") from None
                if proc.returncode != 0:
                    raise StoreError("indexing failed: " + (proc.stderr or proc.stdout).strip()[-400:])
                (self.home / "dirty").unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
