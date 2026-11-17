#!/usr/bin/env python3
"""Write/update one Obsidian vault session note from a Claude Code or Codex transcript.

Invoked by claude-lab-clp session-layer hooks (Stop / SessionEnd / PreCompact), by the
claude-clp plugin, and by the Codex hooks alnair adds for --model gpt sessions (--codex). Offline, deterministic, no API calls. Fail soft: log under
~/.clp/logs and exit 0 so the session is never blocked.

Never edits, renames, or deletes any vault note it did not create for this session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = 1
TZ = ZoneInfo(os.environ.get("ALNAIR_TZ") or "America/Toronto")
DEFAULT_VAULT = Path(os.environ.get("ALNAIR_VAULT") or Path.home() / "Documents" / "Obsidian Vault").expanduser()
SESSIONS_FOLDER = "Claude Sessions"
DEFAULT_STORE = Path.home() / ".clp"
MAX_PROMPTS = 40
MAX_PROMPT_CHARS = 1200
MAX_ASSISTANT_CHARS = 4000
MAX_FINDINGS = 30
MAX_FINDING_CHARS = 400
MAX_FILES = 40
MAX_NOTE_CHARS = 120_000

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{8,})\b"),
    re.compile(r"(?i)\b(sk-ant-[A-Za-z0-9_\-]{8,})\b"),
    re.compile(r"(?i)\b(xox[baprs]-[A-Za-z0-9\-]{10,})\b"),
    re.compile(r"(?i)\b(ghp_[A-Za-z0-9]{20,})\b"),
    re.compile(r"(?i)\b(github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)\b(AIza[0-9A-Za-z_\-]{20,})\b"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]?([^\s'\"]{8,})"),
    re.compile(r"(?i)(bearer)\s+([A-Za-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
]

SAFE_TITLE = re.compile(r"[^A-Za-z0-9._\- ]+")
MULTI_SPACE = re.compile(r"\s+")


def store_root(store: Path | None = None) -> Path:
    if store is not None:
        return store.expanduser().resolve()
    env = os.environ.get("CLP_STORE")
    return Path(env).expanduser().resolve() if env else DEFAULT_STORE


def log_path(store: Path | None = None) -> Path:
    return store_root(store) / "logs" / "vault-session-note.log"


def _log(msg: str, store: Path | None = None) -> None:
    try:
        path = log_path(store)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        ts = datetime.now(TZ).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {msg}\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        pass


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in SECRET_PATTERNS:
        def repl(m: re.Match[str]) -> str:
            if m.lastindex and m.lastindex >= 2:
                return f"{m.group(1)} [REDACTED]"
            return "[REDACTED]"
        out = pat.sub(repl, out)
    return out


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
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


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif t == "tool_use":
                    name = block.get("name") or "tool"
                    inp = block.get("input")
                    if isinstance(inp, dict):
                        # Capture file paths and commands without dumping huge blobs
                        bits = []
                        for k in ("file_path", "path", "command", "pattern", "query"):
                            if k in inp and isinstance(inp[k], str):
                                bits.append(f"{k}={inp[k][:200]}")
                        parts.append(f"[tool_use {name} {' '.join(bits)}]".strip())
                    else:
                        parts.append(f"[tool_use {name}]")
                elif t == "tool_result":
                    # Prefer short success markers / file mentions
                    tr = block.get("content")
                    if isinstance(tr, str):
                        parts.append(tr[:300])
                    elif isinstance(tr, list):
                        for sub in tr[:3]:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                parts.append(str(sub.get("text") or "")[:300])
        return "\n".join(p for p in parts if p)
    return str(content)


CODEX_TYPES = {"session_meta", "response_item", "event_msg", "turn_context", "compacted", "world_state", "token_usage_record"}
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)


def _codex_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") in {"input_text", "output_text", "text"})
    return ""


def _is_codex_context(text: str) -> bool:
    """Codex puts environment and instruction blocks in user-role messages; they aren't prompts."""
    head = text.lstrip()[:200]
    return head.startswith("<") or head.startswith("# AGENTS.md") or head.startswith("# Instructions")


