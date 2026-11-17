import React, { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { motion, AnimatePresence } from "motion/react";
import {
  Sparkles,
  Command,
  Plus,
  History,
  BookOpen,
  Search,
  PanelRight,
  ArrowUpRight,
  ChevronDown,
  ShieldCheck,
  Orbit,
  Layers,
  Square,
  Trash2,
} from "lucide-react";
import { ComponentView } from "./ComponentView";
import { gallery } from "./demo";
import { validate, type Spec } from "./spec";
import { TerminalPane, native, type TerminalHandle } from "./TerminalPane";
export default function App() {
  const query = new URLSearchParams(location.search),
    demo = query.has("demo");
  const [items, setItems] = useState<Spec[]>(demo ? gallery : []),
    [model, setModel] = useState("auto"),
    [status, setStatus] = useState(
      demo ? "Design preview · no model connected" : "Ready when you are",
    ),
    [palette, setPalette] = useState(false),
    [search, setSearch] = useState(""),
    [ratio, setRatio] = useState(45),
    [active, setActive] = useState(false),
    [busy, setBusy] = useState(false),
    [showGallery, setGallery] = useState(demo),
    [answerError, setAnswerError] = useState(""),
    [route, setRoute] = useState(""),
    [health, setHealth] = useState({ memory: false, research: false });
  const term = useRef<TerminalHandle>(null),
    workspace = useRef<HTMLDivElement>(null),
    revision = useRef(-1);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
      if (e.key === "Escape") setPalette(false);
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  useEffect(() => {
    if (!native() || showGallery) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await invoke<{ revision: number; components: unknown[] }>(
          "canvas_snapshot",
        );
        if (!cancelled && data.revision !== revision.current) {
          revision.current = data.revision;
          setItems(data.components.map(validate));
        }
      } catch (e) {
        if (!cancelled) setStatus(`Canvas unavailable: ${String(e)}`);
      }
    };
    poll();
    const id = setInterval(poll, 500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [showGallery]);
  useEffect(() => {
    if (!native() || !active) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await invoke<{
          active: boolean;
          route: string | null;
          memory: boolean;
          research: boolean;
        }>("session_status");
        if (!cancelled) {
          setRoute(data.route || "");
          setHealth({ memory: data.memory, research: data.research });
          if (!data.active) {
            setActive(false);
            setStatus("Session ended · start or resume from ⌘K");
          }
        }
      } catch {}
    };
    poll();
    const timer = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [active]);
  const terminalStatus = (message: string) => {
    setStatus(message);
    if (message.startsWith("Session ended")) setActive(false);
  };
  const start = async (resume = false) => {
    setPalette(false);
    if (active && !confirm("Stop the current session and start another?"))
      return;
    setBusy(true);
    try {
      setGallery(false);
      revision.current = -1;
      await term.current?.start(model, resume);
      setActive(true);
    } catch (e) {
      setStatus(String(e));
      setActive(false);
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    if (active && !confirm("Stop this interactive session?")) return;
    await term.current?.stop();
    setActive(false);
  };
  const openNote = async () => {
    setPalette(false);
    try {
      if (!native())
        throw Error("Vault notes open from the installed Mac app.");
      await invoke("open_vault_note");
    } catch (e) {
      setStatus(String(e));
    }
  };
  const answer = async (id: string, value: Record<string, string>) => {
    setAnswerError("");
    try {
      if (showGallery) {
        setStatus("Preview answer captured locally · not sent to a model");
        return;
      }
      await invoke("submit_answer", { id, answer: value });
    } catch (e) {
      setAnswerError(String(e));
      throw e;
    }
  };
  const galleryAction = () => {
    setPalette(false);
    setGallery((v) => {
      setItems(!v ? gallery : []);
      revision.current = -1;
      return !v;
    });
  };
  const commands = [
    {
      name: "New interactive session",
      detail: "Current model selection",
      icon: Plus,
      action: () => start(),
    },
    {
      name: "Resume latest session",
      detail: "Alnair-owned history",
      icon: History,
      action: () => start(true),
    },
    ...["auto", "sonnet", "opus", "fable"].map((m) => ({
      name: `Model: ${m === "fable" ? "Fable 5.2" : m[0].toUpperCase() + m.slice(1)}`,
      detail: "Used for the next session",
      icon: Orbit,
      action: () => {
        setModel(m);
        setPalette(false);
      },
    })),
    {
      name: "Open vault note",
      detail: "Current session · Obsidian",
      icon: BookOpen,
      action: openNote,
    },
    {
      name: "Component gallery",
      detail: "Local examples · no model call",
      icon: Layers,
      action: galleryAction,
    },
  ];
  const drag = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const el = e.currentTarget;
    const move = (event: PointerEvent) => {
      const rect = workspace.current!.getBoundingClientRect();
      setRatio(
        Math.max(
          30,
          Math.min(68, (100 * (event.clientX - rect.left)) / rect.width),
        ),
      );
    };
    const end = () => {
      el.removeEventListener("pointermove", move as EventListener);
      el.removeEventListener("pointerup", end);
    };
    el.addEventListener("pointermove", move as EventListener);
    el.addEventListener("pointerup", end, { once: true });
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="traffic-space" data-tauri-drag-region />
        <div className="brand">
          <div className="star-mark">✦</div>
          <div>
            Alnair<small>A BRIGHTER WORKSPACE</small>
          </div>
        </div>
        <button className="new-session" onClick={() => start()} disabled={busy}>
          <Plus size={16} />
          New session<span>⌘ K</span>
        </button>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <button className="selected" onClick={() => setPalette(true)}>
            <Sparkles size={16} />
            Conversation
            <span className="nav-dot" />
          </button>
          <button onClick={galleryAction}>
            <Layers size={16} />
            Component gallery
          </button>
          <button onClick={openNote}>
            <BookOpen size={16} />
            Vault note
            <ArrowUpRight size={12} />
          </button>
          <button onClick={() => start(true)} disabled={busy}>
            <History size={16} />
            Resume latest
          </button>
        </nav>
        <div className="sidebar-card">
          <div className="orbit-illustration">
            <i />
            <i />
            <b>✦</b>
            <em />
          </div>
          <div className="eyebrow">BEYOND THE CONTEXT WINDOW</div>
          <h4>
            Think further.
            <br />
            Remember more.
          </h4>
          <p>Your memory travels with the work, not the window.</p>
        </div>
        <div className="sidebar-bottom">
          <ShieldCheck size={17} />
          <div>
            Local by design<small>Interactive · no API wrapper</small>
          </div>
          <span className="mint">●</span>
        </div>
      </aside>
      <main>
        <header className="topbar" data-tauri-drag-region>
          <div className="breadcrumb">
            <span className="mini-star">✦</span>Workspace<span>/</span>
            <strong>
              {showGallery
                ? "The component observatory"
                : "A new constellation"}
            </strong>
          </div>
          <button className="palette-trigger" onClick={() => setPalette(true)}>
            <Search size={13} />
            <span>Jump to anything</span>
            <kbd>⌘ K</kbd>
          </button>
        </header>
        <section className="session-header">
          <div>
            <div className="eyebrow">
              {showGallery
                ? "EXPLORE THE POSSIBILITIES"
                : "YOUR NEXT GREAT THOUGHT"}
            </div>
            <h1>
              A little more space to think.<span>✦</span>
            </h1>
            <p>
              One conversation. Lasting memory. A canvas that follows your
              ideas.
            </p>
          </div>
          <div className="session-actions">
            <div className="model-picker">
              <Orbit size={15} />
              <select
                aria-label="Model for next session"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="auto">Auto routing</option>
                <option value="sonnet">Sonnet</option>
                <option value="opus">Opus · xhigh</option>
                <option value="fable">Fable 5.2 · xhigh</option>
              </select>
              <ChevronDown size={12} />
            </div>
            <div className="status-pills">
              <span>
                <i />
                Memory{" "}
                {active ? (health.memory ? "configured" : "starting") : "wired"}
              </span>
              <span>
                <i />
                Research{" "}
                {active
                  ? health.research
                    ? "configured"
                    : "starting"
                  : "wired"}
              </span>
            </div>
            {active && route && (
              <span className="route-badge">Harness route: {route}</span>
            )}
            <small className="model-hint">
              {active
                ? "Model switches are shown in the terminal"
                : "Model selection applies to the next session"}
            </small>
          </div>
        </section>
        <div className="workspace" ref={workspace}>
          <section className="terminal-pane" style={{ width: `${ratio}%` }}>
            <div className="pane-toolbar">
              <span>
                <Command size={14} />
                Conversation
              </span>
              <span className="tag">
                {demo ? "PREVIEW" : "INTERACTIVE PTY"}
              </span>
            </div>
            <TerminalPane ref={term} demo={demo} onStatus={terminalStatus} />
            <div className="terminal-bottom">
              <span className="tiny-dot" />
              {status}
              <div className="terminal-controls">
                {active ? (
                  <button onClick={stop} title="Stop session">
                    <Square size={12} />
                  </button>
                ) : (
                  <button onClick={() => start()} disabled={busy}>
                    {busy ? "Starting…" : "Start session"}
                    <ArrowUpRight size={12} />
                  </button>
                )}
              </div>
            </div>
          </section>
          <div
            className="splitter"
            role="separator"
            tabIndex={0}
            aria-label="Resize terminal and canvas"
            aria-valuenow={ratio}
            aria-valuemin={30}
            aria-valuemax={68}
            onPointerDown={drag}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") setRatio((r) => Math.max(30, r - 2));
              if (e.key === "ArrowRight") setRatio((r) => Math.min(68, r + 2));
            }}
          >
            <span />
          </div>
          <section className="canvas-pane">
            <div className="pane-toolbar">
              <span>
                <PanelRight size={15} />
                Canvas <span className="canvas-count">{items.length}</span>
              </span>
              <div>
                <span className="live-pill">
                  {showGallery ? "GALLERY" : "LIVE CANVAS"}
                </span>
                <button
                  title="Clear canvas"
                  aria-label="Clear canvas"
                  onClick={async () => {
                    if (native() && !showGallery) await invoke("clear_canvas");
                    setItems([]);
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
            <div className="canvas-scroll">
              {showGallery && (
                <div className="canvas-intro">
                  <span className="eyebrow">TASK-SHAPED INTERFACES</span>
                  <p>Not just an answer. A way to work with it.</p>
                </div>
              )}
              {answerError && (
                <div role="alert" className="error-banner">
                  {answerError}
                </div>
              )}
              <AnimatePresence initial={false}>
                {items.map((spec, i) => (
                  <motion.div
                    key={`${spec.id}-${JSON.stringify(spec)}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{
                      duration: 0.25,
                      delay: Math.min(i * 0.025, 0.15),
                    }}
                  >
                    <ComponentView spec={spec} onAnswer={answer} />
                  </motion.div>
                ))}
              </AnimatePresence>
              {!items.length && (
                <div className="empty-canvas">
                  <div className="constellation">
                    ✦<span>✧</span>
                    <i>✦</i>
                  </div>
                  <h2>Ideas deserve a canvas.</h2>
                  <p>
                    Ask Claude to visualize a comparison, map a plan, or help
                    you make a decision. The right interface appears here.
                  </p>
                  <button onClick={galleryAction}>
                    Explore the component gallery
                    <ArrowUpRight size={14} />
                  </button>
                  <small>Cards · charts · forms · code · and more</small>
                </div>
              )}
              <div className="canvas-footer">
                ✧ Designed by the task. Rendered safely by Alnair.
              </div>
            </div>
          </section>
        </div>
        <footer className="app-footer">
          <span>
            <ShieldCheck size={12} /> Your subscription. Your machine. Your
            memory.
          </span>
          <span>
            ALNAIR <b>α</b>
            <i />
            MAC PROTOTYPE
          </span>
        </footer>
      </main>
      <AnimatePresence>
        {palette && (
          <motion.div
            className="palette-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setPalette(false)}
          >
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-label="Command palette"
              className="command-palette"
              initial={{ y: -10, scale: 0.98 }}
              animate={{ y: 0, scale: 1 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="command-search">
                <Search size={18} />
                <input
                  autoFocus
                  placeholder="Where would you like to go?"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter")
                      commands
                        .find((c) =>
                          c.name.toLowerCase().includes(search.toLowerCase()),
                        )
                        ?.action();
                    if (e.key === "Tab") {
                      e.preventDefault();
                      const buttons = e.currentTarget
                        .closest("[role=dialog]")
                        ?.querySelectorAll("button");
                      buttons?.[0]?.focus();
                    }
                  }}
                />
                <kbd>ESC</kbd>
              </div>
              <div className="command-list">
                {commands
                  .filter((c) =>
                    c.name.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((c) => (
                    <button
                      key={c.name}
                      onClick={c.action}
                      onKeyDown={(e) => {
                        if (
                          e.key === "Tab" &&
                          !e.shiftKey &&
                          e.currentTarget ===
                            e.currentTarget.parentElement?.lastElementChild
                        ) {
                          e.preventDefault();
                          e.currentTarget
                            .closest("[role=dialog]")
                            ?.querySelector("input")
                            ?.focus();
                        }
                      }}
                    >
                      <c.icon size={17} />
                      <span>
                        {c.name}
                        <small>{c.detail}</small>
                      </span>
                      <ArrowUpRight size={13} />
                    </button>
                  ))}
              </div>
              <div className="command-foot">
                Make room for the next idea.<span>↵ to select</span>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
