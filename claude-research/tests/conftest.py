"""Offline test harness: a local mock HTTP server, a fake gh, no real network, a throwaway research home."""

from __future__ import annotations

import gzip
import ipaddress
import json
import os
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ["RESEARCH_MAILTO"] = "tester@example.com"  # before clpresearch.config is imported

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clpresearch import config, http, netsafe  # noqa: E402
from clpresearch.common import Ctx  # noqa: E402
from clpresearch.store import Store  # noqa: E402

VENV_PY = REPO / ".venv" / "bin" / "python"


class Mock:
    """Routes: path prefix -> list of responses consumed in order (the last one repeats)."""

    def __init__(self):
        self.routes: dict[str, list[dict]] = {}
        self.log: list[dict] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, prefix: str, body=b"", status=200, ctype="application/json", headers=None, delay=0.0, gz=False, declared_length=None, cut=None):
        """declared_length: send this Content-Length instead of the real one (a wrong header).
        cut: send only the first `cut` bytes of the body (a download that really was cut off)."""
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.routes.setdefault(prefix, []).append({"body": body, "status": status, "ctype": ctype, "headers": headers or {}, "delay": delay, "gz": gz,
                                                   "declared_length": declared_length, "cut": cut})

    def hits(self, prefix: str = "") -> list[dict]:
        return [r for r in self.log if r["path"].startswith(prefix)]

    def _handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.do_GET()

            def do_GET(self):
                sent = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else b""
                mock.log.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "method": self.command, "body": sent})
                best = max((p for p in mock.routes if self.path.startswith(p)), key=len, default=None)
                if best is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                seq = mock.routes[best]
                n = sum(1 for r in mock.log if r["path"].startswith(best))
                resp = seq[min(n - 1, len(seq) - 1)]
                if resp["delay"]:
                    time.sleep(resp["delay"])
                body = gzip.compress(resp["body"]) if resp["gz"] else resp["body"]
                try:
                    self.send_response(resp["status"])
                    self.send_header("Content-Type", resp["ctype"])
                    self.send_header("Content-Length", str(resp["declared_length"] if resp["declared_length"] is not None else len(body)))
                    if resp["gz"]:
                        self.send_header("Content-Encoding", "gzip")
                    for k, v in resp["headers"].items():
                        for one in (v if isinstance(v, list) else [v]):   # a list sends the header several times (Set-Cookie)
                            self.send_header(k, one)
                    self.end_headers()
                    self.wfile.write(body if resp["cut"] is None else body[:resp["cut"]])
                    if resp["declared_length"] is not None or resp["cut"] is not None:
                        self.close_connection = True   # the declared length is wrong: end the reply by closing
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return H


@pytest.fixture(scope="session")
def mock_server():
    m = Mock()
    yield m
    m.server.shutdown()


@pytest.fixture
def mock(mock_server):
    mock_server.routes.clear()
    mock_server.log.clear()
    return mock_server


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch, mock_server):
    """Fresh research home, instant sleeps, no rate-limit gaps, only the mock host reachable."""
    home = tmp_path / "rhome"
    monkeypatch.setenv("RESEARCH_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CLP_PYTHON", str(VENV_PY))
    monkeypatch.setenv("CLAUDE_CLP_ROOT", str(REPO / "claude-clp"))
    monkeypatch.delenv("CLAUDE_RESEARCH_DISABLE", raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(http, "SLEEP", lambda s: sleeps.append(s))
    monkeypatch.setattr(config, "MIN_INTERVAL", {k: 0 for k in config.MIN_INTERVAL})
    monkeypatch.setattr(netsafe.POLICY, "exempt", {f"127.0.0.1:{mock_server.port}"})
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *a, **k):
        try:  # IP literals never touch DNS
            ipaddress.ip_address(str(host).strip("[]"))
            return real_getaddrinfo(host, *a, **k)
        except ValueError:
            pass
        if host in ("127.0.0.1", "localhost") or host.startswith("rebind."):
            return real_getaddrinfo(host if host != "rebind.test" else "127.0.0.1", *a, **k)
        raise socket.gaierror(f"offline test: refusing to resolve {host}")

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    bases = {k: f"{mock_server.base}/{k}" for k in config.BASES}
    bases["arxiv"] = f"{mock_server.base}/arxiv/query"
    bases["sec_efts"] = f"{mock_server.base}/sec_efts/search-index"
    bases["wayback_cdx"] = f"{mock_server.base}/wayback_cdx/cdx"
    monkeypatch.setattr(config, "BASES", bases)
    monkeypatch.setattr(config, "SEC_SUFFIXES", ("127.0.0.1",))
    monkeypatch.setattr(config, "WAYBACK_SUFFIXES", ("127.0.0.1",))
    monkeypatch.setattr(config, "CASELAW_SUFFIXES", ("127.0.0.1",))
    for name in ("COURTLISTENER_TOKEN", "COURTLISTENER_API_TOKEN", "PATENTSVIEW_API_KEY", "USPTO_ODP_API_KEY", "RESEARCH_MIN_QUOTE", "CLP_MIN_QUOTE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "HTTP_TIMEOUT", 3.0)
    return sleeps


@pytest.fixture
def sleeps(sandbox):
    return sandbox


@pytest.fixture
def ctx(tmp_path) -> Ctx:
    return Ctx(Store(tmp_path / "rhome"))


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A fake `gh` on PATH. Script reads canned replies from $FAKE_GH_DIR (keyed by the api path) and records its env names."""
    d = tmp_path / "ghbin"
    d.mkdir()
    state = tmp_path / "ghstate"
    state.mkdir()
    script = d / "gh"
    script.write_text(f"""#!{sys.executable}
import json, os, sys, pathlib
st = pathlib.Path({str(state)!r})
(st / "env.json").write_text(json.dumps(sorted(os.environ)))
args = sys.argv[1:]
with open(st / "calls.jsonl", "a") as f:
    f.write(json.dumps(args) + "\\n")
i = args.index("GET") + 1
if args[i] == "-H":
    i += 2
route = args[i]
key = route.replace("/", "_").replace("?", "_")
replies = json.loads((st / "replies.json").read_text()) if (st / "replies.json").exists() else {{}}
n = len([l for l in (st / "calls.jsonl").read_text().splitlines() if route in l])
r = replies.get(route)
if isinstance(r, dict) and "__seq__" in r:
    r = r["__seq__"][min(n - 1, len(r["__seq__"]) - 1)]
if r is None:
    sys.stderr.write("gh: Not Found (HTTP 404)"); sys.exit(1)
if isinstance(r, dict) and "error" in r:
    sys.stderr.write(r["error"]); sys.exit(1)
print(json.dumps(r))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{d}:{os.environ['PATH']}")

    class G:
        dir = state

        @staticmethod
        def reply(route, value):
            p = state / "replies.json"
            data = json.loads(p.read_text()) if p.exists() else {}
            data[route] = value
            p.write_text(json.dumps(data))

        @staticmethod
        def calls():
            p = state / "calls.jsonl"
            return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

        @staticmethod
        def env_names():
            return json.loads((state / "env.json").read_text())

    return G


MINI_PDF = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            b"4 0 obj<</Length 66>>stream\nBT /F1 12 Tf 20 100 Td (Photon flux was 4213 units in 1999.) Tj ET\nendstream endobj\n"
            b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R/Size 6>>\n%%EOF\n")


@pytest.fixture
def pdf_bytes() -> bytes:
    return MINI_PDF
