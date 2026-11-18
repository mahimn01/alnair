# Code Mode A/B task

One small, mixed task run twice by the same launcher and model: once with Code Mode off, once on. It has
some search (find broken links across notes), some edits (docstrings), and a check to run. That's the kind
of work where batching tool calls in a script should help, if it helps at all.

## Steps (the operator runs these; nothing here starts a session by itself)

1. `bash claude-mem/bench/code_mode_ab/setup.sh` makes `/tmp/alnair-ab-off` and `/tmp/alnair-ab-on`, two
   identical copies of `claude-clp/fixture` with the task material added.
2. In one terminal, arm OFF:
   `<repo>/claude-mem/bin/claude-lab-clp --model opus --mode deep --no-code-mode --project /tmp/alnair-ab-off`
3. In another terminal, arm ON:
   `<repo>/claude-mem/bin/claude-lab-clp --model opus --mode deep --code-mode --project /tmp/alnair-ab-on`
4. Paste the prompt below into each, unchanged. Don't help either session along. When it has answered,
   type `/exit` so the session-end hooks write the session note.
5. `<repo>/.venv/bin/python claude-mem/bench/code_mode_ab/collect.py --off /tmp/alnair-ab-off --on /tmp/alnair-ab-on`
   prints both arms side by side. You can pass session ids instead of folders.

Use `<repo>` = the Alnair checkout (the repo launcher, not the `~/.local/bin` link), and run the arms one
after the other or at the same time. The rate limits you share between them affect only wall time.

## The prompt (paste exactly)

```text
Work only inside this folder.
1. Find every broken local link in the Markdown files under notes/. A link is the [text](target) or ![alt](target) form; ignore [[wiki]] links, http(s) links and links that are only an #anchor. A local link is broken when the file it points to (resolved from the note's own folder, after %20-style decoding, ignoring any #anchor or "title") does not exist. Write them to broken-links.txt in this folder, one per line, as `<note path relative to this folder>: <target exactly as written in the note>`, sorted, with nothing else in the file.
2. Add a docstring to every function and method in code/limits.py that lacks one. Don't change any behaviour, names or signatures.
3. Run `python3 code/check_limits.py` and tell me its output.
Don't change any other file.
```

## Success criteria (collect.py checks each one)

1. `broken-links.txt` holds exactly these four lines, in this order (collect.py also accepts the targets
   %20-decoded):

   ```text
   notes/Plan.md: Risk.md
   notes/Plan.md: archive/Old%20Plan.md
   notes/Review.md: Missing%20Note.md
   notes/Review.md: img/drawdown.png
   ```

2. `python3 -B code/check_limits.py` exits 0 and prints `check passed`: all six functions and methods
   (`backoff`, `cap`, `attempts_left`, `jitter`, `Window.__init__`, `Window.allows`) have a docstring, and
   the twelve behaviour cases hold.
3. No other file changed: compared with `/tmp/alnair-ab-<arm>.seed.json`, only `code/limits.py` changed and
   only `broken-links.txt` was added. `__pycache__` folders are ignored, and files Claude Code itself
   writes under `.claude/` (a "don't ask again" answer) are listed apart and don't count.

An arm passes when all three hold. Then compare cost and behaviour: API requests, input, output and cache
tokens, tool calls (direct, REPL scripts, calls inside scripts), wall time, whether the lab hooks fired
(route, brief, metrics) and whether a vault session note was written.

## Reading the result

One run per arm is a single sample. Model output varies run to run, so a gap smaller than about 20% in
tokens or requests isn't a finding. Repeat both arms (rerun setup.sh first) before you conclude anything.
