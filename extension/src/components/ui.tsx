import type { ReactNode } from "react";
import { scoreTone } from "../services/format";

export type SourceKind = "verified" | "estimate" | "model" | "gpt";

const SOURCE_LABEL: Record<SourceKind, string> = { verified: "verified", estimate: "estimate", model: "model", gpt: "gpt" };
const SOURCE_TITLE: Record<SourceKind, string> = {
  verified: "Verified fact observed on-chain / from the real-time trade stream",
  estimate: "Estimate derived from provider data, reserve maths or stream approximations - not verified",
  model: "Machine-learning prediction - a probability estimate, never a guarantee",
  gpt: "GPT research analysis - model-generated interpretation of the provided data",
};

/** Visual distinction between verified facts, estimates and model predictions. */
export function SourceBadge({ kind, label }: { kind: SourceKind; label?: string }): JSX.Element {
  return (
    <span className={`source ${kind}`} title={SOURCE_TITLE[kind]}>
      {label ?? SOURCE_LABEL[kind]}
    </span>
  );
}

export function Badge({ tone = "", children, title }: { tone?: "good" | "ok" | "bad" | "info" | "purple" | ""; children: ReactNode; title?: string }): JSX.Element {
  return (
    <span className={`badge ${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Card({ title, right, children, tight }: { title?: ReactNode; right?: ReactNode; children: ReactNode; tight?: boolean }): JSX.Element {
  return (
    <section className={`card${tight ? " tight" : ""}`}>
      {(title || right) && (
        <div className="card-header">
          {title ? <h2>{title}</h2> : <span />}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "ok" | "bad" }): JSX.Element {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }): JSX.Element {
  return <div className="empty">{children}</div>;
}

export function Spinner(): JSX.Element {
  return <span className="spinner" aria-label="loading" />;
}

export function Banner({ kind, children }: { kind: "error" | "warn" | "info"; children: ReactNode }): JSX.Element {
  return <div className={`banner ${kind}`}>{children}</div>;
}

export function ScoreBar({ score }: { score: number }): JSX.Element {
  const tone = scoreTone(score);
  const color = tone === "good" ? "var(--good)" : tone === "ok" ? "var(--ok)" : "var(--bad)";
  return (
    <div className="scorebar" title={`${score.toFixed(1)} / 100`}>
      <div style={{ width: `${Math.max(2, Math.min(100, score))}%`, background: color }} />
    </div>
  );
}

export function Score({ value }: { value: number }): JSX.Element {
  return <span className={`score ${scoreTone(value)}`}>{value.toFixed(0)}</span>;
}

export function Address({ value, chars = 4 }: { value: string | null | undefined; chars?: number }): JSX.Element {
  if (!value) return <span className="faint">n/a</span>;
  const short = value.length > chars * 2 + 1 ? `${value.slice(0, chars)}…${value.slice(-chars)}` : value;
  return (
    <code className="copy" title={`${value} (click to copy)`} onClick={() => void navigator.clipboard?.writeText(value)}>
      {short}
    </code>
  );
}

export function KV({ rows }: { rows: [string, ReactNode][] }): JSX.Element {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: { id: T; label: string }[]; onChange: (v: T) => void }): JSX.Element {
  return (
    <div className="tabs">
      {options.map((o) => (
        <button key={o.id} className={o.id === value ? "active" : ""} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function statusTone(status: string | null | undefined): "good" | "ok" | "bad" | "" {
  switch ((status ?? "").toLowerCase()) {
    case "candidate":
    case "connected":
    case "running":
    case "ok":
    case "ready":
    case "live":
    case "sent":
      return "good";
    case "watchlist":
    case "degraded":
    case "standby":
    case "stale":
    case "connecting":
    case "suppressed":
      return "ok";
    case "rejected":
    case "down":
    case "offline":
    case "dead":
    case "failed":
    case "error":
      return "bad";
    default:
      return "";
  }
}

export function whaleTone(signal: string | null | undefined): "good" | "ok" | "bad" | "" {
  if (!signal) return "";
  if (signal.includes("ACCUMULATION") || signal.includes("CONSENSUS")) return "good";
  if (signal.includes("DISTRIBUTION") || signal.includes("EXIT") || signal.includes("INSIDER") || signal.includes("COORDINATED")) return "bad";
  return "";
}

export function gptTone(rec: string | null | undefined): "good" | "ok" | "bad" | "" {
  switch (rec) {
    case "STRONG_CANDIDATE":
      return "good";
    case "WATCHLIST":
    case "INSUFFICIENT_DATA":
      return "ok";
    case "HIGH_RISK":
    case "REJECT":
      return "bad";
    default:
      return "";
  }
}
