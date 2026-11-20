#!/usr/bin/env python3
"""clp_mcp: the offline CLP memory tools as a stdio MCP server (JSON-RPC 2.0, one message per line).

What it does: gives MCP hosts that can't load the Claude Code plugin (Codex in particular) the same offline
tools the plugin registers in hooks/register.js: lookup, read, verify, status, refresh and the session ledger.
The paid `ask` tool is left out on purpose.
How to run: python clp_mcp.py   (Alnair adds it to ~/.codex/config.toml as mcp_servers.alnair_clp)
What it changes: only the clp store (~/.clp or $CLP_STORE), through bin/clp_tool.py. No network, no API calls.
Session tools default to $CLAUDE_MEM_SESSION_ID, which the alnair launcher sets for each run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin" / "clp_tool.py"
PROTOCOL = "2025-06-18"
VERSION = "0.2.0"

CORPUS = {"corpus": {"type": "string", "maxLength": 100, "description": "Corpus name, only when the user has several (status names the default)."}}


def _named(a: dict) -> list[str]:
    return ["--name=" + a["corpus"]] if a.get("corpus") else []


def _session(a: dict) -> list[str]:
    sid = a.get("session") or os.environ.get("CLAUDE_MEM_SESSION_ID")
    if not sid:
        raise ValueError("session tools need a session id: start the session with alnair, or pass session")
    return ["--session=" + sid]


# name, description, properties, required, argv(args) -> list, stdin(args) -> str | None, timeout seconds
TOOLS: list[dict] = [
    {"name": "lookup", "timeout": 60,
     "description": "Find where something is in the user's indexed notes and code (the CLP corpus, by default a copy of their notes vault): "
                    "a note ([[AMZN]] or positions/AMZN.md), a Python name (pkg.mod.func) or keywords. Returns addresses path:start-end#sha "
                    "with short previews. Offline. Read an address before quoting it.",
     "props": {"query": {"type": "string", "minLength": 1, "maxLength": 500},
               "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Keyword hits (default 5)."}, **CORPUS},
     "required": ["query"],
     "argv": lambda a: ["lookup", *(["--limit=%d" % a["limit"]] if a.get("limit") else []), *_named(a), "--", a["query"]]},
    {"name": "read", "timeout": 60,
     "description": "The verbatim lines at an address from lookup (path:start-end#sha, path:start-end, a file path or [[Note]]), at most 200 lines. "
                    "The header line is the ref to cite. Offline. A #sha that no longer matches means the file changed: read it again.",
     "props": {"address": {"type": "string", "minLength": 1, "maxLength": 1000}, **CORPUS},
     "required": ["address"],
     "argv": lambda a: ["read", *_named(a), "--", a["address"]]},
    {"name": "verify", "timeout": 60,
     "description": "The local answer gate; call it before presenting an answer from the user's files as fact. answer is exactly: one line "
                    "\"ANSWER: <the answer>\" then <claims>[{\"text\": \"...\", \"citations\": [{\"ref\": \"path:start-end#sha\", \"quote\": \"exact words from those lines\"}]}]</claims> "
                    "(or \"ANSWER: UNANSWERABLE\" and <claims>[]</claims>). PASS: every quote is word for word in its lines, every number or name is in the quotes, "
                    "and the note or Python name the question names is the one quoted. REFUSED lists what to fix. Offline.",
     "props": {"question": {"type": "string", "minLength": 1, "maxLength": 4000, "description": "The user's question, as asked."},
               "answer": {"type": "string", "minLength": 1, "maxLength": 60000}, **CORPUS},
     "required": ["question", "answer"],
     "argv": lambda a: ["verify", *_named(a), "--question=" + a["question"], "--answer-file=-"],
     "stdin": lambda a: a["answer"]},
    {"name": "status", "timeout": 300,
     "description": "The CLP corpus: which folder is indexed, how many files, when it was last indexed, and whether the folder changed since. Offline.",
     "props": {**CORPUS}, "required": [],
     "argv": lambda a: ["status", *_named(a)]},
    {"name": "refresh", "timeout": 300,
     "description": "Re-scan the indexed folder and re-index it (writes only the clp store, never the folder). Use when verify or status says a file "
                    "changed since indexing. Offline.",
     "props": {**CORPUS}, "required": [],
     "argv": lambda a: ["refresh", *_named(a)]},
    {"name": "session_append", "timeout": 60,
     "description": "Append a durable turn or fact to this chat's session ledger under ~/.clp/sessions/<id> (address + sha). The window is a cache; "
                    "the ledger survives compaction and resume. Offline. Use after verifying important facts or decisions so they are not forgotten.",
     "props": {"text": {"type": "string", "minLength": 1, "maxLength": 100000},
               "kind": {"type": "string", "enum": ["turn", "fact", "compact_flush", "note"], "description": "Default turn."},
               "role": {"type": "string", "enum": ["user", "assistant", "system"], "description": "Default assistant."},
               "session": {"type": "string", "maxLength": 128, "description": "Defaults to the session alnair started."},
               "tag": {"type": "string", "maxLength": 64}},
     "required": ["text"],
     "argv": lambda a: ["session-append", *_session(a), *(["--kind=" + a["kind"]] if a.get("kind") else []),
                        *(["--role=" + a["role"]] if a.get("role") else []), *(["--tag=" + a["tag"]] if a.get("tag") else []), "--text-file=-"],
     "stdin": lambda a: a["text"]},
    {"name": "session_read", "timeout": 60,
     "description": "Read a session-ledger address (sessions/<id>/turns.jsonl#seq=N#sha=...). Stale #sha is refused. Offline.",
     "props": {"address": {"type": "string", "minLength": 1, "maxLength": 1000}},
     "required": ["address"],
     "argv": lambda a: ["session-read", "--", a["address"]]},
    {"name": "session_lookup", "timeout": 60,
     "description": "Keyword search over this chat's durable session ledger (prior turns and facts). Returns addresses and previews. Offline. "
                    "Prefer this before answering from memory of earlier turns.",
     "props": {"query": {"type": "string", "minLength": 1, "maxLength": 500},
               "session": {"type": "string", "maxLength": 128, "description": "Session id or 'latest' (default: the session alnair started)."},
               "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
     "required": ["query"],
     "argv": lambda a: ["session-lookup", *_session(a), *(["--limit=%d" % a["limit"]] if a.get("limit") else []), "--", a["query"]]},
    {"name": "session_list", "timeout": 60,
     "description": "List recent session ledgers under ~/.clp/sessions. Offline.",
     "props": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": [],
     "argv": lambda a: ["session-list", *(["--limit=%d" % a["limit"]] if a.get("limit") else [])]},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def python() -> str:
    return os.environ.get("CLAUDE_CLP_PYTHON") or sys.executable


def call(name: str, args: dict) -> tuple[bool, str]:
    """Run one tool through clp_tool.py by argv (no shell). Returns (is_error, text)."""
    spec = BY_NAME[name]
    missing = [k for k in spec["required"] if not isinstance(args.get(k), str) or not args.get(k)]
    if missing:
        return True, "missing " + ", ".join(missing)
    try:
        argv = [python(), str(TOOL), *spec["argv"](args)]
    except ValueError as exc:
        return True, str(exc)
    except (TypeError, KeyError, AttributeError):
        return True, "an argument is missing or has the wrong type"
    stdin = spec["stdin"](args) if "stdin" in spec else None
    # UTF-8 both ways, whatever the locale: the child reads and prints through its own stdin and stdout
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if os.name != "nt":  # and its arguments as UTF-8 bytes: on Linux a locale that can't encode one would stop the call
        argv = [a.encode("utf-8", "surrogateescape") for a in argv]
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=spec["timeout"], check=False)
    except subprocess.TimeoutExpired:
        return True, f"clp_tool {name} timed out after {spec['timeout']}s"
    text = "\n".join(s.strip() for s in (done.stdout, done.stderr) if s and s.strip()) or "(no output)"
    ok = done.returncode == 0 or (name == "verify" and done.returncode == 1)
    return (not ok), text


def tool_list() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": {"type": "object", "properties": t["props"], "required": t["required"]}} for t in TOOLS]


def handle(msg: dict) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if msg.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "invalid request"}}
    if mid is None:
        return None
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                                                       "capabilities": {"tools": {"listChanged": False}},
                                                       "serverInfo": {"name": "alnair-clp", "version": VERSION}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in BY_NAME:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        if not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "arguments must be an object"}}
        is_error, text = call(name, args)
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            out = handle(msg) if isinstance(msg, dict) else {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if out is not None:
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    # the MCP wire is UTF-8 whatever the locale (Windows pipes default to cp1252)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    serve()
