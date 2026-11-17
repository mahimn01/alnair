"""The Claude pane: reuse alnair in-process, add the vault-note instruction, let it exec Claude.

alnair is loaded as a module and left unchanged on disk; only its in-memory MEM_GUIDE gets one extra
paragraph. Its own main() builds the lab run and execve()s Claude, so Claude keeps the native PTY and its own
OSC 7501 status. This module writes nothing to the terminal before that exec.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

from . import core

RUNE_GUIDE = """\

This session runs inside the Rune terminal workspace "Alnair".
When you finish research or analysis, save the key findings as a NEW note in the Obsidian vault's
"Claude Sessions" folder: create a new file, and never edit, rename or delete an existing vault note.
Keep secrets, keys and raw transcripts out of notes. Do not ask to switch to bypass or auto-approve modes.
"""

PREFIX_ASK = "Task: "
PREFIX_RESEARCH = ("Use the research:research-loop skill (search broadly, fetch primary sources, record exact-quote claims, "
                   "gate the answer with research_report, abstain where thin) on: ")
MAX_PROMPT = 8000
PENDING_TTL = 120.0


def safe_prompt(prefix: str, words: list[str]) -> str:
    """One argv-safe prompt: fixed non-dash prefix + the user's words as inert text (no NULs, bounded)."""
    text = " ".join(words).replace("\x00", "").strip()
    if not text:
        raise ValueError("empty prompt")
    if not prefix or prefix[0] == "-":
        raise ValueError("prefix must not start with a dash")
    return (prefix + text)[:MAX_PROMPT]


def load_launcher():
    loader = SourceFileLoader("claude_lab_clp_for_rune", str(core.LAUNCHER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def build_args(project: Path, resume: bool, prompt: str | None, dry_run: bool) -> list[str]:
    """Arguments handed to alnair's main(). Never contains permission/bypass flags or a literal `--`."""
    args = ["--resume", "latest"] if resume else ["--project", str(project)]
    if dry_run:
        args.append("--dry-run")
    if prompt:
        args.append(prompt)
    return args


def run(resume: bool = False, prompt: str | None = None, project: Path | None = None, dry_run: bool = False,
        loader=load_launcher) -> int:
    mod = loader()
    mod.MEM_GUIDE = mod.MEM_GUIDE + RUNE_GUIDE  # in-memory only; inject_clp_plugin reads it at call time
    return int(mod.main(build_args(project or Path.cwd(), resume, prompt, dry_run)) or 0)


# ---- staged prompts: the prompt crosses from Rune to the Claude tab through a private one-shot file ----

def state_dir() -> Path:
    return Path(os.environ.get("CLP_RUNE_STATE") or core.home() / ".local/state/clp-rune")


def stage(kind: str, words: list[str], state: Path | None = None) -> Path:
    prefix = {"ask": PREFIX_ASK, "research-loop": PREFIX_RESEARCH}.get(kind)
    if prefix is None:
        raise ValueError(f"unknown prompt kind {kind!r}")
    prompt = safe_prompt(prefix, words)
    d = state or state_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    tmp = d / f".pending.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"kind": kind, "prompt": prompt, "ts": time.time()}, fh)
    os.replace(tmp, d / "pending.json")
    return d / "pending.json"


def consume(state: Path | None = None, wait: float = 20.0, poll: float = 0.2) -> str:
    """Take the staged prompt exactly once. Prompts staged before this tab started are stale and discarded.

    Raises ValueError when nothing valid shows up in time.
    """
    d = state or state_dir()
    path = d / "pending.json"
    started = time.time()
    deadline = time.monotonic() + wait
    while True:
        if not path.exists():
            if time.monotonic() >= deadline:
                raise ValueError("no staged prompt arrived")
            time.sleep(poll)
            continue
        taken = d / f".taken.{os.getpid()}"
        try:
            os.replace(path, taken)
            st = taken.lstat()
            if taken.is_symlink() or not taken.is_file() or st.st_uid != os.getuid():
                raise ValueError("staged prompt rejected (not a private regular file)")
            data = json.loads(taken.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue  # another tab won the race
        finally:
            taken.unlink(missing_ok=True)
        if not isinstance(data, dict) or not isinstance(data.get("prompt"), str):
            raise ValueError("staged prompt malformed")
        ts = data.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise ValueError("staged prompt malformed")
        if time.time() - ts > PENDING_TTL:
            raise ValueError("staged prompt expired")
        if ts < started - 2:
            continue  # left over from an earlier tab: never hand it to this session
        if data["prompt"][:1] == "-":
            raise ValueError("staged prompt rejected")
        return data["prompt"][:MAX_PROMPT]