def parse_codex_transcript(transcript_path: Path) -> dict:
    """The same summary as parse_transcript, from a Codex rollout JSONL. The format isn't a stable interface,
    so unknown lines are skipped rather than failing."""
    prompts: list[str] = []
    assistants: list[str] = []
    ev_prompts: list[str] = []
    ev_assistants: list[str] = []
    findings: list[str] = []
    files_changed: list[str] = []
    cwd = session = model = None
    with transcript_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            typ, payload = obj.get("type"), obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if typ == "session_meta":
                session = session or payload.get("id") or payload.get("session_id")
                cwd = cwd or payload.get("cwd")
            elif typ == "turn_context":
                model = payload.get("model") or model
                cwd = cwd or payload.get("cwd")
            elif typ == "response_item" and payload.get("type") == "message":
                text = _codex_text(payload.get("content")).strip()
                if not text:
                    continue
                if payload.get("role") == "user" and not _is_codex_context(text):
                    prompts.append(text[:MAX_PROMPT_CHARS])
                elif payload.get("role") == "assistant":
                    assistants.append(text[:MAX_ASSISTANT_CHARS])
            elif typ == "response_item" and payload.get("type") in {"custom_tool_call", "function_call"}:
                raw = payload.get("input") if payload.get("type") == "custom_tool_call" else payload.get("arguments")
                if isinstance(raw, str) and "*** " in raw:
                    for path in PATCH_FILE.findall(raw.replace("\\n", "\n")):
                        path = path.strip()
                        if path and path not in files_changed:
                            files_changed.append(path)
            elif typ == "event_msg" and payload.get("type") == "item_completed" and isinstance(payload.get("item"), dict) \
                    and payload["item"].get("type") == "FileChange" and isinstance(payload["item"].get("changes"), dict):
                for path in payload["item"]["changes"]:
                    if isinstance(path, str) and path not in files_changed:
                        files_changed.append(path)
            elif typ == "event_msg" and payload.get("type") in {"user_message", "agent_message"}:
                text = str(payload.get("message") or "").strip()
                if text:
                    (ev_prompts if payload["type"] == "user_message" else ev_assistants).append(text[:MAX_PROMPT_CHARS if payload["type"] == "user_message" else MAX_ASSISTANT_CHARS])
    prompts = prompts or [p for p in ev_prompts if not _is_codex_context(p)]
    assistants = assistants or ev_assistants
    for text in assistants:
        for ln in text.splitlines():
            st = ln.strip()
            if st.startswith(("- ", "* ", "# ", "ANSWER:", "Decision:", "Finding:", "Changed:", "Created:", "Updated:")):
                findings.append(st[:MAX_FINDING_CHARS])
    return {
        "prompts": prompts[-MAX_PROMPTS:],
        "assistants": assistants[-12:],
        "findings": findings[-MAX_FINDINGS:],
        "files_changed": files_changed[:MAX_FILES],
        "cwd": cwd,
        "session_id": session,
        "model": model,
        "backend": "codex",
        "error": None,
    }


def is_codex_transcript(transcript_path: Path) -> bool:
    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") in CODEX_TYPES and isinstance(obj.get("payload"), dict):
                    return True
    except OSError:
        return False
    return False


def parse_transcript(transcript_path: Path) -> dict:
    if transcript_path.is_file() and is_codex_transcript(transcript_path):
        try:
            return parse_codex_transcript(transcript_path)
        except OSError as exc:
            return {"prompts": [], "assistants": [], "findings": [], "files_changed": [], "cwd": None,
                    "session_id": None, "backend": "codex", "error": str(exc)}
    return parse_claude_transcript(transcript_path)


