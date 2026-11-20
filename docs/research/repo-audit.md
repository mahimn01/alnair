# Audit of this repository against the research

This is the second half of [oss-best-practices.md](oss-best-practices.md). That page says what popular command-line tools have. This page says where Alnair stood against it, ranked by how much each gap is likely to cost in stars, and what the `oss-polish` branch did about each one.

Audited: `main` at commit 097bb65 on 2026-11-20. The README numbers come from the same script that measured the dataset ([data/fetch_repos.py](data/fetch_repos.py)). The repository settings come from the GitHub API on the same day. The ranking is my judgment. Nothing in the research measures the star value of any single fix.

## Where it stood

| | Alnair on `main` | Popular AI coding tools (34) | Popular command-line tools (20) |
|---|---|---|---|
| Stars | 0 | median 41839 | median 38716 |
| README length | 3993 words, 290 lines | median 1827.5 words | median 2264 words |
| First image | line 10, a static screenshot of `--help` | 27/34 have one in the first 40 lines | 19/20 |
| Animated demo | none | 8/34 | 11/20 |
| First install command | line 32 | median line 54 | median line 100 |
| Badges in the first 40 lines | 2 | median 4 | median 3 |
| A section that says why it exists | no | | |
| Releases | 2, both on 2026-11-17 | 31/34 publish releases, median 165.5 | 20/20, median 82 |
| Topics | 14 | median 6 | median 7.5 |
| Discussions | off | 27/34 on | 17/20 on |
| examples folder | no | 9/34 | 7/20 |
| Social preview image | none | not measured | not measured |
| License, CONTRIBUTING, security policy, code of conduct, PR template, issue forms, changelog | all present | | |
| CI | the five test suites on macOS only | not measured | not measured |

## Gaps, highest impact first

### 1. Nothing showed it working

The first screen had a screenshot of `alnair --help`. A visitor could read what Alnair claims to do and could not see it do any of it, and there was nothing to run short of a full install and a model session. The research has people starring after they understand a tool or get it working, and 19/20 popular command-line tools show the product in the first 40 lines.

On this branch: [examples/verify-gate.sh](../../examples/verify-gate.sh) runs the verify gate on two sample notes with no model call and no network, and the README prints its real output, one answer passing and one refused. [docs/demo/demo.tape](../demo/demo.tape) records that run as a GIF.

Still open: recording the GIF and a short clip of a real session. Both need a person at a terminal.

### 2. The README said what, and left out why

It opened with a 33-word sentence and went straight to a feature list. It never said which problems the tool is for, and a study of README files found that many leave out purpose and status. At 3993 words it was about twice the median for this kind of tool, and most of the extra length was reference material (Code Mode internals, every setting, Windows details) sitting between a visitor and the comparison table.

On this branch: the README is rewritten at 2566 words. It has an 18-word first line, a "Why I built it" section with the three problems, the runnable example, a diagram of the parts, and a Status section. The reference material moved to [docs/](../README.md) unchanged in substance.

### 3. First-run failures on machines that are not the author's

A stability pass over the whole codebase found bugs that a new user on a different setup would hit in the first minutes: a `config.env` saved with a byte-order mark lost its first setting, a missing Claude Code ended in a bare "No such file" error with no hint, Python 3.10 lost the user's Codex hooks, non-ASCII text crashed the memory server under a locale that is not UTF-8, and a relative `--vault` path pointed somewhere different in every session. Someone whose first run crashes does not star the project.

On this branch: fixed, each with a test that failed before the fix. The full list is in the changelog under Unreleased.

### 4. Linux was "not tested yet", and CI ran on macOS only

`install.sh` printed "Linux hasn't been tested yet" to every Linux user, and the README said "Linux should work but hasn't been tested yet". CI ran the suites on macOS with Python 3.12 only, although the README promises Python 3.10+ and Windows support.

On this branch: CI runs every suite on macOS, Ubuntu and Windows, runs them again on Python 3.10 and 3.13, runs the real installer on all three systems, and checks the PowerShell scripts for syntax errors.

### 5. The installer asked for trust without earning it

The quickstart is `curl | bash`. The README did not list everything the installer touches next to the command, did not say how to remove Alnair, and did not mention that session notes go to `~/Documents/Obsidian Vault` by default, a folder most people don't have. Session notes were also stamped in the author's time zone.

On this branch: the README lists everything the installer creates and names the default notes folder, [docs/install.md](../install.md#removing-it) has removal steps, and session notes use the computer's time zone.

Still open: whether the default notes folder should change. That is a behaviour change for existing users, so it is left to the owner.

### 6. No release since the features the README describes

Both releases are from 2026-11-17. `main` is 37 commits past v0.2.0, and those commits hold Windows support, Code Mode and the one-line installer. 31/34 popular AI coding tools publish releases, and a release is what people who watch a repository get notified about.

On this branch: the changelog's Unreleased section is up to date. Tagging a release is left to the owner.

### 7. Repository settings

No social preview image, so a shared link shows GitHub's generic card. The website field points at a personal site instead of documentation. Discussions are off, so a question has nowhere to go except an issue. The description is good but long at 171 characters.

On this branch: a social preview image is in [docs/social-preview.png](../social-preview.png). The settings themselves need the owner, and the exact values are in the hand-off report.

### 8. Docs had no front door

`docs/` held one page about Windows and an internal design document in a folder named after the tool that produced it. Everything else was in the README.

On this branch: `docs/` has an index, a page per topic, and the design note under `docs/design/`.

### 9. Small trust gaps

The security policy did not say which versions get fixes. CONTRIBUTING did not mention the plugin tests or what CI runs.

On this branch: both are updated.

### 10. No measured results

The repository has two benchmark kits (model routing, Code Mode A/B) and no published numbers from them. The README makes no speed, cost or accuracy claim, which is the right call until there are numbers. A measured result, for example how often the verify gate refuses a wrong answer on a fixed question set, would be the strongest thing the first screen could carry.

Still open. It needs real model sessions, and the routing benchmark runner cannot start as written (see the hand-off report).

## What was already right

Keep these. They match what the popular repositories do.

- The install command is near the top of the README.
- MIT license, CONTRIBUTING, a security policy with private reporting, a code of conduct, a PR template and issue forms.
- A changelog written for people.
- A comparison table that says what you give up.
- An FAQ that answers the money question first.
- Tests that run offline and never start a model session.
