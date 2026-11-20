"""clp_mcp: the offline CLP tools over stdio MCP, for Codex. Mirrors the Claude Code plugin minus the paid ask."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clp_mcp", ROOT / "bin" / "clp_mcp.py")
M = importlib.util.module_from_spec(spec)
sys.modules["clp_mcp"] = M
spec.loader.exec_module(M)


def rpc(method, params=None, mid=1):
    return M.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_tools_match_the_plugin_without_ask():
    js = (ROOT / "plugin/hooks/register.js").read_text()
    plugin = re.findall(r"\{name:'([a-z_]+)'", js)
    assert "ask" in plugin
    listed = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    assert listed == [n for n in plugin if n != "ask"]
    for name in listed:
        block = js[js.index(f"{{name:'{name}'"):]
        block = block[:block.index("argv:")]
        required = re.search(r"required:\[([^\]]*)\]", block)
        want = re.findall(r"'([a-z_]+)'", required.group(1)) if required else []
        assert M.BY_NAME[name]["required"] == want, name


def test_initialize_and_unknown_tool():
    init = rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["serverInfo"]["name"] == "alnair-clp" and "tools" in init["capabilities"]
    assert rpc("tools/call", {"name": "ask", "arguments": {"question": "q"}})["error"]["code"] == -32602
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_session_tools_round_trip_through_clp_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CLP_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_MEM_SESSION_ID", "11111111-2222-4333-8444-555555555555")
    out = rpc("tools/call", {"name": "session_append", "arguments": {"text": "Decided: ship the Codex backend", "kind": "fact"}})["result"]
    assert out["isError"] is False, out
    found = rpc("tools/call", {"name": "session_lookup", "arguments": {"query": "Codex backend"}})["result"]
    assert found["isError"] is False and "Codex backend" in found["content"][0]["text"]


def test_session_tools_without_a_session_explain_themselves(monkeypatch):
    monkeypatch.delenv("CLAUDE_MEM_SESSION_ID", raising=False)
    out = rpc("tools/call", {"name": "session_append", "arguments": {"text": "x"}})["result"]
    assert out["isError"] and "session id" in out["content"][0]["text"]
    assert rpc("tools/call", {"name": "lookup", "arguments": {}})["result"]["isError"]


def test_serve_speaks_one_json_message_per_line():
    out = io.StringIO()
    M.serve(io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\nnot json\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": 7, "result": {}} and lines[1]["error"]["code"] == -32700


def test_malformed_requests_get_an_error_not_a_crash():
    bad = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]},
           {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": "x"},
           {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {"corpus": 5}}},
           {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "lookup", "arguments": {"query": "q", "limit": "x"}}},
           {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "session_append", "arguments": {"text": "t", "kind": 3, "session": "s"}}}]
    out = io.StringIO()
    M.serve(io.StringIO("\n".join(json.dumps(m) for m in bad) + "\n" + '{"jsonrpc":"2.0","id":9,"method":"ping"}\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert len(lines) == 6 and lines[-1]["result"] == {}
    for line in lines[:-1]:
        assert "error" in line or line["result"]["isError"] is True, line


def test_server_keeps_utf8_on_a_non_utf8_locale(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "CLAUDE_MEM_SESSION_ID")}
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C", CLP_STORE=str(tmp_path / "store"),
               CLAUDE_MEM_SESSION_ID="11111111-2222-4333-8444-555555555555")
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "caf\u00e9", "arguments": {}}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "session_append", "arguments": {"text": "Decided: caf\u00e9 \u20ac5 \u2713", "kind": "fact"}}},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "session_lookup", "arguments": {"query": "caf\u00e9"}}}]
    data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in calls).encode("utf-8")
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "clp_mcp.py")], input=data, capture_output=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    lines = [json.loads(x) for x in done.stdout.decode("utf-8").splitlines()]
    assert "caf\u00e9" in lines[0]["error"]["message"]
    assert lines[1]["result"]["isError"] is False, lines[1]
    assert "caf\u00e9 \u20ac5 \u2713" in lines[2]["result"]["content"][0]["text"]


def test_server_survives_input_that_is_not_utf8():
    """A byte that isn't UTF-8 ended the server with UnicodeDecodeError. It is now a parse error for that line only."""
    good = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode("utf-8")
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "clp_mcp.py")], input=b"\xff\xfe bad\n" + good + b"\n", capture_output=True, timeout=60)
    lines = [json.loads(x) for x in done.stdout.decode("utf-8").splitlines()]
    assert [x.get("id") for x in lines] == [None, 9] and lines[0]["error"]["code"] == -32700, done.stderr.decode("utf-8", "replace")[-300:]


@pytest.mark.skipif(os.name == "nt", reason="Windows takes the command as text")
def test_a_tool_call_sends_paths_as_file_system_bytes_and_text_as_utf8(monkeypatch):
    """Text goes as UTF-8, which the child reads back exactly. The interpreter and the script go as the file system's
    own bytes: encoded as UTF-8 they would name other files under an older 8-bit locale."""
    seen = []

    def run(argv, **kwargs):
        seen.extend(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(M.subprocess, "run", run)
    monkeypatch.setattr(M.os, "fsencode", lambda path: b"fs:" + str(path).encode("utf-8"))
    assert M.call("lookup", {"query": "caf\u00e9"}) == (False, "ok")
    assert seen[0].startswith(b"fs:") and seen[1] == b"fs:" + str(M.TOOL).encode("utf-8")
    assert seen[2] == b"lookup" and seen[-1] == "caf\u00e9".encode("utf-8")
