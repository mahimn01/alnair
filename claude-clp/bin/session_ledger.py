#!/usr/bin/env python3
"""Session ledger: address-verified durable turns/facts under ~/.clp/sessions/<id>/.

The chat window is a cache; this ledger is disk memory. Every append gets a stable address
and sha256. Reads refuse a stale #sha. No network. Never writes the Obsidian vault or ~/.claude.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
DEFAULT_STORE = Path.home() / ".clp"
_ADDR = re.compile(
    r"^sessions/(?P<sid>[^/]+)/turns\.jsonl#seq=(?P<seq>\d+)(?:#sha=(?P<sha>[0-9a-fA-F]{4,64}))?$"
)
_SHA_ONLY = re.compile(r"^(?P<path>.+)#sha=(?P<sha>[0-9a-fA-F]{4,64})$")


def store_root(store: Path | None = None) -> Path:
    if store is not None:
        return store.expanduser().resolve()
    env = os.environ.get("CLP_STORE")
    return Path(env).expanduser().resolve() if env else DEFAULT_STORE


def sessions_dir(store: Path | None = None) -> Path:
    return store_root(store) / "sessions"


def session_dir(session_id: str, store: Path | None = None) -> Path:
    sid = _safe_id(session_id)
    return sessions_dir(store) / sid


def _safe_id(session_id: str) -> str:
    sid = (session_id or "").strip()
    if not sid or "/" in sid or sid in {".", ".."} or len(sid) > 128:
        raise ValueError(f"invalid session id: {session_id!r}")
    return sid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _dump_json(path: Path, obj: dict) -> None:
    _atomic_write(path, json.dumps(obj, indent=2) + "\n")


def create(session_id: str, project: str | None = None, store: Path | None = None, **extra) -> Path:
    """Create sessions/<id>/ with meta.json. Idempotent if meta already matches schema."""
    path = session_dir(session_id, store)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    (path / "facts").mkdir(exist_ok=True, mode=0o700)
    turns = path / "turns.jsonl"
    if not turns.exists():
        turns.touch(mode=0o600)
    meta_path = path / "meta.json"
    now = _now()
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["updatedAt"] = now
        if project:
            meta["project"] = project
        meta.update({k: v for k, v in extra.items() if v is not None})
    else:
        meta = {
            "schema": SCHEMA,
            "sessionId": _safe_id(session_id),
            "project": project,
            "createdAt": now,
            "updatedAt": now,
            **{k: v for k, v in extra.items() if v is not None},
        }
    _dump_json(meta_path, meta)
    return path


@contextmanager
def _locked(path: Path):
    """Hold an exclusive lock on <session dir>/.lock so appenders from different processes take turns."""
    with open(path / ".lock", "a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _next_seq(turns: Path) -> int:
    if not turns.exists() or turns.stat().st_size == 0:
        return 1
    last = 0
    with turns.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                last = int(json.loads(line).get("seq") or last)
            except (ValueError, AttributeError, TypeError):
                continue
    return last + 1


def append(
    session_id: str,
    text: str,
    *,
    kind: str = "turn",
    role: str = "assistant",
    tags: list[str] | None = None,
    store: Path | None = None,
    project: str | None = None,
) -> dict:
    """Append one durable record; return it (includes address + sha256)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    kind = kind or "turn"
    role = role or "assistant"
    if kind not in {"turn", "fact", "compact_flush", "note"}:
        raise ValueError(f"unsupported kind: {kind}")
    if role not in {"user", "assistant", "system"}:
        raise ValueError(f"unsupported role: {role}")
    path = session_dir(session_id, store)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except FileExistsError:  # another process made it first
        pass
    with _locked(path):
        create(session_id, project=project, store=store)
        turns = path / "turns.jsonl"
        seq = _next_seq(turns)
        digest = _sha(text)
        short = digest[:12]
        address = f"sessions/{_safe_id(session_id)}/turns.jsonl#seq={seq}#sha={short}"
        record = {
            "schema": SCHEMA,
            "seq": seq,
            "ts": _now(),
            "kind": kind,
            "role": role,
            "text": text,
            "sha256": digest,
            "address": address,
            "tags": list(tags or []),
        }
        # a crash can leave the last line without its newline; start a fresh line so the new record stays readable
        with turns.open("rb") as raw:
            torn = raw.seek(0, os.SEEK_END) > 0 and raw.seek(-1, os.SEEK_END) >= 0 and raw.read(1) != b"\n"
        with turns.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(("\n" if torn else "") + json.dumps(record, ensure_ascii=False) + "\n")
        meta_path = path / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updatedAt"] = record["ts"]
        meta["lastSeq"] = seq
        _dump_json(meta_path, meta)
    return record


