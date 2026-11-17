"""clp_mcp: the offline CLP tools over stdio MCP, for Codex. Mirrors the Claude Code plugin minus the paid ask."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

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