def parse_claude_transcript(transcript_path: Path) -> dict:
    prompts: list[str] = []
    assistants: list[str] = []
    findings: list[str] = []
    files_changed: list[str] = []
    cwd = None
    session_from_file = None
    if not transcript_path.is_file():
        return {
            "prompts": prompts,
            "assistants": assistants,
            "findings": findings,
            "files_changed": files_changed,
            "cwd": cwd,
            "session_id": session_from_file,
            "error": f"transcript missing: {transcript_path}",
        }
    file_re = re.compile(
        r"(?:Wrote|Updated|Created|Edited|Modified|Deleted|Changed)\s+[`']?([^\s`']+\.\w{1,8})[`']?"
        r"|file_path[=:][`'\"]?([^\s`'\"]+)"
        r"|path[=:][`'\"]?([^\s`'\"]+\.\w{1,8})"
    )
    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if session_from_file is None and isinstance(obj.get("sessionId"), str):
                    session_from_file = obj["sessionId"]
                if cwd is None and isinstance(obj.get("cwd"), str):
                    cwd = obj["cwd"]
                typ = obj.get("type")
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
                if typ == "user" and msg and msg.get("role") == "user":
                    text = _text_from_content(msg.get("content")).strip()
                    # Skip pure tool_result echoes (no human prompt)
                    if text and not text.startswith("[tool_use") and "tool_result" not in (msg.get("content") and "" or ""):
                        # Heuristic: if content is only tool_result blocks, skip
                        content = msg.get("content")
                        if isinstance(content, list) and content and all(
                            isinstance(b, dict) and b.get("type") in {"tool_result", "tool_use"} for b in content
                        ):
                            # Still mine file paths from tool results
                            for b in content:
                                if isinstance(b, dict) and b.get("type") == "tool_use":
                                    inp = b.get("input") or {}
                                    if isinstance(inp, dict):
                                        for k in ("file_path", "path"):
                                            v = inp.get(k)
                                            if isinstance(v, str) and v not in files_changed:
                                                files_changed.append(v)
                            continue
                        prompts.append(text[:MAX_PROMPT_CHARS])
                elif typ == "assistant" and msg:
                    text = _text_from_content(msg.get("content")).strip()
                    if text:
                        assistants.append(text[:MAX_ASSISTANT_CHARS])
                        # Pull short finding-like lines
                        for ln in text.splitlines():
                            s = ln.strip()
                            if not s or s.startswith("```"):
                                continue
                            if s.startswith(("- ", "* ", "# ", "ANSWER:", "Decision:", "Finding:", "Changed:", "Created:", "Updated:")):
                                findings.append(s[:MAX_FINDING_CHARS])
                            for m in file_re.finditer(s):
                                hit = next(g for g in m.groups() if g)
                                if hit not in files_changed:
                                    files_changed.append(hit)
                    # tool_use file paths on assistant side
                    content = msg.get("content")
                    if isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_use":
                                inp = b.get("input") or {}
                                if isinstance(inp, dict):
                                    for k in ("file_path", "path"):
                                        v = inp.get(k)
                                        if isinstance(v, str) and v not in files_changed:
                                            files_changed.append(v)
    except OSError as exc:
        return {
            "prompts": prompts,
            "assistants": assistants,
            "findings": findings,
            "files_changed": files_changed,
            "cwd": cwd,
            "session_id": session_from_file,
            "error": str(exc),
        }
    return {
        "prompts": prompts[-MAX_PROMPTS:],
        "assistants": assistants[-12:],
        "findings": findings[-MAX_FINDINGS:],
        "files_changed": files_changed[:MAX_FILES],
        "cwd": cwd,
        "session_id": session_from_file,
        "error": None,
    }


def short_title(prompts: list[str], fallback: str = "session") -> str:
    raw = (prompts[0] if prompts else fallback) or fallback
    raw = raw.strip().splitlines()[0][:80]
    raw = SAFE_TITLE.sub("", raw)
    raw = MULTI_SPACE.sub(" ", raw).strip(" .-_")
    if not raw:
        raw = fallback
    return raw[:48]


def note_filename(when: datetime, title: str, session_id: str) -> str:
    prefix = (session_id or "unknown")[:8]
    day = when.strftime("%Y-%m-%d")
    return f"{day} {title} {prefix}.md"



def find_transcript(session_id: str, cwd: str | None = None) -> Path | None:
    """Locate Claude Code's transcript for this session (read-only under ~/.claude/projects)."""
    if not session_id:
        return None
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return None
    # Exact match anywhere under projects (cwd encoding varies)
    hits = list(projects.rglob(f"{session_id}.jsonl"))
    if len(hits) == 1:
        return hits[0]
    if hits and cwd:
        enc = "".join(ch if ch.isalnum() else "-" for ch in str(Path(cwd).resolve()))
        for h in hits:
            if enc[:40] in str(h) or Path(cwd).name in str(h):
                return h
    if hits:
        # Prefer newest
        hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return hits[0]
    return None


def marker_path(session_id: str, store: Path | None = None) -> Path:
    return store_root(store) / "sessions" / session_id / "vault-note.json"


