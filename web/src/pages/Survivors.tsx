import { useState } from "react";
import { Address, Badge, Banner, Card, EmptyState, SourceBadge, Spinner, statusTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useDebouncedLiveVersion } from "../hooks/useLive";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { fmtNum, fmtPct, fmtUsd, timeAgo } from "../services/format";
import type { Json } from "../types/api";

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (v == null ? "" : String(v));

function age(h: number | null): string {
  if (h == null) return "n/a";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

export function SurvivorsPage(): JSX.Element {
  const api = useApi();
  const live = useDebouncedLiveVersion(10000);
  const [minLiq, setMinLiq] = useState(5000);
  const [maxDays, setMaxDays] = useState(7);
  const data = useAsync(() => api.survivors({ min_liquidity_usd: minLiq, max_age_days: maxDays, limit: 150 }), [api, live, minLiq, maxDays], { enabled: api.configured });
  const items = (data.data?.items ?? []) as Json[];
  return (
    <>
      <Card title="Survivors - tokens 30 min to 7 days old, re-checked for growth" right={data.loading ? <Spinner /> : null}>
        <p className="small muted">
          Every launch SolHunter saw (plus tokens found through DexScreener profiles/boosts) is re-checked on a schedule after its launch window:
          liquidity, holders, market cap, drawdown from the high, higher lows, turnover and buy pressure. Strong, liquid growers are put back into
          real-time tracking and run through the full pipeline. <SourceBadge kind="estimate" label="DexScreener data" />
        </p>
        <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
          <label className="small">min liquidity $ <input type="number" min={0} step={1000} value={minLiq} onChange={(e) => setMinLiq(Number(e.target.value) || 0)} style={{ width: 90 }} /></label>
          <label className="small">max age (days) <input type="number" min={0.1} max={30} step={0.5} value={maxDays} onChange={(e) => setMaxDays(Number(e.target.value) || 7)} style={{ width: 70 }} /></label>
        </div>
        {data.error ? <Banner kind="error">{errorMessage(data.error)}</Banner> : null}
        {!items.length && !data.loading ? <EmptyState>No survivors yet. Tokens appear here 30+ minutes after launch once they have been re-checked and still have liquidity.</EmptyState> : null}
        {items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>token</th>
                  <th>age</th>
                  <th>phase</th>
                  <th>growth</th>
                  <th>mcap</th>
                  <th>liquidity</th>
                  <th>vs ATH</th>
                  <th>vol 24h</th>
                  <th>buys/sells 1h</th>
                  <th>SolHunter</th>
                  <th>P(2x/24h)</th>
                  <th>signals</th>
                  <th>checked</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => {
                  const latest = (t.latest ?? {}) as Json;
                  const g = (latest.growth ?? {}) as Json;
                  const gs = num(t.growth_score);
                  const dd = num(g.drawdown_from_ath_pct);
                  return (
                    <tr key={str(t.mint)}>
                      <td>
                        <a href={`#/candidates/${str(t.mint)}`} onClick={(e) => { e.preventDefault(); navigate("candidates", str(t.mint)); }}>
                          <b>{str(t.symbol) || <Address value={str(t.mint)} />}</b>
                        </a>
                        {t.boosted ? <Badge tone="info" title="DexScreener profile/boost">boost</Badge> : null} {str(t.migrated_at) ? <Badge tone="good" title="completed bonding curve">migrated</Badge> : null}
                      </td>
                      <td>{age(num(t.age_hours))}</td>
                      <td><Badge tone={str(t.phase) === "established" ? "good" : "ok"}>{str(t.phase)}</Badge></td>
                      <td><b>{gs != null ? (gs * 100).toFixed(0) : "n/a"}</b></td>
                      <td>{fmtUsd(num(latest.market_cap_usd))}</td>
                      <td>{fmtUsd(num(latest.liquidity_usd))}</td>
                      <td style={{ color: dd != null && dd > -30 ? "var(--good)" : dd != null && dd < -60 ? "var(--bad)" : undefined }}>{dd != null ? `${dd.toFixed(0)}%` : "n/a"}</td>
                      <td>{fmtUsd(num(latest.volume_h24_usd))}</td>
                      <td>{fmtNum(num(latest.buys_h1) ?? 0)}/{fmtNum(num(latest.sells_h1) ?? 0)}</td>
                      <td>{t.score_status ? <Badge tone={statusTone(str(t.score_status))}>{str(t.score_status)} {num(t.score) != null ? Math.round(num(t.score) as number) : ""}</Badge> : <span className="small muted">not yet</span>}</td>
                      <td>{num(t.p_2x_24h) != null ? fmtPct(num(t.p_2x_24h)) : "n/a"}</td>
                      <td className="small">{((g.positives as unknown[]) ?? []).slice(0, 2).map(String).join(" · ")}{((g.reasons as unknown[]) ?? []).length ? <span className="muted"> · {((g.reasons as unknown[]) ?? []).slice(0, 1).map(String).join("")}</span> : null}</td>
                      <td className="small muted">{str(latest.captured_at) ? timeAgo(str(latest.captured_at)) : "n/a"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
    </>
  );
}
