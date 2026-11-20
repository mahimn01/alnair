#!/usr/bin/env python3
"""research_mcp: the clp-research tools as a standard stdio MCP server (JSON-RPC 2.0, one message per line).

What it does: exposes the same tools the Claude Code plugin does (scholar_*, sec_*, wayback_*, github_*,
research_lookup/read/verify ...) to any MCP client. Not used by claude-lab-clp (its session layer strips
mcpServers and loads the plugin instead); this is for other MCP hosts and for protocol tests.
How to run: python research_mcp.py   (reads requests on stdin, writes responses on stdout)
What it changes: the same as research_tool.py: only $RESEARCH_HOME (default ~/.clp/research). Public reads only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clpresearch import __version__, tools  # noqa: E402

PROTOCOL = "2025-06-18"


def _tool_list() -> list[dict]:
    return [{"name": t.name, "description": t.description, "inputSchema": {"type": "object", "properties": t.props, "required": t.required}} for t in tools.TOOLS]


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, one response (or None for notifications) out."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if msg.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "invalid request"}}
    if mid is None:  # notification (e.g. notifications/initialized): no response
        return None
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                                                       "serverInfo": {"name": "clp-research", "version": __version__}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tool_list()}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or name not in tools.BY_NAME:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        if not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "arguments must be an object"}}
        code, text = tools.run(name, dict(args))
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": code in (2, 4)}}
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
    # Bytes that are not UTF-8 become a parse error on that line instead of ending the server; output is always UTF-8.
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8")
    serve()
