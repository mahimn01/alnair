"""clp-rune command line. Dispatches by subcommand, or by launcher name (clp-rune-claude, -resume, -dash, -pending).

Read-only unless a subcommand says otherwise (install, config --apply, refresh, stage).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from . import claude, config, core, dash, helpers

HELPERS = {"lookup", "read", "verify", "status", "refresh", "research-status", "tests", "bench"}
BY_NAME = {"clp-rune-claude": ["claude"], "clp-rune-resume": ["claude", "--resume"],
           "clp-rune-dash": ["dash"], "clp-rune-pending": ["pending"]}

USAGE = """clp-rune: Alnair in Rune

  clp-rune                      open Rune on this folder (then press ctrl-cmd-C to build the layout)
  clp-rune launch [--project D] [--dry-run]
  clp-rune install [--apply]    symlinks in ~/.local/bin + Rune config (dry run unless --apply)
  clp-rune config [--apply] [--datadir D]
  clp-rune claude [--resume] [--dry-run] [PROMPT...]   Claude pane (same as alnair + vault-note rule)
  clp-rune research-loop [--dry-run] PROMPT...
  clp-rune dash [--once]        live dashboard
  clp-rune lookup|read|verify|status|refresh|research-status|doctor|tests|bench|rescore|note-latest|keys
"""

KEY_LABELS = {"clp": "add Claude | dashboard panes (never closes; repeat = duplicates)", "clpdash": "reopen dashboard", "clpnew": "new Claude tab",
              "clpresume": "resume latest session", "clpstatus": "CLP status", "clprefresh": "CLP refresh index",
              "clpresstatus": "research status", "clpdoctor": "doctor", "clpnote": "open latest vault note",
              "clptests": "run tests", "clpbench": "helper benchmark", "clpkeys": "this list",
              "clplookup": "CLP lookup (then type query)", "clpread": "CLP read (then type address)",
              "clpverify": "CLP verify (question, answer-file)", "clpresearch": "research-loop (then type prompt)",
              "clpask": "new Claude with prompt (then type prompt)"}


def cmd_keys() -> str:
    rows = ["Alnair keys (Control+Command+letter; terminals leave these to Rune)", ""]
    for key, target in config.CLP_KEYS.items():
        alias = target.replace("echo {prompt}", "").replace("<space>", "")
        pretty = key.replace("<ctrl-meta-", "ctrl-cmd-").rstrip(">")
        rows.append(f"  {pretty:<12} {KEY_LABELS.get(alias, alias)}")
    rows += ["", "Layout (ctrl-cmd-C) only ADDS panes; it never closes windows or sessions (repeating may duplicate panes)."]
    rows += ["", "Command prompt: shift-cmd-P.  Aliases: " + " ".join(sorted(config.aliases(False)))]
    return "\n".join(rows)


def cmd_launch(project: Path, dry_run: bool) -> int:
    rune = config.find_rune()
    if not rune:
        print("clp-rune: rune not found (brew install unstablebuild/tap/rune)", file=sys.stderr)
        return 1
    argv = [rune, "-w", "file://" + quote(str(project.resolve()), safe="/")]
    if dry_run:
        print(" ".join(argv))
        return 0
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    print(f"Rune opening {project}. Press ctrl-cmd-C (or run :clp) to build the Claude | dashboard layout.")
    return 0


def main(argv: list[str], prog: str = "clp-rune") -> int:
    argv = BY_NAME.get(prog, []) + list(argv)
    if not argv:
        argv = ["launch"]
    try:
        return dispatch(argv[0], argv[1:])
    except (ValueError, OSError) as e:
        print(f"clp-rune {argv[0]}: {e}", file=sys.stderr)
        return 2


def dispatch(cmd: str, rest: list[str]) -> int:
    if cmd in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    if cmd == "keys":
        print(cmd_keys())
        return 0
    if cmd == "launch":
        p = argparse.ArgumentParser(prog="clp-rune launch")
        p.add_argument("--project", type=Path, default=Path.cwd())
        p.add_argument("--dry-run", action="store_true")
        o = p.parse_args(rest)
        return cmd_launch(o.project, o.dry_run)
    if cmd == "dash":
        p = argparse.ArgumentParser(prog="clp-rune dash")
        p.add_argument("--once", action="store_true")
        p.add_argument("--interval", type=float, default=5.0)
        o = p.parse_args(rest)
        return dash.run(once=o.once, interval=o.interval)
    if cmd == "claude":
        p = argparse.ArgumentParser(prog="clp-rune claude")
        p.add_argument("--resume", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--project", type=Path, default=Path.cwd())
        p.add_argument("words", nargs="*")
        o = p.parse_args(rest)
        prompt = claude.safe_prompt(claude.PREFIX_ASK, o.words) if o.words else None
        return claude.run(resume=o.resume, prompt=prompt, project=o.project, dry_run=o.dry_run)
    if cmd == "research-loop":
        p = argparse.ArgumentParser(prog="clp-rune research-loop")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("words", nargs="+")
        o = p.parse_args(rest)
        return claude.run(prompt=claude.safe_prompt(claude.PREFIX_RESEARCH, o.words), dry_run=o.dry_run)
    if cmd == "stage":
        if not rest:
            raise ValueError("usage: stage ask|research-loop WORDS...")
        claude.stage(rest[0], rest[1:])
        return 0
    if cmd == "pending":
        prompt = None
        try:
            prompt = claude.consume()
        except (ValueError, OSError, TypeError) as e:
            print(f"clp-rune: {e}; starting a plain Claude session instead", file=sys.stderr)
        return claude.run(prompt=prompt)
    if cmd in HELPERS:
        return helpers.run_status(cmd, helpers.plan(cmd, rest))
    if cmd == "doctor":
        core.osc7501("working")
        rc, text = helpers.doctor()
        print(text)
        core.osc7501("done" if rc == 0 else "error", "doctor ok" if rc == 0 else "doctor found problems")
        return rc
    if cmd == "rescore":
        script = helpers.rescore_script()
        if script is None:
            print("rescore: unavailable (no *rescore* script under scripts/ in this repo)", file=sys.stderr)
            return 2
        return helpers.run_status("rescore", [helpers.tool_python(), str(script)])
    if cmd == "note-latest":
        note = core.latest_note(core.vault_notes_dir())
        if note is None:
            print("no Claude Sessions note found", file=sys.stderr)
            return 1
        print(note)
        return 0
    if cmd == "config":
        p = argparse.ArgumentParser(prog="clp-rune config")
        p.add_argument("--apply", action="store_true")
        p.add_argument("--datadir")
        o = p.parse_args(rest)
        rc, lines = config.apply(o.datadir, rescore=helpers.rescore_script() is not None, dry_run=not o.apply)
        print("\n".join(lines))
        return rc
    if cmd == "install":
        p = argparse.ArgumentParser(prog="clp-rune install")
        p.add_argument("--apply", action="store_true")
        o = p.parse_args(rest)
        rc1, l1 = config.install_links(dry_run=not o.apply)
        rc2, l2 = config.apply(None, rescore=helpers.rescore_script() is not None, dry_run=not o.apply)
        print("\n".join(l1 + l2))
        return max(rc1, rc2)
    print(USAGE, file=sys.stderr)
    return 2