def _load_records(session_id: str, store: Path | None = None) -> list[dict]:
    turns = session_dir(session_id, store) / "turns.jsonl"
    if not turns.is_file():
        raise FileNotFoundError(f"no session ledger at {turns}")
    out: list[dict] = []
    with turns.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:  # a torn line from a crash
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def read(address: str, store: Path | None = None) -> dict:
    """Return the record for an address. Refuse if #sha no longer matches."""
    address = (address or "").strip()
    m = _ADDR.match(address)
    if not m:
        raise ValueError(
            "address must look like sessions/<id>/turns.jsonl#seq=<n>#sha=<hex> "
            f"(got {address!r})"
        )
    sid, seq = m.group("sid"), int(m.group("seq"))
    want = (m.group("sha") or "").lower()
    for rec in _load_records(sid, store):
        if int(rec.get("seq", -1)) != seq:
            continue
        digest = rec.get("sha256") or _sha(rec.get("text", ""))
        if want and not digest.lower().startswith(want.lower()):
            raise ValueError(
                f"stale sha for {address}: ledger has {digest[:12]}…; "
                "read the current address from session-lookup / session-status"
            )
        return rec
    raise FileNotFoundError(f"no record at {address}")


def lookup(session_id: str, query: str, limit: int = 5, store: Path | None = None) -> list[dict]:
    """Keyword hits over durable texts; each hit includes address + preview."""
    q = (query or "").strip().lower()
    if not q:
        raise ValueError("query must be non-empty")
    limit = max(1, min(int(limit), 20))
    terms = [t for t in re.split(r"\s+", q) if t]
    hits: list[dict] = []
    for rec in reversed(_load_records(session_id, store)):
        text = rec.get("text") or ""
        low = text.lower()
        if not all(t in low for t in terms):
            continue
        preview = text if len(text) <= 160 else text[:157] + "..."
        hits.append(
            {
                "address": rec["address"],
                "seq": rec["seq"],
                "kind": rec.get("kind"),
                "role": rec.get("role"),
                "preview": preview,
                "ts": rec.get("ts"),
            }
        )
        if len(hits) >= limit:
            break
    return hits


def list_sessions(limit: int = 20, store: Path | None = None) -> list[dict]:
    root = sessions_dir(store)
    if not root.is_dir():
        return []
    metas: list[dict] = []
    for child in root.iterdir():
        meta = child / "meta.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        data["_path"] = str(child)
        data["_mtime"] = meta.stat().st_mtime
        metas.append(data)
    metas.sort(key=lambda m: m.get("_mtime", 0), reverse=True)
    out = []
    for m in metas[: max(1, min(int(limit), 100))]:
        m.pop("_mtime", None)
        out.append(m)
    return out


def resolve_session(id_or_latest: str, store: Path | None = None) -> str:
    raw = (id_or_latest or "").strip()
    if not raw or raw == "latest":
        sessions = list_sessions(limit=1, store=store)
        if not sessions:
            raise FileNotFoundError("no session ledger exists yet under ~/.clp/sessions")
        return sessions[0]["sessionId"]
    path = session_dir(raw, store)
    if not (path / "meta.json").is_file():
        raise FileNotFoundError(f"no session ledger for {raw!r}")
    return _safe_id(raw)


def status(session_id: str | None = None, store: Path | None = None) -> dict:
    root = sessions_dir(store)
    report: dict = {"sessionsRoot": str(root), "exists": root.is_dir()}
    if session_id:
        sid = resolve_session(session_id, store)
        path = session_dir(sid, store)
        meta = json.loads((path / "meta.json").read_text())
        n = 0
        turns = path / "turns.jsonl"
        if turns.is_file() and turns.stat().st_size:
            with turns.open(encoding="utf-8") as fh:
                n = sum(1 for line in fh if line.strip())
        report["session"] = {**meta, "turns": n, "path": str(path)}
    else:
        report["sessions"] = list_sessions(limit=10, store=store)
    return report