def load_marker(session_id: str, store: Path | None = None) -> dict | None:
    path = marker_path(session_id, store)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_marker(session_id: str, data: dict, store: Path | None = None) -> None:
    path = marker_path(session_id, store)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def render_note(
    *,
    session_id: str,
    cwd: str | None,
    project: str | None,
    event: str,
    parsed: dict,
    prior_events: list[str],
    created_at: str,
    updated_at: str,
    note_rel: str,
) -> str:
    prompts = [redact(p) for p in parsed.get("prompts") or []]
    findings = [redact(f) for f in parsed.get("findings") or []]
    assistants = [redact(a) for a in parsed.get("assistants") or []]
    files_changed = [redact(f) for f in parsed.get("files_changed") or []]
    # Prefer last assistant reply as "outputs" summary
    last_out = assistants[-1] if assistants else "(no assistant text yet)"
    if len(last_out) > 2500:
        last_out = last_out[:2497] + "..."
    events = prior_events + [event] if event and event not in prior_events[-1:] else prior_events
    # keep unique order
    seen = set()
    ev_list = []
    for e in events:
        if e not in seen:
            seen.add(e)
            ev_list.append(e)
    codex = parsed.get("backend") == "codex" or os.environ.get("ALNAIR_BACKEND") == "codex"
    lines = [
        "---",
        "tags: [codex-session, alnair]" if codex else "tags: [claude-session, claude-lab-clp]",
        f"session_id: {session_id}",
        f"created: {created_at}",
        f"updated: {updated_at}",
        f"timezone: {TZ.key}",
        "source: alnair-codex" if codex else "source: claude-lab-clp",
        f"hooks: [{', '.join(ev_list)}]",
        "---",
        "",
        f"# {'Codex' if codex else 'Claude'} Session · {session_id[:8]}",
        "",
        *([f"- **Model:** `{parsed['model']}`"] if parsed.get("model") else []),
        f"- **When ({TZ.key}):** {updated_at}",
        f"- **Working dir / project:** `{project or cwd or '(unknown)'}`",
        f"- **Session id:** `{session_id}`",
        f"- **Last hook:** `{event}`",
        f"- **Note path (vault-relative):** `{note_rel}`",
        "",
        "## User prompts",
        "",
    ]
    if prompts:
        for i, p in enumerate(prompts, 1):
            lines.append(f"### Prompt {i}")
            lines.append("")
            lines.append("```")
            lines.append(p)
            lines.append("```")
            lines.append("")
    else:
        lines.append("(none parsed yet)")
        lines.append("")
    lines.extend(["## Key findings / decisions", ""])
    if findings:
        for f in findings:
            lines.append(f"- {f}")
    else:
        # Fall back to a truncated last assistant message
        snippet = last_out.splitlines()[:12]
        for s in snippet:
            if s.strip():
                lines.append(f"- {s.strip()[:MAX_FINDING_CHARS]}")
        if not snippet:
            lines.append("- (none yet)")
    lines.append("")
    lines.extend(["## Latest assistant output (concise)", "", "```", last_out, "```", ""])
    lines.extend(["## Files touched (from transcript)", ""])
    if files_changed:
        for f in files_changed:
            lines.append(f"- `{f}`")
    else:
        lines.append("- (none detected)")
    lines.append("")
    lines.append("---")
    lines.append("Auto-saved by `claude-lab-clp` vault_session_note. Do not hand-edit the frontmatter `session_id` if you want later hook fires to keep updating this same note.")
    body = "\n".join(lines) + "\n"
    if len(body) > MAX_NOTE_CHARS:
        body = body[: MAX_NOTE_CHARS - 20] + "\n\n…truncated…\n"
    return body


def update_session_meta(session_id: str, note_path: Path, store: Path | None = None) -> None:
    """Record vault note path on the session ledger meta (create meta if missing)."""
    try:
        here = Path(__file__).resolve().parent
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        import session_ledger as sl  # type: ignore

        path = sl.create(session_id, store=store, vaultNote=str(note_path), vaultNoteRel=str(note_path.name))
        # Append a pointer once per session (or when the note path changes)
        meta = __import__("json").loads((path / "meta.json").read_text())
        if meta.get("vaultNotePointer") != str(note_path):
            sl.append(
                session_id,
                f"Vault session note: {note_path}",
                kind="note",
                role="system",
                store=store,
                tags=["vault-session-note"],
            )
            sl.create(session_id, store=store, vaultNotePointer=str(note_path))
    except Exception as exc:  # noqa: BLE001
        _log(f"session meta update failed for {session_id}: {exc}", store)


