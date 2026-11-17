# Alnair Desktop · Mac prototype

A colorful Tauri 2 desktop workspace around the **real interactive Alnair harness**. Rust owns a portable PTY; xterm.js displays it beside a schema-validated generative canvas. No `-p`, SDK, API key, or inference endpoint is used. The command stays `alnair`; old aliases remain.

## Use

1. Open `~/Applications/Alnair.app` yourself.
2. Choose a model, then **Start session**. The app deliberately opens ready, without silently starting a model. The project/cwd is your trusted home directory.
3. Ask Claude to use the Alnair canvas. Example: “Compare these options in a table, chart the trade-offs, and use ui_ask for my decision.”
4. `⌘K` / `Ctrl-K`: new session, resume latest Alnair lab session, model for the **next** session, component gallery, current vault note.
5. Drag the divider or focus it and use arrow keys. Terminal selection, scrolling, colors and input are real xterm.js.

**First launch:** locally ad-hoc signed, not notarized. macOS may show Gatekeeper or file-access prompts. Claude may show folder/MCP trust or tool permission prompts in the terminal. Approve those yourself if desired. This app does not use System Events, Accessibility, or Automation and never answers dialogs. If the MCP tools require permission, approve them in the interactive terminal. Existing Claude subscription login is used by the harness; no login material is read by this app.

**Previous app:** preserved at `~/Applications/Alnair (Rune).app`.

## Rebuild / verify (no model calls)

```sh
alnair install-app --gui --dry-run
alnair install-app --gui
cd ~/alnair/alnair-app
npm ci
npm test
python3 -m unittest discover -s tests -p 'test_*.py'
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri -- build
# browser-only gallery and interaction checks (never launches the Mac app):
npm run dev
npm run screens
npm run test:ui    # official Tauri mockIPC; start/model/PTY events/canvas/form/palette checks
```

Source remains local-only. The installed prototype references this source tree for MCP/plugin files; do not move it without rebuilding, or set `ALNAIR_APP_ROOT`. Node/npm and Rust are needed to rebuild, Python is needed for the canvas MCP server. Dependencies are pinned in package-lock.json / Cargo.lock.

## Boundaries

- `src/spec.ts` is the Zod contract. `spec.schema.json` is generated from it and is consumed by both Rust and the MCP tool definitions. Unknown keys/types are rejected. Rust validates before storing; React validates before rendering. To regenerate: `npm run schema`; `npm run schema:check` detects drift and is part of every frontend build.
- `src/ComponentView.tsx` renders card, table, line/bar chart, diff, code, checklist/progress, form, choices/buttons, markdown, and embedded raster image. No model-authored HTML, JavaScript or CSS. Markdown HTML is dropped, links are inert, image Markdown is disabled. Images allow only embedded PNG/JPEG/WebP, never SVG, file paths or tracking URLs. CSP is an additional boundary.
- `mcp/server.py` is Python standard-library newline JSON-RPC MCP over stdio. Four tools: `ui_render`, `ui_update`, `ui_clear`, `ui_ask`. `ui_update` replaces the full spec with the same id. `ui_ask` accepts only forms/choices and waits 1–600 seconds for a human answer, returning an error on timeout or clear. Use `ui_ask`, not a nonblocking render, to collect an answer.
- The Rust bridge binds only `127.0.0.1` on a random port. Requests require the per-app-launch random bearer token; browser Origin headers, wrong Host, unauthenticated requests, oversized payloads, too many panels/questions/requests and invalid answers are rejected. Token-bearing MCP config is in a private 0700 directory, file mode 0600, deleted when the PTY session stops. Same-user local processes remain part of the trust boundary.
- `src-tauri/src/pty.rs` owns resize, I/O and process cleanup. It starts `~/.local/bin/alnair` with its ordinary interactive arguments and a per-session `--mcp-config` plus plugin directory, never a shell command string. Routing is **not reimplemented**: the harness decides the route. Its dry-run argv was inspected during this build. API/bearer/provider override environment variables are removed; no Keychain helper or secret is copied.
- `platform` isolates Mac vibrancy, launcher/Python discovery and vault opening. The title bar retains macOS traffic lights. `session_status` reads only known fields of the current harness runtime/metrics. The header reports **harness route**, not an independently verified served model; manual `/model` switches remain authoritative in the terminal. “Configured” memory/research means plugins are wired, not a live health claim.
- New/resume commands ask before stopping an existing session. Resuming uses the harness's dry-run to resolve its latest run, then pins that run and uses its ledger id for vault notes. Selecting a model affects new sessions; existing resumed conversations preserve the harness route.
- Closing the window or stopping the session kills/waits for the PTY child and cancels waiting questions. No global Claude configuration is written. Claude itself may maintain its normal runtime bookkeeping when you later start a real session.

## Tests and caveats

Rust tests exercise a real **non-model shell PTY** and the real Python stdio MCP server → authenticated Rust HTTP bridge → validated component → form answer. Vitest exercises every component plus unsafe content and failed answers. Python tests cover protocol framing and safe installer preservation. Playwright exercises the full mock layout, gallery, palette, and form submission in a headless Chromium browser.

No real Claude session or installed app was launched during this build, so live subscription execution, actual macOS materials/traffic lights, MCP permission flow and native PTY rendering require your first-launch check. Screenshots are browser design previews, not proof of a live Claude conversation. Canvas state is in memory for one app launch and not a durable transcript. Terminal output is not persisted by the app.

Release build compatibility: Rust 1.95 strips build-time proc macros into dylibs that current macOS dyld can reject (`E0463`, mis-aligned LINKEDIT string pool). `[profile.release.build-override] strip = false` fixes build-only artifacts without disabling security controls. See https://github.com/rust-lang/rust/issues/157750 .

## Windows next

React, the fixed schema, stdio MCP and portable-pty core are portable. Still required: install/sign in to Claude Code interactively; port the existing Alnair Python launcher/lab's Mac paths and Keychain assumptions; implement Windows launcher/Python discovery and vault opening; add Mica/Acrylic in the platform shim; install WebView2/MSVC; produce and test an MSI/NSIS package; verify PTY resize, Unicode, Ctrl-C, process-tree cleanup and credential environment filtering on the laptop. Windows has not been built or tested. No repository was pushed or created remotely.
