import { useState } from "react";
import { ScoreHistoryChart, SnapshotCharts } from "../components/Charts";
import { Address, Badge, Banner, Card, EmptyState, KV, Score, ScoreBar, SourceBadge, Spinner, Stat, Tabs, gptTone, statusTone, whaleTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useDebouncedLiveVersion } from "../hooks/useLive";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { fmtAge, fmtDateTime, fmtNum, fmtPct, fmtPctPoints, fmtPrice, fmtSol, fmtUsd, timeAgo } from "../services/format";
import type { CandidateDetail, Json } from "../types/api";

type Tab = "overview" | "charts" | "holders" | "whales" | "ml" | "gpt" | "exec" | "filters" | "history";

const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);

export function CandidateDetailPage({ mint }: { mint: string }): JSX.Element {
  const api = useApi();
  const version = useDebouncedLiveVersion(5000);
  const [tab, setTab] = useState<Tab>("overview");
  const { data, error, loading } = useAsync(() => api.candidate(mint), [api, mint, version], { refreshMs: 45_000, enabled: api.configured });
  if (error) return <Banner kind="error">{errorMessage(error)}</Banner>;
  if (!data) return <div className="row"><Spinner /> <span className="muted">Loading token…</span></div>;
  const d: CandidateDetail = data;
  const score = d.latest_score;
  const snap = d.snapshots.length ? d.snapshots[d.snapshots.length - 1] : null;
  const ws = d.whale_summary;
  const risk = d.risk_events;
  return (
    <>
      <Card
        title={
          <span>
            {d.token.symbol ?? "?"} <span className="muted small">{d.token.name ?? ""}</span>
          </span>
        }
        right={
          <span className="row">
            <Badge tone={statusTone(d.token.status)}>{d.token.status}</Badge>
            {score ? <Score value={score.overall_score} /> : null}
            {loading ? <Spinner /> : null}
          </span>
        }
      >
        <div className="row small">
          <Address value={d.token.mint} chars={6} />
          <a href={`https://dexscreener.com/solana/${d.token.mint}`} target="_blank" rel="noreferrer">
            DexScreener ↗
          </a>
          <a href={`https://pump.fun/coin/${d.token.mint}`} target="_blank" rel="noreferrer">
            pump.fun ↗
          </a>
        </div>
        {score ? <ScoreBar score={score.overall_score} /> : null}
        {score?.hard_fail ? <Banner kind="error">Hard safety failure: {(score.rejection_reasons ?? []).slice(0, 2).join("; ")}</Banner> : null}
        {risk.filter((r) => r.severity === "critical").slice(0, 2).map((r) => (
          <Banner key={r.id} kind="error">
            🚨 {r.title} <span className="faint">({timeAgo(r.occurred_at)})</span>
          </Banner>
        ))}
        <div className="grid-3" style={{ marginTop: 6 }}>
          <Stat label="Price" value={fmtPrice(snap?.price_usd)} sub={<SourceBadge kind={d.token.pool_kind === "pump" ? "verified" : "estimate"} label={d.token.pool_kind === "pump" ? "curve" : "dex"} />} />
          <Stat label="Mcap" value={fmtUsd(snap?.market_cap_usd)} sub={<SourceBadge kind="estimate" />} />
          <Stat label="Liquidity" value={fmtUsd(snap?.liquidity_usd)} sub={<SourceBadge kind="estimate" />} />
          <Stat label="Age" value={fmtAge(snap?.age_seconds ?? null)} sub={d.token.pool_kind} />
          <Stat label="Holders" value={snap?.holder_count ?? "n/a"} sub={<SourceBadge kind={str(snap?.data_quality && (snap.data_quality as Json).holder_count_source).startsWith("helius") ? "verified" : "estimate"} label={str(snap?.data_quality && (snap.data_quality as Json).holder_count_source) || "n/a"} />} />
          <Stat label="Buyers 5m" value={snap?.unique_buyers_5m ?? "n/a"} sub={<SourceBadge kind="verified" label="stream" />} />
        </div>
      </Card>
      <Tabs
        value={tab}
        onChange={setTab}
        options={[
          { id: "overview", label: "Overview" },
          { id: "charts", label: "Charts" },
          { id: "holders", label: "Holders & dev" },
          { id: "whales", label: "Whales" },
          { id: "ml", label: "ML" },
          { id: "gpt", label: "GPT" },
          { id: "exec", label: "Execution" },
          { id: "filters", label: "Filters" },
          { id: "history", label: "History" },
        ]}
      />
      {tab === "overview" && (
        <>
          <Card title="Ranking components">
            {score ? (
              <>
                <KV rows={Object.entries(score.components ?? {}).map(([k, v]) => [k, <span key={k}>{(v as number).toFixed(0)}</span>])} />
                {Object.keys(score.penalties ?? {}).length ? (
                  <div style={{ marginTop: 6 }}>
                    <h3>Penalties</h3>
                    <KV rows={Object.entries(score.penalties ?? {}).map(([k, v]) => [k, <span key={k} className="bad">-{(v as number).toFixed(0)}</span>])} />
                  </div>
                ) : null}
                <div className="row" style={{ marginTop: 6 }}>
                  <Badge>confidence {score.confidence}</Badge>
                  {score.whale_signal ? <Badge tone={whaleTone(score.whale_signal)}>{score.whale_signal.replace(/_/g, " ").toLowerCase()}</Badge> : null}
                  {score.gpt_recommendation ? <Badge tone={gptTone(score.gpt_recommendation)}>GPT {score.gpt_recommendation.replace(/_/g, " ").toLowerCase()}</Badge> : null}
                </div>
                {score.explanation?.length ? <ul className="clean small muted" style={{ marginTop: 6 }}>{score.explanation.map((e, i) => <li key={i}>{e}</li>)}</ul> : null}
              </>
            ) : (
              <EmptyState>Not evaluated yet (evaluation starts at the first snapshot).</EmptyState>
            )}
          </Card>
          <Card title="Risk warnings">
            {score?.rejection_reasons?.length || risk.length ? (
              <ul className="clean small">
                {(score?.rejection_reasons ?? []).map((r, i) => (
                  <li key={`r${i}`} className="ok">
                    {r}
                  </li>
                ))}
                {risk.slice(0, 8).map((r) => (
                  <li key={r.id} className={r.severity === "critical" ? "bad" : "ok"}>
                    {r.risk_type}: {r.title} <span className="faint">({timeAgo(r.occurred_at)})</span>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="muted small">No active warnings recorded.</span>
            )}
          </Card>
          <Card title="Score history">
            <ScoreHistoryChart points={d.score_history} /> {d.score_history.length < 2 ? <span className="faint small">single evaluation so far</span> : null}
          </Card>
        </>
      )}
      {tab === "charts" && (
        <Card title="Market snapshots">
          <SnapshotCharts snapshots={d.snapshots} />
        </Card>
      )}
      {tab === "holders" && (
        <>
          <Card title="Holder distribution" right={d.holders ? <SourceBadge kind={d.holders.source.startsWith("helius") ? "verified" : "estimate"} label={d.holders.source} /> : null}>
            {d.holders ? (
              <>
                <div className="grid-3">
                  <Stat label="Holders" value={d.holders.holder_count ?? "n/a"} sub={d.holders.is_complete ? "complete count" : "partial"} />
                  <Stat label="Top 10" value={fmtPct(d.holders.top10_pct)} sub="excl. pool/burn" />
                  <Stat label="Top 20" value={fmtPct(d.holders.top20_pct)} />
                  <Stat label="Largest" value={fmtPct(d.holders.largest_holder_pct)} />
                  <Stat label="Developer" value={fmtPct(d.holders.dev_holding_pct)} />
                  <Stat label="Infra excluded" value={fmtPct(d.holders.infra_excluded_pct)} sub="LP / curve / burn" />
                </div>
                {d.holders.top_holders?.length ? (
                  <div className="table-wrap" style={{ marginTop: 6 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>owner</th>
                          <th>label</th>
                          <th className="num">amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.holders.top_holders.slice(0, 20).map((h) => (
                          <tr key={h.token_account} style={{ cursor: h.owner ? "pointer" : "default" }} onClick={() => h.owner && navigate("wallets", h.owner)}>
                            <td>
                              <code>{h.owner ? `${h.owner.slice(0, 4)}…${h.owner.slice(-4)}` : "unknown owner"}</code>
                            </td>
                            <td>{h.is_infrastructure ? <Badge>{h.label ?? "infra"}</Badge> : h.label ? <Badge tone="ok">{h.label}</Badge> : null}</td>
                            <td className="num">{fmtNum(h.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <div className="faint tiny" style={{ marginTop: 4 }}>captured {timeAgo(d.holders.captured_at)}</div>
              </>
            ) : (
              <EmptyState>No holder snapshot yet (fetched from Helius once a token passes the cheap filters; stream estimates are used meanwhile).</EmptyState>
            )}
          </Card>
          <Card title="Developer">
            {d.creator ? (
              <>
                <KV
                  rows={[
                    ["creator", <Address key="c" value={d.creator.creator_wallet} chars={6} />],
                    ["wallet category", d.creator.profile ? `${d.creator.profile.category} (${fmtPct(d.creator.profile.category_confidence)})` : "no profile yet"],
                    ["prior launches seen", String(d.creator.prior_launches.length)],
                    ["initial buy", str((d.launches[0] as Json | undefined)?.initial_buy_sol) ? fmtSol(num((d.launches[0] as Json).initial_buy_sol)) : "n/a"],
                  ]}
                />
                {d.creator.prior_launches.length ? (
                  <div className="table-wrap" style={{ marginTop: 6 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>token</th>
                          <th>status</th>
                          <th>launched</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.creator.prior_launches.map((p) => (
                          <tr key={p.mint} style={{ cursor: "pointer" }} onClick={() => navigate("candidates", p.mint)}>
                            <td>{p.symbol ?? p.mint.slice(0, 6)}</td>
                            <td>
                              <Badge tone={statusTone(p.status)}>{p.status}</Badge>
                            </td>
                            <td>{fmtDateTime(p.launch_time)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="faint small" style={{ marginTop: 6 }}>No prior launches seen by this scanner (unknown history is not the same as a clean history).</p>
                )}
              </>
            ) : (
              <EmptyState>Creator unknown.</EmptyState>
            )}
          </Card>
        </>
      )}
      {tab === "whales" && (
        <>
          <Card title="Whale intelligence" right={ws ? <Badge>{ws.data_confidence} data confidence</Badge> : null}>
            {ws ? (
              <>
                <div className="row" style={{ marginBottom: 6 }}>
                  {ws.signals.map((s) => (
                    <Badge key={s} tone={whaleTone(s)}>
                      {s.replace(/_/g, " ").toLowerCase()}
                    </Badge>
                  ))}
                </div>
                <div className="grid-3">
                  <Stat label="Net 5m" value={fmtSol(ws.whale_net_flow_5m)} sub={`1m ${ws.whale_net_flow_1m.toFixed(2)} · 15m ${ws.whale_net_flow_15m.toFixed(2)} · 1h ${ws.whale_net_flow_1h.toFixed(2)}`} tone={ws.whale_net_flow_5m > 0 ? "good" : ws.whale_net_flow_5m < 0 ? "bad" : undefined} />
                  <Stat label="Smart money" value={`${ws.smart_money_buyers} buy / ${ws.smart_money_sellers} sell`} sub={`${ws.independent_profitable_buyers} independent`} />
                  <Stat label="Whales" value={`${ws.whales_holding} hold / ${ws.whales_distributing} dist.`} sub={`trend ${ws.accumulation_trend}`} />
                </div>
                {ws.hard_warning ? <Banner kind="error">{ws.hard_warning}</Banner> : null}
                {ws.notes.map((n, i) => (
                  <p key={i} className="faint small" style={{ marginTop: 4 }}>
                    {n}
                  </p>
                ))}
              </>
            ) : (
              <EmptyState>No whale assessment yet.</EmptyState>
            )}
          </Card>
          <Card title="Activity timeline">
            {ws?.activities.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>time</th>
                      <th>wallet</th>
                      <th>side</th>
                      <th className="num">SOL</th>
                      <th>perf</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ws.activities.map((a) => (
                      <tr key={a.signature + a.wallet} style={{ cursor: "pointer" }} onClick={() => navigate("wallets", a.wallet)}>
                        <td>{timeAgo(a.timestamp)}</td>
                        <td>
                          <code>{a.short}</code>
                          <div className="faint tiny">
                            {a.category.replace(/_/g, " ")} {a.confidence ? `(${fmtPct(a.confidence)})` : ""} {a.corroborated ? "· corroborated" : ""}
                          </div>
                        </td>
                        <td className={a.direction === "buy" ? "good" : "bad"}>{a.direction}</td>
                        <td className="num">
                          {a.sol_amount.toFixed(2)}
                          <div className="faint tiny">{a.usd_amount ? fmtUsd(a.usd_amount) : ""}</div>
                        </td>
                        <td className="tiny">
                          {num((a.performance as Json).win_rate) !== null ? `WR ${fmtPct(num((a.performance as Json).win_rate))}` : <span className="faint">no history</span>}
                          {num((a.performance as Json).realized_pnl_sol) !== null ? <div className="faint">{fmtSol(num((a.performance as Json).realized_pnl_sol))}</div> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState>No whale or smart-money trades detected on this token.</EmptyState>
            )}
          </Card>
          {d.wallet_profiles.length ? (
            <Card title="Wallet profiles">
              <div className="list">
                {d.wallet_profiles.map((p) => (
                  <div key={p.address} className="item" onClick={() => navigate("wallets", p.address)}>
                    <div className="title">
                      <code>{p.address.slice(0, 4)}…{p.address.slice(-4)}</code>
                      <Badge tone={p.category === "smart_money" || p.category === "profitable_early_buyer" ? "good" : p.category === "insider_associated" || p.category === "developer" ? "bad" : ""}>
                        {p.category.replace(/_/g, " ")} {p.category_confidence ? fmtPct(p.category_confidence) : ""}
                      </Badge>
                    </div>
                    <div className="meta">
                      <span>pnl {fmtSol(p.realized_pnl_sol)}</span>
                      <span>WR {fmtPct(p.win_rate)}</span>
                      <span>PF {p.profit_factor?.toFixed(2) ?? "n/a"}</span>
                      <span>{p.tokens_traded} tokens</span>
                      <span>rugs {p.rug_exposure_count}</span>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          ) : null}
        </>
      )}
      {tab === "ml" && (
        <Card title="ML predictions" right={<SourceBadge kind="model" />}>
          {score ? (
            <>
              {score.ml_is_experimental ? <Banner kind="warn">Experimental: the model has not been validated on enough historical data. Treat these numbers as rough indications, not probabilities you can rely on.</Banner> : null}
              <div className="grid-3" style={{ marginTop: 6 }}>
                <Stat label="Rug risk" value={fmtPct(score.rug_risk)} tone={score.rug_risk !== null && score.rug_risk > 0.45 ? "bad" : undefined} />
                <Stat label="P(+10%)" value={fmtPct(score.breakout_p10)} />
                <Stat label="P(+15%)" value={fmtPct(score.breakout_p15)} />
                <Stat label="P(+20%)" value={fmtPct(score.breakout_p20)} />
                <Stat label="Exp. return" value={fmtPctPoints(score.expected_return_pct, 1, true)} sub="net of fees/slippage" />
                <Stat label="Model" value={<span className="small">{score.model_version ?? "n/a"}</span>} />
              </div>
              {d.predictions.length ? (
                <div className="table-wrap" style={{ marginTop: 8 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>at</th>
                        <th className="num">rug</th>
                        <th className="num">p10</th>
                        <th className="num">downside</th>
                        <th className="num">P(stop)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.predictions.slice(0, 10).map((p, i) => (
                        <tr key={i}>
                          <td>{timeAgo(str(p.predicted_at))}</td>
                          <td className="num">{fmtPct(num(p.rug_risk))}</td>
                          <td className="num">{fmtPct(num(p.breakout_p10))}</td>
                          <td className="num">{fmtPctPoints(num(p.expected_downside_pct))}</td>
                          <td className="num">{fmtPct(num(p.p_stop_loss))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : (
            <EmptyState>No prediction yet.</EmptyState>
          )}
        </Card>
      )}
      {tab === "gpt" && (
        <Card title="GPT research analysis" right={<SourceBadge kind="gpt" />}>
          {d.gpt_analysis?.analysis ? (
            <GptView row={d.gpt_analysis.analysis} meta={d.gpt_analysis} />
          ) : (
            <EmptyState>No GPT analysis for this token. GPT only runs for candidates that pass the inexpensive filters and the spending limits.</EmptyState>
          )}
        </Card>
      )}
      {tab === "exec" && (
        <Card title="Execution estimates" right={<SourceBadge kind="estimate" label={d.execution?.source ?? "n/a"} />}>
          {d.execution ? (
            <>
              <p className="small muted">
                Venue {d.execution.venue} · fee {(d.execution.fee_bps / 100).toFixed(2)}% · max size within slippage limit {fmtUsd(d.execution.max_size_usd_within_slippage)}
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>size</th>
                      <th className="num">buy impact</th>
                      <th className="num">sell impact</th>
                      <th className="num">round-trip cost</th>
                      <th className="num">net @+10/+15/+20</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.execution.estimates.map((e) => (
                      <tr key={e.size_usd}>
                        <td>{fmtUsd(e.size_usd)}</td>
                        {e.available ? (
                          <>
                            <td className="num">{fmtPctPoints(e.buy_impact_pct, 2)}</td>
                            <td className="num">{fmtPctPoints(e.sell_impact_pct, 2)}</td>
                            <td className="num bad">{fmtPctPoints(e.round_trip_cost_pct, 2)}</td>
                            <td className="num">{["+10%", "+15%", "+20%"].map((t) => fmtPctPoints(e.targets[t]?.net_return_pct ?? null, 1, true)).join(" / ")}</td>
                          </>
                        ) : (
                          <td colSpan={4} className="faint">
                            unavailable: {e.reason}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {d.execution.notes.map((n, i) => (
                <p key={i} className="faint tiny" style={{ marginTop: 4 }}>
                  {n}
                </p>
              ))}
              <p className="disclaimer">{d.execution.disclaimer ?? "Estimates from reserve data; not quotes. Nothing is executed by GemFinder."}</p>
            </>
          ) : (
            <EmptyState>No execution estimate (reserves unknown).</EmptyState>
          )}
        </Card>
      )}
      {tab === "filters" && (
        <Card title="Filter stages">
          {d.filter_results ? (
            <>
              <div className="row" style={{ marginBottom: 6 }}>
                <Badge tone={d.filter_results.rejected ? "bad" : "good"}>{d.filter_results.rejected ? "rejected" : "passed safety"}</Badge>
                {d.filter_results.hard_fail ? <Badge tone="bad">hard fail</Badge> : null}
                {d.filter_results.quality_failures.length ? <Badge tone="ok">{d.filter_results.quality_failures.length} quality issue(s)</Badge> : null}
              </div>
              <div className="stages">
                {d.filter_results.stages.map((s) => (
                  <div key={s.stage} className="stage">
                    <span className={s.passed ? "good" : s.hard_fail ? "bad" : "ok"}>{s.passed ? "✓" : s.hard_fail ? "✕" : "!"}</span>
                    <div>
                      <div>
                        {s.name.replace(/_/g, " ")} {!s.data_available ? <span className="faint tiny">(no data)</span> : null} {!s.safety ? <span className="faint tiny">(quality)</span> : null}
                      </div>
                      {s.reasons.length ? <div className="reasons">{s.reasons.join(" · ")}</div> : null}
                    </div>
                    <span className="mono muted">{(s.score * 100).toFixed(0)}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <EmptyState>No filter results yet.</EmptyState>
          )}
        </Card>
      )}
      {tab === "history" && (
        <Card title="Status history">
          {d.history.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>when</th>
                    <th>event</th>
                    <th>from → to</th>
                    <th className="num">score</th>
                  </tr>
                </thead>
                <tbody>
                  {d.history.map((h) => (
                    <tr key={h.id}>
                      <td>{timeAgo(h.occurred_at)}</td>
                      <td>
                        {h.event}
                        {h.reason ? <div className="faint tiny">{h.reason}</div> : null}
                      </td>
                      <td>
                        {h.from_status ?? "–"} → {h.to_status ?? "–"}
                      </td>
                      <td className="num">{h.score?.toFixed(0) ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No history yet.</EmptyState>
          )}
        </Card>
      )}
    </>
  );
}

function GptView({ row, meta }: { row: Json; meta: { recommendation: string | null; created_at: string; model: string; estimated_cost_usd?: number | null } }): JSX.Element {
  const p = (row.probability_estimates as Json | undefined) ?? {};
  const scenarios = Array.isArray(row.target_scenarios) ? (row.target_scenarios as Json[]) : [];
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        <Badge tone={gptTone(meta.recommendation)}>{(meta.recommendation ?? "").replace(/_/g, " ").toLowerCase()}</Badge>
        <Badge>{str(row.recommendation_confidence)} confidence</Badge>
        <Badge tone={str(row.data_sufficiency) === "sufficient" ? "good" : "ok"}>data {str(row.data_sufficiency)}</Badge>
        <span className="faint tiny">
          {meta.model} · {timeAgo(meta.created_at)} {meta.estimated_cost_usd ? `· ${fmtUsd(meta.estimated_cost_usd, 4)}` : ""}
        </span>
      </div>
      <p className="small">{str(row.overall_assessment)}</p>
      <div className="grid-3">
        <Stat label="P(+10%)" value={fmtPct(num(p.p_plus_10_pct))} />
        <Stat label="P(+15%)" value={fmtPct(num(p.p_plus_15_pct))} />
        <Stat label="P(+20%)" value={fmtPct(num(p.p_plus_20_pct))} />
        <Stat label="P(rug/collapse)" value={fmtPct(num(p.p_rug_or_collapse))} tone="bad" />
        <Stat label="Uncertainty" value={str(p.uncertainty)} />
        <Stat label="Horizon" value={row.estimated_time_horizon_minutes ? `${str(row.estimated_time_horizon_minutes)}m` : "n/a"} />
      </div>
      {str(p.rationale) ? <p className="faint small" style={{ marginTop: 4 }}>{str(p.rationale)}</p> : null}
      <Section title="Bullish evidence" items={list(row.bullish_evidence)} tone="good" />
      <Section title="Bearish evidence" items={list(row.bearish_evidence)} tone="bad" />
      <Section title="Whale activity" items={[str(row.whale_activity_interpretation)]} />
      <Section title="Developer / insider risk" items={[str(row.developer_insider_risk)]} />
      <Section title="Liquidity / exit risk" items={[str(row.liquidity_exit_risk)]} />
      <Section title="Potential catalysts" items={list(row.potential_catalysts)} />
      <Section title="Potential manipulation" items={list(row.potential_manipulation)} tone="bad" />
      {scenarios.length ? (
        <div style={{ marginTop: 6 }}>
          <h3>Target scenarios</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>target</th>
                  <th className="num">prob.</th>
                  <th>conditions</th>
                  <th className="num">horizon</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.map((s, i) => (
                  <tr key={i}>
                    <td>+{str(s.target_pct)}%</td>
                    <td className="num">{fmtPct(num(s.probability))}</td>
                    <td>{str(s.conditions)}</td>
                    <td className="num">{s.estimated_time_horizon_minutes ? `${str(s.estimated_time_horizon_minutes)}m` : "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      <Section title="Invalidating conditions" items={list(row.invalidating_conditions)} tone="ok" />
      <Section title="Missing data" items={list(row.missing_data)} />
      <p className="disclaimer">GPT output is an interpretation of the scanner's data, cannot override safety filters, and is not financial advice.</p>
    </>
  );
}

function Section({ title, items, tone }: { title: string; items: string[]; tone?: "good" | "bad" | "ok" }): JSX.Element | null {
  const clean = items.filter((i) => i && i.trim());
  if (!clean.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <h3>{title}</h3>
      <ul className={`clean small ${tone ?? ""}`}>
        {clean.map((i, idx) => (
          <li key={idx}>{i}</li>
        ))}
      </ul>
    </div>
  );
}