def spawn_refresh(store: Path | None = None) -> None:
    """Best-effort detached corpus refresh so the new note becomes lookup-able."""
    try:
        import subprocess

        root = Path(os.environ.get("CLAUDE_CLP_ROOT") or Path(__file__).resolve().parents[1])
        tool = root / "bin" / "clp_tool.py"
        python = os.environ.get("CLAUDE_CLP_PYTHON") or sys.executable
        if not tool.is_file():
            return
        argv = [python, str(tool), "refresh"]
        if store is not None:
            argv = [python, str(tool), "--store", str(store), "refresh"]
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        _log(f"spawned refresh: {' '.join(argv)}", store)
    except Exception as exc:  # noqa: BLE001
        _log(f"refresh spawn failed: {exc}", store)


def run(
    *,
    payload: dict | None = None,
    transcript_path: Path | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    event: str = "manual",
    vault: Path | None = None,
    store: Path | None = None,
    refresh: bool = True,
) -> dict:
    """Create or update the session note. Returns a result dict. Never raises to callers of main()."""
    store = store_root(store)
    vault = (vault or Path(os.environ.get("CLAUDE_MEM_VAULT") or DEFAULT_VAULT)).expanduser().resolve()
    payload = payload or {}
    event = (
        event
        or payload.get("hook_event_name")
        or payload.get("event")
        or os.environ.get("CLAUDE_MEM_HOOK_EVENT")
        or "manual"
    )
    session_id = (
        session_id
        or os.environ.get("CLAUDE_MEM_SESSION_ID")
        or payload.get("session_id")
        or ""
    ).strip()
    cwd = cwd or payload.get("cwd") or os.environ.get("CLAUDE_MEM_CWD")
    tpath = transcript_path
    if tpath is None:
        raw = payload.get("transcript_path") or os.environ.get("CLAUDE_MEM_TRANSCRIPT")
        if raw:
            tpath = Path(raw)
    if not session_id and tpath and tpath.is_file():
        # Fall back to filename stem (Claude names transcript <session-id>.jsonl)
        session_id = tpath.stem
    if not session_id:
        raise ValueError("session_id is required (env CLAUDE_MEM_SESSION_ID or hook payload)")
    if tpath is None or not Path(tpath).is_file():
        found = find_transcript(session_id, cwd)
        if found is not None:
            tpath = found

    parsed = parse_transcript(tpath) if tpath else {
        "prompts": [], "assistants": [], "findings": [], "files_changed": [],
        "cwd": cwd, "session_id": session_id, "error": "no transcript_path",
    }
    # Never clobber a rich note with an empty parse (later hooks can race before the
    # transcript is visible, or fire without transcript_path).
    marker_early = load_marker(session_id, store) or {}
    empty_parse = not (parsed.get("prompts") or parsed.get("assistants") or parsed.get("findings"))
    if empty_parse and marker_early.get("path") and Path(marker_early["path"]).is_file():
        existing = Path(marker_early["path"]).read_text(encoding="utf-8", errors="replace")
        if "## User prompts" in existing and "(none parsed yet)" not in existing.split("## User prompts", 1)[-1][:200]:
            # Keep content; only append hook name to marker metadata
            hooks = list(dict.fromkeys([*(marker_early.get("hooks") or []), str(event)]))
            marker_early["hooks"] = hooks
            marker_early["updatedAt"] = datetime.now(TZ).isoformat(timespec="seconds")
            save_marker(session_id, marker_early, store)
            _log(f"skip empty overwrite for {session_id} on {event}", store)
            return {
                "ok": True,
                "path": marker_early["path"],
                "rel": marker_early.get("rel"),
                "sessionId": session_id,
                "event": event,
                "skipped": "empty_parse",
            }
    if not cwd:
        cwd = parsed.get("cwd")
    project = os.environ.get("CLAUDE_MEM_PROJECT") or cwd

    now = datetime.now(TZ)
    now_s = now.isoformat(timespec="seconds")
    marker = load_marker(session_id, store) or {}
    sessions_dir = vault / SESSIONS_FOLDER
    sessions_dir.mkdir(parents=True, exist_ok=True)

    if marker.get("path"):
        note_path = Path(marker["path"])
        # Safety: only update if it lives under Claude Sessions/ and frontmatter matches
        try:
            note_path.relative_to(sessions_dir)
        except ValueError as exc:
            raise RuntimeError(f"refusing to update note outside Claude Sessions/: {note_path}") from exc
        if note_path.is_file():
            head = note_path.read_text(encoding="utf-8", errors="replace")[:500]
            if f"session_id: {session_id}" not in head and f"session_id: '{session_id}'" not in head:
                # Do not overwrite a foreign file that happens to share the path string
                raise RuntimeError(f"existing note lacks our session_id marker: {note_path}")
        created_at = marker.get("createdAt") or now_s
        prior_events = list(marker.get("hooks") or [])
        title = marker.get("title") or short_title(parsed.get("prompts") or [])
    else:
        title = short_title(parsed.get("prompts") or [])
        note_path = sessions_dir / note_filename(now, title, session_id)
        # Collision: if file exists without our marker, pick a unique name
        if note_path.exists() and not marker:
            note_path = sessions_dir / note_filename(now, title, session_id + "-x")
        created_at = now_s
        prior_events = []

    note_rel = f"{SESSIONS_FOLDER}/{note_path.name}"
    body = render_note(
        session_id=session_id,
        cwd=cwd,
        project=project,
        event=str(event),
        parsed=parsed,
        prior_events=prior_events,
        created_at=created_at,
        updated_at=now_s,
        note_rel=note_rel,
    )
    atomic_write(note_path, body)
    new_marker = {
        "schema": SCHEMA,
        "sessionId": session_id,
        "path": str(note_path),
        "rel": note_rel,
        "title": title,
        "createdAt": created_at,
        "updatedAt": now_s,
        "hooks": list(dict.fromkeys([*prior_events, str(event)])),
        "transcript": str(tpath) if tpath else None,
        "sha256": _sha(body),
        "createdBy": "claude-lab-clp",
    }
    save_marker(session_id, new_marker, store)
    update_session_meta(session_id, note_path, store)
    if refresh:
        spawn_refresh(store if store != DEFAULT_STORE else None)
    return {"ok": True, "path": str(note_path), "rel": note_rel, "sessionId": session_id, "event": event}


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Save/update a Claude session note into the Obsidian vault.")
    p.add_argument("--stdin-payload", action="store_true", help="Read classic hook JSON from stdin")
    p.add_argument("--payload-file", type=Path, help="Hook JSON file (tests)")
    p.add_argument("--transcript", type=Path)
    p.add_argument("--session-id")
    p.add_argument("--cwd")
    p.add_argument("--event", default=None, help="Hook event name (default: from payload or 'manual')")
    p.add_argument("--vault", type=Path)
    p.add_argument("--store", type=Path)
    p.add_argument("--no-refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--codex", action="store_true",
                   help="Codex hook mode: always print {} (Codex wants JSON on stdout) and log the result instead")
    opt = p.parse_args(argv)
    payload = None
    try:
        if opt.stdin_payload:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        elif opt.payload_file:
            payload = json.loads(opt.payload_file.read_text(encoding="utf-8"))
        result = run(
            payload=payload,
            transcript_path=opt.transcript,
            session_id=opt.session_id,
            cwd=opt.cwd,
            event=opt.event or "",  # empty → run() reads payload / env / 'manual'
            vault=opt.vault,
            store=opt.store,
            refresh=not opt.no_refresh,
        )
        if opt.codex:
            _log(f"codex {result.get('event')} {result.get('path')}", opt.store)
            print("{}")
        elif opt.json:
            print(json.dumps(result))
        else:
            print(result["path"])
        return 0
    except Exception as exc:  # noqa: BLE001 — hooks must fail soft
        _log(f"FAIL {exc}\n{traceback.format_exc()}", opt.store)
        if opt.codex:
            print("{}")
        elif opt.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"vault_session_note: {exc}", file=sys.stderr)
        return 0  # soft fail for hooks


if __name__ == "__main__":
    raise SystemExit(main())
