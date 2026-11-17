import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, ArrowUpRight, Send } from "lucide-react";
import type { Spec } from "./spec";
type Props = {
  spec: Spec;
  onAnswer: (
    id: string,
    answer: Record<string, string>,
  ) => void | Promise<void>;
};
export function ComponentView({ spec: s, onAnswer }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const answer = async (value: Record<string, string>) => {
    if (sent || sending) return;
    setSending(true);
    setError("");
    try {
      await onAnswer(s.id, value);
      setSent(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  };
  let content: React.ReactNode;
  switch (s.type) {
    case "card":
      content = (
        <>
          <div className="eyebrow">{s.badge || "INSIGHT"}</div>
          <div className="insight">
            <div>
              <h2>{s.title}</h2>
              <p>{s.body}</p>
            </div>
            {s.metric && <strong className="metric">{s.metric}</strong>}
          </div>
        </>
      );
      break;
    case "table":
      content = (
        <>
          <h3>{s.title}</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {s.columns.map((v, i) => (
                    <th key={i}>{v}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((v, j) => (
                      <td key={j}>{v}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      );
      break;
    case "chart": {
      const values = s.points.map((p) => p.value);
      const low = Math.min(0, ...values),
        high = Math.max(1, ...values),
        range = high - low;
      const x = (i: number) =>
          30 + (i * 440) / Math.max(1, s.points.length - 1),
        y = (v: number) => 150 - ((v - low) / range) * 120;
      const baseline = y(0);
      content = (
        <>
          <h3>{s.title}</h3>
          <svg
            className="chart"
            viewBox="0 0 500 190"
            role="img"
            aria-label={`${s.kind} chart: ${s.title}`}
          >
            <defs>
              <linearGradient id={`fill-${s.id}`} x1="0" y1="0" x2="0" y2="1">
                <stop stopColor="#a88bfa" stopOpacity=".35" />
                <stop offset="1" stopColor="#a88bfa" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[30, 70, 110, 150].map((y) => (
              <line key={y} x1="25" y1={y} x2="480" y2={y} stroke="#aca0e018" />
            ))}
            {s.kind === "line" ? (
              <>
                <path
                  d={`M ${x(0)} ${baseline} ${s.points.map((p, i) => `L ${x(i)} ${y(p.value)}`).join(" ")} L ${x(s.points.length - 1)} ${baseline} Z`}
                  fill={`url(#fill-${s.id})`}
                />
                <polyline
                  points={s.points
                    .map((p, i) => `${x(i)},${y(p.value)}`)
                    .join(" ")}
                  fill="none"
                  stroke="#b7a0ff"
                  strokeWidth="3"
                />
                {s.points.map((p, i) => (
                  <circle
                    key={i}
                    cx={x(i)}
                    cy={y(p.value)}
                    r="4"
                    fill="#ded1ff"
                  />
                ))}
              </>
            ) : (
              s.points.map((p, i) => {
                const w = 440 / s.points.length;
                return (
                  <rect
                    key={i}
                    x={30 + i * w}
                    y={Math.min(y(p.value), baseline)}
                    width={Math.max(1, w - 16)}
                    height={Math.max(1, Math.abs(baseline - y(p.value)))}
                    rx="5"
                    fill={i % 2 ? "#58c8bd" : "#ab8cf5"}
                  />
                );
              })
            )}
            {s.points.map((p, i) => (
              <text
                key={i}
                x={
                  s.kind === "line"
                    ? x(i)
                    : 30 + ((i + 0.35) * 440) / s.points.length
                }
                y="177"
                textAnchor="middle"
                fill="#8e8baa"
                fontSize="11"
              >
                {p.label}
              </text>
            ))}
          </svg>
        </>
      );
      break;
    }
    case "diff":
      content = (
        <>
          <h3>{s.title}</h3>
          <div className="diff">
            <pre className="removed">
              {s.before.split("\n").map((l, i) => (
                <div key={i}>− {l}</div>
              ))}
            </pre>
            <pre className="added">
              {s.after.split("\n").map((l, i) => (
                <div key={i}>+ {l}</div>
              ))}
            </pre>
          </div>
        </>
      );
      break;
    case "code":
      content = (
        <>
          <div className="component-heading">
            <h3>{s.title}</h3>
            <span className="tag">{s.language || "code"}</span>
          </div>
          <pre className="code">
            <code>{s.text}</code>
          </pre>
        </>
      );
      break;
    case "checklist": {
      const progress =
        s.progress ??
        Math.round(
          (100 * s.items.filter((i) => i.done).length) /
            Math.max(1, s.items.length),
        );
      content = (
        <>
          <div className="component-heading">
            <h3>{s.title}</h3>
            <span className="mint">{progress}%</span>
          </div>
          <div
            className="progress"
            role="progressbar"
            aria-valuenow={progress}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div style={{ width: `${progress}%` }} />
          </div>
          <ul className="checklist">
            {s.items.map((i, j) => (
              <li key={j}>
                <span className={i.done ? "check done" : "check"}>
                  {i.done && <Check size={12} />}
                </span>
                {i.label}
              </li>
            ))}
          </ul>
        </>
      );
      break;
    }
    case "choices":
      content = (
        <>
          <h3>{s.title}</h3>
          <div className="choices">
            {s.options.map((o, i) => (
              <button
                disabled={sent || sending}
                onClick={() => answer({ choice: o })}
                key={i}
              >
                {o}
                <ArrowUpRight size={14} />
              </button>
            ))}
          </div>
          {sent && <small className="mint">Answer sent to the session</small>}
        </>
      );
      break;
    case "form":
      content = (
        <>
          <h3>{s.title}</h3>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              answer(
                Object.fromEntries(
                  new FormData(e.currentTarget).entries(),
                ) as Record<string, string>,
              );
            }}
          >
            {s.fields.map((f) => (
              <label key={f.name} htmlFor={`${s.id}-${f.name}`}>
                {f.label}
                {f.kind === "select" ? (
                  <select
                    id={`${s.id}-${f.name}`}
                    name={f.name}
                    required={f.required}
                    disabled={sent || sending}
                  >
                    {f.options?.map((o, i) => (
                      <option key={i}>{o}</option>
                    ))}
                  </select>
                ) : f.kind === "textarea" ? (
                  <textarea
                    id={`${s.id}-${f.name}`}
                    name={f.name}
                    required={f.required}
                    disabled={sent || sending}
                  />
                ) : (
                  <input
                    id={`${s.id}-${f.name}`}
                    name={f.name}
                    required={f.required}
                    disabled={sent || sending}
                    value={values[f.name] || ""}
                    onChange={(e) =>
                      setValues({ ...values, [f.name]: e.target.value })
                    }
                  />
                )}
              </label>
            ))}
            <button
              className="primary"
              type="submit"
              disabled={sent || sending}
            >
              <Send size={13} />
              {sent ? "Answer sent" : "Send answer"}
            </button>
          </form>
        </>
      );
      break;
    case "markdown":
      content = (
        <div className="markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            skipHtml
            components={{
              a: ({ children }) => <span className="mint">{children}</span>,
              img: () => null,
            }}
          >
            {s.text}
          </ReactMarkdown>
        </div>
      );
      break;
    case "image":
      content = (
        <figure>
          <div className="image-well">
            <img src={s.src} alt={s.alt} />
            <span>✦</span>
          </div>
          {s.caption && <figcaption>{s.caption}</figcaption>}
        </figure>
      );
      break;
  }
  return (
    <article
      className={`component component-${s.type}`}
      data-testid={`component-${s.id}`}
    >
      {content}
      {error && (
        <div role="alert" className="error-banner">
          {error}
        </div>
      )}
    </article>
  );
}
