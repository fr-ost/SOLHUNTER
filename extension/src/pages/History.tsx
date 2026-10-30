import { useState } from "react";
import { Badge, Banner, Card, EmptyState, Spinner, Stat, Tabs, statusTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { fmtDateTime, fmtPct, fmtPctPoints, timeAgo } from "../services/format";
import type { Json } from "../types/api";

const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function HistoryPage({ initialTab }: { initialTab?: string }): JSX.Element {
  const api = useApi();
  const [tab, setTab] = useState<"candidates" | "outcomes" | "alerts">(initialTab === "alerts" ? "alerts" : "candidates");
  const [status, setStatus] = useState("");
  const candidates = useAsync(() => api.historyCandidates({ status: status || undefined, limit: 100 }), [api, status], { enabled: api.configured && tab === "candidates" });
  const outcomes = useAsync(() => api.outcomeSummary(), [api], { enabled: api.configured && tab === "outcomes" });
  const alerts = useAsync(() => api.alertHistory({ limit: 100 }), [api], { enabled: api.configured && tab === "alerts" });
  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  return (
    <>
      <Card title="History">
        <Tabs value={tab} onChange={setTab} options={[{ id: "candidates", label: "Past candidates" }, { id: "outcomes", label: "Outcome stats" }, { id: "alerts", label: "Alerts" }]} />
        {tab === "candidates" ? (
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">candidates + watchlist</option>
            <option value="candidate">candidates only</option>
            <option value="watchlist">watchlist only</option>
          </select>
        ) : null}
      </Card>
      {tab === "candidates" && (
        <>
          {candidates.error ? <Banner kind="error">{errorMessage(candidates.error)}</Banner> : null}
          {candidates.loading && !candidates.data ? <Spinner /> : null}
          {candidates.data?.items.length === 0 ? <EmptyState>No historical candidates yet.</EmptyState> : null}
          <div className="list">
            {(candidates.data?.items ?? []).map((item) => {
              const o = item.outcome;
              return (
                <div key={item.id} className="item" onClick={() => navigate("candidates", item.mint)}>
                  <div className="title">
                    <span>
                      <strong>{item.symbol ?? "?"}</strong> <span className="faint small">{fmtDateTime(item.scored_at)}</span>
                    </span>
                    <span className="row">
                      <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                      <span className="score">{item.overall_score.toFixed(0)}</span>
                    </span>
                  </div>
                  <div className="meta">
                    {o && o.label_complete ? (
                      <>
                        <span className={(o.max_return_pct ?? 0) >= 10 ? "good" : ""}>max {fmtPctPoints(o.max_return_pct, 1, true)}</span>
                        <span className={(o.close_return_pct ?? 0) >= 0 ? "good" : "bad"}>close {fmtPctPoints(o.close_return_pct, 1, true)}</span>
                        <span>min {fmtPctPoints(o.min_return_pct, 1, true)}</span>
                        <span>{o.hit_10 ? "✓10" : "✗10"} {o.hit_15 ? "✓15" : "✗15"} {o.hit_20 ? "✓20" : "✗20"}</span>
                        {o.hit_stop ? <Badge tone="ok">stop hit</Badge> : null}
                        {o.rugged ? <Badge tone="bad">rugged</Badge> : null}
                        <span className="faint">{o.horizon_minutes}m horizon</span>
                      </>
                    ) : (
                      <span className="faint">outcome not labelled yet (needs {"~"}65 minutes after scoring)</span>
                    )}
                  </div>
                  <div className="meta">
                    {item.gpt_recommendation ? <span>GPT {item.gpt_recommendation.replace(/_/g, " ").toLowerCase()}</span> : null}
                    {item.whale_signal ? <span>{item.whale_signal.replace(/_/g, " ").toLowerCase()}</span> : null}
                    <span>{item.ml_is_experimental ? "ml experimental" : item.model_version}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
      {tab === "outcomes" && (
        <Card title="Realised outcomes of past candidates">
          {outcomes.error ? <Banner kind="error">{errorMessage(outcomes.error)}</Banner> : null}
          {outcomes.data ? (
            Object.entries(outcomes.data).filter(([k]) => k !== "note").length ? (
              <>
                {Object.entries(outcomes.data)
                  .filter(([k]) => k !== "note")
                  .map(([bucket, stats]) => {
                    const s = stats as Json;
                    return (
                      <div key={bucket} style={{ marginBottom: 8 }}>
                        <h3>
                          {bucket} · {str(s.count)} labelled
                        </h3>
                        <div className="grid-3">
                          <Stat label="hit +10%" value={fmtPct(num(s.hit_10_rate))} />
                          <Stat label="hit +15%" value={fmtPct(num(s.hit_15_rate))} />
                          <Stat label="hit +20%" value={fmtPct(num(s.hit_20_rate))} />
                          <Stat label="stop rate" value={fmtPct(num(s.stop_rate))} />
                          <Stat label="rug rate" value={fmtPct(num(s.rug_rate))} tone={(num(s.rug_rate) ?? 0) > 0.2 ? "bad" : undefined} />
                          <Stat label="avg close" value={fmtPctPoints(num(s.avg_close_return_pct), 1, true)} />
                        </div>
                      </div>
                    );
                  })}
                <p className="faint tiny">{str(outcomes.data.note)}</p>
              </>
            ) : (
              <EmptyState>No labelled outcomes yet. Outcomes are measured ~1 hour after each candidate snapshot.</EmptyState>
            )
          ) : (
            <Spinner />
          )}
        </Card>
      )}
      {tab === "alerts" && (
        <>
          {alerts.error ? <Banner kind="error">{errorMessage(alerts.error)}</Banner> : null}
          {alerts.data?.items.length === 0 ? <EmptyState>No alerts recorded.</EmptyState> : null}
          <div className="list">
            {(alerts.data?.items ?? []).map((a, i) => (
              <div key={i} className={`item${a.mint ? "" : " static"}`} onClick={() => a.mint && navigate("candidates", str(a.mint))}>
                <div className="title">
                  <span>{str(a.title)}</span>
                  <Badge tone={statusTone(str(a.status))}>{str(a.status)}</Badge>
                </div>
                <div className="meta">
                  <span>{str(a.alert_type)}</span>
                  <span>{str(a.channel)}</span>
                  <span>{timeAgo(str(a.created_at))}</span>
                  {a.urgent ? <Badge tone="bad">urgent</Badge> : null}
                  {a.error ? <span className="bad">{str(a.error).slice(0, 80)}</span> : null}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
