export const fmtUsd = (value: number | null | undefined, digits = 0): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `$${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1_000_000) return `$${(value / 1e6).toFixed(2)}M`;
  if (abs >= 10_000) return `$${(value / 1e3).toFixed(1)}k`;
  if (abs >= 1) return `$${value.toFixed(Math.max(digits, abs < 100 ? 2 : 0))}`;
  if (abs === 0) return "$0";
  return `$${value.toPrecision(3)}`;
};

export const fmtPrice = (value: number | null | undefined): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (value >= 1) return `$${value.toFixed(4)}`;
  if (value === 0) return "$0";
  const exp = Math.floor(Math.log10(value));
  return `$${value.toFixed(Math.min(12, Math.max(4, -exp + 3)))}`;
};

export const fmtNum = (value: number | null | undefined, digits = 0): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 10_000) return `${(value / 1e3).toFixed(1)}k`;
  return value.toFixed(digits);
};

/** value is a fraction 0..1 */
export const fmtPct = (value: number | null | undefined, digits = 0): string =>
  value === null || value === undefined || Number.isNaN(value) ? "n/a" : `${(value * 100).toFixed(digits)}%`;

/** value is already in percent units */
export const fmtPctPoints = (value: number | null | undefined, digits = 1, signed = false): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
};

export const fmtSol = (value: number | null | undefined, digits = 2): string =>
  value === null || value === undefined || Number.isNaN(value) ? "n/a" : `${value.toFixed(digits)} SOL`;

export const fmtAge = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) return "n/a";
  const m = Math.floor(seconds / 60);
  if (m < 1) return `${Math.floor(seconds)}s`;
  if (m < 120) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h${String(m % 60).padStart(2, "0")}`;
  return `${Math.floor(h / 24)}d`;
};

export const timeAgo = (iso: string | null | undefined): string => {
  if (!iso) return "n/a";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "n/a";
  const diff = Math.max(0, (Date.now() - t) / 1000);
  return `${fmtAge(diff)} ago`;
};

export const fmtTime = (iso: string | null | undefined): string => {
  if (!iso) return "n/a";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "n/a" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export const fmtDateTime = (iso: string | null | undefined): string => {
  if (!iso) return "n/a";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "n/a" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

export const shortAddr = (addr: string | null | undefined, n = 4): string => (!addr ? "n/a" : addr.length > n * 2 + 1 ? `${addr.slice(0, n)}…${addr.slice(-n)}` : addr);

export const ageSecondsFromLaunch = (launch: string | null | undefined): number | null => {
  if (!launch) return null;
  const t = Date.parse(launch);
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000);
};

export const scoreTone = (score: number): "good" | "ok" | "bad" => (score >= 70 ? "good" : score >= 52 ? "ok" : "bad");
