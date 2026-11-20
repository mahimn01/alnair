"""Dispatch layer for EVERY tool, exit codes, disable mode, CLI, stdio MCP protocol, and plugin wiring."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import research_mcp  # noqa: E402
import research_tool  # noqa: E402

from clpresearch import config, tools  # noqa: E402
from tests_data import ATOM, CDX, EFTS, FILING, OA_WORK, PATENT, S2_PAPER, SUBMISSIONS, TICKERS  # noqa: E402
from test_part1_fixes import court_routes, ppubs_routes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT.parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
SENTINEL = "ZXQ-quote-sentinel-9981"


def routes(mock, pdf):
    mock.add("/openalex/works/doi:10.1234/abc", OA_WORK)
    mock.add("/openalex/works/W1", {"id": "https://openalex.org/W1", "referenced_works": ["https://openalex.org/W8"]})
    mock.add("/openalex/works", {"results": [OA_WORK], "meta": {"count": 1}})
    mock.add("/openalex/authors", {"results": [{"id": "https://openalex.org/A1", "display_name": "Ada One"}]})
    mock.add("/semanticscholar/paper/search", {"data": [S2_PAPER]})
    mock.add("/arxiv/query", ATOM, ctype="application/atom+xml")
    mock.add("/sec_efts", EFTS)
    mock.add("/sec_www/files/company_tickers.json", TICKERS)
    mock.add("/sec_data/submissions/CIK0000320193.json", SUBMISSIONS)
    mock.add("/sec_www/Archives/edgar/data/1/a/f.htm", FILING, ctype="text/html")
    mock.add("/sec_data/api/xbrl/companyconcept", {"entityName": "Apple", "label": "Revenues", "units": {"USD": [{"end": "2023-09-30", "val": 1, "accn": "x", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "d"}]}})
    mock.add("/robots.txt", b"", status=404)
    mock.add("/gpatents/patent/US6285999B1/en", PATENT, ctype="text/html")
    ppubs_routes(mock)
    court_routes(mock)
    mock.add("/courtlistener", b"{}", status=401)
    mock.add("/wayback_cdx/cdx", CDX)
    mock.add("/wayback/2002id_/http://example.com/", "<html><body>archived</body></html>", ctype="text/html")
    mock.add("/page.html", "<html><title>P</title><body><p>page</p></body></html>", ctype="text/html")


def args_for(mock):
    return {
        "scholar_search": {"query": "deep things", "source": "openalex"}, "scholar_work": {"id": "10.1234/abc"}, "scholar_author": {"name": "Ada One"},
        "scholar_citations": {"id": "W1", "direction": "cited_by"}, "scholar_fulltext": {"id": f"{mock.base}/p.pdf"},
        "sec_search": {"query": "x"}, "sec_filings": {"company": "AAPL"}, "sec_get": {"url": f"{mock.base}/sec_www/Archives/edgar/data/1/a/f.htm"},
        "sec_facts": {"company": "320193", "concept": "Revenues"}, "patent_get": {"number": "US6285999B1"}, "patent_search": {"query": "x"},
        "court_search": {"query": "miranda"}, "court_opinion": {"citation": "384 U.S. 436"}, "wayback_cdx": {"url": "example.com"},
        "wayback_fetch": {"url": "http://example.com/", "timestamp": "2002"}, "github_search": {"kind": "repos", "query": "r"},
        "github_get": {"repo": "o/r", "path": "a.py"}, "github_issue": {"repo": "o/r", "number": 7}, "research_fetch": {"url": f"{mock.base}/page.html"},
    }


EXPECT_UNAVAILABLE: set[str] = set()   # 2026-11-11: patent_search, court_search and court_opinion now have keyless routes


def test_every_tool_is_exercised_through_the_dispatcher(ctx, mock, pdf_bytes, fake_gh):
    import base64
    routes(mock, pdf_bytes)
    mock.add("/p.pdf", pdf_bytes, ctype="application/pdf")
    fake_gh.reply("search/repositories", {"total_count": 1, "items": [{"full_name": "o/r", "stargazers_count": 1, "pushed_at": "2024-01-01T00:00:00Z", "description": "d", "html_url": "u"}]})
    fake_gh.reply("repos/o/r/contents/a.py", {"type": "file", "content": base64.b64encode(f"print('{SENTINEL}')\n".encode()).decode(), "url": "x?ref=main"})
    fake_gh.reply("repos/o/r/commits/main", {"sha": "d" * 40})
    fake_gh.reply("repos/o/r/issues/7", {"title": "T", "state": "open", "user": {"login": "u"}, "labels": [], "body": "b", "comments": 0, "html_url": "https://github.com/o/r/issues/7"})
    seen = set()
    for name, a in args_for(mock).items():
        code, out = tools.run(name, dict(a), ctx)
        want = 3 if name in EXPECT_UNAVAILABLE else 0
        assert code == want, f"{name}: exit {code}: {out[:300]}"
        assert (out.startswith("UNAVAILABLE[") if want == 3 else True), name
        seen.add(name)
    # memory tools (need the sources saved above)
    code, out = tools.run("research_lookup", {"query": SENTINEL}, ctx)
    assert code == 0 and ".txt:" in out
    f = re.search(r"(github-file-[\w.\-]+\.txt):", out).group(1)
    code, out = tools.run("research_read", {"address": f"{f}:1-9"}, ctx)
    assert code == 0 and SENTINEL in out
    addr = re.search(r"\[(\S+\.txt:\d+-\d+#[0-9a-f]{8})\]", out).group(1)
    quote = "print('" + SENTINEL + "')"
    claims = json.dumps([{"text": SENTINEL, "citations": [{"ref": addr, "quote": quote}]}])
    ans = f"ANSWER: {SENTINEL}\n<claims>{claims}</claims>"
    code, out = tools.run("research_verify", {"question": "what is printed?", "answer": ans}, ctx)
    assert code == 0 and out.startswith("PASS"), out
    code, out = tools.run("research_verify", {"question": "q", "answer": ans.replace(SENTINEL, "ZXQ-forged-0000")}, ctx)
    assert code == 1
    assert tools.run("research_sources", {}, ctx)[0] == 0 and tools.run("research_status", {}, ctx)[0] == 0
    seen |= {"research_lookup", "research_read", "research_verify", "research_sources", "research_status"}
    # New tools are exercised through this same dispatcher, not excluded from coverage.
    assert tools.run('research_find', {'source':f,'query':SENTINEL},ctx)[0] == 0
    mock.add('/crossref/works', {'message':{'items':[]}})
    assert tools.run('scholar_discover', {'query':'CEO misconduct','source':'crossref'},ctx)[0] == 0
    mock.add('/yahoo/v8/finance/chart/AAPL', {'chart':{'result':[{'meta':{'currency':'USD'},'timestamp':[1700000000],'indicators':{'quote':[{'close':[103]}]}}]}})
    assert tools.run('market_history', {'symbol':'AAPL'},ctx)[0] == 0
    mock.add('/cboe/AAPL.json', {'data':{'current_price':100,'options':[]}})
    mock.add('/yahoo/v7/finance/options/AAPL', '',status=401)
    mock.add('/yahoo/v10/finance/quoteSummary/AAPL', '',status=401)
    from datetime import date,timedelta
    assert tools.run('market_options', {'symbol':'AAPL','earnings_date':(date.today()+timedelta(days=10)).isoformat()},ctx)[0] == 3
    assert tools.run('market_earnings', {'symbol':'AAPL'},ctx)[0] == 3
    seen |= {'research_find','scholar_discover','market_history','market_options','market_earnings'}
    # The revamped flow, through the same dispatcher (its behaviour is tested in test_revamp.py).
    code, out = tools.run('research_get', {'items': [{'spec': 'patent:US6285999B1', 'focus': 'priority_date'}], 'inquiry': 'dispatch'}, ctx)
    assert code == 0 and out.startswith('research_get: 1 ') and 'tier B' in out
    src = re.search(r' src ([0-9a-f]{12}) ', out).group(1)
    quote = re.search(r' \| (priority_date.*)', out).group(1).strip()
    assert tools.run('research_search', {'query': 'deep things', 'domains': ['papers']}, ctx)[0] == 0
    assert tools.run('research_evidence', {'query': 'priority_date', 'src': src}, ctx)[0] == 0
    code, out = tools.run('research_claim', {'inquiry': 'dispatch', 'claims': [{'id': 'p', 'text': 'The priority date is ' + re.search(r'\d{4}-\d{2}-\d{2}', quote).group(0), 'cite': [{'src': src, 'quote': quote}]}]}, ctx)
    assert code == 0 and '"status":"verified"' in out, out
    assert tools.run('research_report', {'inquiry': 'dispatch', 'answer': 'UNANSWERABLE'}, ctx)[0] == 0
    seen |= {'research_get', 'research_search', 'research_evidence', 'research_claim', 'research_report'}
    assert seen == set(tools.BY_NAME), set(tools.BY_NAME) - seen


def test_status_live_probes_each_provider_with_mock(ctx, mock, fake_gh, pdf_bytes, monkeypatch):
    routes(mock, pdf_bytes)
    fake_gh.reply("rate_limit", {"resources": {"search": {"remaining": 29}}})
    out = tools.status_report(ctx, live=True)
    for p in ("openalex", "semanticscholar", "arxiv", "sec", "wayback", "patents", "ppubs", "caselaw", "courtlistener", "github", "web"):
        assert f"- {p}:" in out
    assert re.search(r"ppubs[^\n]*\n[^\n]*\n\s+live probe: live", out) and re.search(r"caselaw[^\n]*\n[^\n]*\n\s+live probe: live", out)
    assert "not configured (no COURTLISTENER_TOKEN)" in out and not mock.hits("/courtlistener") and "search remaining 29" in out
    # With a token that the service refuses, the refusal is reported and remembered; the token itself is never shown.
    monkeypatch.setenv("COURTLISTENER_TOKEN", "tok-should-not-appear")
    out = tools.status_report(ctx, live=True)
    assert re.search(r"courtlistener[^\n]*\n[^\n]*\n\s+live probe: NOT LIVE", out) and "tok-should-not-appear" not in out
    assert ctx.cap_read()["courtlistener"]["status"] == "blocked"


def test_status_deep_detects_tampering(ctx):
    ref = ctx.store.put("web", "https://e.org/a", "A", "text here\n")
    assert "all saved sources match" in tools.status_report(ctx, deep=True)
    p = ctx.store.docs / ref.parts[0]
    p.write_text(p.read_text().replace("text", "TEXT"))
    assert "PROBLEM" in tools.status_report(ctx, deep=True)


# ---- validation, errors, disable ---------------------------------------------------------------------------
def test_run_validation_messages(ctx):
    assert tools.run("nope", {}, ctx)[0] == 2
    assert "missing required" in tools.run("scholar_work", {}, ctx)[1]
    assert "unknown argument" in tools.run("scholar_work", {"id": "W1", "bogus": 1}, ctx)[1]
    assert "must be an integer" in tools.run("scholar_search", {"query": "xx", "limit": "many"}, ctx)[1]
    assert "must be one of" in tools.run("scholar_search", {"query": "xx", "source": "google"}, ctx)[1]
    assert "too long" in tools.run("scholar_search", {"query": "x" * 400}, ctx)[1]
    code, out = tools.run("scholar_work", {"id": "not an id"}, ctx)
    assert code == 2 and out.startswith("ERROR:")


def test_envelope_fields_are_ignored(ctx):
    code, out = tools.run("research_sources", {"tool": "mcp__research__research_sources", "tool_use_id": "toolu_1"}, ctx)
    assert code == 0


def test_unexpected_exception_is_reported_without_a_traceback(ctx, monkeypatch):
    t = tools.BY_NAME["research_sources"]
    monkeypatch.setattr(t, "handler", lambda c, a: (_ for _ in ()).throw(ValueError("boom " + "x" * 500)))
    code, out = tools.run("research_sources", {}, ctx)
    assert code == 2 and "unexpected ValueError" in out and "Traceback" not in out and len(out) < 400


def test_output_is_capped(ctx, monkeypatch):
    t = tools.BY_NAME["research_sources"]
    monkeypatch.setattr(t, "handler", lambda c, a: "y" * 50000)
    code, out = tools.run("research_sources", {}, ctx)
    assert len(out) < tools.MAX_OUT + 200 and "output cut" in out


def test_disabled_mode_blocks_every_tool_but_status(ctx, monkeypatch):
    monkeypatch.setenv("CLAUDE_RESEARCH_DISABLE", "1")
    for name in tools.BY_NAME:
        code, out = tools.run(name, {}, ctx)
        if name == "research_status":
            assert code == 0 and "enabled: False" in out
        else:
            assert code == 4 and out.startswith("DISABLED"), name


def test_disabled_does_not_touch_network_or_store(ctx, mock, monkeypatch):
    monkeypatch.setenv("CLAUDE_RESEARCH_DISABLE", "true")
    tools.run("scholar_search", {"query": "abc"}, ctx)
    assert mock.log == [] and not ctx.store.docs.exists()


# ---- CLI ---------------------------------------------------------------------------------------------------------
def cli(args, stdin="", env=None):
    e = {**os.environ, "RESEARCH_HOME": os.environ["RESEARCH_HOME"], **(env or {})}
    p = subprocess.run([PY, "-I", str(ROOT / "bin" / "research_tool.py"), *args], input=stdin, capture_output=True, text=True, env=e, timeout=60)
    return p.returncode, p.stdout


def test_cli_list_and_help():
    code, out = cli(["list"])
    assert code == 0 and "scholar_search" in out and out.count("\n") >= len(tools.TOOLS) - 1
    assert cli(["--help"])[0] == 0


def test_cli_call_json_roundtrip_and_errors():
    code, out = cli(["call", "research_sources"], "{}")
    assert code == 0 and "No sources saved" in out
    assert cli(["call", "research_sources"], "not json")[0] == 2
    assert cli(["call", "research_sources"], "[1,2]")[0] == 2
    assert cli(["call", "scholar_work"], "{}")[0] == 2


def test_cli_disabled_exit_code_4():
    assert cli(["call", "scholar_search"], '{"query":"abc"}', env={"CLAUDE_RESEARCH_DISABLE": "1"})[0] == 4


def test_cli_friendly_parsing():
    assert research_tool.parse_cli(["scholar_search", "attention", "--source", "arxiv", "--limit", "3"]) == ("scholar_search", {"query": "attention", "source": "arxiv", "limit": "3"})
    assert research_tool.parse_cli(["status", "--live"]) == ("research_status", {"live": True})
    assert research_tool.parse_cli(["status", "--deep", "false"]) == ("research_status", {"deep": False})
    with pytest.raises(SystemExit):
        research_tool.parse_cli(["scholar_search", "q", "--nope", "1"])
    with pytest.raises(SystemExit):
        research_tool.parse_cli(["nope"])
    with pytest.raises(SystemExit):
        research_tool.parse_cli(["sec_filings", "a", "b"])


def test_cli_status_offline_runs_without_network():
    code, out = cli(["status"])
    assert code == 0 and "providers" in out and "pdftotext:" in out


# ---- stdio MCP protocol ----------------------------------------------------------------------------------------------
def test_mcp_handle_unit():
    init = research_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert init["result"]["protocolVersion"] == "2024-11-05" and init["result"]["serverInfo"]["name"] == "clp-research" and "tools" in init["result"]["capabilities"]
    assert research_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert research_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}
    listed = research_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in listed] == [t.name for t in tools.TOOLS] and all(t["inputSchema"]["type"] == "object" for t in listed)
    assert research_mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601
    assert research_mcp.handle({"id": 5, "method": "ping"})["error"]["code"] == -32600
    bad = research_mcp.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "zzz"}})
    assert bad["error"]["code"] == -32602
    assert research_mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "research_sources", "arguments": "x"}})["error"]["code"] == -32602


def test_mcp_call_maps_errors_and_results(ctx):
    ok = research_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "research_sources", "arguments": {}}})["result"]
    assert ok["isError"] is False and ok["content"][0]["type"] == "text"
    err = research_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "scholar_work", "arguments": {}}})["result"]
    assert err["isError"] is True and "missing required" in err["content"][0]["text"]


def test_mcp_stdio_session_end_to_end():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "research_sources", "arguments": {}}}]
    stdin = "\n".join(json.dumps(m) for m in msgs) + "\n\nnot json\n[1]\n"
    p = subprocess.run([PY, "-I", str(ROOT / "bin" / "research_mcp.py")], input=stdin, capture_output=True, text=True, timeout=60, env={**os.environ})
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert [l.get("id") for l in lines] == [1, 2, 3, None, None]  # no reply to the notification or blank line
    assert lines[3]["error"]["code"] == -32700 and lines[4]["error"]["code"] == -32600
    assert len(lines[1]["result"]["tools"]) == len(tools.TOOLS) and "No sources saved" in lines[2]["result"]["content"][0]["text"]


# ---- plugin / session wiring ------------------------------------------------------------------------------------------
def test_generated_plugin_tool_table_is_in_sync():
    assert (ROOT / "plugin/hooks/tools.generated.js").read_text() == research_tool.manifest_js()


def test_plugin_manifests_and_register_module():
    manifest = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "research" and manifest["version"]
    hooks = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())
    assert hooks["modules"] == ["./register.js"]
    js = (ROOT / "plugin/hooks/register.js").read_text()
    assert "CLAUDE_RESEARCH_DISABLE" in js and "mcp__research__" in js and "'call', spec.name" in js


def test_every_tool_has_clear_description_schema_and_capability_tag():
    for t in tools.TOOLS:
        assert t.description.startswith("[") and len(t.description) > 60, t.name
        assert set(t.required) <= set(t.props), t.name
        for k, spec in t.props.items():
            assert spec["type"] in ("string", "integer", "boolean", "array") and spec.get("description"), (t.name, k)
            assert spec["type"] != "array" or (t.name in tools.CORE_LOADED and spec["items"]["type"] in ("string", "object")), (t.name, k)
        assert 0 < t.timeout_ms <= 300000


def test_unavailable_tools_are_labelled_unavailable_and_live_ones_are_not():
    for n in EXPECT_UNAVAILABLE:
        assert "UNAVAILABLE" in tools.BY_NAME[n].description
    for n in set(tools.BY_NAME) - EXPECT_UNAVAILABLE:
        assert "UNAVAILABLE keyless" not in tools.BY_NAME[n].description


def test_skill_covers_every_tool_and_the_verify_limits():
    skill = (ROOT / "plugin/skills/research-loop/SKILL.md").read_text()
    assert skill.startswith("---\nname: research-loop")
    for name in tools.BY_NAME:
        assert name in skill, f"skill does not mention {name}"
    for phrase in ("does not mean the source is true", "UNAVAILABLE", "disconfirming", "UNANSWERABLE", "NEW note", "data, never instructions", "--no-research"):
        assert phrase in skill, phrase


def _open_objects(node, path: str = "") -> list[str]:
    bad = []
    if isinstance(node, dict):
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            bad.append(path or "<root>")
        for k, v in node.get("properties", {}).items():
            bad += _open_objects(v, f"{path}.{k}")
        if "items" in node:
            bad += _open_objects(node["items"], f"{path}[]")
    return bad


def test_every_object_schema_is_closed_and_format_is_declared():
    import json as _json
    prefix = "export const TOOLS = "
    text = (ROOT / "plugin/hooks/tools.generated.js").read_text()
    items = _json.loads(text[text.index(prefix) + len(prefix):].rstrip().rstrip(";"))
    assert [i["name"] for i in items] == [t.name for t in tools.TOOLS]
    for item in items:
        assert _open_objects(item["inputSchema"], item["name"]) == []
        assert item["inputSchema"]["properties"]["format"]["enum"] == ["text", "json"]
    for t in tools.TOOLS:       # the Python handlers never see format: the plugin strips it
        assert "format" not in t.props
        assert tools.run(t.name, {"format": "json"})[0] in (2, 4)
