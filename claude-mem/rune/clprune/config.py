"""Rune config for Alnair: aliases, key bindings, stock Rune colors, quiet sounds; backed-up conservative merge.

Writes only under the Rune data dir (default ~/.rune) and ~/.local/bin symlinks, and only from `install`/`config --apply`.
Never touches ~/.claude*, the vault or any network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import core

MARKER = "# clp-rune managed config v1: regenerate with `clp-rune config --apply`; edit freely, a changed file is never overwritten"
STAR_BEGIN = "# >>> clp-rune managed block (regenerate with `clp-rune config --apply`)"
STAR_END = "# <<< clp-rune managed block"
TARGET = core.RUNE_DIR / "bin" / "clp-rune"
LAUNCHERS = ["clp-rune", "clp-rune-claude", "clp-rune-resume", "clp-rune-dash", "clp-rune-pending"]
B = "$HOME/.local/bin"

# Commands used by the aliases below. Each one was read in the Rune docs or the installed binary's help text.
RUNE_COMMANDS = {"windowcloseall", "windowdefaultsplit", "terminalnewtab", "windownew", "windowresize", "windowfocus",
                 "edit", "echo", "programstatus", "cheatsheet"}

# ---- standard editor preset (macOS) as shipped in the first-run config of rune v1.3.4 ----
def preset_bindings() -> dict[str, str | list[str]]:
    b: dict[str, str | list[str]] = {
        "<meta-s>": "write", "<meta-o>": "searchfile", "<shift-meta-s>": "writeall", "<meta-q>": "quit",
        "<meta-w>": "tabclose", "<meta-f2>": "locationtoggle bookmark", "<f2>": "jumptolocation next bookmark",
        "<shift-f2>": "jumptolocation previous bookmark", "<alt-f2>": "locationhighlight bookmark",
        "<shift-meta-f2>": "locationdeleteall bookmark", "<shift-meta-f>": "searchtext",
        "<meta-g>": "jumptolocation next search", "<shift-meta-g>": "jumptolocation prev search",
        "<alt-c>": "lsp declaration", "<alt-y>": "lsp type-definition", "<alt-g>": "lsp signature-help",
        "<alt-.>": "cursorhistory next", "<alt-,>": "cursorhistory prev",
        "<alt-i>": "windowfocus up", "<alt-j>": "windowfocus left", "<alt-k>": "windowfocus down", "<alt-l>": "windowfocus right",
        "<alt-shift-i>": "windowmove up", "<alt-shift-j>": "windowmove left", "<alt-shift-k>": "windowmove down",
        "<alt-shift-l>": "windowmove right",
        "<alt-meta-i>": "windowresize increase height", "<alt-meta-j>": "windowresize decrease width",
        "<alt-meta-k>": "windowresize decrease height", "<alt-meta-l>": "windowresize increase width",
        "<alt-n>": "windownew", "<alt-enter>": "terminalneworsplit", "<alt-q>": "windowclose",
        "<alt-shift-q>": "windowcloseall", "<alt-m>": "windowtogglemaximize",
        "<alt-shift-enter>": "echo {prompt}windowconverttab<space>", "<alt-w>": "tabclose",
        "<alt-[>": "tabprevious", "<alt-]>": "tabnext", "<alt-shift-[>": "tabmove left", "<alt-shift-]>": "tabmove right",
        "<alt-h>": "windowdefaultsplit h", "<alt-v>": "windowdefaultsplit v",
        "<ctrl-meta-i>": "gitprevchange", "<ctrl-meta-k>": "gitnextchange",
        "<ctrl-meta-j>": "lspprevdiagnostic", "<ctrl-meta-l>": "lspnextdiagnostic",
        "<alt-t>": "lsp hover", "<alt-p>": "lsp implementation", "<alt-e>": "lsp diagnostics", "<alt-r>": "lsp references",
        "<alt-d>": "lsp definition", "<alt-b>": "lsp format", "<ctrl-space>": "lsp complete",
        "<shift-meta-r>": "history", "<shift-meta-e>": "fexplorer", "<meta-,>": "config", "<alt-/>": "cheatsheet",
        "<meta-=>": "guifontsize increase", "<meta-->": "guifontsize decrease", "<meta-t>": "tabnew",
        "<meta-c>": "clipboardcopy", "<meta-v>": "clipboardpaste", "<alt-`>": "tabsearch",
        "<shift-meta-backspace>": "windowresize reset",
        "<shift-meta-+>": ["windowresize max width", "windowresize max height"],
        "<shift-meta-->": ["windowresize min width", "windowresize min height"],
        "<meta-enter>": "terminalneworsplit", "<shift-meta-enter>": "!", "<meta-`>": "workspacesearch",
        "<ctrl-`>": "programstatus",
    }
    for n in range(1, 10):
        b[f"<alt-{n}>"] = f"tabfocus {n}"
        b[f"<alt-shift-{n}>"] = f"tabmove {n}"
        b[f"<meta-{n}>"] = f"workspacefocus {n}"
        b[f"<shift-meta-{n}>"] = f"workspacemove {n}"
    return b


def aliases(rescore: bool = False) -> dict[str, str | list[str]]:
    dash_layout = ["windowdefaultsplit v", "windownew right", f"terminalnewtab {B}/clp-rune-dash"] + \
                  ["windowresize decrease width"] * 4 + ["windowfocus left"]
    a: dict[str, str | list[str]] = {
        # layout: ADDS Claude (left) and dashboard (right) panes; never closes anything. Repeating it adds more panes.
        "clp": ["windowdefaultsplit v", f"terminalnewtab {B}/clp-rune-claude"] + dash_layout[1:],
        "clpdash": dash_layout,
        "clpnew": f"terminalnewtab {B}/clp-rune-claude",
        "clpresume": f"terminalnewtab {B}/clp-rune-resume",
        # prompt aliases: open the tab first, then stage the typed words (the tab waits for them)
        "clpask": [f"terminalnewtab {B}/clp-rune-pending", f"!! {B}/clp-rune stage ask"],
        "clpresearch": [f"terminalnewtab {B}/clp-rune-pending", f"!! {B}/clp-rune stage research-loop"],
        "clplookup": f"! {B}/clp-rune lookup",
        "clpread": f"! {B}/clp-rune read",
        "clpverify": f"! {B}/clp-rune verify",
        "clpstatus": f"! {B}/clp-rune status",
        "clprefresh": f"! {B}/clp-rune refresh",
        "clpresstatus": f"! {B}/clp-rune research-status",
        "clpdoctor": f"! {B}/clp-rune doctor",
        "clptests": f"! {B}/clp-rune tests",
        "clpbench": f"! {B}/clp-rune bench",
        "clpkeys": f"! {B}/clp-rune keys",
        "clpnote": [f"!! CLPNOTE=$({B}/clp-rune note-latest)", "edit $CLPNOTE"],
    }
    if rescore:
        a["clprescore"] = f"! {B}/clp-rune rescore"
    return a


# Control+Command chords: a terminal forwards ctrl/shift combos but leaves alt/meta ones to Rune.
# Avoided: ctrl-cmd-Q (lock), -F (full screen), -D (look up), -Space (emoji) and the preset's I/J/K/L.
CLP_KEYS: dict[str, str] = {
    "<ctrl-meta-c>": "clp", "<ctrl-meta-z>": "clpdash", "<ctrl-meta-n>": "clpnew", "<ctrl-meta-r>": "clpresume",
    "<ctrl-meta-s>": "clpstatus", "<ctrl-meta-u>": "clprefresh", "<ctrl-meta-e>": "clpresstatus",
    "<ctrl-meta-x>": "clpdoctor", "<ctrl-meta-o>": "clpnote", "<ctrl-meta-t>": "clptests", "<ctrl-meta-b>": "clpbench",
    "<ctrl-meta-h>": "clpkeys",
    "<ctrl-meta-y>": "echo {prompt}clplookup<space>", "<ctrl-meta-g>": "echo {prompt}clpread<space>",
    "<ctrl-meta-v>": "echo {prompt}clpverify<space>", "<ctrl-meta-a>": "echo {prompt}clpresearch<space>",
    "<ctrl-meta-p>": "echo {prompt}clpask<space>",
}

def overlay(rescore: bool = False) -> dict:
    """Everything Alnair sets on top of the preset (new setup). Quiet, calm, offline."""
    keys = preset_bindings()
    keys.update(CLP_KEYS)
    return {
        "gui": {"sound_volume": 0.3},
        "editor": {"mode": "standard", "auto_pair": True, "file_explorer": {"read_only": True}},
        "terminal": {"modal": False, "bell": ""},
        "command": {"key": "<shift-meta-p>", "aliases": aliases(rescore), "key_bindings": keys},
        "program_status": {"sounds": {"done": "", "working": ""}},
        "animations": {"intro": False, "loading_workspace": False},
        "telemetry": {"enabled": False},
        "upgrade": {"auto_check_enabled": False},
    }


# ---- key syntax validator (docs: learn/key-syntax) ----
MOD_ALIASES = {"c": "ctrl", "s": "shift", "a": "alt", "m": "meta", "ctrl": "ctrl", "shift": "shift", "alt": "alt", "meta": "meta"}
MOD_COMBOS = {("ctrl", "shift"), ("ctrl", "alt"), ("ctrl", "meta"), ("ctrl", "shift", "alt"), ("ctrl", "shift", "meta"),
              ("ctrl", "alt", "meta"), ("shift", "meta"), ("alt", "shift"), ("alt", "meta"), ("alt", "shift", "meta")}
NAMED = {"enter", "esc", "tab", "space", "backspace", "insert", "delete", "home", "end", "pgup", "pgdn", "up", "down",
         "left", "right"} | {f"f{i}" for i in range(1, 13)} | {"capslock", "numlock", "scrolllock", "menu"}
MOUSE = {"mouse-left", "mouse-middle", "mouse-right", "mouse-release", "mouse-wheel-up", "mouse-wheel-down"}


def valid_key_combo(spec: str) -> bool:
    """True when `spec` is one documented key combination (a bracketed token, or a single printable char)."""
    if not spec:
        return False
    if len(spec) == 1:
        return spec not in "<>\\ "
    if not (spec.startswith("<") and spec.endswith(">")):
        return False
    body = spec[1:-1]
    mods: list[str] = []
    while True:
        head, sep, rest = body.partition("-")
        if sep and head in MOD_ALIASES and rest:
            mods.append(MOD_ALIASES[head])
            body = rest
        else:
            break
    if mods and tuple(mods) not in MOD_COMBOS and len(mods) != 1:
        return False
    if len(set(mods)) != len(mods):
        return False
    return body in NAMED or body in MOUSE or (len(body) == 1 and body not in "<>\\ ")


def valid_key_sequence(seq: str) -> bool:
    """A binding value like `{prompt}` is not a key; this validates plain `<key>` sequences used after `echo`."""
    return bool(re.fullmatch(r"(?:<[^<>\s]+>|\\[<>\\]|[^<>\\\s])+", seq))


# ---- YAML emitter (block style; strings are JSON-quoted, which is valid YAML) ----
def to_yaml(obj, indent: int = 0) -> str:
    pad = "  " * indent
    out = []
    for k, v in obj.items():
        key = json.dumps(k) if re.search(r"[^A-Za-z0-9_\-]", k) else k
        if isinstance(v, dict):
            out.append(f"{pad}{key}:")
            out.append(to_yaml(v, indent + 1))
        elif isinstance(v, list):
            out.append(f"{pad}{key}:")
            out.extend(f"{pad}  - {json.dumps(x)}" for x in v)
        else:
            out.append(f"{pad}{key}: {json.dumps(v) if isinstance(v, (str, bool, int, float)) else v}")
    return "\n".join(out)


def render_yaml(rescore: bool = False) -> str:
    return MARKER + "\n# Rune user configuration: standard editor preset + Alnair overlay. See claude-mem/rune/README.md\n" + \
        to_yaml(overlay(rescore)) + "\n"


def render_star_block(rescore: bool = False) -> str:
    ov = overlay(rescore)
    lines = [STAR_BEGIN,
             "# Adds Alnair aliases and chords only where free. Colors and other settings are yours.",
             'config["command"].setdefault("aliases", {})',
             'config["command"].setdefault("key_bindings", {})']
    for k, v in ov["command"]["aliases"].items():
        lines.append(f'if {json.dumps(k)} not in config["command"]["aliases"]:')
        lines.append(f'    config["command"]["aliases"][{json.dumps(k)}] = {json.dumps(v)}')
    for k, v in CLP_KEYS.items():
        lines.append(f'if {json.dumps(k)} not in config["command"]["key_bindings"]:')
        lines.append(f'    config["command"]["key_bindings"][{json.dumps(k)}] = {json.dumps(v)}')
    lines.append(STAR_END)
    return "\n".join(lines) + "\n"


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def datadir(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get("RUNE_DATADIR") or core.home() / ".rune").expanduser()


def find_rune() -> str | None:
    for cand in (shutil.which("rune"), "/opt/homebrew/bin/rune", "/Applications/Rune.app/Contents/MacOS/rune"):
        if cand and os.access(cand, os.X_OK):
            return cand
    return None


def status(d: Path | None = None) -> str:
    d = datadir(d)
    y, s = d / "config.yaml", d / "config.star"
    man = core.read_json(d / "clp-rune.json") or {}
    if s.exists() and y.exists():
        return "BOTH config.yaml and config.star exist (Rune expects one)"
    if s.exists():
        return "config.star " + ("has CLP block" if STAR_BEGIN in s.read_text(encoding="utf-8", errors="replace") else "no CLP block yet")
    if y.exists():
        if man.get("sha256") == sha(y.read_bytes()):
            return "config.yaml managed by clp-rune (unchanged since install)"
        return "config.yaml present, " + ("edited since install" if man else "not ours (proposal only)")
    return "no config yet (first Rune launch would show the setup wizard)"


def backup(d: Path, now: float | None = None) -> tuple[Path | None, list[str]]:
    """Copy existing config.yaml/config.star into backups/<stamp>/. Returns (dir or None, files)."""
    found = [n for n in ("config.yaml", "config.star") if (d / n).is_file()]
    if not found:
        return None, []
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    dest = d / "backups" / stamp
    n = 1
    while dest.exists():
        n += 1
        dest = d / "backups" / f"{stamp}-{n}"
    dest.mkdir(parents=True, mode=0o700)
    for name in found:
        shutil.copy2(d / name, dest / name)
    return dest, found


def write_atomic(path: Path, text: str) -> None:
    path = path.resolve()  # keep a symlinked dotfile a symlink: write the real target
    tmp = path.with_name(f".{path.name}.clp-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def apply(d: Path | None = None, rescore: bool = False, dry_run: bool = True, now: float | None = None) -> tuple[int, list[str]]:
    """Merge Alnair into the Rune config. Returns (rc, report lines). rc 3 = proposal written, manual merge needed."""
    d = datadir(d)
    y, s, man_path = d / "config.yaml", d / "config.star", d / "clp-rune.json"
    man = core.read_json(man_path) or {}
    rep: list[str] = []
    if y.exists() and s.exists():
        return 2, ["both config.yaml and config.star exist; Rune expects one. Nothing changed."]
    new_yaml = render_yaml(rescore)
    if s.exists():
        text = s.read_text(encoding="utf-8")
        block = render_star_block(rescore)
        if STAR_BEGIN in text and STAR_END in text:
            a, rest = text.split(STAR_BEGIN, 1)
            _, b = rest.split(STAR_END, 1)
            merged = a.rstrip("\n") + ("\n\n" if a.strip() else "") + block + b.lstrip("\n")
        else:
            merged = text.rstrip("\n") + ("\n\n" if text.strip() else "") + block
        rep.append(f"config.star: {'replace' if STAR_BEGIN in text else 'append'} marked block ({len(block.splitlines())} lines); rest untouched")
        if merged == text:
            return 0, rep + ["already up to date"]
        if dry_run:
            return 0, rep + ["dry run: nothing written (use --apply)"]
        d.mkdir(parents=True, exist_ok=True)
        bdir, files = backup(d, now)
        write_atomic(s, merged)
        man.update({"star_sha256": sha(merged), "backup": str(bdir) if bdir else None})
        write_atomic(man_path, json.dumps(man, indent=1) + "\n")
        return 0, rep + [f"backup: {bdir} ({', '.join(files)})", "written"]
    if y.exists():
        cur = y.read_bytes()
        ours = man.get("sha256") == sha(cur) and cur.decode("utf-8", "replace").startswith(MARKER)
        if not ours:
            prop = d / "config.clp-proposal.yaml"
            rep.append("config.yaml is not ours or was edited: it will NOT be overwritten")
            if dry_run:
                return 3, rep + [f"dry run: would write proposal to {prop} for manual merge"]
            bdir, files = backup(d, now)
            write_atomic(prop, new_yaml)
            return 3, rep + [f"backup: {bdir} ({', '.join(files)})", f"wrote proposal {prop}; merge it by hand, then restart Rune"]
        if new_yaml.encode() == cur:
            return 0, ["config.yaml already up to date"]
        rep.append("config.yaml is clp-rune's own, unchanged since install: regenerate")
    else:
        rep.append("no config yet: create config.yaml (skips the first-run wizard; standard preset included)")
    if dry_run:
        return 0, rep + ["dry run: nothing written (use --apply)"]
    d.mkdir(parents=True, exist_ok=True)
    bdir, files = backup(d, now)
    write_atomic(y, new_yaml)
    man.update({"sha256": sha(new_yaml), "version": 1, "backup": str(bdir) if bdir else None})
    write_atomic(man_path, json.dumps(man, indent=1) + "\n")
    rep.append(f"backup: {bdir} ({', '.join(files)})" if bdir else "backup: none needed (no previous config)")
    return 0, rep + [f"written {y}", "restart Rune: gui.*, telemetry.*, upgrade.* and program_status.* apply only at startup"]


def install_links(bindir: Path | None = None, dry_run: bool = True) -> tuple[int, list[str]]:
    """Symlink the single-token launchers into ~/.local/bin. Never replaces a file/link that is not already ours."""
    bindir = bindir or core.home() / core.BIN_DIR_NAME
    rep, rc = [], 0
    for name in LAUNCHERS:
        link = bindir / name
        if link.is_symlink() and link.resolve() == TARGET.resolve():
            rep.append(f"{name}: ok")
        elif link.exists() or link.is_symlink():
            rep.append(f"{name}: SKIPPED, {link} exists and is not ours")
            rc = 1
        elif dry_run:
            rep.append(f"{name}: would link -> {TARGET}")
        else:
            bindir.mkdir(parents=True, exist_ok=True)
            link.symlink_to(TARGET)
            rep.append(f"{name}: linked")
    return rc, rep
