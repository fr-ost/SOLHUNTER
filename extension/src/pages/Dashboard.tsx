import { CandidateRow } from "../components/CandidateRow";
import { Badge, Banner, Card, EmptyState, Spinner, Stat, statusTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useDebouncedLiveVersion } from "../hooks/useLive";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { fmtSol, fmtUsd, shortAddr, timeAgo } from "../services/format";
import type { CandidateItem, Json } from "../types/api";

const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function DashboardPage(): JSX.Element {
  const api = useApi();
  const version = useDebouncedLiveVersion(5000);
  const { data, error, loading, reload } = useAsync(() => api.dashboard(), [api, version], { refreshMs: 30_000, enabled: api.configured });

  if (!api.configured) return <Banner kind="warn">Configure the API base URL and key in Settings to connect to your Railway deployment.</Banner>;
  if (error) return <Banner kind="error">{errorMessage(error)}</Banner>;
  if (!data) return <div className="row"><Spinner /> <span className="muted">Loading dashboard…</span></div>;

  const scanner = data.scanner;
  const scannerTone = scanner.stale || scanner.status === "offline" ? "bad" : statusTone(scanner.status);
  const tracked = num((scanner.stats as Json | undefined)?.tracker && ((scanner.stats as Json).tracker as Json).tracked);
  const degraded = data.providers.filter((p) => p.status === "down" || p.status === "degraded");
  return (
    <>
      <Card
        title="System"
        right={
          <button className="small" onClick={reload} disabled={loading}>
            {loading ? <Spinner /> : "refresh"}
          </button>
        }
      >
        <div className="grid-2">
          <Stat label="Scanner" value={<Badge tone={scannerTone}>{scanner.status}</Badge>} sub={scanner.last_heartbeat_at ? `heartbeat ${timeAgo(scanner.last_heartbeat_at)}` : "no heartbeat seen"} />
          <Stat label="Tracking" value={tracked ?? "n/a"} sub="tokens in memory" />
        </div>
        {scanner.stale ? <Banner kind="warn">Scanner heartbeat is stale - data below may be incomplete or old.</Banner> : null}
        {degraded.length ? <Banner kind="warn">Degraded providers: {degraded.map((p) => p.provider).join(", ")}. Operating with incomplete data.</Banner> : null}
        <div className="row" style={{ marginTop: 6 }}>
          {data.providers.map((p) => (
            <Badge key={p.provider} tone={statusTone(p.status)} title={p.last_error ?? undefined}>
              {p.provider}: {p.status}
            </Badge>
          ))}
          {data.providers.length === 0 ? <span className="faint small">no provider status reported yet</span> : null}
        </div>
      </Card>

      <Card title="Last 24 hours">
        <div className="grid-3">
          <Stat label="Discovered" value={data.counts_24h.discovered} />
          <Stat label="Rejected" value={data.counts_24h.rejected} />
          <Stat label="Candidates" value={data.counts_24h.candidates} tone={data.counts_24h.candidates ? "good" : undefined} />
          <Stat label="Watchlist" value={data.counts_24h.watchlist} />
          <Stat label="High-risk alerts" value={data.counts_24h.high_risk_alerts} tone={data.counts_24h.high_risk_alerts ? "bad" : undefined} />
          <Stat label="Today (live)" value={data.live_counters.discovered ?? 0} sub={`${data.live_counters.trades ?? 0} trades · ${data.live_counters.evaluated_candidate ?? 0} cand.`} />
        </div>
      </Card>

      <Card title="Top candidates" right={<a href="#/candidates">all →</a>}>
        {data.top_candidates.length ? (
          <div className="list">{data.top_candidates.slice(0, 6).map((c) => <CandidateRow key={c.id} item={c as CandidateItem} />)}</div>
        ) : (
          <EmptyState>No shortlisted candidates right now. A day with zero candidates is a valid outcome.</EmptyState>
        )}
      </Card>

      <Card title="Recent whale activity" right={<a href="#/wallets">wallets →</a>}>
        {data.recent_whale_activity.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>when</th>
                  <th>wallet</th>
                  <th>type</th>
                  <th className="num">SOL</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_whale_activity.slice(0, 10).map((e, i) => (
                  <tr key={i} style={{ cursor: "pointer" }} onClick={() => navigate("candidates", str(e.mint))}>
                    <td>{timeAgo(str(e.occurred_at))}</td>
                    <td>
                      <code>{shortAddr(str(e.wallet))}</code> <span className="faint">{str(e.wallet_category)}</span>
                    </td>
                    <td className={str(e.direction) === "buy" ? "good" : "bad"}>{str(e.event_type)}</td>
                    <td className="num">{fmtSol(num(e.sol_amount))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>No whale events recorded yet.</EmptyState>
        )}
      </Card>

      <Card title="Recent alerts" right={<a href="#/history?tab=alerts">history →</a>}>
        {data.recent_alerts.length ? (
          <div className="list">
            {data.recent_alerts.slice(0, 5).map((a, i) => (
              <div key={i} className="item static">
                <div className="title">
                  <span>{str(a.title)}</span>
                  <Badge tone={statusTone(str(a.status))}>{str(a.status)}</Badge>
                </div>
                <div className="meta">
                  <span>{str(a.alert_type)}</span>
                  <span>{timeAgo(str(a.created_at))}</span>
                  {a.urgent ? <Badge tone="bad">urgent</Badge> : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>No alerts sent yet.</EmptyState>
        )}
      </Card>

      <Card title="Estimated operating cost" right={<a href="#/status">details →</a>}>
        <div className="grid-2">
          <Stat label="Month to date" value={fmtUsd(data.costs.month_to_date_usd, 2)} sub="AI + providers (estimate)" />
          <Stat label="Projected / month" value={fmtUsd(data.costs.projected_month_usd, 2)} sub={`incl. Railway est. ${fmtUsd(data.costs.breakdown.railway_estimate_usd)}`} />
        </div>
        <p className="faint tiny" style={{ marginTop: 6 }}>{data.costs.note}</p>
      </Card>
    </>
  );
}
