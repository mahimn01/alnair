import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "@xterm/xterm/css/xterm.css";
export const native = () =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
export type TerminalHandle = {
  start: (model: string, resume: boolean) => Promise<void>;
  stop: () => Promise<void>;
};
export const TerminalPane = forwardRef<
  TerminalHandle,
  { demo: boolean; onStatus: (status: string) => void }
>(function TerminalPane({ demo, onStatus }, ref) {
  const host = useRef<HTMLDivElement>(null),
    term = useRef<Terminal | null>(null),
    fit = useRef<FitAddon | null>(null),
    session = useRef<string | null>(null),
    buffer = useRef<Map<string, number[][]>>(new Map());
  const ready = useRef<Promise<void>>(Promise.resolve());
  useImperativeHandle(ref, () => ({
    start: async (model, resume) => {
      if (!native())
        throw Error("This browser is a preview. Start sessions in Alnair.app.");
      await ready.current;
      session.current = null;
      buffer.current.clear();
      term.current?.reset();
      onStatus("Starting interactive session…");
      const result = await invoke<{ id: string }>("start_session", {
        model,
        resume,
        cols: term.current?.cols || 80,
        rows: term.current?.rows || 24,
      });
      session.current = result.id;
      buffer.current
        .get(result.id)
        ?.forEach((bytes) => term.current?.write(new Uint8Array(bytes)));
      buffer.current.clear();
      term.current?.focus();
      onStatus("Interactive session · subscription login");
    },
    stop: async () => {
      if (native()) await invoke("stop_session");
      session.current = null;
      term.current?.writeln(
        "\r\n\x1b[38;2;178;147;255mSession stopped.\x1b[0m",
      );
      onStatus("Ready when you are");
    },
  }));
  useEffect(() => {
    if (!host.current) return;
    const terminal = new Terminal({
      fontFamily: '"SFMono-Regular", "Cascadia Code", Menlo, monospace',
      fontSize: 13,
      lineHeight: 1.5,
      cursorBlink: true,
      allowProposedApi: false,
      scrollback: 5000,
      theme: {
        background: "#10101e",
        foreground: "#d9d5ec",
        cursor: "#b89bff",
        selectionBackground: "#6852a966",
        black: "#151425",
        red: "#ef819b",
        green: "#74d5bd",
        yellow: "#ebca85",
        blue: "#8fa9ff",
        magenta: "#c59cff",
        cyan: "#74d4df",
        white: "#e8e3f5",
        brightBlack: "#77738f",
        brightRed: "#ff9ab1",
        brightGreen: "#a4e8d5",
        brightYellow: "#f4deb4",
        brightBlue: "#becbff",
        brightMagenta: "#e5c6ff",
        brightCyan: "#aff4f7",
        brightWhite: "#ffffff",
      },
    });
    const fitting = new FitAddon();
    terminal.loadAddon(fitting);
    terminal.open(host.current);
    term.current = terminal;
    fit.current = fitting;
    fitting.fit();
    const disposers: (() => void)[] = [];
    ready.current = (async () => {
      if (native()) {
        disposers.push(
          await listen<{ id: string; bytes: number[] }>(
            "pty-output",
            ({ payload: p }) => {
              if (session.current === p.id)
                terminal.write(new Uint8Array(p.bytes));
              else if (session.current === null) {
                const chunks = buffer.current.get(p.id) || [];
                if (chunks.length < 128) chunks.push(p.bytes);
                buffer.current.set(p.id, chunks);
              }
            },
          ),
        );
        disposers.push(
          await listen<{ id: string }>("pty-exit", ({ payload: p }) => {
            if (p.id === session.current)
              onStatus("Session ended · start or resume from ⌘K");
          }),
        );
      }
    })();
    terminal.writeln("\x1b[38;2;186;156;255m  ✦  A L N A I R\x1b[0m\r\n");
    if (demo) {
      terminal.writeln(
        "\x1b[38;2;119;213;192m  Interactive terminal · design preview\x1b[0m\r\n",
      );
      terminal.writeln(
        "  \x1b[38;2;139;135;164m>\x1b[0m Help me explore the architecture of Alnair.\r\n",
      );
      terminal.writeln(
        "  \x1b[38;2;197;156;255m✦\x1b[0m I’ll keep the conversation here, and shape the",
      );
      terminal.writeln("    canvas around what we’re working on.\r\n");
      terminal.writeln(
        "    \x1b[38;2;119;213;192m✓\x1b[0m Verified memory, connected",
      );
      terminal.writeln(
        "    \x1b[38;2;119;213;192m✓\x1b[0m Research tools, ready",
      );
      terminal.writeln(
        "    \x1b[38;2;119;213;192m✓\x1b[0m Canvas, waiting for the next idea\r\n",
      );
      terminal.writeln(
        "  \x1b[38;2;145;142;169mThe window is a cache. Memory is a constellation.\x1b[0m\r\n",
      );
      terminal.writeln(
        "  \x1b[38;2;91;88;114m─────────────────────────────────────────────────\x1b[0m",
      );
      terminal.writeln(
        "  \x1b[38;2;145;142;169mMock output · no Claude session or model call\x1b[0m",
      );
    } else {
      terminal.writeln(
        "  A real Claude Code session. Your Alnair harness.\r\n",
      );
      terminal.writeln("  Choose Start session or press ⌘K.\r\n");
      terminal.writeln("  No API keys. No headless execution.");
      terminal.writeln("  Approve any trust or permission prompt yourself.");
    }
    const input = terminal.onData((data) => {
      if (native() && session.current)
        invoke("pty_write", { data }).catch((e) => onStatus(String(e)));
    });
    const ro = new ResizeObserver(() => {
      try {
        fitting.fit();
        if (native())
          invoke("pty_resize", {
            cols: terminal.cols,
            rows: terminal.rows,
          }).catch(() => {});
      } catch {}
    });
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      input.dispose();
      ready.current.then(() => disposers.forEach((f) => f()));
      terminal.dispose();
      term.current = null;
    };
  }, [demo]);
  return (
    <div
      className="terminal-host"
      ref={host}
      aria-label="Interactive Claude terminal"
    />
  );
});
