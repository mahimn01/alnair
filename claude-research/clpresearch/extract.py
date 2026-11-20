"""Turn fetched bytes into plain text we can store and quote: HTML, JSON, PDF (via pdftotext)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser

from . import config
from .netsafe import NetError

_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "object", "head"}
_BLOCK = {"p", "div", "br", "tr", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
          "table", "pre", "blockquote", "header", "footer", "hr", "dd", "dt", "dl", "form", "main", "nav", "aside"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
_HIDDEN = re.compile(r"display\s*:\s*none", re.I)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag in _BLOCK and not self._skip:
                self.out.append("\n")
            return
        hidden = tag in _SKIP or bool(_HIDDEN.search(dict(attrs).get("style") or "")) or "hidden" in dict(attrs)
        self._stack.append(hidden)
        if hidden:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if self._skip:
            return
        if tag in ("td", "th"):
            self.out.append(" | ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if tag == "title":
            self._in_title = False
        if self._stack:
            if self._stack.pop():
                self._skip -= 1
        if not self._skip and tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self._skip:
            self.out.append(data)


def wrap_lines(text: str, width: int = config.LINE_WRAP) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        while len(line) > width:
            cut = line.rfind(" ", width // 2, width)
            cut = cut if cut > 0 else width
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").replace("\xa0", " ")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln in ("|", "| |"):
            continue
        blank = blank + 1 if not ln else 0
        if blank <= 1:
            out.append(ln)
    return wrap_lines("\n".join(out).strip() + "\n")


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text). Scripts, styles and hidden elements are dropped; table cells are joined with ' | '."""
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - malformed HTML must degrade, not crash
        raise NetError("http", f"could not parse HTML ({exc.__class__.__name__})") from None
    text = "".join(parser.out)
    text = re.sub(r"[ \t]*\|[ \t]*(\n)", r"\1", text)
    return " ".join(parser.title.split()), clean(text)


def json_to_text(data) -> str:
    return clean(json.dumps(data, indent=1, ensure_ascii=False))


def pdf_to_text(pdf: bytes) -> str:
    """Text of a PDF through the system `pdftotext` (bounded time, pages and output). Raises NetError on any problem."""
    if not pdf.startswith(b"%PDF-"):
        raise NetError("http", "not a PDF (missing %PDF- header)")
    if len(pdf) > config.MAX_PDF:
        raise NetError("too_large", f"PDF exceeds {config.MAX_PDF} bytes")
    exe = shutil.which("pdftotext")
    if not exe:
        raise NetError("unavailable", "pdftotext is not installed (brew install poppler)")
    with tempfile.TemporaryDirectory(prefix="clpr-pdf-") as tmp:
        path = os.path.join(tmp, "in.pdf")
        with open(path, "wb") as fh:
            fh.write(pdf)
        argv = [exe, "-q", "-enc", "UTF-8", "-l", str(config.PDF_MAX_PAGES), path, "-"]
        env = {"PATH": os.environ.get("PATH", os.defpath if os.name == "nt" else "/usr/bin:/bin"), "LANG": "en_US.UTF-8",
               **{k: os.environ[k] for k in config.WINDOWS_ENV if k in os.environ}}
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=config.PDFTOTEXT_TIMEOUT, env=env, check=False)
        except subprocess.TimeoutExpired:
            raise NetError("timeout", f"pdftotext exceeded {config.PDFTOTEXT_TIMEOUT}s") from None
        if proc.returncode != 0:
            raise NetError("http", f"pdftotext failed (exit {proc.returncode}): {proc.stderr[:200].decode('utf-8', 'replace')}")
    raw = proc.stdout[: config.MAX_TEXT + 1].decode("utf-8", errors="replace")
    pages = raw.split("\f")
    body = "".join(f"\n[page {i}]\n{p}" for i, p in enumerate(pages, 1) if p.strip())
    if not body.strip():
        raise NetError("http", "PDF has no extractable text (scanned image?); OCR is not supported")
    return clean(body)


def looks_like(body: bytes, ctype: str) -> str:
    """'pdf' | 'html' | 'json' | 'text' | 'binary'"""
    ctype = (ctype or "").lower()
    if body.startswith(b"%PDF-") or "pdf" in ctype:
        return "pdf"
    head = body[:2048].lstrip().lower()
    if "html" in ctype or head.startswith((b"<!doctype html", b"<html")):
        return "html"
    if "json" in ctype:
        return "json"
    if "xml" in ctype or ctype.startswith("text/") or not ctype:
        return "text" if b"\x00" not in body[:4096] else "binary"
    return "binary"


def article_to_text(html: str) -> tuple[str, str]:
    """Extract article content locally, without an LLM rewrite. Raw bytes remain in Store.raw."""
    title, fallback = html_to_text(html)
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=True,
                                   favor_precision=True, deduplicate=True)
        if text and text.strip():
            return title, clean(text)
    except ImportError:
        pass
    # Deterministic fallback for minimal installations; never changes quoted words.
    main = re.search(r'<(article|main)\b[^>]*>(.*?)</\1\s*>', html, re.I | re.S)
    if main:
        return title, html_to_text(main.group(2))[1]
    stripped = re.sub(r'<(nav|footer|aside)\b[^>]*>.*?</\1\s*>', '', html, flags=re.I | re.S)
    return title, html_to_text(stripped)[1]
