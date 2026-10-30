import { Badge, Banner, Card, EmptyState, KV, Spinner, Stat, statusTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useApi } from "../hooks/useSettings";
import { fmtNum, fmtUsd, timeAgo } from "../services/format";
import type { Json } from "../types/api";

const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

export function StatusPage(): JSX.Element {
  const api = useApi();
  const system = useAsync(() => api.systemStatus(), [api], { refreshMs: 20_000, enabled: api.configured });
  const costs = useAsync(() => api.costs(), [api], { refreshMs: 60_000, enabled: api.configured });
  const ai = useAsync(() => api.aiStatus(), [api], { refreshMs: 60_000, enabled: api.configured });
  const models = useAsync(() => api.models(), [api], { enabled: api.configured });
  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  const s = system.data;
  const stats = (s?.scanner.stats ?? {}) as Json;
  const tracker = (stats.tracker ?? {}) as Json;
  const evaluator = (stats.evaluator ?? {}) as Json;
  const pumpportal = (stats.pumpportal ?? {}) as Json;
  const persistence = (stats.persistence ?? {}) as Json;
  const gpt = (evaluator.gpt ?? {}) as Json;
  return (
    <>
      {system.error ? <Banner kind="error">{errorMessage(system.error)}</Banner> : null}
      {s ? (
        <>
          {s.incomplete_data_warning ? <Banner kind="warn">{s.incomplete_data_warning}</Banner> : null}
          {s.config_warnings.map((w) => (
            <Banner key={w} kind="warn">
              config: {w}
            </Banner>
          ))}
          <Card title="Services" right={system.loading ? <Spinner /> : null}>
            <div className="grid-3">
              <Stat label="API" value={<Badge tone="good">{s.api.status}</Badge>} sub={`v${s.api.version} · ${s.api.environment} · ${s.api.memory_mb} MB · ${s.api.ws_clients} ws`} />
              <Stat label="Database" value={<Badge tone={s.database.ok ? "good" : "bad"}>{s.database.ok ? "ok" : "down"}</Badge>} sub={s.database.error ?? ""} />
              <Stat label="Redis" value={<Badge tone={s.redis.ok ? "good" : "bad"}>{s.redis.ok ? "ok" : "down"}</Badge>} sub={s.redis.error ?? ""} />
            </div>
          </Card>
          <Card title="Scanner worker">
            <div className="grid-3">
              <Stat label="Status" value={<Badge tone={s.scanner.stale ? "bad" : statusTone(s.scanner.status)}>{s.scanner.status}</Badge>} sub={s.scanner.last_heartbeat_at ? `heartbeat ${timeAgo(s.scanner.last_heartbeat_at)}` : "no heartbeat"} />
              <Stat label="Tracked" value={str(tracker.tracked) || "n/a"} sub={`events ${fmtNum(Number(tracker.events_processed) || 0)}`} />
              <Stat label="PumpPortal" value={<Badge tone={pumpportal.connected ? "good" : "bad"}>{pumpportal.connected ? "connected" : "disconnected"}</Badge>} sub={`${str(pumpportal.subscriptions) || 0} subs`} />
              <Stat label="Evaluations" value={str(evaluator.evaluations) || "0"} sub={`${str(evaluator.candidates) || 0} cand · ${str(evaluator.watchlist) || 0} watch · ${str(evaluator.hard_fails) || 0} hard fails`} />
              <Stat label="Predictor" value={<span className="small">{str(evaluator.predictor) || "n/a"}</span>} sub={evaluator.predictor_experimental ? "experimental" : "validated"} />
              <Stat label="Persistence" value={`${str(persistence.pending) || 0} pending`} sub={`${fmtNum(Number(persistence.rows_written) || 0)} rows · ${str(persistence.failures) || 0} failures`} />
            </div>
            <KV
              rows={[
                ["worker", s.scanner.worker_id ?? "n/a"],
                ["leader", s.scanner.is_leader ? "yes" : "no"],
                ["uptime", stats.uptime_seconds ? `${Math.floor(Number(stats.uptime_seconds) / 60)} min` : "n/a"],
                ["GPT", `${gpt.enabled ? "enabled" : "disabled"} ${str(gpt.model)} ${gpt.disabled_reason ? `- ${str(gpt.disabled_reason)}` : ""}`],
                ["queue", Object.entries(s.queue).map(([k, v]) => `${k} ${v}`).join(" · ") || "n/a"],
              ]}
            />
            {s.workers.length ? (
              <div className="table-wrap" style={{ marginTop: 6 }}>
                <table>
                  <thead>
                    <tr>
                      <th>worker</th>
                      <th>status</th>
                      <th>heartbeat</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.workers.map((w, i) => (
                      <tr key={i}>
                        <td className="mono">{str(w.worker_id).slice(0, 28)}</td>
                        <td>
                          <Badge tone={w.stale ? "bad" : statusTone(str(w.status))}>{str(w.status)}</Badge> {w.is_leader ? <Badge tone="info">leader</Badge> : null}
                        </td>
                        <td>{timeAgo(str(w.last_heartbeat_at))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>
          <Card title="Providers">
            {s.providers.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>provider</th>
                      <th>status</th>
                      <th>last ok</th>
                      <th>reconnects</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.providers.map((p) => (
                      <tr key={p.provider}>
                        <td>{p.provider}</td>
                        <td>
                          <Badge tone={statusTone(p.status)}>{p.status}</Badge>
                          {p.last_error ? <div className="faint tiny">{p.last_error.slice(0, 80)}</div> : null}
                        </td>
                        <td>{timeAgo(p.last_success_at)}</td>
                        <td>{p.reconnect_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState>No provider status reported yet (the scanner publishes it after start-up).</EmptyState>
            )}
          </Card>
        </>
      ) : system.loading ? (
        <Spinner />
      ) : null}
      <Card title="AI spend">
        {ai.data ? (
          <>
            <div className="grid-3">
              <Stat label="Today" value={fmtUsd(ai.data.today.estimated_cost_usd, 3)} sub={`limit ${fmtUsd(ai.data.today.limit_usd, 2)} · ${ai.data.today.analyses}/${ai.data.today.max_analyses} analyses`} />
              <Stat label="Month" value={fmtUsd(ai.data.month.estimated_cost_usd, 2)} sub={`limit ${fmtUsd(ai.data.month.limit_usd, 2)}`} />
              <Stat label="Model" value={<span className="small">{ai.data.model}</span>} sub={ai.data.enabled ? "enabled" : "disabled"} />
            </div>
            <p className="faint tiny" style={{ marginTop: 4 }}>
              pricing assumption ${ai.data.pricing_assumption.input_per_1m_usd}/1M in · ${ai.data.pricing_assumption.output_per_1m_usd}/1M out - {ai.data.pricing_assumption.note}
            </p>
          </>
        ) : ai.error ? (
          <Banner kind="error">{errorMessage(ai.error)}</Banner>
        ) : (
          <Spinner />
        )}
      </Card>
      <Card title="Estimated monthly cost">
        {costs.data ? (
          <>
            <div className="grid-2">
              <Stat label="Month to date" value={fmtUsd(costs.data.month_to_date_usd, 2)} />
              <Stat label="Projected" value={fmtUsd(costs.data.projected_month_usd, 2)} />
            </div>
            <KV
              rows={[
                ["Railway (configured estimate)", fmtUsd(costs.data.breakdown.railway_estimate_usd)],
                ["Helius plan", fmtUsd(costs.data.breakdown.helius_plan_usd)],
                ["OpenAI month to date", fmtUsd(costs.data.breakdown.openai_month_to_date_usd, 2)],
                ["OpenAI projected", fmtUsd(costs.data.breakdown.openai_projected_usd, 2)],
                ["Helius credits used", `${fmtNum(costs.data.helius_credits.month_to_date)} / ${fmtNum(costs.data.helius_credits.monthly_budget)}`],
              ]}
            />
            <p className="faint tiny" style={{ marginTop: 4 }}>{costs.data.note}</p>
          </>
        ) : costs.error ? (
          <Banner kind="error">{errorMessage(costs.error)}</Banner>
        ) : (
          <Spinner />
        )}
      </Card>
      <Card title="ML models">
        {models.data ? (
          models.data.items.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>model</th>
                    <th>version</th>
                    <th className="num">samples</th>
                    <th>status</th>
                  </tr>
                </thead>
                <tbody>
                  {models.data.items.map((m) => (
                    <tr key={m.id}>
                      <td>{m.name}</td>
                      <td className="mono">{m.version}</td>
                      <td className="num">{m.training_samples}</td>
                      <td>
                        {m.is_active ? <Badge tone="good">active</Badge> : <Badge>stored</Badge>} {m.is_experimental ? <Badge tone="ok">experimental</Badge> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No trained models yet - the heuristic baseline is in use (labelled experimental). Models train automatically once enough labelled history exists.</EmptyState>
          )
        ) : models.error ? (
          <Banner kind="error">{errorMessage(models.error)}</Banner>
        ) : (
          <Spinner />
        )}
      </Card>
    </>
  );
}
