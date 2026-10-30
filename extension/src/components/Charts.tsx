import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtNum, fmtPrice, fmtTime, fmtUsd } from "../services/format";
import type { Snapshot } from "../types/api";
import { SourceBadge } from "./ui";

interface Point {
  t: number;
  label: string;
  price: number | null;
  mcap: number | null;
  liquidity: number | null;
  volume: number | null;
  buys: number | null;
  sells: number | null;
  holders: number | null;
}

const toPoints = (snapshots: Snapshot[]): Point[] =>
  snapshots
    .map((s) => ({
      t: Date.parse(s.captured_at),
      label: fmtTime(s.captured_at),
      price: s.price_usd ?? null,
      mcap: s.market_cap_usd ?? null,
      liquidity: s.liquidity_usd ?? null,
      volume: s.volume_sol_5m ?? null,
      buys: s.buys_5m ?? null,
      sells: s.sells_5m ?? null,
      holders: s.holder_count ?? null,
    }))
    .filter((p) => !Number.isNaN(p.t))
    .sort((a, b) => a.t - b.t);

const axisStyle = { fontSize: 9, fill: "#6b7890" };
const tooltipStyle = { background: "#121724", border: "1px solid #243047", borderRadius: 6, fontSize: 11 };

export function SnapshotCharts({ snapshots }: { snapshots: Snapshot[] }): JSX.Element {
  const data = toPoints(snapshots);
  if (data.length < 2) return <div className="empty">Not enough snapshots yet for charts ({data.length} point{data.length === 1 ? "" : "s"}).</div>;
  return (
    <div className="list">
      <div>
        <div className="chart-title">
          <span className="small muted">Price (USD)</span>
          <SourceBadge kind="estimate" label="stream / dexscreener" />
        </div>
        <div className="chart">
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1a2132" vertical={false} />
              <XAxis dataKey="label" tick={axisStyle} minTickGap={30} />
              <YAxis tick={axisStyle} width={54} tickFormatter={(v: number) => fmtPrice(v).replace("$", "")} domain={["auto", "auto"]} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => fmtPrice(typeof v === "number" ? v : null)} />
              <Line type="monotone" dataKey="price" stroke="#48d6ff" dot={false} strokeWidth={1.6} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div>
        <div className="chart-title">
          <span className="small muted">Volume 5m (SOL) · buys vs sells</span>
          <SourceBadge kind="verified" label="stream" />
        </div>
        <div className="chart">
          <ResponsiveContainer>
            <BarChart data={data} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1a2132" vertical={false} />
              <XAxis dataKey="label" tick={axisStyle} minTickGap={30} />
              <YAxis tick={axisStyle} width={40} tickFormatter={(v: number) => fmtNum(v, 1)} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="buys" stackId="t" fill="#3ddc97" name="buys" />
              <Bar dataKey="sells" stackId="t" fill="#ff6b6b" name="sells" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div>
        <div className="chart-title">
          <span className="small muted">Liquidity (USD) &amp; market cap</span>
          <SourceBadge kind="estimate" />
        </div>
        <div className="chart">
          <ResponsiveContainer>
            <AreaChart data={data} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1a2132" vertical={false} />
              <XAxis dataKey="label" tick={axisStyle} minTickGap={30} />
              <YAxis tick={axisStyle} width={44} tickFormatter={(v: number) => fmtUsd(v)} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => fmtUsd(typeof v === "number" ? v : null)} />
              <Area type="monotone" dataKey="liquidity" stroke="#f5c451" fill="rgba(245,196,81,0.15)" name="liquidity" connectNulls />
              <Area type="monotone" dataKey="mcap" stroke="#8b7cff" fill="rgba(139,124,255,0.12)" name="market cap" connectNulls />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export function ScoreHistoryChart({ points }: { points: { scored_at: string; overall_score: number }[] }): JSX.Element | null {
  if (points.length < 2) return null;
  const data = points.map((p) => ({ label: fmtTime(p.scored_at), score: p.overall_score }));
  return (
    <div className="chart" style={{ height: 90 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
          <XAxis dataKey="label" tick={axisStyle} minTickGap={30} />
          <YAxis tick={axisStyle} width={28} domain={[0, 100]} />
          <Tooltip contentStyle={tooltipStyle} />
          <Line type="monotone" dataKey="score" stroke="#3ddc97" dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
