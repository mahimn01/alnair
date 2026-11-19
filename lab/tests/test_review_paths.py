"""Reviewer prompt paths (lab/bin/review_paths.py): what the lab does with file names in a reviewer's prompt.

The 2026-11-18 Code Mode A/B refused four reviewer launches with "Reviewer artifact missing": a bare
`limits.py` (the file is code/limits.py), `reviewer.md` (the reviewer's own instructions) and `Index.md`
(the tail of notes/00 Index.md, cut at the space). A file name in prose is a mention, not a precondition:
the lab resolves what it can find and leaves the rest as written with a note, so a review always starts.
"""
from __future__ import annotations

import importlib.util
import json
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load():
    spec = importlib.util.spec_from_file_location("lab_review_paths", ROOT / "bin/review_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ab(tmp_path, monkeypatch):
    """A task folder laid out like the A/B one, and a lab run folder with its policies and artifacts."""
    monkeypatch.setenv("HOME", str(tmp_path))   # keeps the real ~/.clp/research/docs out of the lookup
    project = tmp_path / "alnair-ab-on"
    for name in ["notes/00 Index.md", "notes/AMZN.md", "notes/Plan.md", "notes/Review.md", "code/limits.py",
                 "code/check_limits.py", "broken-links.txt", "ideas.txt", ".alnair-ab"]:
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text("x\n")
    run = tmp_path / "run"
    run.mkdir()
    for name in ["personal-core.md", "trading-policy.md", "acceptance.json", "metrics.json", "verification.md",
                 "check-0.log", "check-1.log", "implementation.md"]:
        (run / name).write_text("x\n")
    return project, run


def note_of(prompt: str) -> str:
    return prompt.split("LAB NOTE", 1)[1] if "LAB NOTE" in prompt else ""


@pytest.mark.parametrize("name", ["Index.md", "limits.py (OFF)", "limits.py (ON)", "reviewer.md"])
def test_the_reviewer_prompts_the_ab_refused_now_start(ab, tmp_path, name):
    project, run = ab
    m = load()
    raw = json.loads((FIXTURES / "ab_reviewer_prompts.json").read_text())["denied"][name]
    prompt = raw.replace("{PROJECT}", str(project)).replace("{RUN}", str(run)).replace("{SCRATCH}", str(tmp_path / "scratch"))
    out = m.resolve_prompt(prompt, project, run)
    # Everything the prompt said is still there; real files are made absolute.
    assert out.startswith(prompt.split("broken-links.txt")[0][:40])
    if "code/limits.py" in prompt:
        assert str(project / "code/limits.py") in out
    if "notes/00 Index.md" in prompt:
        assert str(project / "notes/00 Index.md") in out and "`Index.md`" not in note_of(out)
    left = note_of(out)
    if name == "reviewer.md":
        assert "reviewer.md" in left
    if "limits.py (" in name:
        assert "limits.py" in left and str(project / "code/limits.py") not in left


def test_the_cli_starts_the_review_and_exits_0_for_missing_mentions(ab):
    project, run = ab
    m = load()
    code = m.main(["--base", str(project), "--run", str(run), "--prompt", "Check limits.py, reviewer.md and notes/00 Index.md"])
    assert code == 0


def test_spaces_quotes_backslash_escapes_and_unicode(ab):
    project, run = ab
    m = load()
    (project / "My Notes").mkdir()
    (project / "My Notes/Plan v2.md").write_text("x")
    nfc = unicodedata.normalize("NFC", "Café ñ.md")
    (project / nfc).write_text("x")
    (project / "日本語").mkdir()
    (project / "日本語/ノート.md").write_text("x")
    index = str(project / "notes/00 Index.md")
    cases = {
        "Read notes/00 Index.md first.": index,                      # bare, with a space
        "Read notes/00\\ Index.md first.": index,                  # shell-style escaped space
        'Read "notes/00 Index.md" first.': index,                    # double quotes
        "Read `notes/00 Index.md` first.": index,                  # backticks
        f"Read {project}/My Notes/Plan v2.md now": str(project / "My Notes/Plan v2.md"),   # absolute, two spaces
        "Read ~/alnair-ab-on/notes/00 Index.md": index,            # ~ path with a space
        "Read My Notes/Plan v2.md:3-9 now": str(project / "My Notes/Plan v2.md") + ":3-9",  # line range kept
        f"Read {unicodedata.normalize('NFD', 'Café ñ.md')} now": str(project / nfc),        # NFD text, NFC file
        "Read 日本語/ノート.md now": str(project / "日本語/ノート.md"),                      # non-Latin
    }
    for prompt, expected in cases.items():
        out = m.resolve_prompt(prompt, project, run)
        assert expected in unicodedata.normalize("NFC", out), (prompt, out)
        assert "LAB NOTE" not in out, (prompt, out)


def test_missing_and_ambiguous_mentions_are_left_with_a_note_never_refused(ab):
    project, run = ab
    m = load()
    (project / "implementation.md").write_text("project copy")
    out = m.resolve_prompt("Risk.md is a broken link target; compare implementation.md; see reviewer.md.", project, run)
    left = note_of(out)
    assert out.startswith("Risk.md is a broken link target; compare implementation.md; see reviewer.md.")
    assert "Risk.md" in left and "reviewer.md" in left
    assert "implementation.md" in left and str(project / "implementation.md") in left and str(run / "implementation.md") in left


def test_the_base_folder_is_optional_for_an_unscoped_review(ab):
    project, run = ab
    m = load()
    out = m.resolve_prompt("Review acceptance.json and notes/AMZN.md", None, run)
    assert str(run / "acceptance.json") in out and "notes/AMZN.md" in note_of(out)
    assert m.main(["--run", str(run), "--prompt", "Review acceptance.json"]) == 0


def test_a_missing_policy_file_is_the_one_refusal_and_says_what_to_do(ab, capsys):
    project, run = ab
    m = load()
    (run / "trading-policy.md").unlink()
    assert m.main(["--base", str(project), "--run", str(run), "--prompt", "Review"]) == 2
    err = capsys.readouterr().err
    assert str(run / "trading-policy.md") in err and "alnair --resume" in err
