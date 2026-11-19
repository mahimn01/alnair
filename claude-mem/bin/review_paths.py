#!/usr/bin/env python3
"""Point a reviewer's prompt at real files before the lab starts the review. Read-only; runs no model.

The lab's agent.spawn hook runs it for every reviewer:
    review_paths.py [--base CODE_FOLDER] --run RUN_FOLDER --prompt TEXT
and starts the reviewer with the prompt it prints as {"prompt": ...}. Each file name in the prompt that
matches exactly one real file (in the code folder, the run folder or the research docs) becomes that
file's absolute path. A name that matches no file, or several, is left as written and listed in a LAB
NOTE at the end, so the reviewer looks for it; a file name in prose never blocks a review. Names may
contain spaces (bare, in quotes, or escaped as a backslash-space) and any Unicode, in either NFC or NFD.
The one refusal (exit 2) is a run folder without the policy files the reviewer is told to read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

EXTENSIONS = 'md|txt|json|csv|py|js|ts|tsx|jsx|html|pdf|yaml|yml|toml|log'
# A file name: something before a known extension, then an optional :line, :line-line or :line#hash.
MENTION = re.compile(r'(?P<path>.*[^/\s]\.(?:' + EXTENSIONS + r'))(?P<suffix>:\d+(?:-\d+)?(?:#[a-f0-9]+)?)?', re.I | re.S)
CODE = re.compile(r'`[^`\n]+`')
WORD = re.compile(r'\S+')
OPENERS = '([{<"\'*'
CLOSERS = ')]}>"\'*,.;:!?'
QUOTES = '"\'`'
SPACED_WORDS = 6   # a name with spaces may reach back this many words, on the same line
POLICIES = ('personal-core.md', 'trading-policy.md')


def locate(name: str, roots: list[Path]) -> list[Path]:
    """The distinct real files a name can mean: as written, unescaped, and in both Unicode forms."""
    forms = [name, re.sub(r'\\(.)', r'\1', name)]
    forms += [unicodedata.normalize(n, f) for f in forms[:2] for n in ('NFC', 'NFD')]
    found: dict[tuple[int, int], Path] = {}
    for form in dict.fromkeys(forms):
        try:
            path = Path(form).expanduser()
            for candidate in [path] if path.is_absolute() else [root / path for root in roots]:
                if candidate.is_file():
                    # One file reached by two spellings (NFC and NFD on macOS) is still one file.
                    stat = candidate.stat()
                    found.setdefault((stat.st_dev, stat.st_ino), candidate.parent.resolve() / candidate.name)   # a link keeps its own name
        except (OSError, RuntimeError, ValueError):
            continue   # an unknown ~user, a name too long or with a NUL byte is simply not a file
    return sorted(found.values(), key=str)


def trimmed(word: str) -> tuple[int, int]:
    """How many characters of punctuation (and a trailing 's) to drop from each end of a word around a name."""
    core = re.sub(r"['’]s$", '', word.rstrip(CLOSERS)).rstrip(CLOSERS)
    lead = len(core) - len(core.lstrip(OPENERS))
    if '](' in core[lead:]:
        lead += core[lead:].index('](') + 2   # a Markdown link: the name is its destination
    return lead, len(word) - len(core)


class Resolver:
    def __init__(self, base: str | Path | None, run: str | Path):
        self.roots = [Path(x).expanduser().resolve() for x in (base, run) if x] + [Path.home() / '.clp/research/docs']
        self.notes: dict[str, str] = {}

    def note(self, name: str, found: list[Path]) -> None:
        if found:
            self.notes.setdefault(name, 'matches more than one file: ' + ', '.join(map(str, found)))
        else:
            self.notes.setdefault(name, 'no file at that path in ' + ', '.join(map(str, self.roots)) + ' (subfolders are not searched)')

    def code(self, match: re.Match) -> str:
        """A backtick span: rewritten only when the whole span is one file name; commands stay as written."""
        inner = match.group()[1:-1]
        mention = MENTION.fullmatch(inner.strip())
        if not mention or '://' in inner or '*' in inner:
            return match.group()
        found = locate(mention['path'], self.roots)
        if len(found) == 1:
            return '`' + str(found[0]) + (mention['suffix'] or '') + '`'
        if not re.search(r'\s', inner.strip()):
            self.note(inner.strip(), found)
        return match.group()

    def prose(self, text: str) -> str:
        """Plain text: each word ending in a file name is tried alone and with up to six words before it."""
        words = list(WORD.finditer(text))
        out, done = [], 0
        for i, word in enumerate(words):
            lead, tail = trimmed(word.group())
            end = word.end() - tail
            first = MENTION.fullmatch(text[word.start() + lead:end])
            if not first or '://' in first.group() or '*' in first.group():
                continue
            best, seen = None, None
            for j in range(i, max(-1, i - SPACED_WORDS - 1), -1):
                if words[j].start() < done or (j < i and '\n' in text[words[j].end():words[j + 1].start()]):
                    break
                start = words[j].start() + (lead if j == i else trimmed(words[j].group())[0])
                mention = MENTION.fullmatch(text[start:end])
                if not mention:
                    continue
                found = locate(mention['path'], self.roots)
                if len(found) == 1:
                    best = (start, found[0], mention['suffix'] or '')
                elif found:
                    seen = (mention['path'], found)
            if best is None:
                self.note(*(seen or (first['path'], [])))
                continue
            start, path, suffix = best
            result = str(path) + suffix
            quoted = (start > 0 and text[start - 1] in QUOTES) or (end < len(text) and text[end] in QUOTES)
            if re.search(r'\s', result) and not quoted:
                result = '`' + result + '`'   # keeps a name with spaces readable as one name
            out += [text[done:start], result]
            done = end
        return ''.join(out) + text[done:]


def resolve_prompt(prompt: str, base: str | Path | None, run: str | Path) -> str:
    """The prompt with real files made absolute, plus a LAB NOTE naming the file names left as written."""
    resolver = Resolver(base, run)
    pieces, last = [], 0
    for match in CODE.finditer(prompt):
        pieces += [resolver.prose(prompt[last:match.start()]), resolver.code(match)]
        last = match.end()
    pieces.append(resolver.prose(prompt[last:]))
    out = ''.join(pieces)
    if resolver.notes:
        out += ('\n\nLAB NOTE (file names): these names in the prompt did not match exactly one file, so they are '
                'left as written. Find them yourself before relying on them:\n'
                + '\n'.join(f'- `{name}`: {why}' for name, why in resolver.notes.items()))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Resolve the file names in a reviewer prompt (read-only).')
    parser.add_argument('--base', help='the code folder of the task; omit it for an unscoped review')
    parser.add_argument('--run', required=True, help='the lab run folder')
    parser.add_argument('--prompt', required=True)
    args = parser.parse_args(argv)
    run = Path(args.run)
    # The lab tells every reviewer to read these two files, so they must exist before a review starts.
    missing = [str(run / name) for name in POLICIES if not (run / name).is_file()]
    if missing:
        print('Reviewer policy missing: ' + ', '.join(missing) + '. The lab writes these files when a run starts '
              'and restores them when it resumes: resume this run (alnair --resume, or claude-lab launch --resume '
              + str(run) + '), then start the review again.', file=sys.stderr)
        return 2
    try:
        prompt = resolve_prompt(args.prompt, args.base, run)
    except Exception as error:   # the name check is a convenience: never let it stop a review
        prompt = args.prompt + f'\n\nLAB NOTE (file names): the lab could not check the file names in this prompt ({type(error).__name__}).'
    print(json.dumps({'prompt': prompt}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
